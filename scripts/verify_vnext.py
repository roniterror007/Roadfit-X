"""Audit current result provenance and independently recompute reported scores."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def local_path(name):
    return ROOT / name.replace('\\', '/')


def main():
    checked = {}

    def check(path, expected):
        actual = digest(path)
        if actual != expected:
            raise ValueError(f'Hash mismatch: {path.relative_to(ROOT)}')
        checked[path.relative_to(ROOT).as_posix()] = actual

    osm = json.loads((ROOT / 'data/public/bengaluru_manifest.json').read_text())
    check(ROOT / 'data/public/bengaluru_drive.graphml', osm['map_sha256'])
    for tile in osm['building_tiles']:
        check(local_path(tile['file']), tile['sha256'])
    regional = json.loads((ROOT / 'data/public/bengaluru_regions/manifest.json').read_text())
    assert regional['source_sha256'] == osm['map_sha256']
    for name, meta in regional['regions'].items():
        check(ROOT / f'data/public/bengaluru_regions/{name}.graphml', meta['sha256'])
    tntp = json.loads((ROOT / 'data/public/tntp/manifest.json').read_text())
    for name, meta in tntp['files'].items():
        check(local_path(name), meta['sha256'])
    eta_manifest = json.loads((ROOT / 'data/public/deeptte/manifest.json').read_text())
    for meta in eta_manifest['files']:
        check(ROOT / 'data/public/deeptte' / meta['name'], meta['sha256'])

    cohorts = {}
    for folder in ['traffic_control_final', 'bengaluru_control_final', 'bengaluru_arterial']:
        base = ROOT / 'experiments' / folder
        manifest = json.loads((base / 'manifest.json').read_text())
        for name, expected in {**manifest['sources'], **manifest.get('inputs', {})}.items():
            check(local_path(name), expected)
        if 'regional_manifest_sha256' in manifest:
            check(ROOT / 'data/public/bengaluru_regions/manifest.json', manifest['regional_manifest_sha256'])
        trips = pd.read_csv(base / 'trips.csv')
        episodes = pd.read_csv(base / 'episodes.csv')
        assert len(trips) == manifest['rows']
        keys = ['dataset' if 'dataset' in trips else 'region', 'seed', 'capacity_scale']
        if 'adoption' in trips:
            keys += ['adoption']
        assert trips[keys].drop_duplicates().shape[0] == manifest['episodes']
        assert not trips.duplicated(keys + ['method', 'trip']).any()
        expected_penalty = trips.actual_time_s.fillna(3600).clip(upper=3600)
        np.testing.assert_allclose(trips.penalized_time_s, expected_penalty, atol=1e-8)
        observed = trips.groupby(keys + ['method']).penalized_time_s.mean().sort_index()
        recorded = episodes.set_index(keys + ['method']).penalized_mean_s.sort_index()
        np.testing.assert_allclose(observed, recorded, atol=1e-8)
        pivot = recorded.unstack('method')
        cohorts[folder] = {'rows': len(trips), 'episodes': manifest['episodes'],
            'balanced_minus_static_s': float((pivot.balanced - pivot.static).mean()),
            'mean_scenario_relative_reduction_pct': float(((pivot.static - pivot.balanced) / pivot.static * 100).mean())}
        for name in ['trips.csv', 'episodes.csv', 'manifest.json']:
            checked[(base / name).relative_to(ROOT).as_posix()] = digest(base / name)

    eta = json.loads((ROOT / 'experiments/eta_sample/results.json').read_text())
    check(ROOT / 'scripts/evaluate_eta_sample.py', eta['source_script_sha256'])
    check(ROOT / 'data/public/deeptte/manifest.json', eta['dataset_manifest_sha256'])
    predictions = pd.read_csv(ROOT / 'experiments/eta_sample/predictions.csv')
    assert len(predictions) == eta['test_records'] == 1400
    for name, recorded in eta['metrics'].items():
        error = predictions[name] - predictions.truth_min
        actual = [abs(error).mean(), np.sqrt((error ** 2).mean()),
                  (abs(error) / predictions.truth_min).mean() * 100, error.mean()]
        np.testing.assert_allclose(actual, [recorded[k] for k in ['mae_min', 'rmse_min', 'mape_pct', 'bias_min']], atol=1e-9)
    out = ROOT / 'experiments/verification/vnext-provenance.json'
    report = {'status': 'verified', 'checked_sha256': checked, 'cohorts': cohorts,
              'eta': eta['metrics']['hist_full'],
              'boundary': 'Hashes and score arithmetic verified; no field validity or original-algorithm claim.'}
    out.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'checked_sha256'}, indent=2))


if __name__ == '__main__':
    main()
