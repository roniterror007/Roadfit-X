"""Package the evaluated source, declared data, and evidence without local memory."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    files = set()
    for folder, patterns in {
        'src': ['*.py'], 'tests': ['*.py'], 'scripts': ['*.py'],
        'docs': ['*.md'], 'frontend/src': ['*.jsx', '*.css', '*.js'],
        'experiments/verification': ['*.md', '*.txt', '*.json', '*.xml', '*.jpg', '*.py'],
        'experiments/reproducible/final': ['*.csv', '*.json', '*.png', '*.svg'],
    }.items():
        for pattern in patterns:
            files.update((ROOT / folder).rglob(pattern))
    for name in [
        'README.md', '.gitignore', '.github/workflows/ci.yml',
        'requirements.txt', 'requirements-dev.txt', 'requirements.lock.txt',
        'requirements_relaxed.txt',
        'frontend/package.json', 'frontend/package-lock.json',
        'frontend/index.html', 'frontend/vite.config.js', 'frontend/.env.example',
        'data/koramangala_bengaluru_karnataka_india_drive.graphml',
        'data/koramangala_enriched_v2.graphml',
    ]:
        files.add(ROOT / name)
    for path in files:
        if not path.is_file():
            raise FileNotFoundError(path)
    destination = ROOT / 'experiments/reproducible/final/roadfit-research-bundle.zip'
    # Do not embed the preceding archive's report when rebuilding the bundle.
    files.discard(destination.with_suffix('.json'))
    files.discard(ROOT / 'experiments/verification/bundle-smoke.json')
    inventory = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted(files)}
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(ROOT).as_posix())
        archive.writestr('bundle-file-hashes.json', json.dumps(inventory, indent=2) + '\n')
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        for name, digest in inventory.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
    report = {'status': 'verified', 'archive': destination.name,
              'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
              'files': len(files), 'bytes': destination.stat().st_size,
              'excluded': ['local SQLite memories', 'user serialized models',
                           'virtual environments', 'node_modules', 'credentials']}
    destination.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Verified {len(files)} files in {destination}; {destination.stat().st_size / 2**20:.2f} MiB.')


if __name__ == '__main__':
    main()
