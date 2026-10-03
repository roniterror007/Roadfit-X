"""Controlled departure-policy comparison in independent SUMO microsimulation.

Pan et al. selectors are adaptations to a common frozen candidate pool and
departure-only advice. This does NOT reproduce their upstream vehicle selection,
periodic en-route interventions, or Brooklyn/Newark experiments.

Changes from the original study:
  - Background traffic defaults to 0.0 (was 0.15) since SUMO has no background fleet.
  - Added queue-aware routing variant that initializes from observed SUMO queues.
  - Added graduated locality variant that replaces the hard cutoff.
  - Per-trip failure diagnostics are collected for analysis.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
import hashlib
from itertools import islice
import json
import math
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import libsumo as sim
import networkx as nx
import numpy as np
import pandas as pd
import sumolib

from src.routing.anticipatory import ArrivalRouter, TrafficLedger, Control, capacity, LOCAL
from src.routing.prior_art import choose_path, greenshields_time
from src.routing.queue_model import observe_queue_from_sumo, estimate_signal_delay
from src.vehicle.profiles import PROFILES, make_vehicle

ROOT = Path(__file__).resolve().parents[2]
CLASSES = {'motorcycle': 'motorcycle', 'hatchback': 'passenger', 'truck': 'truck'}
METHODS = ['static', 'dsp', 'rksp', 'ebksp',
           'roadfit', 'roadfit_no_future', 'roadfit_no_locality',
           'roadfit_soft_locality', 'roadfit_graduated', 'roadfit_queue_aware']
HORIZON = 3600
# Honest default: no background fleet exists in SUMO.
# Set >0 only when a separate background fleet is injected.
DEFAULT_BACKGROUND = 0.0


def load_network(region, signal):
    base = ROOT / f'data/public/sumo/{region}-{signal}'
    meta = json.loads((base/'manifest.json').read_text())
    net = sumolib.net.readNet(str(base/'network.net.xml'))
    edge_map, graph, storage, free = {}, nx.MultiDiGraph(), {}, {}
    for e in net.getEdges():
        name = e.getID(); d = meta['edges'][name]
        u, v = e.getFromNode().getID(), e.getToNode().getID()
        graph.add_node(u, x=d['from_lonlat'][0], y=d['from_lonlat'][1])
        graph.add_node(v, x=d['to_lonlat'][0], y=d['to_lonlat'][1])
        attributes = dict(highway=d['highway'], length=e.getLength(), free_time_s=e.getLength()/e.getSpeed(),
                          lanes=e.getLaneNumber(), oneway=True, building_count_50m=d['building_count_50m'])
        attributes['capacity_pcu_h'] = capacity(attributes)
        graph.add_edge(u, v, key=name, **attributes)
        edge_map[name] = (u, v, name)
        storage[name] = max(.1, e.getLength()*e.getLaneNumber()/7.5)
        free[name] = attributes['free_time_s']
    movements = {}
    for kind, vclass in CLASSES.items():
        h = nx.DiGraph()
        for e in net.getEdges():
            if not e.allows(vclass):
                continue
            h.add_node(e.getID())
            for successor, connections in e.getOutgoing().items():
                if successor.allows(vclass) and any(c.getFromLane().allows(vclass) and c.getToLane().allows(vclass) for c in connections):
                    h.add_edge(e.getID(), successor.getID(), cost=free[successor.getID()])
        component = max(nx.strongly_connected_components(h), key=len)
        movements[kind] = h.subgraph(component).copy()
    return base, net, meta, graph, edge_map, storage, free, movements


def make_demand(net, movements, seed, count, adoption):
    rng = np.random.default_rng(seed)
    anchors = {}
    for kind, h in movements.items():
        coords = {e: net.getEdge(e).getFromNode().getCoord() for e in h}
        x = sorted(h, key=lambda e: (coords[e][0], e)); y = sorted(h, key=lambda e: (coords[e][1], e))
        anchors[kind] = [(x[:4], x[-4:]), (x[-4:], x[:4]), (y[:4], y[-4:]), (y[-4:], y[:4])]
    trips = []
    for i, departure in enumerate(np.sort(rng.uniform(0, 900, count))):
        kind = str(rng.choice(list(CLASSES), p=[.4, .5, .1]))
        origins, destinations = anchors[kind][int(rng.integers(4))]
        origin, destination = str(rng.choice(origins)), str(rng.choice(destinations))
        trips.append(dict(id=f'v{i}', kind=kind, origin=origin, destination=destination,
                          departure=int(departure), follows=bool(rng.random() < adoption)))
    return trips


def candidate_pools(trips, movements):
    pools = {}
    for trip in trips:
        key = (trip['kind'], trip['origin'], trip['destination'])
        if key not in pools:
            h = movements[key[0]]
            pools[key] = [tuple(p) for p in islice(nx.shortest_simple_paths(h, key[1], key[2], weight='cost'), 6)]
    return pools


def snapshot(net, free, storage, edge_map, graph, clock):
    """Observable active routes; no future arrivals or realized trip labels."""
    active = []
    occupancy = Counter()
    for vehicle in sim.vehicle.getIDList():
        edge = sim.vehicle.getRoadID(vehicle)
        if edge not in free:  # internal junction connectors have no road forecast
            continue
        remaining = sim.vehicle.getRoute(vehicle)[sim.vehicle.getRouteIndex(vehicle):]
        active.append((vehicle, remaining, sim.vehicle.getLanePosition(vehicle)))
        occupancy[edge] += 1
    # Waiting-to-insert vehicles remain route intentions; omitting them would
    # make an overloaded departure edge look artificially empty to the ledger.
    for vehicle in sim.simulation.getPendingVehicles():
        active.append((vehicle, sim.vehicle.getRoute(vehicle), 0.))
    costs = {e: greenshields_time(net.getEdge(e).getLength(), net.getEdge(e).getSpeed(), occupancy[e], storage[e]) for e in free}
    ledger = TrafficLedger(clock=lambda: clock)
    footprints = Counter()
    for vehicle, remaining, position in active:
        kind = sim.vehicle.getTypeID(vehicle)
        predicted = clock
        for j, e in enumerate(remaining):
            if e not in edge_map:
                continue
            if predicted < clock+60:
                footprints[e] += 1
            ledger.entries[(edge_map[e], ledger.bin(predicted))] += PROFILES[kind].pcu
            fraction = max(0., 1-position/net.getEdge(e).getLength()) if j == 0 else 1.
            predicted += costs[e]*fraction
    # Observed queue state for queue-aware routing.
    initial_backlogs = {}
    downstream_state = {}
    for edge_name, keyed_edge in edge_map.items():
        store = storage.get(edge_name, float('inf'))
        occ = occupancy.get(edge_name, 0.)
        # Backlog: vehicles exceeding 70% of free-flow storage.
        initial_backlogs[keyed_edge] = max(0., occ - store * 0.7)
        downstream_state[keyed_edge] = {
            'occupancy': occ,
            'storage': store,
        }
    # Signal delays per edge.
    signal_delays = {}
    for edge_name, keyed_edge in edge_map.items():
        delay = estimate_signal_delay(net, edge_name, clock)
        if delay > 0:
            signal_delays[keyed_edge] = delay
    return costs, ledger, footprints, initial_backlogs, downstream_state, signal_delays


def executed_route(recommendation, static_route, follows):
    return recommendation if follows else static_route


def run_policy(spec, method, prepared):
    region, signal, seed, count, adoption, out = spec
    base, net, meta, graph, edge_map, storage, free, movements, trips, pools = prepared
    run_dir = out / f'{region}-{signal}-{seed}-{count}-{adoption:g}-{method}'
    run_dir.mkdir(parents=True, exist_ok=True)
    types = ET.Element('routes')
    for kind, vclass in CLASSES.items():
        ET.SubElement(types, 'vType', id=kind, vClass=vclass, width=str(PROFILES[kind].width),
                      length={'motorcycle':'2.2','hatchback':'4.4','truck':'8.0'}[kind],
                      accel='1.2' if kind == 'truck' else '2.6', decel='4.5', sigma='0.5',
                      minGap='2.5', tau='1.0', maxSpeed=str(PROFILES[kind].speed/3.6), speedFactor='1.0', speedDev='0')
    ET.ElementTree(types).write(run_dir/'types.rou.xml')
    command = [sumolib.checkBinary('sumo'), '-n', str(base/'network.net.xml'), '-r', str(run_dir/'types.rou.xml'),
        '--seed', str(seed), '--step-length', '1', '--time-to-teleport', '-1', '--no-step-log', 'true',
        '--duration-log.disable', 'true', '--no-warnings', 'true', '--collision.action', 'warn',
        '--tripinfo-output', str(run_dir/'tripinfo.xml'), '--tripinfo-output.write-unfinished', 'true',
        '--tripinfo-output.write-undeparted', 'true', '--summary-output', str(run_dir/'summary.xml')]
    rng = np.random.default_rng(seed+70000)
    decisions, cursor, collisions, teleports = {}, 0, 0, 0
    started = time.perf_counter()
    sim.start(command)
    try:
        router = ArrivalRouter(graph, TrafficLedger(clock=lambda: 0.))
        costs, footprints = dict(free), Counter()
        initial_backlogs, downstream_state, signal_delays = {}, {}, {}
        for clock in range(HORIZON):
            if clock % 30 == 0:
                costs, router.ledger, footprints, initial_backlogs, downstream_state, signal_delays = (
                    snapshot(net, free, storage, edge_map, graph, clock))
            while cursor < len(trips) and trips[cursor]['departure'] <= clock:
                trip = trips[cursor]; cursor += 1
                key = (trip['kind'], trip['origin'], trip['destination'])
                original = pools[key]
                # All adaptive methods receive identical up-to-three current-cost
                # choices within 20% of the fastest among six frozen paths.
                ranked = sorted(original, key=lambda p: (sum(costs[e] for e in p), p))
                fastest = sum(costs[e] for e in ranked[0])
                candidates = [p for p in ranked if sum(costs[e] for e in p) <= 1.2*fastest+1e-9][:3]
                predicted, route = None, None
                if method == 'static':
                    route = original[0]
                elif method in {'dsp', 'rksp', 'ebksp'}:
                    chosen = choose_path(method, candidates, costs, footprints, storage, rng)
                    route = candidates[chosen]
                else:
                    # Determine control settings per method variant.
                    use_future = method not in ('roadfit_no_future',)
                    use_locality = method not in ('roadfit_no_locality',)
                    res_limit = math.inf if method in ('roadfit_soft_locality', 'roadfit_graduated', 'roadfit_queue_aware') else .8
                    graduated = method in ('roadfit_graduated', 'roadfit_queue_aware')
                    control = replace(Control(),
                                      use_future=use_future,
                                      use_locality=use_locality,
                                      residential_entry_limit=res_limit,
                                      graduated_locality=graduated,
                                      default_background=DEFAULT_BACKGROUND)
                    mapped = [tuple(edge_map[e] for e in p) for p in candidates]
                    # Queue-aware variant passes observed state.
                    plan_kwargs = {}
                    if method == 'roadfit_queue_aware':
                        plan_kwargs = {
                            'initial_backlogs': initial_backlogs,
                            'downstream_state': downstream_state,
                            'signal_delays': signal_delays,
                        }
                    plan = router.plan(edge_map[trip['origin']][0], edge_map[trip['destination']][1],
                        make_vehicle(trip['kind'], 'exploratory'), trip['kind'], float(clock),
                        DEFAULT_BACKGROUND,
                        control=control, commit=False, candidates=mapped, **plan_kwargs)
                    if plan and plan.get('status') != 'no_feasible_plan':
                        route = tuple(e[2] for e in plan['edges']); predicted = plan['eta_s']
                advice_available = route is not None
                route = executed_route(route, original[0], trip['follows'])
                assigned = route is not None
                if route:
                    sim.route.add(trip['id'], route)
                    sim.vehicle.add(trip['id'], trip['id'], typeID=trip['kind'], depart=str(clock), departLane='best')
                    # Forecast route intentions with current-cost traversal; all
                    # methods see the same fully observable compliance assumption.
                    predicted_clock = float(clock)
                    for e in route:
                        if predicted_clock < clock+60:
                            footprints[e] += 1
                        router.ledger.entries[(edge_map[e], router.ledger.bin(predicted_clock))] += PROFILES[trip['kind']].pcu
                        predicted_clock += costs[e]
                decisions[trip['id']] = dict(assigned=assigned, advice_available=advice_available, predicted_eta_s=predicted,
                    local_distance_m=sum(net.getEdge(e).getLength() for e in route or [] if meta['edges'][e]['highway'] in LOCAL),
                    selected_edges=list(route or []), candidate_count=len(candidates))
            sim.simulationStep()
            collisions += sim.simulation.getCollidingVehiclesNumber()
            teleports += sim.simulation.getStartingTeleportNumber()
            if cursor == len(trips) and sim.simulation.getMinExpectedNumber() == 0:
                break
    finally:
        sim.close()
    infos = {e.attrib['id']: e.attrib for e in ET.parse(run_dir/'tripinfo.xml').getroot().findall('tripinfo')}
    rows = []
    for trip in trips:
        info = infos.get(trip['id'], {})
        finished = float(info.get('arrival', -1)) >= 0
        actual = float(info['arrival'])-trip['departure'] if finished else None
        decision = decisions[trip['id']]
        rows.append(dict(region=region, signal=signal, seed=seed, trips=count, adoption=adoption, method=method,
            vehicle=trip['id'], kind=trip['kind'], scheduled_departure=trip['departure'], follows=trip['follows'],
            assigned=decision['assigned'], advice_available=decision['advice_available'], arrived=finished, actual_time_s=actual,
            penalized_time_s=min(actual, HORIZON) if finished else HORIZON,
            depart_delay_s=float(info.get('departDelay', 'nan')), time_loss_s=float(info.get('timeLoss', 'nan')),
            local_distance_m=decision['local_distance_m'], predicted_eta_s=decision['predicted_eta_s'],
            candidate_count=decision['candidate_count']))
    frame = pd.DataFrame(rows)
    summary = dict(region=region, signal=signal, seed=seed, trips=count, adoption=adoption, method=method,
        assigned=int(frame.assigned.sum()), arrived=int(frame.arrived.sum()), penalized_mean_s=float(frame.penalized_time_s.mean()),
        mean_time_s=float(frame.actual_time_s.mean()), p90_time_s=float(frame.actual_time_s.quantile(.9)),
        mean_depart_delay_s=float(frame.depart_delay_s.mean()), local_distance_km=float(frame.local_distance_m.sum()/1000),
        collision_vehicle_events=collisions, teleport_events=teleports, wall_seconds=time.perf_counter()-started)
    (run_dir/'decisions.json').write_text(json.dumps(decisions), encoding='utf-8')
    (run_dir/'command.json').write_text(json.dumps(command, indent=2), encoding='utf-8')
    return rows, summary


def episode(spec):
    region, signal, seed, count, adoption, out = spec
    prepared = load_network(region, signal)
    trips = make_demand(prepared[1], prepared[-1], seed, count, adoption)
    pools = candidate_pools(trips, prepared[-1])
    prepared = (*prepared, trips, pools)
    case = out / f'{region}-{signal}-{seed}-{count}-{adoption:g}'
    case.mkdir(parents=True, exist_ok=True)
    (case/'demand.json').write_text(json.dumps(trips, indent=2), encoding='utf-8')
    rows, summaries = [], []
    for method in METHODS:
        r, s = run_policy(spec, method, prepared); rows.extend(r); summaries.append(s)
        print(f'{region} {signal} seed={seed} n={count} adoption={adoption:g} {method}: {s["penalized_mean_s"]:.1f}s, {s["arrived"]}/{count} arrived', flush=True)
    return rows, summaries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--regions', nargs='+', default=['Indiranagar', 'Hebbal'])
    parser.add_argument('--signals', nargs='+', choices=['static', 'actuated'], default=['static', 'actuated'])
    parser.add_argument('--seeds', nargs='+', type=int, default=[51, 52, 53, 54, 55])
    parser.add_argument('--counts', nargs='+', type=int, default=[300, 900])
    parser.add_argument('--adoptions', nargs='+', type=float, default=[1., .7])
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--output', type=Path, default=ROOT/'experiments/sumo_control')
    args = parser.parse_args(); args.output = args.output.resolve(); args.output.mkdir(parents=True, exist_ok=True)
    specs = [(r, s, seed, n, a, args.output) for r in args.regions for s in args.signals
             for seed in args.seeds for n in args.counts for a in args.adoptions]
    rows, summaries = [], []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(episode, spec) for spec in specs]
        for future in as_completed(futures):
            r, s = future.result(); rows.extend(r); summaries.extend(s)
    keys = ['region', 'signal', 'seed', 'trips', 'adoption', 'method']
    pd.DataFrame(rows).sort_values(keys+['vehicle']).to_csv(args.output/'trips.csv', index=False)
    pd.DataFrame(summaries).sort_values(keys).to_csv(args.output/'episodes.csv', index=False)
    paths = [Path(__file__), ROOT/'src/routing/prior_art.py', ROOT/'src/routing/anticipatory.py',
             ROOT/'src/vehicle/profiles.py', ROOT/'scripts/prepare_sumo.py']
    manifest = {'args': {**vars(args), 'output': str(args.output)}, 'episodes': len(specs), 'rows': len(rows),
        'methods': METHODS, 'simulator': sim.getVersion(),
        'source_sha256': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        'network_sha256': {f'{r}-{s}': hashlib.sha256((ROOT/f'data/public/sumo/{r}-{s}/network.net.xml').read_bytes()).hexdigest() for r in args.regions for s in args.signals},
        'protocol': 'Departure-only policy comparison, identical six frozen loopless paths; adaptive methods select up to three within 20% current-cost detour. 30s state refresh, 60s footprint horizon. No future realized labels.',
        'limitations': ['Published DSP/RkSP/EBkSP selectors adapted to this common pool, not full 2012 periodic rerouting reproduction.',
            'OSM topology is public, but signal placement/timing, vehicle behavior and demand are synthetic and uncalibrated.',
            'Full active route observability and immediate compliance feedback are assumed.',
            'Truck endpoints use a class-feasible main-road strongly connected component.',
            'Unfinished/unassigned requests receive 3600s; completed duration includes insertion delay.',
            'Internal junction links are simulated but omitted from route-intention forecasts. Teleporting is disabled.']}
    (args.output/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
