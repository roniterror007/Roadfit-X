"""Package the current public-data study; preserve the historical source bundle."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def main():
    files = set()
    patterns = {
        'src': ['*.py'], 'tests': ['*.py'], 'scripts': ['*.py'], 'docs': ['*.md'],
        'frontend/src': ['*.jsx', '*.css', '*.js'],
        'research': ['*.tex', '*.json', '*.png', '*.svg'],
        'experiments/verification': ['VNEXT_VERIFICATION.md', 'vnext-*.json', 'vnext-*.xml', 'officer-dashboard.png', 'driver-route.png'],
    }
    for folder in ['traffic_control_final', 'bengaluru_control_final', 'bengaluru_arterial', 'eta_sample']:
        patterns['experiments/' + folder] = ['*.csv', '*.json']
    for folder in ['bengaluru_regions', 'building_centres', 'tntp', 'deeptte']:
        patterns['data/public/' + folder] = ['*']
    for folder, globs in patterns.items():
        for pattern in globs:
            files.update(p for p in (ROOT / folder).rglob(pattern) if p.is_file() and '__pycache__' not in p.parts)
    for name in [
        'README.md', '.gitignore', 'pytest.ini', '.github/workflows/ci.yml',
        'requirements.txt', 'requirements-dev.txt', 'requirements.lock.txt',
        'frontend/package.json', 'frontend/package-lock.json', 'frontend/index.html',
        'frontend/vite.config.js', 'frontend/.env.example',
        'data/public/bengaluru_drive.graphml', 'data/public/bengaluru_manifest.json',
        'data/koramangala_bengaluru_karnataka_india_drive.graphml',
        'data/koramangala_enriched_v2.graphml', 'output/pdf/roadfit_review.pdf',
    ]:
        path = ROOT / name
        if not path.is_file():
            raise FileNotFoundError(path)
        files.add(path)
    inventory = {path.relative_to(ROOT).as_posix(): digest(path) for path in sorted(files)}
    assert not any('.local/' in name or name.endswith(('.db', '.joblib', '.pkl')) for name in inventory)
    destination = ROOT / 'experiments/roadfit-vnext-bundle.zip'
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(ROOT).as_posix())
        archive.writestr('bundle-file-hashes.json', json.dumps(inventory, indent=2) + '\n')
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        for name, expected in inventory.items():
            value = hashlib.sha256()
            with archive.open(name) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    value.update(chunk)
            assert value.hexdigest() == expected, name
    report = {'status': 'verified', 'archive': destination.name, 'sha256': digest(destination),
        'files': len(files), 'bytes': destination.stat().st_size,
        'excluded': ['local officer credentials', 'SQLite memories', 'user models', 'virtual environments',
                     'node_modules', 'OSM request cache', 'pilot outputs', 'historical result bundle']}
    destination.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
