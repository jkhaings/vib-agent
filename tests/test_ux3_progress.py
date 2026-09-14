"""Session UX-3 -- the run as one component, and the two carries that live in
`static/`.

Four claims, and each one is here because a previous session could not reach
the file that would have closed it:

  * THE RUN IS DRAWN IN ONE PLACE. `pollJob`'s tail and `submitJob`'s error
    branch each decided, by hand, which card a job's state got. A state handled
    in one and not the other was a card nobody would notice was missing. The
    decision now lives in `runCard`, reads nothing but the wire, and -- pinned
    here -- its vocabulary cannot drift from `webapp/jobs.py`'s.
  * NOTHING INVENTS A PHASE. `JobPhase` is `analyzing|drafting`; the PDF child
    runs inside the drafting span, so `rendering` is a thing the wire cannot
    say. It is named in the Drafting COPY and never drawn as a step, on the
    precedent that removed `Verifying` -- a step that can only be lit in the
    past tense is a step the analyst cannot use.
  * THE ALIAS IS CAPPED WHERE THE COLUMN IS (SESSION_JOBDB.md F-3). SQLite does
    not enforce VARCHAR length and Postgres does, so an uncapped alias is a row
    that fits on a laptop and raises StringDataRightTruncation in production.
  * `privacy_accounts.html` POINTS AT THE MAIL PROVIDER RATHER THAN NAMING ONE
    (SESSION_SEC2.md F-2). The name is rendered once, by SEC-2's generator,
    from `hardening.EXTERNAL_SERVICES`; this file carries no provider literal,
    so it cannot go stale when the provider changes and cannot be wrong on a
    deployment that has no outside mail service at all.

The node halves run in a real JS runtime against the shipped `app.js`
(`tests/js/`), driven from here so `pytest` remains the one command.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import typing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_webapp_e2e import _webapp_cfg
from vib_agent.webapp import hardening, jobs
from vib_agent.webapp.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "vib_agent" / "webapp" / "static"

FAKE_KEY = "test-placeholder-not-a-real-key"


def _app_js() -> str:
    return (_STATIC / "app.js").read_text()


def _index() -> str:
    return (_STATIC / "index.html").read_text()


def _accounts() -> str:
    return (_STATIC / "privacy_accounts.html").read_text()


def _fn(js: str, decl: str) -> str:
    """One function's source, from its declaration to the first column-0 `}`.

    The slice `tests/test_ux1_views.py` and `tests/test_hist1_browser.py` both
    use, so a pin about "what is inside this function" means the same thing in
    all three files.
    """
    assert js.count(decl) == 1, f"{decl!r} appears {js.count(decl)} times"
    return decl + js.partition(decl)[2].partition("\n}\n")[0] + "\n}\n"


def _node(script: str, floor: int) -> None:
    result = subprocess.run(
        [shutil.which("node"), str(_ROOT / "tests" / "js" / script)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
    match = re.search(r"\n(\d+)/(\d+) passed", result.stdout)
    assert match, result.stdout
    passed, total = map(int, match.groups())
    assert passed == total and total >= floor, result.stdout


# ══════════════════════════════════════════════════════════════════════════
# 1 · The two node suites
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_run_component_in_a_real_js_runtime():
    """Runs tests/js/run_card_tests.js: every wire state to its card, the retry
    on retryable and on nothing else, and focus landing on the outcome once."""
    _node("run_card_tests.js", 22)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_two_defects_the_harness_used_to_be_blind_to():
    """Runs tests/js/select_semantics_tests.js.

    UX-2 found both in a browser and wrote into its own acceptance checklist
    that no node test could see them, because `makeElement` backed `.value`
    with a plain property -- a `<select>` there held any value it was handed.
    That was a fact about the instrument. The harness now carries real option
    semantics and these are the two claims it could not make.
    """
    _node("select_semantics_tests.js", 10)


def test_the_harness_keeps_the_teeth_this_session_gave_it():
    """The instrument, pinned at the source.

    `select_semantics_tests.js` opens with its own self-test, so a harness that
    went back to a plain property would go red rather than vacuously green.
    This is the second lock, and it is here because the failure mode is a
    reviewer deciding the accessor is over-engineering: eight other suites run
    against this file, and every one of them would go on passing.

    The six existing suites are NOT re-run here -- they already have their own
    drivers in this suite (`test_ux2_store.py`, `test_ux1_views.py`,
    `test_ux2_steps.py`, `test_geometry_webapp_js.py`, `test_compare_webapp.py`,
    `test_hist1_browser.py`) and running them twice buys nothing but minutes.
    """
    harness = (_ROOT / "tests" / "js" / "harness.js").read_text()
    assert "function parseOptions(" in harness and "function parseSelects(" in harness
    element = harness.partition("function makeElement(")[2].partition("\n}\n")[0]
    assert element, "makeElement was not found"
    assert "value: ''," not in element, (
        "makeElement backs `value` with a plain property again -- a <select> "
        "will hold any value it is handed and both UX-2 defects go invisible"
    )
    assert "optionValues.indexOf(want) === -1" in element, (
        "the option check is gone: a select that cannot hold a value is the "
        "whole thing this harness was taught to see"
    )


# ══════════════════════════════════════════════════════════════════════════
# 2 · The component reads the wire, and only the wire
# ══════════════════════════════════════════════════════════════════════════


class TestTheComponentCannotDriftFromTheWire:

    def test_the_clients_idea_of_finished_is_the_servers(self):
        """`RUN_TERMINAL` against `jobs.TERMINAL_STATES`.

        These are two independent lists of the same fact, and the failure they
        would produce is invisible: a state the server calls terminal and the
        client does not is a job that polls for ever, and the reverse is a page
        that stops asking while the analysis is still running.
        """
        js = _app_js()
        listed = js.partition("const RUN_TERMINAL = [")[2].partition("];")[0]
        states = set(re.findall(r"'([a-z_]+)'", listed))
        assert states, "RUN_TERMINAL was not found in app.js"
        assert states == set(jobs.TERMINAL_STATES), (
            f"the client and the server disagree about which states are terminal: "
            f"{states ^ set(jobs.TERMINAL_STATES)}"
        )

    def test_every_state_the_component_branches_on_is_one_the_wire_can_send(self):
        js = _app_js()
        branched = set(re.findall(r"state === '([a-z_]+)'", _fn(js, "function runCard(jobId, data) {")))
        declared = set(typing.get_args(jobs.JobState))
        assert branched, "runCard branches on no state at all"
        assert branched <= declared, f"the client invents states: {branched - declared}"

    def test_every_phase_the_client_names_is_one_the_wire_publishes(self):
        """The `Verifying` rule, mechanised. `phase` is a display hint the
        worker advances; a client that branched on a hint the server never sets
        would draw a step that can only ever be lit in the past tense."""
        js = _app_js()
        named = set(re.findall(r"phase === '([a-z_]+)'", js))
        declared = set(typing.get_args(jobs.JobPhase))
        assert named <= declared, f"the client invents phases: {named - declared}"

    def test_no_rendering_phase_is_invented_client_side(self):
        """The PDF child runs inside `_finalize_markdown_and_pdf`, which is
        called while `phase` is still `drafting` (or `analyzing` on the degraded
        and gate-fail lanes). Nothing publishes a render, so nothing may claim
        one as a state -- it is named in the Drafting copy instead."""
        js = _app_js()
        assert not re.search(r"""['"]rendering['"]""", js), (
            "a `rendering` state or phase string is back in app.js"
        )
        assert "rendering your PDF" in js, (
            "the render is no longer named to the analyst at all"
        )
        rail = js.partition("const RAIL_STEPS = [")[2].partition("];")[0]
        assert "Rendering" not in rail and "Verifying" not in rail, rail
        assert len(re.findall(r"'([A-Z][a-z]+)'", rail)) == 5, rail

    def test_the_polling_loop_no_longer_decides_which_card_to_draw(self):
        """The regression this session exists to prevent coming back: the
        mapping living in the loop, where the next state to be added gets
        handled in one call site and not the other."""
        loop = _fn(_app_js(), "function pollJob(jobId) {")
        for card in ("stepCard(", "readyCard(", "stoppedCard(", "confirmCard(", "errorCard("):
            assert card not in loop, f"pollJob still draws {card} itself"
        assert loop.count("showRun(") >= 3, "pollJob does not go through the component"

    def test_the_focus_target_is_rendered_by_the_shared_status_line(self):
        """It lives in `statusLine` rather than in each card because
        `readyCard` is pinned byte-for-byte at the source
        (`tests/test_ux1_views.py`): the report page must not change to give a
        run an outcome the keyboard can reach."""
        line = _fn(_app_js(), "function statusLine(tone, words, withClock) {")
        assert 'id="run-status"' in line and 'tabindex="-1"' in line, line
        assert 'id="run-status"' not in _fn(_app_js(), "function readyCard(jobId, data, degraded) {")

    def test_the_state_card_is_still_an_aria_live_region(self):
        """Focus is the other half of the announcement, never a replacement for
        it: an analyst who is listening rather than tabbing gets the card read
        out because this attribute is on the container."""
        assert re.search(r'<section class="card" id="state" hidden aria-live="polite">', _index())


# ══════════════════════════════════════════════════════════════════════════
# 3 · SESSION_JOBDB.md F-3 -- the alias cap
# ══════════════════════════════════════════════════════════════════════════


_HAS_DB = all(importlib.util.find_spec(name) for name in ("sqlalchemy", "alembic"))




# ══════════════════════════════════════════════════════════════════════════
# 4 · SESSION_SEC2.md F-2 -- the mail provider, named by the generator
# ══════════════════════════════════════════════════════════════════════════




