"""Session INTAKEFIX-1 — the per-location undo (INTAKE-2's F-3).

The behaviour runs in node (`tests/js/intakefix1_undo_tests.js`) against the
real `app.js`. This file runs it, so `pytest` stays the one command — the idiom
`tests/test_intake2_trend.py` established.

It also holds the two claims the node suite CANNOT make, both for the same
reason: `RETAINED` is a top-level `const` in `app.js`, which is a lexical
binding rather than a property of the vm sandbox's global (measured:
`sandbox.RETAINED` is `undefined` while every `function` declaration beside it
is reachable). So the node suite can see a function's EFFECT and not that
binding, and these two are assertions about the source.

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
_SUITE = _ROOT / "tests" / "js" / "intakefix1_undo_tests.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_undo_suite_passes():
    result = subprocess.run(
        [shutil.which("node"), str(_SUITE)],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    # Non-vacuity: a suite that ran nothing exits 0 too.
    assert re.search(r"\b1[0-9]/1[0-9] passed", result.stdout), result.stdout


class TestTheLedgerCountBelongsToOnePoint:
    """`RETAINED.trendCount` is what the retention ledger renders — *"This
    machine's trend readings — N kept in this browser"* — and N is the series
    for `RETAINED.trendKey`, the point the form's own fields name.

    Undoing four points calls `undoAutoSave` four times. Without the guard, the
    last call assigns ANOTHER location's series length to that counter, so the
    one panel whose job is to say what this browser holds would show a number
    belonging to a different measurement point. The node suite cannot see the
    binding; this can see the line.
    """

    def test_the_count_is_written_only_for_its_own_point(self):
        body = _APP_JS.read_text()
        assert "if (key === RETAINED.trendKey) RETAINED.trendCount = readTrend(key).length;" in body, (
            "undoAutoSave assigns RETAINED.trendCount unconditionally; called "
            "once per measurement location, the last call wins and the ledger "
            "shows another point's count"
        )

    def test_there_is_exactly_one_such_assignment_in_undo(self):
        """Non-vacuity: a second, unguarded assignment further down the function
        would defeat the guard above while leaving it in place."""
        body = _APP_JS.read_text()
        start = body.index("function undoAutoSave(")
        end = body.index("\n}", start)
        assert body[start:end].count("RETAINED.trendCount") == 1


class TestTheUndoIsOneActionOverEveryPoint:
    """The shape of the fix, asserted on the source because it is a decision
    rather than a behaviour: ONE confirmation for the run, not N controls."""

    def test_the_undo_action_removes_the_other_locations_too(self):
        body = _APP_JS.read_text()
        assert "saved.others.forEach((other) => undoAutoSave(other.key, other.ts));" in body

    def test_there_is_still_one_confirmation(self):
        """INTAKE-2 declined this fix partly to avoid inventing a per-location
        surface, and that judgement is kept: one upload is one action."""
        body = _APP_JS.read_text()
        start = body.index("    saved = autoSaveReading(jobId, data);")
        end = body.index("    // RULED D-26.", start)
        assert body[start:end].count("notify(") == 2, (
            "the save/undo block must raise exactly two toasts — one when the "
            "run is filed and one when it is put back"
        )

    def test_no_new_endpoint_was_added_for_it(self):
        """The brief's condition for taking this item at all: app.js and
        index.html, no new endpoint. The undo is entirely browser-side — these
        readings only ever existed in this browser."""
        app_py = (_ROOT / "src" / "vib_agent" / "webapp" / "app.py").read_text()
        for verb in ("undo", "unsave", "delete_reading"):
            assert f'@app.post("/api/{verb}' not in app_py
            assert f'@app.delete("/api/{verb}' not in app_py

    def test_the_stale_deliberately_no_undo_note_is_gone(self):
        """It said *"Deliberately does NOT offer an undo per location"*. Leaving
        that in the tree beside the undo is how a reader is told the opposite of
        what the code does."""
        body = _APP_JS.read_text()
        assert "Deliberately does NOT offer an undo per location" not in body


class TestTheBrowserRefusesAnUndiagnosedPoint:
    """Contract section 5 rule 1, browser half. Pinned on the source as well as
    in node, because it is the assertion that stops a future producer change
    from silently re-enabling it."""

    def test_the_status_gate_is_in_the_save_path(self):
        body = _APP_JS.read_text()
        start = body.index("function saveOtherLocationReadings(")
        end = body.index("\n}", start)
        assert "entry.status !== 'ok'" in body[start:end], (
            "saveOtherLocationReadings files any entry carrying a trend_point; "
            "a gate_fail point would join the machine's trend as a reading"
        )
