"""Build a verified research supplement without overwriting earlier snapshots."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def main():
    verification = json.loads((ROOT/'experiments/verification/sumo-verification.json').read_text())
    assert verification['status'] == 'passed'
    patterns = {
        'src': ['*.py'], 'data/public/sumo': ['*.xml', '*.json', '*.log'],
        'data/public/ioc_2021': ['*.json'], 'experiments/sumo_control': ['*.xml', '*.json', '*.csv'],
        'experiments/sumo_replay': ['*.xml', '*.json', '*.csv'],
        'experiments/sumo_smoke': ['*.json', '*.csv'],
        'experiments/sumo_load_smoke': ['*.json', '*.csv'],
    }
    files = set()
    for folder, globs in patterns.items():
        for pattern in globs:
            files.update(p for p in (ROOT/folder).rglob(pattern) if p.is_file() and '__pycache__' not in p.parts)
    required = [
        'requirements.lock.txt', 'requirements-sumo.txt',
        'scripts/prepare_sumo.py', 'scripts/analyze_sumo.py', 'scripts/verify_sumo.py',
        'scripts/package_sumo.py', 'scripts/record_ioc_survey.py', 'scripts/verify_sumo_replay.py',
        'tests/test_prior_art.py', 'tests/test_sumo_adapter.py',
        'docs/SUMO_PROTOCOL.md', 'docs/SUMO_STUDY.md',
        'research/roadfit_paper.tex', 'research/sumo_analysis.json',
        'research/sumo_results.png', 'research/sumo_results.svg',
        'research/references/bsmile_source.json',
        'research/references/prior_art_source.json',
        'experiments/verification/sumo-verification.json',
        'experiments/verification/sumo-replay.json', 'experiments/verification/sumo-tests.xml',
        'experiments/verification/sumo-latex-compile.json',
        'data/public/bengaluru_regions/Indiranagar.graphml',
        'data/public/bengaluru_regions/Hebbal.graphml',
        'data/public/bengaluru_regions/manifest.json',
    ]
    for name in required:
        path = ROOT/name
        assert path.is_file(), name
        files.add(path)
    hashes = {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(files)}
    assert not any('.local/' in n or n.endswith(('.db', '.joblib', '.pkl', '.pdf')) for n in hashes)
    destination = ROOT/'experiments/roadfit-sumo-study.zip'
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(ROOT).as_posix())
        archive.writestr('bundle-file-hashes.json', json.dumps(hashes, indent=2)+'\n')
        archive.writestr('README_SUMO.txt',
            'RoadFit SUMO research supplement. Read docs/SUMO_STUDY.md and docs/SUMO_PROTOCOL.md.\n'
            'Contains independent microscopic evaluation, raw native outputs, source and current manuscript.\n'
            'This is not a full application distribution or a verified typeset submission.\n'
            'Earlier point-queue and ETA results belong to the separately preserved roadfit-vnext-bundle.zip.\n'
            'OSM-derived maps: copyright OpenStreetMap contributors, Open Database License.\n'
            'https://www.openstreetmap.org/copyright\n'
            'Full third-party reference PDFs and local credentials are excluded.\n')
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        for name, expected in hashes.items():
            value = hashlib.sha256()
            with archive.open(name) as stream:
                for block in iter(lambda: stream.read(1024*1024), b''):
                    value.update(block)
            assert value.hexdigest() == expected, name
    report = {'status': 'verified', 'archive': destination.name, 'files': len(hashes),
              'bytes': destination.stat().st_size, 'sha256': digest(destination),
              'excluded': ['credentials', 'user memories/models', 'third-party full PDFs',
                           'historical review PDF', 'environments and native wheels'],
              'previous_bundles_preserved': True}
    destination.with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
