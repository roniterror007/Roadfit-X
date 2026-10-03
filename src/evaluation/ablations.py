"""Paired reproducible synthetic ablations. Never evaluates imputation as truth."""
import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path
import networkx as nx
import numpy as np
import osmnx as ox

from src.data.enrich_graph import synthetic_benchmark_graphs
from src.evaluation.baseline_routes import route_baseline, prepare_baseline
from src.evaluation.metrics_engine import MetricsEngine, METRIC_FIELDS
from src.routing.risk_aware_router import (
    SearchConfig, RoutingSearchLimit, route_risk_aware, prepare_search,
    _physics_travel_time, haversine_admissible_heuristic)
from src.vehicle.vehicle_digital_twin import VehicleDigitalTwin
from src.vehicle.geometry_constraints import highway_category

def benchmark_vehicle(policy='conservative'):
    return VehicleDigitalTwin(
        vehicle_type='delivery_van', width_m=2.4, height_m=2.8, gross_weight_t=5.,
        axle_load_t=2.5, wheelbase_m=3., turning_radius_m=6., ground_clearance_m=.2,
        max_grade_pct=15., surface_tolerance=['asphalt', 'concrete', 'paved'],
        rain_tolerance='medium', risk_preference='moderate', unknown_data_policy=policy)

def model_specs():
    full = SearchConfig(exact=True)
    return [
        ('B0 ETA', None, 'exploratory', 'eta'),
        ('B1 Hard constraints', None, 'conservative', 'hard'),
        ('RF Exact', full, 'conservative', 'search'),
        ('RF Rounded', replace(full, exact=False), 'conservative', 'search'),
        ('RF Union bound', replace(full, risk_aggregation='union_bound'), 'conservative', 'search'),
        ('RF Union rounded', replace(full, exact=False, risk_aggregation='union_bound'), 'conservative', 'search'),
        ('RF Strict', full, 'strict', 'search'),
        ('RF Exploratory', full, 'exploratory', 'search'),
        ('RF -geometry', replace(full, use_geometry=False, use_clearance=False), 'conservative', 'search'),
        ('RF -hazard', replace(full, use_hazard=False), 'conservative', 'search'),
        ('RF -uncertainty', replace(full, use_uncertainty=False), 'conservative', 'search'),
        ('RF -clearance', replace(full, use_clearance=False), 'conservative', 'search'),
        ('RF -reverse bounds', replace(full, use_reverse_bounds=False), 'conservative', 'search'),
        ('Oracle Hard constraints', None, 'strict', 'oracle'),
    ]

def categorize_nodes(G):
    arterial, residential = set(), set()
    for u, v, data in G.edges(data=True):
        category = highway_category(data)
        if category in ('primary', 'secondary', 'tertiary', 'trunk', 'motorway'):
            arterial.update((u, v))
        elif category in ('residential', 'living_street', 'unclassified', 'service'):
            residential.update((u, v))
    return sorted(arterial), sorted(residential-arterial)

def sample_stratified_od_pairs(G, n_art_res=40, n_res_res=40, n_art_art=20, seed=104729):
    component = max(nx.strongly_connected_components(G), key=len)
    if len(component) < 2:
        raise ValueError('graph needs a strongly connected component with at least two nodes')
    arterial, residential = categorize_nodes(G.subgraph(component))
    all_nodes = sorted(component)
    rng = random.Random(seed)
    requested = [
        ('Arterial->Residential', arterial, residential, n_art_res),
        ('Residential->Residential', residential, residential, n_res_res),
        ('Arterial->Arterial', arterial, arterial, n_art_art),
    ]
    pairs, used = [], set()
    for category, sources, targets, count in requested:
        for _ in range(count):
            found = None
            for _attempt in range(1000):
                u, v = rng.choice(sources or all_nodes), rng.choice(targets or all_nodes)
                if u == v or (u, v) in used:
                    continue
                if category == 'Residential->Residential':
                    a, b = G.nodes[u], G.nodes[v]
                    distance = haversine_admissible_heuristic(a.get('y', 0.), a.get('x', 0.),
                                                              b.get('y', 0.), b.get('x', 0.), 1.)
                    if distance < 1500:
                        continue
                found = (u, v, category if sources and targets else 'SCC fallback')
                break
            if found is None:
                # Bounded fallback; never hang on a small graph or absent strata.
                available = [(u, v) for u in all_nodes for v in all_nodes
                             if u != v and (u, v) not in used] if len(all_nodes) < 100 else None
                if available is not None:
                    if not available:
                        raise ValueError('requested more unique OD pairs than the graph supports')
                    u, v = rng.choice(available)
                else:
                    for _attempt in range(10000):
                        u, v = rng.sample(all_nodes, 2)
                        if (u, v) not in used:
                            break
                    else:
                        raise ValueError('could not sample a unique OD pair')
                found = (u, v, 'SCC fallback')
            pairs.append(found)
            used.add(found[:2])
    rng.shuffle(pairs)
    return pairs

def extract_path_edges(G, path_nodes):
    return [(u, v, key, dict(G[u][v][key]))
            for u, v in zip(path_nodes[:-1], path_nodes[1:])
            for key in [min(G[u][v], key=lambda k: _physics_travel_time(G[u][v][k]))]] if path_nodes else []

def get_path_travel_time(path_edges):
    return sum(_physics_travel_time(data) for _, _, _, data in path_edges or [])

def evaluate_condition(graph_path, pairs, seed, drop_rate, scenarios, vehicle_values=None):
    base = ox.load_graphml(graph_path)
    observed, truth = synthetic_benchmark_graphs(base, drop_rate=drop_rate, seed=seed)
    vehicle = VehicleDigitalTwin(**vehicle_values) if vehicle_values else benchmark_vehicle()
    specs = model_specs()
    prepared = {}
    preparation_ms = {}
    for name, config, policy, kind in specs:
        profile = vehicle.model_copy(update={'unknown_data_policy': policy})
        start = time.perf_counter()
        if kind == 'search':
            prepared[name] = prepare_search(observed, profile, config=config)
        else:
            graph = truth if kind == 'oracle' else observed
            prepared[name] = prepare_baseline(graph, profile, hard_constraints=kind in ('hard', 'oracle'))
        preparation_ms[name] = (time.perf_counter()-start)*1000.
    rows = []
    for pair_id, (origin, dest, category) in enumerate(pairs, 1):
        baseline_time = None
        for name, config, policy, kind in specs:
            graph = truth if kind == 'oracle' else observed
            profile = vehicle.model_copy(update={'unknown_data_policy': policy})
            diagnostic = {}
            start = time.perf_counter()
            try:
                if kind == 'search':
                    nodes, edges, stats = route_risk_aware(
                        graph, origin, dest, profile, config=config,
                        diagnostics=diagnostic, prepared=prepared[name])
                else:
                    nodes, edges, stats = route_baseline(
                        graph, origin, dest, profile,
                        hard_constraints=kind in ('hard', 'oracle'), prepared=prepared[name])
                status = 'ok' if nodes is not None else 'no_route'
            except RoutingSearchLimit:
                nodes, edges, stats, status = None, None, {}, 'search_limit'
            elapsed_ms = (time.perf_counter()-start)*1000.
            duration = stats.get('travel_time_s')
            if kind == 'eta':
                baseline_time = duration
            # Metrics are evaluated after timing search to avoid mixing MC runtime into routing.
            metrics = MetricsEngine(profile).evaluate_route(
                observed, nodes, edges, duration or 0., baseline_time or 0.,
                truth_graph=truth, seed=seed*100000+pair_id, num_scenarios=scenarios)
            safe_success = int(nodes is not None and metrics['PhysicalFailure'] == 0)
            rows.append({
                'Seed': seed, 'MissingRate': drop_rate, 'PairID': pair_id,
                'Origin': origin, 'Destination': dest, 'Category': category,
                'Model': name, 'Status': status, 'Feasible': int(nodes is not None),
                'SafeSuccess': safe_success, 'TravelTime_s': duration,
                'Runtime_ms': elapsed_ms, 'Preparation_ms': preparation_ms[name],
                'ExpandedLabels': diagnostic.get('expanded_labels'),
                'GeneratedLabels': diagnostic.get('generated_labels'),
                'MaxLabelsPerNode': diagnostic.get('max_labels_per_node'),
                'PathEdges': json.dumps([(u, v, k) for u, v, k, _ in edges]) if edges else None,
                **metrics,
            })
    return rows

def _interval(values, rng, samples=2000):
    values = np.array(values, dtype=float)
    if not len(values):
        return None
    means = values[rng.integers(0, len(values), size=(samples, len(values)))].mean(axis=1)
    return [float(np.quantile(means, .025)), float(np.quantile(means, .975))]

def summarize(rows):
    summaries, contrasts = [], []
    rng = np.random.default_rng(271828)
    models = [s[0] for s in model_specs()]
    for rate in sorted({r['MissingRate'] for r in rows}):
        for model in models:
            group = [r for r in rows if r['MissingRate'] == rate and r['Model'] == model]
            valid = [r for r in group if r['Feasible']]
            summary = {'MissingRate': rate, 'Model': model, 'N': len(group),
                       'Returned': len(valid), 'SearchLimits': sum(r['Status']=='search_limit' for r in group),
                       'ReturnRate_%': len(valid)/len(group)*100.,
                       'SafeSuccess_%': sum(r['SafeSuccess'] for r in group)/len(group)*100.,
                       'ConditionalPhysicalFailure_%': sum(r['PhysicalFailure'] for r in valid)/len(valid)*100. if valid else None,
                       'MedianRuntime_ms': float(np.median([r['Runtime_ms'] for r in group])),
                       'P95Runtime_ms': float(np.quantile([r['Runtime_ms'] for r in group], .95))}
            for metric in ('TRR', 'ETTP_%', 'MDEF_%', 'MinClearance_m'):
                numbers = [r[metric] for r in valid if r[metric] is not None]
                summary['Mean_'+metric] = float(np.mean(numbers)) if numbers else None
            summaries.append(summary)
        reference = {(r['Seed'], r['PairID']): r for r in rows
                     if r['MissingRate']==rate and r['Model']=='RF Exact'}
        for model in models:
            matched = [(reference[r['Seed'], r['PairID']], r) for r in rows
                       if r['MissingRate']==rate and r['Model']==model]
            valid = [(a, b) for a, b in matched if a['Feasible'] and b['Feasible']]
            # Cluster repeated realizations by fixed OD pair. Intervals are descriptive;
            # one city's overlapping routes do not provide independent population data.
            for metric, pairs in [('SafeSuccess', matched), ('TravelTime_s', valid),
                                  ('TRR', valid), ('PhysicalFailure', valid)]:
                by_od = {}
                for a, b in pairs:
                    if a[metric] is not None and b[metric] is not None:
                        by_od.setdefault(a['PairID'], []).append(b[metric]-a[metric])
                values = [sum(v)/len(v) for v in by_od.values()]
                contrasts.append({
                    'MissingRate': rate, 'Reference': 'RF Exact', 'Model': model,
                    'Metric': metric, 'PairedN': sum(map(len, by_od.values())),
                    'ODClusters': len(values), 'MeanDelta': float(np.mean(values)) if values else None,
                    'Descriptive95Interval': _interval(values, rng),
                })
    return summaries, contrasts

def _write_csv(path, rows):
    if not rows:
        raise ValueError('no experiment records')
    with Path(path).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

def run_experiment(G, vehicle, num_pairs, output_csv, seed=42):
    # Compatibility entry point; callers should use the CLI for recorded multi-seed runs.
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory)/'graph.graphml')
        ox.save_graphml(G, path)
        pairs = sample_stratified_od_pairs(G, int(.4*num_pairs), int(.4*num_pairs),
                                           num_pairs-2*int(.4*num_pairs))
        rows = evaluate_condition(path, pairs, seed, .3, 1000, vehicle.model_dump())
    _write_csv(output_csv, rows)
    return rows

def run_all_ablations(G, vehicle, orig_node, dest_node):
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory)/'graph.graphml')
        ox.save_graphml(G, path)
        rows = evaluate_condition(path, [(orig_node, dest_node, 'specified')], 42, .3, 1000, vehicle.model_dump())
    return {r['Model']: r for r in rows}

def git_revision(root):
    """Retain provenance when available; a source archive need not contain .git."""
    try:
        return subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=root, text=True,
            stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--graph', required=True)
    parser.add_argument('--num-pairs', type=int, default=100)
    parser.add_argument('--output', default='experiments/reproducible/ablations.csv')
    parser.add_argument('--seeds', type=int, nargs='+', default=[41, 42, 43])
    parser.add_argument('--missing-rates', type=float, nargs='+', default=[0., .3, .6])
    parser.add_argument('--scenarios', type=int, default=1000)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--orig-node', type=int)
    parser.add_argument('--dest-node', type=int)
    args = parser.parse_args()
    if args.num_pairs < 1 or args.scenarios < 1 or args.workers < 1:
        parser.error('pairs, scenarios, and workers must be positive')
    if any(not 0 <= rate <= 1 for rate in args.missing_rates):
        parser.error('missing rates must lie in [0, 1]')
    if any(seed < 0 for seed in args.seeds) or len(set(args.seeds)) != len(args.seeds):
        parser.error('seeds must be unique nonnegative integers')
    if len(set(args.missing_rates)) != len(args.missing_rates):
        parser.error('missing rates must be unique')
    if (args.orig_node is None) != (args.dest_node is None):
        parser.error('provide both origin and destination')
    graph_path = str(Path(args.graph).resolve())
    graph = ox.load_graphml(graph_path)
    if args.orig_node is not None:
        if args.orig_node == args.dest_node or args.orig_node not in graph or args.dest_node not in graph:
            parser.error('provide distinct existing node IDs')
        pairs = [(args.orig_node, args.dest_node, 'specified')]
    else:
        n = args.num_pairs
        pairs = sample_stratified_od_pairs(graph, int(.4*n), int(.4*n), n-2*int(.4*n))
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    packages = {}
    for name in ('networkx', 'numpy', 'osmnx', 'scipy', 'fastapi', 'pydantic', 'scikit-learn'):
        packages[name] = importlib.metadata.version(name)
    root = Path(__file__).resolve().parents[2]
    code_hash = hashlib.sha256()
    for file in sorted((root/'src').rglob('*.py')):
        code_hash.update(str(file.relative_to(root)).encode())
        code_hash.update(file.read_bytes())
    manifest = {
        'status': 'running', 'graph': graph_path,
        'graph_sha256': hashlib.sha256(Path(graph_path).read_bytes()).hexdigest(),
        'source_sha256': code_hash.hexdigest(),
        'git_commit': git_revision(root),
        'python': platform.python_version(), 'platform': platform.platform(),
        'logical_cpus': os.cpu_count(), 'packages': packages,
        'vehicle': benchmark_vehicle().model_dump(), 'seeds': args.seeds,
        'missing_rates': args.missing_rates, 'od_seed': 104729, 'pairs': pairs,
        'models': [{'name': name, 'config': asdict(config) if config else None, 'policy': policy, 'kind': kind}
                   for name, config, policy, kind in model_specs()],
        'scenarios': args.scenarios, 'workers': args.workers,
        'data_basis': 'OSM topology; synthetic dimensions; evaluator-only synthetic truth',
        'ci_basis': 'descriptive paired OD-cluster bootstrap; not population inference',
    }
    manifest_path = output.with_suffix('.manifest.json')
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    started = time.perf_counter()
    all_rows = []
    conditions = [(seed, rate) for seed in args.seeds for rate in args.missing_rates]
    # Parallel CPU tasks are experiment workers, not agent delegation.
    with ProcessPoolExecutor(max_workers=min(args.workers, len(conditions))) as executor:
        futures = {executor.submit(evaluate_condition, graph_path, pairs, seed, rate, args.scenarios): (seed, rate)
                   for seed, rate in conditions}
        for future in as_completed(futures):
            rows = future.result()  # Unexpected failures stop the run; never silently invent metrics.
            all_rows.extend(rows)
            seed, rate = futures[future]
            print(f'Completed seed={seed}, missing={rate:.0%}: {len(rows)} records', flush=True)
    all_rows.sort(key=lambda r: (r['Seed'], r['MissingRate'], r['PairID'], r['Model']))
    _write_csv(output, all_rows)
    summaries, contrasts = summarize(all_rows)
    _write_csv(output.with_suffix('.summary.csv'), summaries)
    output.with_suffix('.paired.json').write_text(json.dumps(contrasts, indent=2), encoding='utf-8')
    manifest.update(status='complete', records=len(all_rows), duration_seconds=time.perf_counter()-started)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(f'Wrote {len(all_rows)} paired records to {output}; elapsed {manifest["duration_seconds"]:.1f}s', flush=True)

if __name__ == '__main__':
    main()
