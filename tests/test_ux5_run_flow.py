"""Session UX-5 — the run flow, from file to trusted result.

The behavioural half runs in node against the shipped `app.js` (four suites,
driven below), because what a `<select>` does with a value its options do not
carry, what `localStorage` and `IndexedDB` hold after a save, and what a blob
URL is spent on are not things a `TestClient` can be asked.

This file adds the claims that are about SOURCE rather than behaviour — the
ones a browser test would pass while the promise underneath it went stale —
and it carries the two D-22 debts this session owes `static/privacy.html`,
which belongs to LEGAL-1 this round.

It never skips, except the node runner when node is absent.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import vib_agent.webapp as webapp_pkg

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = Path(webapp_pkg.__file__).parent / "static"


def _app_js() -> str:
    return (_STATIC / "app.js").read_text()


def _index() -> str:
    return (_STATIC / "index.html").read_text()


def _code() -> str:
    """`app.js` with its comments stripped.

    SESSION_UX2 F-3, and this file met it on its first run: a source-level word
    ban that reads comments fires on the sentence STATING the promise. Three of
    the bans below quote the stranger verbatim in the comment above the fix, so
    scanning the raw file made them fail against their own explanation — and a
    ban that fires on the reason it exists is a ban that gets deleted rather
    than fixed. The house rule from that finding: a source-level word ban reads
    CODE, never comments.
    """
    js = re.sub(r"/\*[\s\S]*?\*/", "", _app_js())
    return "\n".join(re.sub(r"//.*$", "", line) for line in js.split("\n"))


def _privacy() -> str:
    return (_STATIC / "privacy.html").read_text()


def _node(script: str, floor: int) -> None:
    """Run one node suite and assert it ran the number of checks it claims.

    The floor is the UX-2 F-4 lesson: a printed total that is not the number of
    checks run is a quiet lie in a gate, and a suite whose checks were deleted
    should fail here rather than pass with fewer.
    """
    result = subprocess.run(
        [shutil.which("node"), str(_ROOT / "tests" / "js" / script)],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
    match = re.search(r"\n(\d+)/(\d+) passed", result.stdout)
    assert match, result.stdout
    passed, total = map(int, match.groups())
    assert passed == total and total >= floor, result.stdout


# ══════════════════════════════════════════════════════════════════════════
# 1 · The behaviour, in a real JS runtime
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("script,floor", [
    ("intake_tests.js", 19),
    ("result_tests.js", 24),
    ("machine_page_tests.js", 13),
])
def test_the_run_flow_in_a_real_js_runtime(script: str, floor: int) -> None:
    _node(script, floor)


# ══════════════════════════════════════════════════════════════════════════
# 2 · RULED D-24 AMENDED — asked once, at intake, default keep both
# ══════════════════════════════════════════════════════════════════════════


class TestTheAmendedDuplicateRule:
    """The old default was wrong for the flow this product is sold on:
    before-and-after one repair is the same machine, the same point, the same
    axis, on the same day, and `replace` destroyed the "before"."""

    def test_the_default_is_keep_both_at_the_source(self):
        js = _app_js()
        assert "let duplicateChoice = 'keep_both';" in js
        assert "let duplicateChoice = 'replace'" not in js

    def test_the_apply_side_replaces_only_when_asked(self):
        """Written as `mode === 'replace'` for the reason UX-2 wrote its
        inverse: a call site that passes no mode must inherit the RULING's
        default, and the default is now the other one."""
        block = _app_js().partition("function saveTrendPoint(key, point, mode) {")[2] \
                         .partition("\n}")[0]
        assert "const replace = mode === 'replace';" in block, block

    def test_the_second_ask_is_gone(self):
        """C7: the question was asked at intake and again on the report. One
        decision, one question — and the report's copy promised the second."""
        js = _app_js()
        assert "you are asked again on the report" not in js
        assert 'data-mode="keep_both">Keep both<' not in js
        assert 'data-mode="replace">Replace it<' not in js

    def test_one_source_order_at_every_width(self):
        """C7 again: the two controls swapped places between desktop and
        mobile. Emitted in one order, and no rule reverses them."""
        block = _app_js().partition("function renderDuplicate() {")[2].partition("\n}")[0]
        assert block.index('value="keep_both"') < block.index('value="replace"')
        css = (_STATIC / "style.css").read_text()
        assert not re.search(r"\.dup[^{]*\{[^}]*(column-reverse|row-reverse)", css)


# ══════════════════════════════════════════════════════════════════════════
# 3 · RULED D-26 — the report is kept client-side, and nothing new is sent
# ══════════════════════════════════════════════════════════════════════════


class TestTheReportIsKeptWithTheReading:

    def test_the_bytes_are_not_in_localstorage(self):
        """A ~300 KB blob in a ~5 MB origin-wide quota shared with the machine
        and trend stores makes `setItem` throw — and the thing that would then
        stop working is saving a READING. The facts are small strings and go
        beside the trend; the bytes go in IndexedDB."""
        js = _app_js()
        setters = set(re.findall(r"localStorage\.setItem\(\s*([A-Za-z_][\w]*)", js))
        assert setters == {"STORE_KEY", "TREND_KEY", "REPORTS_KEY"}
        facts = js.partition("function putReportFacts(key, ts, facts) {")[2].partition("\n}")[0]
        for bulk in ("blob", "Blob", "arrayBuffer", "base64"):
            assert bulk not in facts, f"{bulk} in the localStorage store"

    def test_nothing_new_crosses_the_wire(self):
        """The PDF comes from the endpoint the analyst can already click, for
        the 60 minutes it is already served. No new route, no new field."""
        block = _app_js().partition("function keepReport(key, ts, jobId) {")[2] \
                         .partition("\n}")[0]
        assert "/api/jobs/${encodeURIComponent(jobId)}/report.pdf" in block
        urls = set(re.findall(r"fetch\(\s*[`'\"]([^`'\"$]*)", _app_js()))
        assert urls <= {"/api/jobs", "/api/jobs/", "/example-spectrum.csv"}, urls

    def test_every_entry_point_degrades_rather_than_throwing(self):
        """A browser in private mode, with storage disabled or over quota, must
        produce exactly the run it produced before this session. `indexedDB` is
        reached through `typeof` because a bare reference is a ReferenceError."""
        js = _app_js()
        assert "typeof indexedDB !== 'undefined'" in js
        block = js.partition("function reportTx(mode, run) {")[2].partition("\n}")[0]
        assert ".catch(() => null)" in block, "a rejection escapes the store"

    def test_a_kept_report_cannot_outlive_the_reading_it_is_about(self):
        """The ledger and /privacy both say a kept report lasts until the
        reading is deleted. A blob left behind would be undeletable: no surface
        would ever offer it again."""
        js = _app_js()
        for fn, need in (
            ("function deleteReadingLocal(id, ts) {", ("dropReportFacts(id, ts)", "dropReport(id, ts)")),
            ("function forgetMachineLocal(id) {", ("dropReportFacts(id)", "dropReports(id)")),
        ):
            block = js.partition(fn)[2].partition("\n}")[0]
            for call in need:
                assert call in block, f"{fn} does not {call}"
        rename = js.partition("function updateMachineLocal(oldId, fields) {")[2].partition("\n}")[0]
        assert "moveReportFacts(oldId, id)" in rename and "moveReports(oldId, id)" in rename

    def test_the_blob_urls_are_released(self):
        """One per held report per render. A URL created on every render and
        never revoked pins the whole PDF in memory for the life of the page,
        and the machine page re-renders on every delete and every cancel."""
        js = _app_js()
        assert "function releaseReportUrls()" in js
        assert js.count("URL.revokeObjectURL(") == 1
        load = js.partition("function loadReportUrls(key) {")[2].partition("\n}")[0]
        assert "releaseReportUrls();" in load
        route = js.partition("function route() {")[2].partition("\n}")[0]
        assert "releaseReportUrls();" in route


# ══════════════════════════════════════════════════════════════════════════
# 4 · The intake (B5, B7, C3, C8, C11)
# ══════════════════════════════════════════════════════════════════════════


class TestTheIntake:

    def test_the_corpus_bearings_are_filtered_at_the_ui_not_in_config(self):
        """The operator's instruction, and the only correct place: the geometry
        is real, `bearing_spec_from_form` still honours all eight, and the .mat
        adapters name their own rig bearing without going through this form."""
        cfg = json.loads((_ROOT / "config" / "bearings.json").read_text())["bearings"]
        corpus = re.findall(r"'([^']+)'", _app_js()
                            .partition("function corpusBearings() {")[2].partition("\n}")[0])
        assert corpus == ["SKF_6205", "MAFAULDA_ABVT", "MFPT_NICE"]
        for key in corpus:
            assert key in cfg, f"{key} lost its geometry; the benchmarks read it"
            assert key not in _index(), f"{key} is still offered in the markup"

    def test_a_saved_rig_bearing_is_carried_over_rather_than_dropped(self):
        """A `<select>` resets to empty for a value its options do not carry.
        `SKF_6205` and `6205` are the same bearing with the same geometry, so
        dropping it would turn the screen OFF for a machine it had been running
        for, silently."""
        block = _app_js().partition("function cardForForm(card) {")[2].partition("\n}")[0]
        assert "corpusEquivalent(out.bearing_model)" in block
        assert "if (same) out.bearing_model = same;" in block

    def test_the_velocity_unit_is_required_and_never_blank(self):
        """B7: it rendered blank (selectedIndex −1) under a caption reading
        'Stated, never assumed — the #1 severity error', and the wizard let it
        through."""
        assert '<select name="velocity_unit" id="unit" required>' in _index()
        assert '<option value="mm_s" selected>' in _index()
        block = _app_js().partition("function cardForForm(card) {")[2].partition("\n}")[0]
        assert "if (!out.velocity_unit) out.velocity_unit = 'mm_s';" in block

    def test_an_unnamed_card_is_not_a_machine(self):
        """C3: a stale record appeared in the dropdown as a phantom 'Unnamed
        machine' that led to a machine the analyst could not open."""
        block = _app_js().partition("function migrateEntry(entry) {")[2].partition("\n}")[0]
        assert "namedMachine(out.machine_alias) ? out : null" in block
        read = _app_js().partition("function readMachines() {")[2].partition("\n}")[0]
        assert read.count(".filter(Boolean)") == 2, "both read paths, or the v1 migration carries it"

    def test_a_stated_direction_is_not_an_assumed_one(self):
        """C8. Radial-horizontal WAS the empty option, so choosing it posted
        nothing and the server's default made it 'assumed'. The label is born
        here — not in report/ or pdm_core — and so is the fix."""
        block = _index().partition('id="direction"')[2].partition("</select>")[0]
        assert '<option value="radial_h">Radial – horizontal</option>' in block
        assert '<option value="" selected>Not given' in block
        code = _code()
        assert "(direction assumed)" not in code
        assert "direction not given" in code

    def test_one_word_for_an_empty_field(self):
        """C11: the form said 'Not stated' and the machine card said 'Not
        recorded' about the same blank field, two screens apart."""
        code = _code()
        assert "Not recorded" not in code
        assert "Not provided</span>" not in code
        assert '<option value="" selected>Not provided</option>' not in _index()

    def test_one_verb_for_deletion(self):
        assert "const word = 'delete';" in _code()
        assert ">Forget<" not in _index()
        assert "'forget'" not in _code()


# ══════════════════════════════════════════════════════════════════════════
# 5 · The result (B3, C5, C6, C9, U8, U9)
# ══════════════════════════════════════════════════════════════════════════


class TestTheResult:

    def test_the_job_reference_is_shown_wherever_it_is_asked_for(self):
        """B3 was the stranger's worst moment and it happened on 3/3 runs: the
        copy said to send us the job reference and no job reference was on the
        page."""
        js = _app_js()
        assert "function jobRefBlock(jobId, reason)" in js
        assert 'id="job-ref"' in js and 'id="copy-ref"' in js
        # Both places the copy asks for it.
        ready = js.partition("function readyCard(jobId, data, degraded) {")[2].partition("\n}\n")[0]
        assert "jobRefBlock(jobId, data.degraded_reason)" in ready
        assert "send us the job reference below" in ready

    def test_the_reason_is_the_one_the_wire_can_support(self):
        """`degraded_reason` has exactly two values and the finer cause is
        deliberately not on the wire. Saying which of the four causes it was
        would be a guess; saying it is in our log against this reference is
        not."""
        block = _app_js().partition("function jobRefBlock(jobId, reason) {")[2].partition("\n}")[0]
        assert "reason === 'spend_budget'" in block
        assert "in our log against this reference" in block

    def test_the_run_button_does_not_invite_a_second_paid_run(self):
        block = _app_js().partition("const poll = async () => {")[2].partition("\n  };")[0]
        assert "const finished = data.state === 'done' || data.state === 'degraded';" in block
        assert "submitBtn.disabled = finished;" in block

    def test_the_reading_is_saved_against_a_machine_the_analyst_saved(self):
        """RULED (Sep 7), C6. A machine that was only TYPED keeps the button:
        inventing a series for it silently is the same error as losing one."""
        js = _app_js()
        block = js.partition("RETAINED.autosave = ")[2].partition(";")[0]
        assert "rememberBox.checked" in block and "cardKey(m) === RETAINED.trendKey" in block
        assert "function autoSaveReading(jobId, data) {" in js
        assert "function undoAutoSave(key, ts) {" in js

    def test_analyze_another_file_clears_the_stale_result(self):
        """C9: the old result stayed on screen while the new review was being
        edited, and the button landed on Review rather than the file picker."""
        block = _app_js().partition("if (!e.target || e.target.id !== 'again') return;")[2] \
                         .partition("});")[0]
        assert "goToStep(2)" in block
        assert "stateCard.innerHTML = '';" in block
        assert "lastReady = null;" in block
        assert "clearFileSlots();" in block

    def test_the_severity_card_carries_the_number(self):
        """U9: the card said 'Zone B · ISO 20816-3' and the value it classified
        was two panels further down."""
        block = _app_js().partition("function resultCards(rs, point) {")[2].partition("\n}")[0]
        assert "point.severity_rms_mms.toFixed(2)" in block
        assert "mm/s RMS" in block
        # Composed, not spelled: the value and the standard are joined at
        # render time, and the node suite asserts the RENDERED string. This
        # pins the composition so a later edit cannot drop one half.
        assert "${value} · ISO 20816-3" in block, block

    def test_the_ledger_is_one_line_until_it_is_asked_for(self):
        """U8. Shortened, not weakened: every row is still in the document and
        still diffed against static/privacy.html."""
        js = _app_js()
        block = js.partition("function ledger() {")[2].partition("\n}")[0]
        assert '<details class="ledger">' in block and "<summary>" in block
        assert "What was kept, and for how long" in block
        assert "/privacy" in block

    def test_the_ledger_has_eight_live_rows_and_the_eighth_is_the_report(self):
        block = _app_js().partition("function ledger() {")[2].partition("\n}")[0]
        assert block.count("${row('") + block.count("${soon(") >= 8
        assert "<b>Your reports</b>" in block
        assert "Kept until you delete the reading" in block


# ══════════════════════════════════════════════════════════════════════════
# 6 · D-22 — the two promises this session owes `static/privacy.html`
# ══════════════════════════════════════════════════════════════════════════


class TestTheDebtsOwedToLegal1:
    """RULED D-22: a session that adds or widens a localStorage key updates the
    retention ledger AND `static/privacy.html` in the same commit.

    This session does both halves it can: the ledger's eighth row ships here,
    and the wording `privacy.html` needs is written VERBATIM in
    `outputs/SESSION_UX5.md`. The file itself belongs to LEGAL-1 this round,
    which runs after this branch.

    So the debt was mechanical rather than remembered. Both pins were STRICT
    xfails, which under strict=True turn a XPASS into a failure: the suite went
    red the moment the wording landed, and the only way back to green was a
    session deliberately acknowledging the debt was paid.

    PAID -- Session TIDY-1, from `outputs/SESSION_UX5.md` §6 word for word. The
    markers are gone and these are ordinary pins now. They are asserted against
    the SENTENCE rather than a paraphrase of it, so wording that says something
    different still fails here.

    One correction, recorded because it is the kind of thing that gets fixed
    the wrong way round: the first pin read `"Reports are kept in this
    browser"` and §6's paragraph opens `"Your reports are kept in this browser
    too"` -- a capital letter apart, from an earlier draft of the sentence. §6
    is the authority (it is what the branch was told to paste), so the pin moved
    to the wording, not the wording to the pin, and it moved to MORE of the
    sentence rather than less.
    """

    def test_privacy_names_the_reports_kept_in_the_browser(self):
        page = _privacy()
        assert "Your reports are kept in this browser too" in page
        assert "the written narrative" in page

    def test_privacy_calls_the_control_by_the_name_it_now_has(self):
        assert "the “Delete” button deletes it" in _privacy()

    def test_the_ledger_half_of_the_debt_shipped_in_this_commit(self):
        """The half that is this session's to pay. If this fails, the branch is
        claiming a promise it did not make."""
        block = _app_js().partition("function ledger() {")[2].partition("\n}")[0]
        assert "<b>Your reports</b>" in block
        assert "this browser only" in block

    def test_the_server_side_promise_is_untouched(self):
        """D-26 says server retention is UNCHANGED. The 60-minute sentence and
        the schedule behind it are not this session's to move, and the ledger
        still says exactly what it said."""
        block = _app_js().partition("function ledger() {")[2].partition("\n}")[0]
        assert "kept for <b>60 minutes</b> from now, then deleted" in block
        page = _privacy()
        assert "60 minutes" in page
