"""Session GEOM-A — the machine card's v1 -> v2 migration, in a real JS runtime.

`tests/js/geometry_tests.js` drives the shipped `app.js` in node. It is run from
pytest so the suite stays one command, exactly as the compare-mode and polling
suites are (tests/test_compare_webapp.py, tests/test_poll_resilience.py).

Why it cannot be a Python test: the card lives in the analyst's browser, the
migration runs on page load, and its failure mode is silent — the saved
machines simply stop appearing, and nothing on the server ever knows.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_saved_machine_migration_in_a_real_js_runtime():
    result = subprocess.run(
        [shutil.which("node"), str(_ROOT / "tests" / "js" / "geometry_tests.js")],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
    match = re.search(r"\n(\d+)/(\d+) passed", result.stdout)
    assert match, result.stdout
    passed, total = map(int, match.groups())
    assert passed == total and total >= 10, result.stdout
