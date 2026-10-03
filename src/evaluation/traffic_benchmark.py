"""Public-topology controlled routing experiments with an independent FIFO queue.

TNTP link time/capacity/OD values are retained with declared unit conversion. PCUs,
capacity scales, departure schedules, shocks and compliance are synthetic. The
execution model uses capacity service queues, not the planner's BPR function.
"""
import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
import hashlib
import heapq
import json
import math
from pathlib import Path
import re
import time

import networkx as nx
import numpy as np
import pandas as pd

from src.routing.anticipatory import ArrivalRouter, TrafficLedger, Control, free_time, capacity
from src.vehicle.profiles import PROFILES, make_vehicle

ROOT = Path(__file__).resolve().parents[2]
DATASETS = {'SiouxFalls': ('SiouxFalls', 'SiouxFalls', 36., 1000.),
            'Anaheim': ('Anaheim', 'Anaheim', 60., .3048),
            'ChicagoSketch': ('Chicago-Sketch', 'ChicagoSketch', 60., 1609.344)}
METHODS = ['static', 'reactive', 'arrival_selfish', 'balanced', 'no_future', 'no_social', 'no_locality',
           'bpr_balanced', 'bpr_arrival', 'bpr_reactive']


def read_tntp(name, capacity_scale):
    folder, prefix, seconds, metres = DATASETS[name]
    directory = ROOT/'data/public/tntp'/folder
    graph = nx.MultiDiGraph()
    text = (directory/f'{prefix}_net.tntp').read_text()
    first = re.search(r'<FIRST THRU NODE>\s*(\d+)', text)
    graph.graph.update(first_thru_node=int(first[1]) if first else 0,
                       dataset=name, geometry_basis='benchmark_assumption_not_observed')
    for line in text.splitlines():
        if not re.match(r'^\s*\d+\s+\d+', line):
            continue
        a = line.replace(';', '').split()
        u, v = int(a[0]), int(a[1])
        graph.add_edge(u, v, length=float(a[3])*metres, free_time_s=max(.01, float(a[4])*seconds),
                       capacity_pcu_h=float(a[2])*capacity_scale, bpr_alpha=float(a[5]), bpr_power=float(a[6]),
                       highway='primary', width=8., maxheight=5., maxweight=40.,
                       width_source='benchmark_assumption', maxheight_source='benchmark_assumption', maxweight_source='benchmark_assumption')
    od, origin = [], None
    for line in (directory/f'{prefix}_trips.tntp').read_text().splitlines():
        match = re.search(r'Origin\s+(\d+)', line)
        if match:
            origin = int(match[1])
        elif origin is not None:
            for dest, flow in re.findall(r'(\d+)\s*:\s*([\d.eE+-]+)', line):
                if float(flow) > 0 and int(dest) != origin:
                    od.append((origin, int(dest), float(flow)))
    return graph, od


def service_finish(start, work_pcu, cap, background, shock, shock_start, shock_end):
    """Integrate available link-entry service across an exogenous shock window."""
    clock, remaining = start, work_pcu
    while remaining > 1e-12:
        active = shock_start <= clock < shock_end
        rate = cap*max(.05, 1.-background-(shock if active else 0.))/3600.
        boundary = shock_end if active else shock_start if clock < shock_start else math.inf
        available = (boundary-clock)*rate
        if remaining <= available:
            return clock+remaining/rate
        remaining -= available
        clock = boundary
    return clock


def execute_fifo(graph, trips, chosen, shock_edges, shock_start=600., shock_end=1500., background=.15, seed=0):
    rng = np.random.default_rng(seed+10000)
    actual_caps = {e: capacity(d)*float(rng.uniform(.9, 1.1)) for *parts, d in graph.edges(keys=True, data=True) for e in [tuple(parts)]}
    actual_shocks = {e: float(rng.uniform(.55, .75)) for e in sorted(shock_edges)}
    next_service = defaultdict(float)
    events, arrivals = [], {}
    for i, trip in enumerate(trips):
        if chosen[i]:
            heapq.heappush(events, (trip['departure'], i, 0))
    while events:
        entry, i, index = heapq.heappop(events)
        path = chosen[i]
        if index >= len(path):
            arrivals[i] = entry
            continue
        edge = path[index]
        u, v, k = edge
        start = max(entry, next_service[edge])
        finish = service_finish(start, PROFILES[trips[i]['kind']].pcu, actual_caps[edge], background,
                                actual_shocks.get(edge, 0.), shock_start, shock_end)
        next_service[edge] = finish
        exit_time = finish+free_time(graph[u][v][k], trips[i]['kind'])
        heapq.heappush(events, (exit_time, i, index+1))
    return arrivals


def controls(name):
    base = Control()
    if name.startswith('bpr_'):
        base = replace(base, load_model='bpr')
        if name == 'bpr_arrival':
            return replace(base, use_social=False, use_locality=False)
        if name == 'bpr_reactive':
            return replace(base, use_future=False, use_social=False, use_locality=False)
        return base
    if name == 'reactive':
        return replace(base, use_future=False, use_social=False, use_locality=False)
    if name == 'arrival_selfish':
        return replace(base, use_social=False, use_locality=False)
    if name == 'no_future':
        return replace(base, use_future=False)
    if name == 'no_social':
        return replace(base, use_social=False)
    if name == 'no_locality':
        return replace(base, use_locality=False)
    return base


def episode(spec):
    dataset, seed, scale, adoption, count = spec
    started = time.perf_counter()
    graph, od = read_tntp(dataset, scale)
    rng = np.random.default_rng(seed)
    probabilities = np.array([r[2] for r in od]); probabilities /= probabilities.sum()
    selected_od = rng.choice(len(od), count, p=probabilities)
    departures = np.sort(rng.uniform(0., 1800., count))
    kinds = rng.choice(['motorcycle', 'hatchback', 'truck'], count, p=[.4, .5, .1])
    follows = rng.uniform(0., 1., count) < adoption
    trips = [{'origin': od[int(j)][0], 'destination': od[int(j)][1], 'departure': float(t),
              'kind': str(kind), 'follows': bool(follow)} for j, t, kind, follow in zip(selected_od, departures, kinds, follows)]
    base_ledger = TrafficLedger(clock=lambda: 0.)
    router = ArrivalRouter(graph, base_ledger)
    pools = {}
    static_paths = []
    for trip in trips:
        key = (trip['origin'], trip['destination'], trip['kind'])
        if key not in pools:
            pools[key] = router.candidate_paths(key[0], key[1], make_vehicle(key[2]), key[2], 'none',
                            Control(), 0., {}, {}, 0., frozenset())
        paths = pools[key]
        static_paths.append(min(paths, key=lambda path: sum(free_time(graph[u][v][k], key[2]) for u, v, k in path)) if paths else None)
    # Topological centrality selects shock links, without reading execution labels.
    simple = nx.DiGraph()
    for u, v, k, d in graph.edges(keys=True, data=True):
        simple.add_edge(u, v, weight=free_time(d, 'hatchback'))
    centrality = nx.edge_betweenness_centrality(simple, k=min(24, len(simple)), weight='weight', seed=seed)
    top = sorted(centrality, key=centrality.get, reverse=True)[:max(3, int(len(simple.edges)*.02))]
    shock_edges = {(u, v, k) for u, v in top for k in graph[u][v]}
    # Forecast magnitude has multiplicative error; true queue shock is drawn
    # separately. Both are synthetic scenarios, not measured road counts.
    prediction = {e: capacity(graph[e[0]][e[1]][e[2]])*(.15+.65*float(rng.uniform(.75, 1.25))) for e in sorted(shock_edges)}
    rows, summary = [], []
    for method in METHODS:
        ledger = TrafficLedger(clock=lambda: 0.)
        router.ledger = ledger
        for edge, flow in prediction.items():
            ledger.set_forecast(edge, 600., 900., flow)
        chosen, estimates, plans = [], [], []
        for i, trip in enumerate(trips):
            key = (trip['origin'], trip['destination'], trip['kind'])
            if method == 'static':
                plan = router.score(static_paths[i], trip['kind'], trip['departure'], .15,
                        replace(Control(), use_social=False, use_locality=False), {}, {}, key[0], key[1]) if static_paths[i] else None
            else:
                plan = router.plan(key[0], key[1], make_vehicle(key[2]), key[2], trip['departure'], .15,
                                    control=controls(method), commit=True, candidates=pools[key])
            # Handle new plan() return: failures return a dict with status='no_feasible_plan'.
            feasible = plan is not None and plan.get('status') != 'no_feasible_plan'
            plans.append(plan if feasible else None)
            chosen.append(plan['edges'] if feasible and trip['follows'] else static_paths[i] if feasible else None)
            estimates.append(plan['eta_s'] if feasible else None)
        arrivals = execute_fifo(graph, trips, chosen, shock_edges, seed=seed)
        method_rows = []
        for i, trip in enumerate(trips):
            actual = arrivals[i]-trip['departure'] if i in arrivals else None
            row = dict(dataset=dataset, seed=seed, capacity_scale=scale, adoption=adoption, method=method,
                       trip=i, vehicle=trip['kind'], departure_s=trip['departure'], followed=trip['follows'],
                       assigned=plans[i] is not None, actual_time_s=actual, predicted_eta_s=estimates[i],
                       finished_by_3600=i in arrivals and arrivals[i] <= 3600.,
                       penalized_time_s=min(actual, 3600.) if actual is not None else 3600.,
                       local_distance_km=plans[i]['local_distance_km'] if plans[i] else None)
            rows.append(row); method_rows.append(row)
        frame = pd.DataFrame(method_rows)
        summary.append(dict(dataset=dataset, seed=seed, capacity_scale=scale, adoption=adoption, method=method,
                            trips=count, assigned=int(frame.assigned.sum()), throughput_3600=int(frame.finished_by_3600.sum()),
                            mean_time_s=float(frame.actual_time_s.mean()), p90_time_s=float(frame.actual_time_s.quantile(.9)),
                            penalized_mean_s=float(frame.penalized_time_s.mean()),
                            eta_mae_s=float((frame.predicted_eta_s-frame.actual_time_s).abs().mean()),
                            local_distance_km=float(frame.local_distance_km.sum())))
    return rows, summary, time.perf_counter()-started


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--datasets', nargs='+', choices=list(DATASETS), default=list(DATASETS))
    parser.add_argument('--seeds', nargs='+', type=int, default=[41, 42, 43, 44, 45])
    parser.add_argument('--capacity-scales', nargs='+', type=float, default=[.025, .05])
    parser.add_argument('--adoptions', nargs='+', type=float, default=[1., .7])
    parser.add_argument('--trips', type=int, default=400)
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--output', type=Path, default=ROOT/'experiments/traffic_control_v2')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    specs = [(d, seed, scale, adoption, args.trips) for d in args.datasets for seed in args.seeds
             for scale in args.capacity_scales for adoption in args.adoptions]
    rows, summaries = [], []
    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(episode, spec): spec for spec in specs}
        for future in as_completed(futures):
            values, summary, elapsed = future.result()
            rows.extend(values); summaries.extend(summary)
            print(f'Completed {futures[future]} in {elapsed:.1f}s ({len(summaries)//len(METHODS)}/{len(specs)} episodes)', flush=True)
    sort = ['dataset', 'seed', 'capacity_scale', 'adoption', 'method']
    frame = pd.DataFrame(rows).sort_values(sort+['trip'])
    frame.to_csv(args.output/'trips.csv', index=False)
    summary = pd.DataFrame(summaries).sort_values(sort)
    summary.to_csv(args.output/'episodes.csv', index=False)
    aggregate = summary.groupby(['dataset', 'capacity_scale', 'adoption', 'method'], as_index=False).agg(
        mean_time_s=('mean_time_s', 'mean'), p90_time_s=('p90_time_s', 'mean'),
        penalized_mean_s=('penalized_mean_s', 'mean'), throughput_3600=('throughput_3600', 'mean'),
        assigned=('assigned', 'mean'), eta_mae_s=('eta_mae_s', 'mean'))
    aggregate.to_csv(args.output/'summary.csv', index=False)
    paired = summary.pivot(index=['dataset', 'seed', 'capacity_scale', 'adoption'], columns='method', values='penalized_mean_s')
    rng = np.random.default_rng(123)
    effects = {}
    for method in METHODS[1:]:
        delta = (paired[method]-paired['static']).to_numpy()
        samples = rng.choice(delta, (2000, len(delta)), replace=True).mean(axis=1)
        effects[method] = {'mean_delta_s': float(delta.mean()), 'episode_bootstrap_95ci_s': list(np.quantile(samples, [.025, .975])),
                           'mean_relative_reduction_pct': float(((paired['static']-paired[method])/paired['static']*100).mean())}
    (args.output/'paired.json').write_text(json.dumps(effects, indent=2), encoding='utf-8')
    inputs = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'data/public/tntp').glob('*/*.tntp')}
    sources = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__), ROOT/'src/routing/anticipatory.py', ROOT/'src/vehicle/profiles.py', ROOT/'src/vehicle/geometry_constraints.py']}
    (args.output/'manifest.json').write_text(json.dumps({'args': {**vars(args), 'output': str(args.output)},
        'episodes': len(specs), 'rows': len(frame), 'duration_s': time.perf_counter()-started,
        'planner': asdict(Control()), 'sources': sources, 'inputs': inputs,
        'execution': 'FIFO point queues with independently perturbed capacities and exogenous service loss; no BPR travel function in execution',
        'limitations': ['Synthetic departures, PCU vehicle classes, scaled capacities, compliance and forecast shocks.',
                        'TNTP does not supply measured vehicle geometry or buildings; locality ablations are expected null on all-primary benchmark edges.',
                        'Sioux Falls is a debugging network; Chicago is aggregated and its base OD understates congestion.',
                        'No field effectiveness or global optimum claim; candidate pool is frozen from six diversified free-flow shortest paths.',
                        'Confidence intervals resample paired episode scenarios, not independent individual vehicles.']}, indent=2), encoding='utf-8')
    print(json.dumps(effects, indent=2), flush=True)


if __name__ == '__main__':
    main()
