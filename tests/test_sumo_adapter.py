import importlib.util
import subprocess
import sys

import pytest


def test_noncomplier_still_drives_when_controller_abstains():
    if importlib.util.find_spec('libsumo') is None:
        pytest.skip('Optional SUMO dependencies are not installed.')
    # SUMO and GIS wheels can load incompatible DLLs into one Windows process.
    # The real benchmark uses isolated workers; exercise the adapter likewise.
    subprocess.run([sys.executable, '-c', """
from src.evaluation.sumo_benchmark import executed_route
assert executed_route(None, ('a', 'b'), False) == ('a', 'b')
assert executed_route(None, ('a', 'b'), True) is None
assert executed_route(('a', 'c', 'b'), ('a', 'b'), True) == ('a', 'c', 'b')
"""], check=True, capture_output=True, text=True, timeout=60)
