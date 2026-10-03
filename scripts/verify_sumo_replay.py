"""Compare one independently rerun scenario against all eight native originals."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    original, replay = ROOT/'experiments/sumo_control', ROOT/'experiments/sumo_replay'
    manifest = json.loads((replay/'manifest.json').read_text())
    stem = 'Indiranagar-static-51-300-1'
    assert (original/stem/'demand.json').read_bytes() == (replay/stem/'demand.json').read_bytes()
    checks = {}
    for method in manifest['methods']:
        left, right = original/f'{stem}-{method}', replay/f'{stem}-{method}'
        assert json.loads((left/'decisions.json').read_text()) == json.loads((right/'decisions.json').read_text()), method
        for file, tag in [('tripinfo.xml', 'tripinfo'), ('summary.xml', 'step')]:
            a = [node.attrib for node in ET.parse(left/file).getroot().findall(tag)]
            b = [node.attrib for node in ET.parse(right/file).getroot().findall(tag)]
            assert a == b, (method, file)
        checks[method] = 'Exact decisions, native trip attributes and native per-step summaries match.'
    result = {'status': 'passed', 'scenario': stem, 'request_policy_rows': 2400,
              'methods': checks, 'comparison': 'XML creation timestamps and run-specific paths excluded; all native outcome attributes compared exactly.',
              'replay_manifest_sha256': hashlib.sha256((replay/'manifest.json').read_bytes()).hexdigest(),
              'verifier_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (ROOT/'experiments/verification/sumo-replay.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
