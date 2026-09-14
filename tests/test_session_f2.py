"""Session F2 — field-feedback + UI pass.

Covers the demo path, the code-in-link prefill, the slimmed form's defaults and
progressive disclosure, browser-only machine memory, the drop-zone copy, the
"RCA" wording sweep, and the privacy-page additions.

Same discipline as the rest of the webapp suite: a fake (or deliberately
failing) Anthropic client everywhere a job could reach drafting — zero real API
calls. The static assets are asserted as SERVED content, not as files on disk,
so a route that stops serving them fails here too.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_webapp_e2e import _poll_until_terminal, _webapp_cfg
from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.webapp.app import create_app

_STATIC = Path(__file__).resolve().parents[1] / "src" / "vib_agent" / "webapp" / "static"
_EXAMPLE_CSV = _STATIC / "example_spectrum.csv"

# What the hero's "Run the example analysis" button posts (app.js EXAMPLE_FIELDS
# / EXAMPLE_FILENAME). Kept here in Python so a drift between the two is a test
# failure, not a silent behaviour change on the product path.
EXAMPLE_FILENAME = "example-bearing-fault.csv"
EXAMPLE_FIELDS = {
    "machine_alias": "Demo Pump",
    "rpm": "1800",
    "iso_group": "2",
    "iso_support": "rigid",
    # Session INTAKE-2 — required on the wire, and truthful: the demo machine
    # is called "Demo Pump".
    "machine_type": "pump",
    "bearing_model": "6206",
    "velocity_unit": "mm_s",
    "detection_type": "rms",
    "mode": "spectrum",
}


class _NoApiKeyFactory:
    """Stands in for `anthropic.Anthropic()` on a host with no ANTHROPIC_API_KEY:
    constructing the client raises. Nothing here can reach the network."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise RuntimeError("The api_key client option must be set")


def _app(*, factory=None, contact_email="ops@example.test"):
    return create_app(
        webapp_cfg=_webapp_cfg(),
        invite_codes={"demo-code": "engineer-1"},
        contact_email=contact_email,
        anthropic_client_factory=factory,
    )


def _index(client: TestClient) -> str:
    return client.get("/").text


def _app_js() -> str:
    return (_STATIC / "app.js").read_text()


# ══════════════════════════════════════════════════════════════════════════
# 11 · Visual polish, inside the ui-v1 system
# ══════════════════════════════════════════════════════════════════════════
class TestUiV1SystemIntact:
    def _css(self, client) -> str:
        return client.get("/style.css").text

    def test_design_tokens_are_unchanged(self):
        with TestClient(_app()) as client:
            css = self._css(client)
        for token in ("--paper:#F6F7F5", "--card:#FFFFFF", "--ink:#101816", "--mut:#5A6662",
                      "--line:#D8DCD7", "--brand:#1B4640", "--trace:#0B63C5",
                      "--zoneA:#2E7D46", "--zoneC:#8A5A12", "--zoneD:#B3372B", "--r:6px"):
            assert token in css, f"ui-v1 token changed: {token}"

    def test_typography_and_masthead_signature_are_unchanged(self):
        with TestClient(_app()) as client:
            css = self._css(client)
            html = _index(client)
        assert css.count("@font-face") == 6 and "IBM Plex" in css
        lockup = "VIB-AGENT<span>VIBRATION ANALYSIS</span>"
        assert 'class="spectrum"' in html and lockup in html

    def test_no_framework_and_no_build_step(self):
        """One hand-written stylesheet, one hand-written script, nothing fetched
        from a third party — which is also what the CSP allows."""
        with TestClient(_app()) as client:
            html = _index(client)
            css = self._css(client)
        assert html.count("<script") == 1 and 'src="/app.js"' in html
        assert "://" not in css.replace("https://", "")  # no CDN/@import anywhere
        for external in ("cdn.", "googleapis", "unpkg", "jsdelivr", "//fonts."):
            assert external not in html and external not in css

    def test_focus_states_and_reduced_motion_are_honoured(self):
        with TestClient(_app()) as client:
            css = self._css(client)
        assert ":focus-visible{outline:2px solid var(--trace)" in css
        assert "@media (prefers-reduced-motion:reduce)" in css
        # and the scripted scrolling honours it too
        assert "prefers-reduced-motion: reduce" in _app_js()
        assert "reduceMotion ? 'auto' : 'smooth'" in _app_js()

    def test_consent_rows_keep_their_sentence_together(self):
        """.consent is a flex row, so a bare <a> child becomes its own flex item
        and the '(Privacy)' link drifts away from the sentence it belongs to.
        The text is wrapped in a span for that reason — keep it."""
        with TestClient(_app()) as client:
            html = _index(client)
            css = self._css(client)
        consent_labels = html.count('class="consent full"')
        assert consent_labels == 2
        assert html.count("<span>Remember this machine") == 1
        assert html.count("<span>Keep a pseudonymized trace") == 1
        assert ".consent > span{flex:1}" in css

    def test_card_hierarchy_and_phone_rules_exist(self):
        with TestClient(_app()) as client:
            css = self._css(client)
        assert ".card > .eyebrow{" in css                     # card title rule
        assert "form > fieldset + fieldset{border-top" in css  # section hairlines
        phone = css.partition("@media (max-width:640px)")[2]
        assert phone, "phone breakpoint missing"
        for rule in (".hero-actions{flex-direction:column", ".ready-actions{flex-direction:column",
                     ".card{padding:20px 18px 18px}"):
            assert rule in phone, f"phone rule missing: {rule}"


# ══════════════════════════════════════════════════════════════════════════
# 10 · No burned invite code in the placeholder
# ══════════════════════════════════════════════════════════════════════════
class TestInvitePlaceholder:
    def test_placeholder_is_neutral(self):
        with TestClient(_app()) as client:
            html = _index(client)
        assert 'placeholder="your invite code"' in html

    def test_no_real_looking_code_is_shipped_in_the_page(self):
        """A placeholder that looks like a code gets typed in — and the one that
        was burned there was a real issued code."""
        with TestClient(_app()) as client:
            html = _index(client)
        import re as _re

        field = _re.search(r"<input[^>]*name=\"invite_code\"[^>]*>", html)
        assert field, "invite code field missing"
        placeholder = _re.search(r'placeholder="([^"]*)"', field.group(0)).group(1)
        assert " " in placeholder and not _re.fullmatch(r"[A-Za-z0-9]+", placeholder), (
            f"invite placeholder {placeholder!r} reads as a real code"
        )


# ══════════════════════════════════════════════════════════════════════════
# 9 · Privacy page
# ══════════════════════════════════════════════════════════════════════════
class TestPrivacyPage:
    def test_affirmative_no_ip_logging_line(self):
        with TestClient(_app()) as client:
            html = client.get("/privacy").text
        assert "The application keeps no request logs containing visitor IP addresses." in html
        # scoped honestly: system-layer logs are named as out of scope
        assert "System-level security logs" in html

    def test_browser_storage_line(self):
        with TestClient(_app()) as client:
            html = client.get("/privacy").text
        assert "Saved machines are stored only in your browser, never on our servers." in html
        assert "Your invite code is never stored." in html

    def test_the_claim_matches_the_code(self):
        """The affirmative line is only true while the limiter stays stateless —
        assert the mechanism, not just the prose. (The deployment half of this
        claim lived in `deploy/`, which is not part of this repository.)"""
        limiter = (Path(__file__).resolve().parents[1] / "src" / "vib_agent" / "webapp"
                   / "hardening.py").read_text()
        assert "_log" not in limiter and "logging" not in limiter  # the limiter logs nothing


# ══════════════════════════════════════════════════════════════════════════
# 8 · Conflicted-evidence headline
# ══════════════════════════════════════════════════════════════════════════
def _spectrum_case(tmp_path, peaks, *, alias="M", rpm=1800.0, bearing="6206"):
    import csv

    fmax, n = 400.0, 801
    freqs = [round(i * fmax / (n - 1), 2) for i in range(n)]
    amp = [0.006] * n
    for pk, a in peaks:
        amp[min(range(n), key=lambda i: abs(freqs[i] - pk))] = a
    path = tmp_path / "spec.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["freq_hz", "amplitude"])
        for fr, a in zip(freqs, amp):
            w.writerow([fr, a])
    form = UploadForm(machine_alias=alias, rpm=rpm, iso_group="2", iso_support="rigid", machine_type="motor",
                      bearing_model=bearing)
    case, _, _ = parse_upload(path, form, bearings_cfg=load_config("bearings"))
    return case


def _analyze(case):
    return run_analysis(case, iso_table=load_config("iso_zones")["zones"],
                        thresholds=load_thresholds("route"),
                        rules=load_config("next_measurements"))


class TestConflictedEvidenceHeadline:
    """A committed call plus a STRONG candidate from another fault family reads
    as a conflict to any analyst, so the report says so in its headline. The
    commitment itself is unchanged: the committed diagnosis and its computed
    confidence are still stated, in the next sentence, and the candidate keeps
    its adjudication in Also considered."""

    _CONFLICTED = [(30.0, 2.5), (60.0, 0.45), (107.03, 2.3), (214.06, 1.15),
                   (77.03, 0.46), (137.03, 0.46)]

    def test_headline_fires_on_a_real_cross_family_conflict(self, tmp_path):
        from vib_agent.report.generate import render_markdown

        case = _spectrum_case(tmp_path, self._CONFLICTED)
        result = _analyze(case)
        assert {f.fault for f in result.findings} == {"bearing_outer_race"}
        assert {d.fault for d in result.rca.differential} == {"imbalance"}
        md = render_markdown(result, case.machine)
        assert ("Possible Bearing outer-race fault (BPFO) with evidence of Rotor imbalance "
                "— further validation recommended.") in md
        # the commitment is still explicit, with its computed confidence
        assert "The committed diagnosis is Bearing outer-race fault (BPFO) (high confidence)." in md
        # and the candidate still carries its adjudication
        assert "**Also considered:**" in md and "_Rotor imbalance_ (medium confidence)" in md

    def test_no_headline_without_a_differential(self, tmp_path):
        """The ordinary committed report is untouched — byte-for-byte the same
        executive summary as before this feature existed."""
        from vib_agent.report.generate import render_markdown

        form = UploadForm(machine_alias="Demo Pump", rpm=1800.0, iso_group="2",
                          iso_support="rigid", machine_type="motor", bearing_model="6206")
        case, _, _ = parse_upload(_EXAMPLE_CSV, form, bearings_cfg=load_config("bearings"))
        result = _analyze(case)
        assert result.rca.differential == []
        md = render_markdown(result, case.machine)
        assert "with evidence of" not in md
        assert f"{case.machine.name} is in ISO Zone" in md
        assert "The committed diagnosis is Bearing outer-race fault (BPFO) (high confidence)." in md

    def test_same_family_candidate_is_not_a_conflict(self, tmp_path):
        """A second bearing family set aside next to a committed bearing call is a
        sub-type question, not conflicting evidence — Also considered already
        states the adjudication for it."""
        from vib_agent.models import DifferentialCandidate
        from vib_agent.report.generate import _conflicted_pair

        result = _analyze(_spectrum_case(tmp_path, self._CONFLICTED))
        same_family = result.rca.model_copy(update={"differential": [
            DifferentialCandidate(fault="bearing_inner_race", description="d", confidence="medium",
                                  adjudication="Downgraded: synchronous with a shaft order.")]})
        assert _conflicted_pair(result.model_copy(update={"rca": same_family})) is None

    def test_low_confidence_candidate_is_not_strong_enough(self, tmp_path):
        from vib_agent.models import DifferentialCandidate
        from vib_agent.report.generate import _conflicted_pair

        result = _analyze(_spectrum_case(tmp_path, self._CONFLICTED))
        assert _conflicted_pair(result) is not None
        weak = result.rca.model_copy(update={"differential": [
            DifferentialCandidate(fault="imbalance", description="d", confidence="low",
                                  adjudication="Suppressed: a bearing fault explains the 1× energy.")]})
        assert _conflicted_pair(result.model_copy(update={"rca": weak})) is None

    def test_headline_is_the_only_wording_used(self, tmp_path):
        """Exactly the specified sentence — a checker recognises this shape, so
        the report must not improvise around it."""
        from vib_agent.report.generate import _conflicted_headline

        result = _analyze(_spectrum_case(tmp_path, self._CONFLICTED))
        assert _conflicted_headline(result) == (
            "Possible Bearing outer-race fault (BPFO) with evidence of Rotor imbalance "
            "— further validation recommended."
        )


# ══════════════════════════════════════════════════════════════════════════
# 7 · "RCA" wording sweep
# ══════════════════════════════════════════════════════════════════════════
class TestFaultIdentificationWording:
    """Analyst-facing surfaces say "fault identification" / "fault diagnosis".
    Internal code names (result.rca, bearing_rca.py, the `rca` config section)
    are deliberately untouched — this is a vocabulary change for readers, not a
    rename of the machinery."""

    def _served_pages(self, client):
        return {path: client.get(path).text for path in ("/", "/privacy", "/validation")}

    def test_no_analyst_facing_rca_acronym_on_the_site(self):
        with TestClient(_app()) as client:
            for path, html in self._served_pages(client).items():
                assert "RCA" not in html, f"{path} still says RCA"

    def test_the_capability_is_named_on_the_index(self):
        with TestClient(_app()) as client:
            html = _index(client)
        assert "bearing fault identification" in html

    def test_report_template_names_no_rca(self):
        template = (Path(__file__).resolve().parents[1] / "src" / "vib_agent" / "report"
                    / "templates" / "default_survey.md.j2").read_text()
        rendered_text = [ln for ln in template.splitlines() if not ln.strip().startswith("{#")]
        body = "\n".join(rendered_text)
        assert "RCA" not in body
        assert "root cause" not in body.lower()

    def test_the_drafting_model_is_never_taught_the_phrase(self):
        """The deterministic report was already clean; the drafted report is only
        as clean as what the model is HANDED. Assert the whole drafting input --
        system prompt + user prompt (which carries the AnalysisResult JSON, the
        machine context and the reference report) -- names no RCA either."""
        from vib_agent.agent.loop import _build_user_prompt
        from vib_agent.agent.system_prompt import SYSTEM_PROMPT
        from vib_agent.report.generate import render_markdown

        form = UploadForm(machine_alias="Demo Pump", rpm=1800.0, iso_group="2",
                          iso_support="rigid", machine_type="motor", bearing_model="6206")
        case, _, _ = parse_upload(_EXAMPLE_CSV, form, bearings_cfg=load_config("bearings"))
        result = run_analysis(case, iso_table=load_config("iso_zones")["zones"],
                              thresholds=load_thresholds("route"),
                              rules=load_config("next_measurements"))
        drafting_input = SYSTEM_PROMPT + "\n" + _build_user_prompt(
            result, case.machine, render_markdown(result, case.machine)
        )
        assert "RCA" not in drafting_input
        assert "root cause" not in drafting_input.lower()
        assert "root-cause" not in drafting_input.lower()
        # the capability is still named, just in the reader's vocabulary
        assert "bearing fault identification" in SYSTEM_PROMPT

    def test_drafted_report_on_the_fake_client_path_says_neither(self, tmp_path):
        """End of the drafted path: run agent/loop.py against the fake client (no
        network) and read the report.md it actually writes — narrative plus every
        string this repo appends around it."""
        from tests.fake_anthropic import FakeAnthropicClient, build_consistent_echo, draft_message
        from vib_agent.agent.consistency import TITLE_TEMPLATE
        from vib_agent.agent.loop import run_agent_analysis

        form = UploadForm(machine_alias="Demo Pump", rpm=1800.0, iso_group="2",
                          iso_support="rigid", machine_type="motor", bearing_model="6206")
        case, _, _ = parse_upload(_EXAMPLE_CSV, form, bearings_cfg=load_config("bearings"))
        iso_table = load_config("iso_zones")["zones"]
        thresholds = load_thresholds("route")
        rules = load_config("next_measurements")
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        narrative = (
            f"{TITLE_TEMPLATE.format(machine_name='Demo Pump')}\n\n"
            "## Executive Summary\n\nThe committed diagnosis is a bearing outer-race fault "
            "(BPFO), at high confidence.\n\n"
            "DRAFT -- prepared by automated analysis, pending analyst review."
        )
        fake = FakeAnthropicClient(responses=[draft_message(narrative, build_consistent_echo(result))])
        run_agent_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules,
                           out_dir=tmp_path, pdf=False, client=fake)
        drafted = (tmp_path / "report.md").read_text()
        assert "RCA" not in drafted
        assert "root cause" not in drafted.lower() and "root-cause" not in drafted.lower()

    def test_rendered_report_never_says_rca_or_root_cause(self):
        """The strings a reader actually gets: run a real analysis and read the
        markdown, rather than trusting the template alone (labels, reasons and
        recommendations all come from elsewhere)."""
        from vib_agent.report.generate import render_markdown

        form = UploadForm(machine_alias="Demo Pump", rpm=1800.0, iso_group="2",
                          iso_support="rigid", machine_type="motor", bearing_model="6206")
        case, _, _ = parse_upload(_EXAMPLE_CSV, form, bearings_cfg=load_config("bearings"))
        result = run_analysis(case, iso_table=load_config("iso_zones")["zones"],
                              thresholds=load_thresholds("route"),
                              rules=load_config("next_measurements"))
        md = render_markdown(result, case.machine)
        assert "RCA" not in md
        assert "root cause" not in md.lower() and "root-cause" not in md.lower()


# ══════════════════════════════════════════════════════════════════════════
# 6 · Drop-zone copy
# ══════════════════════════════════════════════════════════════════════════
class TestDropZoneCopy:
    def test_waveform_acceptance_is_loud(self):
        with TestClient(_app()) as client:
            html = _index(client)
        assert "WAV, UFF, or MAT: we do the FFT ourselves." in html
        assert "You do not have to export a spectrum first." in html

    def test_export_hints_for_the_three_collectors(self):
        with TestClient(_app()) as client:
            html = _index(client)
        hints = html.partition('<ul class="export-hints">')[2].partition("</ul>")[0]
        assert hints, "export hints missing"
        for tool in ("AMS Machinery Manager", "@ptitude Analyst", "System 1"):
            assert tool in hints
        assert hints.count("<li>") == 3  # one line each

    def test_email_lane_is_an_invitation_not_an_apology(self):
        with TestClient(_app(contact_email="ops@example.test")) as client:
            html = _index(client)
        lane = html.partition('<div class="lane">')[2].partition("</div>")[0]
        assert "Send it anyway" in lane
        assert "mailto:ops@example.test" in lane
        for apology in ("Sorry", "sorry", "Unfortunately", "unsupported", "can’t", "cannot"):
            assert apology not in lane, f"the funnel lane should not apologise ({apology!r})"


# ══════════════════════════════════════════════════════════════════════════
# 5 · Analyze another file for this machine
# ══════════════════════════════════════════════════════════════════════════
class TestAnalyzeAnother:
    def test_ready_card_offers_the_next_run(self):
        js = _app_js()
        ready = js.partition("function readyCard(")[2].partition("\nfunction ")[0]
        assert "Analyze another file for this machine" in ready
        assert 'id="again"' in ready

    def test_it_clears_only_the_file_slots(self):
        js = _app_js()
        clear = js.partition("function clearFileSlots()")[2].partition("\n}")[0]
        # files and the loaded example go
        assert "fileInput.value = ''" in clear and "demoFile = null" in clear
        # machine values do not
        for kept in ("machine_alias", "rpm", "bearing_model", "form.reset"):
            assert kept not in clear, f"{kept} must survive 'analyze another file'"

    def test_previous_report_link_is_not_torn_down(self):
        """The finished card stays on the page, so the previous report is still
        downloadable for the rest of its TTL.

        Scoped to the "again" branch on purpose: Session G added a "Start over"
        button to the confirm card, which DOES hide the card — but that job was
        never analysed, so there is no report to preserve. The two actions differ
        exactly there."""
        js = _app_js()
        again = js.partition("if (!e.target || e.target.id !== 'again') return;")[2]
        again = again.partition("});")[0]
        assert again, "the 'analyze another file' branch is missing"
        assert "stateCard.hidden = true" not in again
        assert "scrollToEl(formCard)" in again


# ══════════════════════════════════════════════════════════════════════════
# 4 · Machine memory (browser-side only)
# ══════════════════════════════════════════════════════════════════════════
class TestMachineMemory:
    def test_ui_offers_remember_saved_list_and_delete(self):
        with TestClient(_app()) as client:
            html = _index(client)
        assert 'id="remember"' in html and "Remember this machine in this browser" in html
        assert 'id="saved-row"' in html and 'id="saved"' in html      # saved-machine dropdown
        # Session UX-5 (C11). The control is still here and still the way to
        # remove a saved machine -- it is named `Delete…` now, and it routes to
        # the machine's own typed-confirmation panel instead of splicing the
        # card out on the spot (which left the readings behind).
        assert 'id="delete-saved"' in html and ">Delete…<" in html

    def test_remember_checkbox_is_never_posted(self):
        """It drives localStorage only — it must not become a form field the
        server sees (no name attribute)."""
        with TestClient(_app()) as client:
            html = _index(client)
        assert 'name="remember"' not in html and 'name="remember_machine"' not in html

    def test_storage_is_localstorage_only_and_holds_no_credential(self):
        js = _app_js()
        assert "localStorage.setItem(STORE_KEY" in js
        # Session GEOM-A took the card to v2 (geometry fields). v1 stays named
        # in the file as the key that is READ and migrated forward, so an
        # analyst's saved machines survive the change.
        assert "'vib.machines.v2'" in js
        assert "'vib.machines.v1'" in js
        # machine card fields only — the invite code and the trace consent are absent
        fields = js.partition("const MEMORY_FIELDS = [")[2].partition("];")[0]
        assert "machine_alias" in fields and "rpm" in fields and "bearing_model" in fields
        for banned in ("invite_code", "code", "retain_trace", "file"):
            assert f"'{banned}'" not in fields, f"{banned} must never be stored in the browser"

    def test_no_cookie_and_no_server_side_machine_state(self):
        """Nothing about a remembered machine reaches the server: no Set-Cookie,
        no state on the app object, no new endpoint."""
        app = _app()
        with TestClient(app) as client:
            r = client.get("/")
        assert "set-cookie" not in {k.lower() for k in r.headers}
        assert not hasattr(app.state.vib, "machines")
        routes = {getattr(route, "path", "") for route in app.routes}
        assert not any("machine" in path for path in routes)


# ══════════════════════════════════════════════════════════════════════════
# 3 · Defaults + progressive disclosure
# ══════════════════════════════════════════════════════════════════════════
class TestDefaultsAndDisclosure:
    def test_every_default_is_preselected(self):
        with TestClient(_app()) as client:
            html = _index(client)
        for selected in ('<option value="2" selected>',        # ISO group 2
                         '<option value="rigid" selected>',    # rigid support
                         '<option value="mm_s" selected>',     # mm/s
                         '<option value="rms" selected>'):     # RMS
            assert selected in html, f"missing preselected default: {selected}"

    def test_direction_preselects_radial_horizontal_without_declaring_it(self):
        """The first slot shows radial–horizontal as the selected option, but its
        value stays EMPTY: a single file with an unstated direction is the
        identity path whose report is byte-identical to the pre-multi-axis one
        (tests/test_multiaxis.py::TestByteCompatDefaultedSingle). Declaring it
        would add per-channel lines to every single-file report."""
        with TestClient(_app()) as client:
            html = _index(client)
        # Session UX-5 (C8). Radial-horizontal was the EMPTY option, so choosing
        # it posted nothing, the server read the absence as its default and the
        # report said "(direction assumed)" for a direction the analyst had
        # explicitly picked. The empty option is still the default and still
        # declares the same axis -- it now says what it is, and the direction
        # can also be STATED.
        assert '<option value="" selected>Not given — read as radial – horizontal</option>' in html
        assert '<option value="radial_h">Radial – horizontal</option>' in html
        # and the JS states it explicitly the moment a second channel joins
        assert "if (nChannels > 1 && !fd.get('direction')) fd.set('direction', 'radial_h');" in _app_js()

    def test_first_run_requires_only_file_rpm_and_code(self):
        with TestClient(_app()) as client:
            html = _index(client)
        required = [line for line in html.splitlines() if " required" in line]
        blob = "\n".join(required)
        assert 'name="file"' in blob and 'name="rpm"' in blob and 'name="invite_code"' in blob
        for not_required in ('name="machine_alias"', 'name="bearing_model"', 'name="iso_support"'):
            assert not_required not in blob, f"{not_required} must not be required for a first run"
        # a blank alias still yields a named report rather than a 422
        assert "fd.set('machine_alias', 'Unnamed machine')" in _app_js()

    def test_advanced_fields_are_collapsed_but_still_posted(self):
        with TestClient(_app()) as client:
            html = _index(client)
        head, _, more = html.partition('<details class="more"')
        assert more, "the More options disclosure is missing"
        more = more.partition("</details>")[0]
        for collapsed in ("bearing_model", "iso_support", "detection_type"):
            # A SPACE before `name=`, so this matches the real attribute and not
            # the tail of another one. Session INTAKE-2 added a per-location
            # `<template>` whose controls carry `data-name="bearing_model"` --
            # inert markup, cloned and prefixed to `loc2_bearing_model` before it
            # can post anything -- and the old substring check read that as
            # "bearing_model is still in the main form". The property being
            # protected is unchanged; it is now tested for an attribute instead
            # of for six characters.
            assert f' name="{collapsed}"' in more, f"{collapsed} should be under More options"
            assert f' name="{collapsed}"' not in head, f"{collapsed} is still in the main form"
        # collapsed, not removed: the fields are real inputs inside the form
        assert "<summary>More options" in html

    def test_three_axis_upload_is_visible_from_the_measurements_section(self):
        """The form must not read as "one axis only" (operator feedback on the
        first pass). U2 satisfies that outright: all three channel slots live in
        MEASUREMENTS and `+ Add a channel` reveals slots 2 and 3 in place.

        Revealing rather than pre-rendering is deliberate and load-bearing: a
        browser posts an EMPTY FILE PART for a revealed-but-unfilled input,
        which FastAPI would see as a zero-byte file (the Session E lesson)."""
        with TestClient(_app()) as client:
            html = _index(client)
        head = html.partition('<details class="more"')[0]
        assert 'id="add-channels"' in head
        assert "+ Add a channel" in head
        assert "separate misalignment from imbalance" in head
        for field in ("file_2", "direction_2", "file_3", "direction_3"):
            assert f'name="{field}"' in head, f"{field} should be in MEASUREMENTS now"
        js = _app_js()
        handler = js.partition("addChannelsBtn.addEventListener('click'")[2].partition("});")[0]
        assert "channelsRevealed = true" in handler and "scrollToEl(extraChannels)" in handler
        # the slots start hidden, and clearing an invisible slot stays in place
        # so a hidden input can never carry a file into the POST
        assert '<div class="extra-channels" hidden>' in html
        sync = js.partition("function syncMode()")[2].partition("\n}")[0]
        # HIST-2-FIX-2 moved this from `extraChannels.hidden = !visible` to
        # `setHidden(...)`. The PROPERTY is unchanged and is asserted at its new
        # address: the panel's visibility tracks `visible`. What moved is HOW,
        # and it had to: the `hidden` attribute works only through the UA
        # stylesheet's [hidden]{display:none}, which any author rule declaring
        # `display` outranks -- which is why compare mode left the third slot
        # (.grid) and "+ Add a channel" (.btn-link) on screen in Chrome. The
        # second assertion pins the fix itself, because dropping the inline
        # display would silently reopen the bug in a browser while every
        # stub-DOM test kept passing.
        assert "setHidden(extraChannels, !visible)" in sync
        hide = js.partition("function setHidden(")[2].partition("\n}")[0]
        assert "el.hidden = hide" in hide
        assert "el.style.display = hide ? 'none' : ''" in hide
        # HIST-2 moved the clearing itself into `clearSlot`, because compare mode
        # clears ONE slot (the third) while the other modes clear both. The
        # property is unchanged and is asserted at its new address: a slot that
        # is not visible has its input emptied, so a hidden input can never carry
        # a file into the POST.
        assert "clearSlot(" in sync
        clear = js.partition("function clearSlot(")[2].partition("\n}")[0]
        assert "inp.value = ''" in clear

    # ── U5 (UX-WIRE): client-side refusals ──────────────────────────────
    def test_a_cleared_slot_deletes_both_of_its_parts(self):
        """The Session E lesson, written into a test rather than left to be
        rediscovered. A browser posts an EMPTY FILE PART for a cleared input,
        and FastAPI would see a zero-byte file. Both `file_n` AND `direction_n`
        must go — deleting only the file leaves a direction with nothing to
        apply to, which is what makes the server's slot-count and its
        direction-count disagree.

        Clearing empties the input; the submit path is what drops the parts,
        and that is deliberate: it is the ONE place the rule holds for every
        route into an empty slot — Remove, switching schema, or never touching
        the slot at all."""
        js = _app_js()
        submit = js.partition("EXTRA_SLOTS.forEach(([fid, did]) => {")[2].partition("});")[0]
        assert "fd.delete(fid); fd.delete(did);" in submit, (
            "an empty slot must delete BOTH parts")
        clear = js.partition("if (clear) clear.addEventListener('click'")[2].partition(");")[0]
        assert "inp.value = ''" in clear

    def test_the_duplicate_direction_check_is_a_strict_subset_of_the_server(self):
        """It refuses locally what the server refuses at app.py:216-224 — and
        nothing more. A client check BROADER than the server's rejects valid
        uploads and the analyst has no way to appeal it."""
        js = _app_js()
        # only on the lane where the server's duplicate loop runs at all:
        # a single file, and every non-spectrum mode, never reach it
        condition = ("if (nChannels > 1 && String(fd.get('mode') || 'spectrum') "
                     "=== 'spectrum') {")
        assert condition in js, "the guard fires outside the server's own lane"
        guard = js.partition(condition)[2].partition("\n  }")[0]
        # same three field names the server reads
        assert "'direction', 'direction_2', 'direction_3'" in guard
        # only an exact repeat fires it — a missing or unknown value is the
        # server's to judge, and it has better words for both
        assert "chosen.indexOf(d) !== i" in guard
        for broader in ("!d", "=== ''", "length < 3"):
            assert broader not in guard, f"the client refuses more than the server: {broader}"

    def test_each_extra_slot_is_its_own_drop_target(self):
        """Live, only slot 1 accepted a drop, so a three-channel upload meant
        three trips through a file picker. The hover state must promise
        "release to choose", never "this will work" — whether the file can be
        read is decided by a magic-byte sniff over bytes that only arrive on
        the drop."""
        js = _app_js()
        for event in ("dragover", "dragenter", "dragleave", "drop"):
            assert f"zone.addEventListener('{event}'" in js, f"no {event} handler"
        assert "dt.items.add(dropped[0])" in js
        assert ".over{" in (_STATIC / "style.css").read_text()

    def test_visible_form_keeps_the_three_first_run_fields(self):
        with TestClient(_app()) as client:
            html = _index(client)
        head = html.partition('<details class="more"')[0]
        for visible in ("file", "rpm", "iso_group", "velocity_unit", "direction"):
            assert f'name="{visible}"' in head, f"{visible} should stay visible"


# ══════════════════════════════════════════════════════════════════════════
# 2 · Code in link
# ══════════════════════════════════════════════════════════════════════════
class TestCodeInLink:
    def test_app_js_prefills_the_invite_field_from_the_query(self):
        js = _app_js()
        assert "prefillCodeFromLink" in js
        assert "URLSearchParams(location.search).get('code')" in js

    def test_code_is_never_persisted_anywhere(self):
        """The link only saves typing. The code must not be written to storage,
        and the app must not carry a second place it could leak from."""
        js = _app_js()
        for banned in ("localStorage.setItem('code", "invite_code'", "setItem('vib.code"):
            assert banned not in js, f"invite code persisted via {banned!r}"
        assert "invite_code" not in js.replace("// ", "")  # only the form field name, in HTML

    def test_server_takes_no_part_in_the_prefill(self):
        """?code= is handled entirely in the browser: the index HTML is byte-identical
        with and without the query, so the code never reaches a server-side render."""
        with TestClient(_app()) as client:
            plain = client.get("/").text
            with_code = client.get("/?code=demo-code").text
        assert plain == with_code


# ══════════════════════════════════════════════════════════════════════════
# 1 · Demo path
# ══════════════════════════════════════════════════════════════════════════
class TestDemoPath:
    def test_hero_carries_both_demo_buttons(self):
        with TestClient(_app()) as client:
            html = _index(client)
        assert 'id="run-example"' in html
        assert "Run the example analysis" in html
        assert 'href="/sample-report.pdf"' in html and "See a sample report" in html

    def test_example_file_is_served_as_csv(self):
        with TestClient(_app()) as client:
            r = client.get("/example-spectrum.csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert r.text.splitlines()[0] == "freq_hz,amplitude"
        assert len(r.text.splitlines()) > 100  # a real spectrum, not a stub

    def test_app_js_posts_the_bundled_example_through_the_normal_form(self):
        js = _app_js()
        assert "'/example-spectrum.csv'" in js
        assert EXAMPLE_FILENAME in js
        for key, value in EXAMPLE_FIELDS.items():
            assert f"{key}: '{value}'" in js, f"demo prefill drifted for {key}"
        # the demo submits the ordinary form — no second endpoint
        assert "/api/jobs/demo" not in js and "demo=1" not in js

    def test_bundled_example_commits_the_bearing_fault_it_advertises(self):
        """The demo must be honest: the file we ship really does produce the
        outer-race call the hero copy promises, through the real pipeline on the
        real (route) profile."""
        form = UploadForm(machine_alias="Demo Pump", rpm=1800.0, iso_group="2",
                          iso_support="rigid", machine_type="motor", bearing_model="6206")
        case, kind, _ = parse_upload(_EXAMPLE_CSV, form, bearings_cfg=load_config("bearings"))
        result = run_analysis(case, iso_table=load_config("iso_zones")["zones"],
                              thresholds=load_thresholds("route"),
                              rules=load_config("next_measurements"))
        assert kind == "tabular_spectrum"
        assert result.quality_gate.overall != "fail"
        assert result.iso is not None and result.iso.iso_zone == "C"
        committed = {f.fault: f.confidence for f in result.findings}
        assert committed == {"bearing_outer_race": "high"}

    def test_keyless_demo_reaches_a_degraded_report_end_to_end(self):
        """No ANTHROPIC_API_KEY -> the client cannot even be constructed. The job
        must still finish, as a degraded deterministic report with a downloadable
        PDF — never a hard error and never stuck in `running`."""
        factory = _NoApiKeyFactory()
        app = _app(factory=factory)
        with TestClient(app) as client:
            with open(_EXAMPLE_CSV, "rb") as fh:
                r = client.post(
                    "/api/jobs",
                    files={"file": (EXAMPLE_FILENAME, fh, "text/csv")},
                    data={"invite_code": "demo-code", **EXAMPLE_FIELDS},
                )
            assert r.status_code == 202
            job_id = r.json()["job_id"]
            data = _poll_until_terminal(client, job_id)
            assert data["state"] == "degraded", data
            assert data["degraded_reason"] == "draft_failure"
            # the deterministic result still carries the real committed finding
            labels = [f["label"] for f in data["result_summary"]["faults"]]
            assert labels == ["Bearing outer-race fault (BPFO)"]
            assert data["result_summary"]["severity"] == "ISO Zone C"
            pdf = client.get(f"/api/jobs/{job_id}/report.pdf")
            assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
        assert factory.calls == 1  # the failure was the client build, not a call

    def test_keyless_gate_fail_still_renders_insufficient_data(self, tmp_path):
        """The other keyless branch: a gate-fail case never needed the model, so
        it must reach `gate_fail` (not `degraded`) with its report intact."""
        off = tmp_path / "off.csv"
        off.write_text("freq_hz,amplitude\n" + "".join(f"{i * 0.5},0.00001\n" for i in range(400)))
        app = _app(factory=_NoApiKeyFactory())
        with TestClient(app) as client:
            with open(off, "rb") as fh:
                r = client.post("/api/jobs", files={"file": ("off.csv", fh, "text/csv")},
                                data={"invite_code": "demo-code", **EXAMPLE_FIELDS})
            job_id = r.json()["job_id"]
            data = _poll_until_terminal(client, job_id)
            assert data["state"] == "gate_fail", data
            assert client.get(f"/api/jobs/{job_id}/report.pdf").status_code == 200
