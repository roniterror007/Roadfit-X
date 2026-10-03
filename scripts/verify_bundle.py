"""Verify a source archive actually runs without Git checkout metadata."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def verify(archive_path):
    archive_path = Path(archive_path).resolve()
    parent = (ROOT / 'experiments/verification').resolve()
    parent.mkdir(parents=True, exist_ok=True)
    # The temporary checkout and its cleanup stay inside this named workspace directory.
    with tempfile.TemporaryDirectory(prefix='bundle-check-', dir=parent) as directory:
        checkout = Path(directory).resolve()
        assert checkout.parent == parent
        with zipfile.ZipFile(archive_path) as archive:
            inventory = json.loads(archive.read('bundle-file-hashes.json'))
            for name in archive.namelist():
                target = (checkout / name).resolve()
                if not target.is_relative_to(checkout):
                    raise ValueError('Archive path escapes the temporary checkout')
            archive.extractall(checkout)
        for name, digest in inventory.items():
            assert hashlib.sha256((checkout / name).read_bytes()).hexdigest() == digest
        environment = dict(os.environ)
        environment.pop('PYTHONPATH', None)
        # This temporary extraction is inside the workspace: stop Git from
        # accidentally reporting the enclosing repository as the archive's checkout.
        environment['GIT_CEILING_DIRECTORIES'] = str(checkout.parent)
        command = [sys.executable, '-m', 'src.evaluation.ablations', '--graph',
                   'data/koramangala_bengaluru_karnataka_india_drive.graphml',
                   '--num-pairs', '3', '--seeds', '41', '--missing-rates', '0.3',
                   '--scenarios', '20', '--workers', '1', '--output', 'bundle-smoke/ablations.csv']
        result = subprocess.run(command, cwd=checkout, env=environment,
                                capture_output=True, text=True, timeout=120, check=True)
        manifest = json.loads((checkout / 'bundle-smoke/ablations.manifest.json').read_text())
        assert manifest['status'] == 'complete' and manifest['records'] == 42
        assert manifest['git_commit'] is None
        report = {'status': 'passed', 'archive_sha256': hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                  'file_hashes_verified': len(inventory), 'records': manifest['records'],
                  'git_commit': manifest['git_commit'], 'stdout': result.stdout,
                  'basis': 'Archive extraction and execution in the validated Python environment; no .git directory'}
    output = parent / 'bundle-smoke.json'
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'PASS: {len(inventory)} file hashes and 42 smoke records from the standalone archive; no Git checkout required.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', default='experiments/reproducible/final/roadfit-research-bundle.zip')
    verify(parser.parse_args().archive)
