"""Download the authors' Chengdu DeepTTE sample, pinned to a Git revision."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT / 'data/public/deeptte'
    out.mkdir(parents=True, exist_ok=True)
    response = requests.get('https://api.github.com/repos/UrbComp/DeepTTE/commits/master', timeout=90)
    response.raise_for_status()
    revision = response.json()['sha']
    records = []
    for name in ['train_00', 'train_01', 'train_02', 'train_03', 'train_04', 'test']:
        path = out / name
        url = f'https://raw.githubusercontent.com/UrbComp/DeepTTE/{revision}/data/{name}'
        if not path.exists():
            response = requests.get(url, timeout=120)
            response.raise_for_status()
            path.write_bytes(response.content)
        records.append({'name': name, 'url': url, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'records': len(path.read_text(encoding='utf-8').splitlines())})
        print(f'DeepTTE sample {name}: {records[-1]["records"]} trips', flush=True)
    (out / 'manifest.json').write_text(json.dumps({
        'retrieved_utc': datetime.now(timezone.utc).isoformat(), 'revision': revision,
        'source': 'https://github.com/UrbComp/DeepTTE', 'files': records,
        'limitations': 'Author-provided taxi sample from Chengdu. Not Bengaluru, not validation of routing/control, not a reproduction of the full paper dataset.'
    }, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
