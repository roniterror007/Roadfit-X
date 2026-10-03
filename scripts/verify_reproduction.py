"""Compare experiment outcomes while excluding machine-dependent timings."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
KEYS = ['Seed', 'MissingRate', 'PairID', 'Model']
TIMINGS = ['Runtime_ms', 'Preparation_ms']


def source_hash():
    digest = hashlib.sha256()
    for path in sorted((ROOT / 'src').rglob('*.py')):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def verify(reference, candidate, output):
    reference, candidate = Path(reference), Path(candidate)
    left, right = pd.read_csv(reference), pd.read_csv(candidate)
    excluded = [name for name in TIMINGS if name in left.columns]
    left_values = left.drop(columns=excluded).sort_values(KEYS).reset_index(drop=True)
    right_values = right.drop(columns=excluded).sort_values(KEYS).reset_index(drop=True)
    pd.testing.assert_frame_equal(left_values, right_values, check_exact=True)
    manifest = json.loads(candidate.with_suffix('.manifest.json').read_text())
    assert manifest['status'] == 'complete'
    assert manifest['records'] == len(right)
    assert not right.duplicated(KEYS).any()
    current_hash = source_hash()
    assert current_hash == manifest['source_sha256'], 'Source differs from the candidate experiment'
    graph = Path(manifest['graph'])
    assert hashlib.sha256(graph.read_bytes()).hexdigest() == manifest['graph_sha256']
    left_paired = json.loads(reference.with_suffix('.paired.json').read_text())
    right_paired = json.loads(candidate.with_suffix('.paired.json').read_text())
    assert left_paired == right_paired, 'Paired descriptive statistics differ'
    report = {
        'status': 'passed', 'reference': str(reference.resolve()),
        'candidate': str(candidate.resolve()), 'records': len(right),
        'compared_columns': list(right_values.columns), 'excluded_columns': excluded,
        'outcomes_identical': True, 'paired_statistics_identical': True,
        'candidate_source_matches_workspace': True,
        'source_sha256': current_hash, 'graph_sha256': manifest['graph_sha256'],
        'reference_csv_sha256': hashlib.sha256(reference.read_bytes()).hexdigest(),
        'candidate_csv_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
        'limitation': 'Reproduction of a synthetic benchmark; timings are not deterministic.',
    }
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'PASS: {len(right):,} outcomes and paired statistics match exactly; source and graph hashes verified.')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--output', default='experiments/verification/reproduction.json')
    args = parser.parse_args()
    verify(args.reference, args.candidate, args.output)
