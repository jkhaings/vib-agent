"""Session UX-2 — the stepped intake, and the roster read forwards.

The behaviour runs in node (`tests/js/steps_tests.js`). This file runs it and
adds the claims about SOURCE — chiefly the one that keeps step 3 honest.

**The roster is a contract, and this is where it is enforced.**
`report/generate.py::COVERAGE_ROSTER` tells the analyst AFTER the fact which
fault families were not assessed and which input would have changed that. Step 3
shows the same table FORWARDS, as checks they can switch on. Two tables that
must say the same thing, in two languages, in two files — so the key sets are
diffed rather than trusted. A roster row renamed, added or removed now fails a
test instead of leaving the intake promising a screen the report will not run
(ROADMAP common law #9, mechanised rather than remembered).

This file never skips, except the node runner when node is absent.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import vib_agent.webapp as webapp_pkg
from vib_agent.report.generate import (
    _COVERAGE_GEOMETRY_ON_FILE,
    _COVERAGE_INPUT_PRESENT,
    COVERAGE_ROSTER,
)

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = Path(webapp_pkg.__file__).parent / "static"


def _app_js() -> str:
    return (_STATIC / "app.js").read_text()


def _index() -> str:
    return (_STATIC / "index.html").read_text()


def _js_list(name: str) -> str:
    return _app_js().partition(f"const {name} = [")[2].partition("\n];")[0]


# ══════════════════════════════════════════════════════════════════════════
# 1 · The behaviour, in a real JS runtime
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_stepped_intake_in_a_real_js_runtime():
    result = subprocess.run(
        [shutil.which("node"), str(_ROOT / "tests" / "js" / "steps_tests.js")],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
    match = re.search(r"\n(\d+)/(\d+) passed", result.stdout)
    assert match, result.stdout
    passed, total = int(match.group(1)), int(match.group(2))
    assert passed == total and total >= 20, result.stdout


# ══════════════════════════════════════════════════════════════════════════
# 2 · The roster and the intake cannot drift apart
# ══════════════════════════════════════════════════════════════════════════


class TestTheUnlocksAreTheRoster:

    def test_the_unlock_keys_are_exactly_the_rosters_input_absent_keys(self):
        """The whole reason this file exists.

        `_COVERAGE_INPUT_PRESENT` is the roster's own list of families that
        are not assessed *because an input was not provided* — and therefore
        the complete list of checks an analyst can switch on. If the intake
        offered a fifth, it would promise a screen the report cannot run; if
        it offered four of five, an analyst would never learn the missing one
        was theirs to fix.
        """
        offered = set(re.findall(r"key: '([a-z_]+)'", _js_list("UNLOCKS")))
        assert offered == set(_COVERAGE_INPUT_PRESENT), (
            f"the intake offers {sorted(offered)}; the roster's input-absent "
            f"rows are {sorted(_COVERAGE_INPUT_PRESENT)}"
        )

    def test_the_recorded_keys_are_exactly_the_rosters_geometry_on_file_keys(self):
        """The third state. Supplying these does NOT flip a roster row to
        assessed — it appends "geometry on file, awaiting a detector". The
        intake groups them separately and says exactly that, so a count of
        rotor bars never reads as a check that will run."""
        recorded = set(re.findall(r"key: '([a-z_]+)'", _js_list("GEOMETRY_ON_FILE")))
        assert recorded == set(_COVERAGE_GEOMETRY_ON_FILE), (
            f"the intake records {sorted(recorded)}; the roster's on-file "
            f"dispatch is {sorted(_COVERAGE_GEOMETRY_ON_FILE)}"
        )

    def test_the_two_groups_do_not_overlap(self):
        offered = set(re.findall(r"key: '([a-z_]+)'", _js_list("UNLOCKS")))
        recorded = set(re.findall(r"key: '([a-z_]+)'", _js_list("GEOMETRY_ON_FILE")))
        assert not offered & recorded, sorted(offered & recorded)

    def test_every_field_an_unlock_names_is_a_field_the_form_has(self):
        """An unlock that names a field the form does not carry is an
        instruction the analyst cannot follow."""
        html = _index()
        block = _js_list("UNLOCKS") + _js_list("GEOMETRY_ON_FILE")
        for field in set(re.findall(r"fieldValue\('([a-z_]+)'\)", block)) \
                | set(re.findall(r"'([a-z_]+)'(?=[,\]])", _js_list("GEOMETRY_ON_FILE"))):
            if field in {"gear_teeth", "rotor_bar_geometry", "line_frequency", "drive_type"}:
                continue          # dispatch keys, checked above
            assert f'name="{field}"' in html, field

    def test_coupled_is_an_answer_and_not_an_unlock(self):
        """`_COVERAGE_INPUT_PRESENT['uncoupled_declared']` is
        `m.coupled is False` — so "coupled" satisfies nothing and the roster
        REWORDS rather than disappearing. An intake that nagged a machine
        declared coupled to declare itself uncoupled would be asking for a
        wrong answer."""
        row = _js_list("UNLOCKS").partition("key: 'uncoupled_declared'")[2]
        assert "answered:" in row, row[:400]
        assert "=== 'coupled'" in row
        assert "=== 'uncoupled'" in row

    def test_the_bearing_unlock_carries_the_caveat_the_roster_adds_with_it(self):
        """Roster row 17: the moment the bearing screen turns on, a caveat
        appears with it — matching reads the radial channels, so an axial
        thrust-loaded fault is still not assessed. Promising four frequencies
        without it over-promises exactly where the roster is careful."""
        row = _js_list("UNLOCKS").partition("key: 'bearing_geometry'")[2]
        assert "caveat:" in row
        assert "axial" in row and "radial" in row

    def test_the_roster_still_has_the_shape_this_reads(self):
        """A floor under the two set comparisons: if the roster were empty,
        both would pass over two empty sets."""
        kinds = {row["kind"] for row in COVERAGE_ROSTER}
        assert len(COVERAGE_ROSTER) >= 15
        assert kinds >= {"no_detector", "input_absent", "caveat"}


# ══════════════════════════════════════════════════════════════════════════
# 3 · The steps are markup, and the form is still one form
# ══════════════════════════════════════════════════════════════════════════


class TestTheStepsAreReal:

    def test_all_four_panels_are_in_the_markup(self):
        """`tests/js/harness.js` refuses to answer "is this visible?" about an
        id index.html does not define — so a step built at runtime could not be
        proved hidden, which is the single question a wizard most needs
        answered."""
        html = _index()
        for i in (1, 2, 3, 4):
            assert f'id="step-{i}"' in html
            assert f'id="step-{i}-head"' in html
            assert f'id="rail-{i}"' in html
        for other in ("unlocks", "review-body", "dup-notice", "spx-1", "spx-2", "spx-3"):
            assert f'id="{other}"' in html, other

    def test_only_the_first_step_is_visible_in_the_source(self):
        html = _index()
        assert '<section class="step" id="step-1">' in html
        for i in (2, 3, 4):
            assert f'<section class="step" id="step-{i}" hidden>' in html

    def test_each_step_heading_can_receive_focus(self):
        html = _index()
        for i in (1, 2, 3, 4):
            block = html.partition(f'id="step-{i}-head"')[2].partition(">")[0]
            assert 'tabindex="-1"' in html.partition(f'id="step-{i}-head"')[0][-40:] \
                or 'tabindex="-1"' in block, i

    def test_the_view_heading_finally_has_its_tabindex(self):
        """UX-1's recorded accessibility gap: routing to `#/new` did not move
        focus, because the only heading in that view was inside `#form-card`
        and that session was byte-pinned not to touch it. This is the session
        that may."""
        assert 'id="new-head" tabindex="-1"' in _index()

    def test_the_direction_selector_sits_under_its_own_chart(self):
        html = _index()
        for i, row in ((1, "direction_row"), (2, "direction_2_row"), (3, "direction_3_row")):
            assert html.index(f'id="spx-{i}"') < html.index(f'id="{row}"'), i

    def test_required_stayed_where_the_server_needs_it(self):
        """Moved into step gating rather than off the elements: native
        validation stays authoritative, and `test_session_f2.py` still pins
        these three."""
        required = "\n".join(l for l in _index().splitlines() if " required" in l)
        for name in ('name="file"', 'name="rpm"', 'name="invite_code"'):
            assert name in required, name

    def test_a_hidden_required_field_is_recoverable(self):
        """Chrome refuses to submit a form containing an INVALID `required`
        control that is `display:none`, and says nothing an analyst can see.
        The listener is `capture`, because `invalid` does not bubble."""
        js = _app_js()
        block = js.partition("form.addEventListener('invalid'")[2].partition("true);")[0]
        assert "goToStep(on)" in block, block
        assert "el.focus()" in block
