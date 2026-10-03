"""Vehicle/locality ablations on six OSM Bengaluru regions, with synthetic demand."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time

import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd

from src.evaluation.traffic_benchmark import execute_fifo, controls, METHODS
from src.routing.anticipatory import ArrivalRouter, TrafficLedger, Control, free_time, capacity, LOCAL
from src.vehicle.profiles import make_vehicle
from src.vehicle.geometry_constraints import highway_category, compute_geometry_margins, _parse_osm_float

ROOT = Path(__file__).resolve().parents[2]
REGIONS = ['Yeshwantpur', 'Hebbal', 'Indiranagar', 'Whitefield', 'Jayanagar', 'ElectronicCity']


def episode(spec):
    region, seed, scale, count, od_policy = spec
    started = time.perf_counter()
    graph = ox.load_graphml(ROOT/f'data/public/bengaluru_regions/{region}.graphml')
    for _, _, _, data in graph.edges(keys=True, data=True):
        data['capacity_pcu_h'] = capacity(data)*scale
    # Sample ODs from the largest strongly connected topology component. This
    # does not ensure every vehicle class is feasible; rejected requests count.
    component = max(nx.strongly_connected_components(graph), key=len)
    nodes = sorted(component)
    truck_anchors = None
    if od_policy == 'arterial':
        anchor_router = ArrivalRouter(graph, TrafficLedger(clock=lambda: 0.))
        feasible = anchor_router.prepare(make_vehicle('truck', 'exploratory'), 'truck', 'none')
        truck_anchors = sorted(max(nx.strongly_connected_components(feasible), key=len))
    rng = np.random.default_rng(seed)
    trips = []
    for departure in np.sort(rng.uniform(0., 1800., count)):
        origin, destination = rng.choice(nodes, 2, replace=False)
        kind = str(rng.choice(['motorcycle', 'hatchback', 'truck'], p=[.4, .5, .1]))
        if kind == 'truck' and truck_anchors is not None:
            origin, destination = rng.choice(truck_anchors, 2, replace=False)
        trips.append({'origin': int(origin), 'destination': int(destination), 'kind': kind, 'departure': float(departure)})
    router = ArrivalRouter(graph, TrafficLedger(clock=lambda: 0.))
    pools, nofit_pools = {}, {}
    static_paths = []
    for trip in trips:
        key = (trip['origin'], trip['destination'], trip['kind'])
        vehicle = make_vehicle(key[2], 'exploratory')
        pools[key] = router.candidate_paths(key[0], key[1], vehicle, key[2], 'none', Control(), 0., {}, {}, 0., frozenset())
        nofit_pools[key] = router.candidate_paths(key[0], key[1], vehicle, key[2], 'none', replace(Control(), use_vehicle_fit=False), 0., {}, {}, 0., frozenset())
        paths = pools[key]
        static_paths.append(min(paths, key=lambda path: sum(free_time(graph[u][v][k], key[2]) for u, v, k in path)) if paths else None)
    # Shocks on structural junction links; future load forecast is perturbed
    # independently from execution's capacity and background-service reductions.
    ranked = sorted(graph.edges(keys=True), key=lambda e: -(graph.degree(e[0])+graph.degree(e[1])))
    shock_edges = set(ranked[:max(3, len(ranked)//100)])
    prediction = {e: capacity(graph[e[0]][e[1]][e[2]])*(.15+.65*float(rng.uniform(.75, 1.25))) for e in sorted(shock_edges)}
    rows, summaries = [], []
    for method in METHODS+['no_fit']:
        ledger = TrafficLedger(clock=lambda: 0.)
        router.ledger = ledger
        for edge, flow in prediction.items():
            ledger.set_forecast(edge, 600., 900., flow)
        chosen, plans = [], []
        for i, trip in enumerate(trips):
            key = (trip['origin'], trip['destination'], trip['kind'])
            if method == 'static':
                plan = router.score(static_paths[i], key[2], trip['departure'], .15,
                                    replace(Control(), use_social=False, use_locality=False), {}, {}, key[0], key[1]) if static_paths[i] else None
            else:
                control = replace(Control(), use_vehicle_fit=False) if method == 'no_fit' else controls(method)
                plan = router.plan(key[0], key[1], make_vehicle(key[2], 'exploratory'), key[2], trip['departure'], .15,
                                   control=control, commit=True, candidates=nofit_pools[key] if method == 'no_fit' else pools[key])
            plans.append(plan); chosen.append(plan['edges'] if plan else None)
        arrivals = execute_fifo(graph, trips, chosen, shock_edges, seed=seed)
        current = []
        for i, trip in enumerate(trips):
            path = chosen[i] or []
            total = sum(float(graph[u][v][k].get('length', 0.)) for u, v, k in path)
            violation = unknown = local = 0.
            for u, v, k in path:
                d = graph[u][v][k]; length = float(d.get('length', 0.))
                margins = compute_geometry_margins(d, make_vehicle(trip['kind'], 'exploratory'))
                if any(x is None or x < 0 for x in margins.values()):
                    violation += length
                if not np.isfinite(_parse_osm_float(d.get('width'), np.nan)):
                    unknown += length
                if highway_category(d) in LOCAL:
                    local += length
            actual = arrivals[i]-trip['departure'] if i in arrivals else None
            row = dict(region=region, seed=seed, capacity_scale=scale, method=method, trip=i, vehicle=trip['kind'],
                       assigned=plans[i] is not None, actual_time_s=actual,
                       predicted_eta_s=plans[i]['eta_s'] if plans[i] else None,
                       penalized_time_s=min(actual, 3600.) if actual is not None else 3600.,
                       local_distance_m=local, distance_m=total, modeled_violation_m=violation, unknown_width_m=unknown,
                       finished_by_3600=i in arrivals and arrivals[i] <= 3600.)
            rows.append(row); current.append(row)
        frame = pd.DataFrame(current)
        summaries.append(dict(region=region, seed=seed, capacity_scale=scale, method=method, trips=count,
                              assigned=int(frame.assigned.sum()), mean_time_s=float(frame.actual_time_s.mean()),
                              p90_time_s=float(frame.actual_time_s.quantile(.9)), penalized_mean_s=float(frame.penalized_time_s.mean()),
                              throughput_3600=int(frame.finished_by_3600.sum()), local_distance_km=float(frame.local_distance_m.sum()/1000),
                              modeled_violation_km=float(frame.modeled_violation_m.sum()/1000),
                              eta_mae_s=float((frame.predicted_eta_s-frame.actual_time_s).abs().mean()),
                              unknown_width_pct=100*frame.unknown_width_m.sum()/max(.001, frame.distance_m.sum())))
    return rows, summaries, time.perf_counter()-started


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--regions', nargs='+', choices=REGIONS, default=REGIONS)
    parser.add_argument('--seeds', nargs='+', type=int, default=[41, 42, 43])
    parser.add_argument('--capacity-scales', nargs='+', type=float, default=[.5, 1.])
    parser.add_argument('--trips', type=int, default=80)
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--od-policy', choices=['all_nodes', 'arterial'], default='all_nodes')
    parser.add_argument('--output', type=Path, default=ROOT/'experiments/bengaluru_control')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    specs = [(region, seed, scale, args.trips, args.od_policy) for region in args.regions for seed in args.seeds for scale in args.capacity_scales]
    started = time.perf_counter()
    rows, summaries = [], []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(episode, spec): spec for spec in specs}
        for future in as_completed(futures):
            r, s, elapsed = future.result(); rows.extend(r); summaries.extend(s)
            print(f'Completed {futures[future]} in {elapsed:.1f}s ({len(summaries)//(len(METHODS)+1)}/{len(specs)} episodes)', flush=True)
    frame = pd.DataFrame(rows).sort_values(['region', 'seed', 'capacity_scale', 'method', 'trip'])
    summary = pd.DataFrame(summaries).sort_values(['region', 'seed', 'capacity_scale', 'method'])
    frame.to_csv(args.output/'trips.csv', index=False)
    summary.to_csv(args.output/'episodes.csv', index=False)
    paired = summary.pivot(index=['region', 'seed', 'capacity_scale'], columns='method', values='penalized_mean_s')
    rng = np.random.default_rng(123)
    effects = {}
    for method in METHODS[1:]+['no_fit']:
        delta = (paired[method]-paired.static).to_numpy()
        bootstrap = rng.choice(delta, (2000, len(delta)), replace=True).mean(axis=1)
        effects[method] = {'mean_delta_s': float(delta.mean()), 'episode_bootstrap_95ci_s': list(np.quantile(bootstrap, [.025, .975])),
                           'relative_reduction_pct': float(((paired.static-paired[method])/paired.static*100).mean())}
    (args.output/'paired.json').write_text(json.dumps(effects, indent=2), encoding='utf-8')
    sources = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__), ROOT/'src/routing/anticipatory.py', ROOT/'src/vehicle/profiles.py', ROOT/'src/evaluation/traffic_benchmark.py']}
    (args.output/'manifest.json').write_text(json.dumps({'episodes': len(specs), 'rows': len(frame),
        'duration_s': time.perf_counter()-started, 'args': {**vars(args), 'output': str(args.output)}, 'sources': sources,
        'regional_manifest_sha256': hashlib.sha256((ROOT/'data/public/bengaluru_regions/manifest.json').read_bytes()).hexdigest(),
        'data_policy': 'exploratory category priors; NOT a physical safety certification',
        'limitations': ['OSM tags and building density are observed public proxies; demand, traffic shocks, capacities and PCUs are synthetic.',
                        'Induced regional subgraphs exclude paths outside each rectangle; citywide optimality is not tested.',
                        'Physical violation metric compares the same declared geometry model, not independently measured road clearance.',
                        'Missing maxweight means full observed constraint coverage is zero; no actual dwelling, parked-car or live-traffic counts.',
                        'no_fit is an evaluation ablation that can return physically infeasible model paths; unavailable in public routing API.']}, indent=2), encoding='utf-8')
    print(json.dumps(effects, indent=2), flush=True)


if __name__ == '__main__':
    main()
