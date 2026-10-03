"""Audit recorded outcomes independently against native SUMO XML and decisions."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'experiments/sumo_control'
KEYS = ['region', 'signal', 'seed', 'trips', 'adoption', 'method']
LOCAL = {'residential', 'living_street', 'service'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    global BASE
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=BASE)
    parser.add_argument('--output', type=Path, default=ROOT/'experiments/verification/sumo-verification.json')
    args = parser.parse_args()
    BASE = args.input.resolve()
    manifest = json.loads((BASE/'manifest.json').read_text())
    for name, expected in manifest['source_sha256'].items():
        assert digest(ROOT/name) == expected, name
    networks = {}
    for name, expected in manifest['network_sha256'].items():
        base = ROOT/'data/public/sumo'/name
        assert digest(base/'network.net.xml') == expected, name
        meta = json.loads((base/'manifest.json').read_text())
        source_graph = ROOT/'data/public/bengaluru_regions'/f"{meta['region']}.graphml"
        assert digest(source_graph) == meta['source_graph_sha256'], name
        root = ET.parse(base/'network.net.xml').getroot()
        lengths = {e.get('id'): float(e.find('lane').get('length'))
                   for e in root.findall('edge') if e.get('function') != 'internal'}
        movements = {(e.get('from'), e.get('to')) for e in root.findall('connection')}
        networks[name] = (meta, lengths, movements)
    frame = pd.read_csv(BASE/'trips.csv')
    episodes = pd.read_csv(BASE/'episodes.csv')
    assert len(frame) == manifest['rows'] == 384000
    assert len(episodes) == 640 and manifest['episodes'] == 80
    assert not frame.duplicated(KEYS+['vehicle']).any()
    episode_index = episodes.set_index(KEYS)
    groups = frame.groupby(KEYS, sort=False)
    assert len(groups) == 640
    demand_by_seed = {}
    raw_hashes, native_arrived, native_unfinished, unassigned = {}, 0, 0, 0
    maximum_identity_error = 0.
    native_collision_steps = 0
    for key, records in groups:
        region, signal, seed, count, adoption, method = key
        stem = f'{region}-{signal}-{seed}-{count}-{adoption:g}'
        run = BASE/f'{stem}-{method}'
        demand_path = BASE/stem/'demand.json'
        demand = json.loads(demand_path.read_text())
        assert len(demand) == len(records) == count
        stripped = [{k:v for k,v in t.items() if k != 'follows'} for t in demand]
        demand_key = (region, seed, count)
        if demand_key in demand_by_seed:
            assert stripped == demand_by_seed[demand_key], stem
        else:
            demand_by_seed[demand_key] = stripped
        demand = {t['id']: t for t in demand}
        decisions = json.loads((run/'decisions.json').read_text())
        infos = {t.get('id'): t.attrib for t in ET.parse(run/'tripinfo.xml').getroot().findall('tripinfo')}
        assert set(decisions) == set(demand)
        assert set(infos) == {v for v,d in decisions.items() if d['assigned']}
        meta, lengths, movements = networks[f'{region}-{signal}']
        scores, completed, local = [], [], []
        for row in records.itertuples(index=False):
            t, d = demand[row.vehicle], decisions[row.vehicle]
            info = infos.get(row.vehicle, {})
            assert row.kind == t['kind'] and row.scheduled_departure == t['departure']
            assert row.follows == t['follows'] and row.assigned == d['assigned']
            assert row.advice_available == d['advice_available']
            route = d['selected_edges']
            assert bool(route) == d['assigned']
            if route:
                assert route[0] == t['origin'] and route[-1] == t['destination']
                assert all(pair in movements for pair in zip(route, route[1:]))
                vehicle_class = {'hatchback': 'passenger', 'motorcycle': 'motorcycle', 'truck': 'truck'}[t['kind']]
                assert all(vehicle_class in meta['edges'][e]['allowed'] for e in route)
            local_m = sum(lengths[e] for e in route if meta['edges'][e]['highway'] in LOCAL)
            assert np.isclose(local_m, row.local_distance_m)
            if t['kind'] == 'truck':
                assert local_m == 0
            finished = float(info.get('arrival', -1)) >= 0
            assert bool(row.arrived) == finished
            if finished:
                elapsed = float(info['arrival'])-t['departure']
                error = abs(elapsed-float(info['duration'])-float(info['departDelay']))
                maximum_identity_error = max(error, maximum_identity_error)
                assert error <= .011 and np.isclose(row.actual_time_s, elapsed)
                score = min(elapsed, 3600)
                completed.append(elapsed)
                native_arrived += 1
            else:
                score = 3600
                assert pd.isna(row.actual_time_s)
                native_unfinished += int(d['assigned'])
                unassigned += int(not d['assigned'])
            assert np.isclose(row.penalized_time_s, score)
            scores.append(score); local.append(local_m)
        summary = episode_index.loc[key]
        assert summary.assigned == sum(d['assigned'] for d in decisions.values())
        assert summary.arrived == len(completed)
        assert np.isclose(summary.penalized_mean_s, np.mean(scores))
        assert np.isclose(summary.local_distance_km, sum(local)/1000)
        if completed:
            assert np.isclose(summary.mean_time_s, np.mean(completed))
            assert np.isclose(summary.p90_time_s, np.quantile(completed, .9))
        steps = ET.parse(run/'summary.xml').getroot().findall('step')
        assert int(steps[-1].get('arrived')) == len(completed)
        assert int(steps[-1].get('loaded')) == int(summary.assigned)
        assert all(int(s.get('teleports')) == 0 for s in steps)
        native_collision_steps += sum(int(s.get('collisions')) > 0 for s in steps)
        for path in [demand_path, run/'decisions.json', run/'tripinfo.xml', run/'summary.xml', run/'command.json', run/'types.rou.xml']:
            raw_hashes[path.relative_to(ROOT).as_posix()] = digest(path)
    assert int(episodes.teleport_events.sum()) == 0
    if native_collision_steps == 0:
        assert int(episodes.collision_vehicle_events.sum()) == 0
    report = {'status': 'passed', 'scenarios': 80, 'policy_runs': 640, 'request_policy_rows': len(frame),
              'native_arrived': native_arrived, 'native_unfinished': native_unfinished, 'unassigned': unassigned,
              'max_duration_plus_insertion_identity_error_s': maximum_identity_error,
              'collision_vehicle_events': int(episodes.collision_vehicle_events.sum()),
              'native_summary_steps_with_collisions': native_collision_steps,
              'teleports': 0, 'source_hashes_match': True, 'network_hashes_match': True,
              'pairing': 'Same OD, vehicle and departure across methods, signal and adoption conditions; follows changes only with adoption.',
              'verified': ['Raw XML scores including unfinished and unassigned', 'Insertion delay included',
                           'Legal connected selected routes', 'Zero planned truck local-road exposure',
                           'Episode means, p90, assignments and arrivals', 'Native zero teleport count'],
              'table_sha256': {p: digest(BASE/p) for p in ['episodes.csv', 'trips.csv', 'manifest.json']},
              'verifier_sha256': digest(Path(__file__)), 'raw_file_sha256': raw_hashes}
    destination = args.output
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'raw_file_sha256'}, indent=2))


if __name__ == '__main__':
    main()
