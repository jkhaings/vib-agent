"""Session INTAKE-2 — the trend key, and the word two files have to agree on.

The behaviour runs in node (`tests/js/location_trend_tests.js`) against the real
`app.js`. This file runs it, so `pytest` stays the one command — the idiom
`tests/test_ux2_steps.py` established.

It also holds the one claim the node suite CANNOT make. `app.js` calls an
unnamed measurement point `Default`, and so does `app.py` — the browser files
the reading under that key and the server labels the first location with it. Two
files, one string, and nothing was diffing them: a `const` at `app.js` top level
is a lexical binding rather than a property of the vm sandbox's global, so the
node suite can see the constant's EFFECT but not the constant.

Only the node runner skips, and only when node is absent.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_APP_JS = _ROOT / "src" / "vib_agent" / "webapp" / "static" / "app.js"
_APP_PY = _ROOT / "src" / "vib_agent" / "webapp" / "app.py"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_location_trend_suite_passes():
    result = subprocess.run(
        [shutil.which("node"), str(_ROOT / "tests" / "js" / "location_trend_tests.js")],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    # Non-vacuity: a suite that ran nothing exits 0 too.
    assert re.search(r"\b1[0-9]/1[0-9] passed", result.stdout), result.stdout


class TestTheTwoFilesAgreeAboutAnUnnamedPoint:
    """`Default` in the browser and `Default` on the server, diffed.

    If these drift, a machine's history splits in two without anything failing:
    the browser keeps filing readings under one label while the server reports
    the point under another, and the analyst sees a trend that stops growing.
    """

    def _js_constant(self) -> str:
        found = re.search(
            r"const LEGACY_BLANK_LOCATION_LABEL = '([^']+)';", _APP_JS.read_text()
        )
        assert found, "LEGACY_BLANK_LOCATION_LABEL is gone from app.js"
        return found.group(1)

    def _py_constant(self) -> str:
        found = re.search(r'_FIRST_LOCATION_LABEL = "([^"]+)"', _APP_PY.read_text())
        assert found, "_FIRST_LOCATION_LABEL is gone from app.py"
        return found.group(1)

    def test_they_are_the_same_word(self):
        assert self._js_constant() == self._py_constant(), (
            "the browser and the server disagree about what an unnamed "
            "measurement point is called; a machine's history would split"
        )

    def test_it_is_the_word_the_privacy_page_and_the_report_can_print(self):
        """Not an empty string and not a placeholder: it reaches a report and a
        machine page, where an analyst reads it."""
        word = self._js_constant()
        assert word.strip() == word and word
        assert word.lower() not in ("none", "null", "undefined", "n/a", "")

    def test_the_key_builder_uses_it_rather_than_a_second_literal(self):
        """`trendKey` is the single choke point, so the normalisation belongs
        there -- doing it only in the migration would leave new readings written
        under the old blank-location shape and migrated back on the next read."""
        js = _APP_JS.read_text()
        body = js.partition("function trendKey(alias, location)")[2].partition("\n}")[0]
        assert "LEGACY_BLANK_LOCATION_LABEL" in body, (
            "trendKey no longer normalises a blank location through the constant"
        )
