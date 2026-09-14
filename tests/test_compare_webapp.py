"""Session HIST-2 — `mode=compare` end to end through the webapp.

Driven through FastAPI's TestClient with a fake Anthropic client, like
tests/test_webapp_e2e.py: zero real API calls anywhere here. Every job in this
file is built so the drafting client cannot be constructed, which is the
ordinary keyless-host case and makes each job degrade to the DETERMINISTIC
report — the document whose comparison section is the thing under test.

What a TestClient cannot exercise is the browser's own form behaviour (empty
file parts for revealed-but-unfilled inputs); that is tests/js/
compare_mode_tests.js, run from here so `pytest` stays the one command.

Session HIST-2-FIX: every document assertion below runs against BOTH renderers
— the v2 HTML page and the markdown document — because a compare job produces
both and which one an analyst is handed depends on the HOST, not on the code.
See the `documents` fixture for the defect that taught this.
"""

from __future__ import annotations

import csv
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.agent.consistency import TITLE_TEMPLATE
from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.report.generate import COMPARISON_HEADING
from vib_agent.webapp.app import create_app
from vib_agent.webapp.jobs import ERROR_TAXONOMY
from tests.fake_anthropic import FakeAnthropicClient, build_consistent_echo, draft_message
from tests.test_webapp_e2e import _poll_until_terminal, _webapp_cfg

_ROOT = Path(__file__).resolve().parents[1]
_CODES = {"demo-code": "engineer-1"}
#: 1800 rpm -> 30 Hz shaft; 6206 BPFO at 107.03 Hz.
_ONE_X = 30.0
_BPFO = 107.03


def _spectrum_csv(path: Path, *, peaks: dict[float, float], n: int = 400,
                  fmax: float = 200.0, floor: float = 0.001) -> None:
    """A spectrum CSV in the documented template schema. `peaks` maps a target
    frequency to its amplitude; everything else is the broadband floor."""
    freqs = [i * fmax / n for i in range(n)]
    amps = [floor] * n
    for target, amplitude in peaks.items():
        idx = min(range(n), key=lambda i: abs(freqs[i] - target))
        amps[idx] = amplitude
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["freq_hz", "amplitude"])
        for frequency, amplitude in zip(freqs, amps):
            writer.writerow([frequency, amplitude])


def _keyless_app(**cfg_overrides):
    """An app whose drafting client cannot be constructed — the ordinary
    keyless-host case. Every job degrades to the deterministic report, which is
    the document these tests read."""
    def _no_client():
        raise RuntimeError("no ANTHROPIC_API_KEY in this test")

    return create_app(
        webapp_cfg=_webapp_cfg(**cfg_overrides),
        invite_codes=dict(_CODES),
        anthropic_client_factory=_no_client,
    )


def _post_compare(client: TestClient, before: Path, after: Path, **form_overrides):
    form = {
        "invite_code": "demo-code",
        "machine_alias": "TestPump",
        "rpm": "1800",
        "iso_group": "2",
        "iso_support": "rigid", "machine_type": "motor",
        "bearing_model": "6206",
        "mode": "compare",
    }
    form.update(form_overrides)
    with open(before, "rb") as first, open(after, "rb") as second:
        return client.post(
            "/api/jobs",
            files={"file": (before.name, first, "text/csv"),
                   "file_2": (after.name, second, "text/csv")},
            data=form,
        )


class _CapturedDocuments:
    """The two documents a job produces, kept apart. See the `documents` fixture."""

    def __init__(self) -> None:
        self.html: list[str] = []
        self.markdown: list[str] = []

    def __bool__(self) -> bool:  # "was anything rendered at all?"
        return bool(self.html or self.markdown)


def _drafting_app(after: Path, **cfg_overrides):
    """An app whose drafting pass SUCCEEDS, stubbed — the other lane.

    The fake client returns one draft whose echo is consistent with the AFTER
    reading's own analysis (the reading a compare job's report is about), so the
    job reaches `done` and the report comes out of `drafted.html.j2` and the
    drafted markdown rather than the deterministic pair. Zero real API calls and
    no key is consulted anywhere: the client is handed in, exactly as
    `_keyless_app` hands in a factory that raises."""
    form = UploadForm(machine_alias="TestPump", rpm=1800.0, iso_group="2",
                      iso_support="rigid", machine_type="motor", bearing_model="6206")
    cfg = _webapp_cfg(**cfg_overrides)
    case, _, _ = parse_upload(after, form, bearings_cfg=load_config("bearings"))
    # The profile the webapp analyses uploads with, read the way app.py reads it
    # — never `load_thresholds()` bare, which resolves a default rather than a
    # decision (CLAUDE.md, threshold-profile doctrine).
    result = run_analysis(
        case,
        iso_table=load_config("iso_zones")["zones"],
        thresholds=load_thresholds(cfg["analysis_profile"]),
        rules=load_config("next_measurements"),
    )
    narrative = (
        f"{TITLE_TEMPLATE.format(machine_name='TestPump')}\n\nBody.\n\n"
        "DRAFT -- prepared by automated analysis, pending analyst review."
    )
    client = FakeAnthropicClient(
        responses=[draft_message(narrative, build_consistent_echo(result))]
    )
    app = create_app(webapp_cfg=cfg, invite_codes=dict(_CODES),
                     anthropic_client_factory=lambda: client)
    return app, client


@pytest.fixture
def documents(monkeypatch):
    """BOTH documents every job in the test produces, captured and kept apart.

    `report.md` cannot be read after the fact — `_keep_only_report` deletes every
    intermediate the moment report.pdf exists, which is the privacy promise
    working — and a tectonic PDF's text is compressed. So each document is spied
    where it is handed on:

      * the **v2 HTML page** (`survey.html.j2` / `drafted.html.j2`) as
        `_pdf_from_html` receives it. That call happens on EVERY host: on one
        whose weasyprint cannot reach its native libs it returns None after the
        import dies, having already been handed the page.
      * the **markdown document** (`default_survey.md.j2`, plus the drafted
        splice) as `render_pdf`/`render_drafted_pdf` receive it in
        `markdown_text=` — the document the pandoc+tectonic fallback issues, and
        the one `_keep_only_report` keeps when no PDF could be written.

    **Session HIST-2-FIX — why it is spied this way.** This fixture used to
    capture whichever string reached a PDF ENGINE and call that host-independent.
    It is the exact opposite. On a host where weasyprint works — which is what
    production is — `_pdf_from_markdown` is never reached, so the capture held
    only the HTML page while every assertion in this file was spelled in
    markdown (`## Before / after comparison`). The five tests that read a
    document failed on the operator's host and passed on this one, and the
    difference was read as the presence of ANTHROPIC_API_KEY. It was the PDF
    engine: with the key stripped and `_pdf_from_html` forced to succeed, the
    same five fail; with the key present and weasyprint unavailable, all
    nineteen pass. Capturing both documents takes the host out of the assertion,
    and `_documents()` fails loudly if either one goes missing.
    """
    from vib_agent.report import generate
    from vib_agent.webapp import worker

    captured = _CapturedDocuments()

    original_pdf_from_html = generate._pdf_from_html

    def _spy_html(html, out_dir, *args, **kwargs):
        captured.html.append(html)
        return original_pdf_from_html(html, out_dir, *args, **kwargs)

    monkeypatch.setattr(generate, "_pdf_from_html", _spy_html)

    # The worker's own names, not generate's: it imported these directly.
    for name in ("render_pdf", "render_drafted_pdf"):
        original = getattr(worker, name)

        def _spy_markdown(*args, _original=original, **kwargs):
            text = kwargs.get("markdown_text")
            if text is not None:
                captured.markdown.append(text)
            return _original(*args, **kwargs)

        monkeypatch.setattr(worker, name, _spy_markdown)

    return captured


#: The comparison heading in each renderer's own spelling, both derived from the
#: ONE constant the report layer publishes — so renaming the section there moves
#: every assertion here with it, in both documents.
_COMPARISON_TITLE = COMPARISON_HEADING.removeprefix("## ")
_HTML_COMPARISON_HEADING = re.compile(r"<h2>[^<]*" + re.escape(_COMPARISON_TITLE) + r"</h2>")


def _documents(captured: _CapturedDocuments) -> dict[str, str]:
    """Both documents, keyed by renderer.

    Asserting that BOTH were captured is itself a pin: a run that produces only
    one of them is the HIST-2-FIX defect returning, and every assertion made
    through this helper would silently become one-sided again."""
    assert captured.html, "no HTML page was rendered"
    assert captured.markdown, "no markdown document was rendered"
    return {
        "html page": "\n".join(captured.html),
        "markdown document": "\n".join(captured.markdown),
    }


def _comparison_sections(captured: _CapturedDocuments) -> dict[str, str]:
    """The comparison section ALONE, per renderer, asserting it is present in
    each. Scoping an assertion to the section is what makes "improved does not
    appear" mean what it says — the word is ordinary elsewhere in a report."""
    sections: dict[str, str] = {}
    for renderer, text in _documents(captured).items():
        if renderer == "markdown document":
            assert COMPARISON_HEADING in text, f"no comparison section in the {renderer}"
            sections[renderer] = text.split(COMPARISON_HEADING, 1)[1].split("\n## ", 1)[0]
        else:
            match = _HTML_COMPARISON_HEADING.search(text)
            assert match is not None, f"no comparison section in the {renderer}"
            sections[renderer] = text[match.end():].split("</section>", 1)[0]
    return sections


# ─────────────────────────────────────────────────────────────────────────
# The ratified taxonomy entry
# ─────────────────────────────────────────────────────────────────────────


class TestTaxonomy:
    def test_not_comparable_is_the_only_new_code(self):
        """The operator ratified exactly one code for this session. Extending the
        taxonomy is a decision, and this pins the size of the one that was made."""
        assert set(ERROR_TAXONOMY) == {
            "upload_unreadable", "interpretation_failed", "inference_unavailable",
            "merge_failed", "internal_error", "timeout", "upload_write_failed",
            "not_comparable",
        }

    def test_not_comparable_is_a_bad_upload_and_not_retryable(self):
        row = ERROR_TAXONOMY["not_comparable"]
        assert row.kind == "bad_upload"
        assert row.retryable is False
        assert "before/after" in row.meaning

    def test_the_declared_vocabulary_and_the_table_agree(self):
        """`ErrorCode` is the declared vocabulary and ERROR_TAXONOMY is what
        validates a code at runtime; they must not drift."""
        import typing

        from vib_agent.webapp import jobs

        declared = set(typing.get_args(jobs.ErrorCode))
        assert declared == set(jobs.ERROR_TAXONOMY)

    def test_the_wire_still_carries_only_kind_and_retryable(self, tmp_path):
        """Wire law: `error_code` is the operator's grep handle and never leaves
        the server. A new code must not become a new field."""
        before = tmp_path / "before.csv"
        after = tmp_path / "after.csv"
        _spectrum_csv(before, peaks={107.03: 0.5})
        _spectrum_csv(after, peaks={107.03: 0.5})
        with TestClient(_keyless_app()) as client:
            response = _post_compare(client, before, after, rpm="1800")
            job_id = response.json()["job_id"]
            _poll_until_terminal(client, job_id)
            payload = client.get(f"/api/jobs/{job_id}").json()
        assert "error_code" not in payload
        assert "not_comparable" not in str(payload)


# ─────────────────────────────────────────────────────────────────────────
# The form boundary
# ─────────────────────────────────────────────────────────────────────────


class TestFormBoundary:
    def test_one_file_in_compare_mode_is_a_400(self, tmp_path):
        only = tmp_path / "only.csv"
        _spectrum_csv(only, peaks={107.03: 0.5})
        with TestClient(_keyless_app()) as client:
            with open(only, "rb") as handle:
                response = client.post(
                    "/api/jobs",
                    files={"file": (only.name, handle, "text/csv")},
                    data={"invite_code": "demo-code", "machine_alias": "M", "rpm": "1800",
                          "iso_group": "2", "iso_support": "rigid", "machine_type": "motor", "mode": "compare"},
                )
        assert response.status_code == 400
        assert "exactly two files" in response.json()["detail"]

    def test_three_files_in_compare_mode_is_a_400(self, tmp_path):
        paths = []
        for name in ("a.csv", "b.csv", "c.csv"):
            path = tmp_path / name
            _spectrum_csv(path, peaks={107.03: 0.5})
            paths.append(path)
        with TestClient(_keyless_app()) as client:
            handles = [open(p, "rb") for p in paths]
            try:
                response = client.post(
                    "/api/jobs",
                    files={
                        "file": (paths[0].name, handles[0], "text/csv"),
                        "file_2": (paths[1].name, handles[1], "text/csv"),
                        "file_3": (paths[2].name, handles[2], "text/csv"),
                    },
                    data={"invite_code": "demo-code", "machine_alias": "M", "rpm": "1800",
                          "iso_group": "2", "iso_support": "rigid", "machine_type": "motor", "mode": "compare"},
                )
            finally:
                for handle in handles:
                    handle.close()
        assert response.status_code == 400
        assert "exactly two files" in response.json()["detail"]

    def test_a_rejected_pair_is_free(self, tmp_path):
        """The daily allowance is spent on ACCEPTED jobs. A mode/file-count
        mix-up costs the analyst nothing, because it cost us nothing."""
        only = tmp_path / "only.csv"
        _spectrum_csv(only, peaks={107.03: 0.5})
        app = _keyless_app(per_code_daily_jobs=1)
        with TestClient(app) as client:
            for _ in range(3):
                with open(only, "rb") as handle:
                    response = client.post(
                        "/api/jobs",
                        files={"file": (only.name, handle, "text/csv")},
                        data={"invite_code": "demo-code", "machine_alias": "M", "rpm": "1800",
                              "iso_group": "2", "iso_support": "rigid", "machine_type": "motor", "mode": "compare"},
                    )
                assert response.status_code == 400

    def test_compare_never_reaches_the_adapters_as_a_schema(self, tmp_path):
        """`mode` names a CSV/XLSX LAYOUT to the adapters, and `compare` is not
        one. If it ever reached them, a spectrum file would be read as a trend
        table (or rejected outright), so this pins the translation."""
        before = tmp_path / "before.csv"
        after = tmp_path / "after.csv"
        _spectrum_csv(before, peaks={107.03: 0.5})
        _spectrum_csv(after, peaks={107.03: 0.5})
        with TestClient(_keyless_app()) as client:
            job_id = _post_compare(client, before, after).json()["job_id"]
            data = _poll_until_terminal(client, job_id)
            job = client.app.state.vib.registry.get(job_id)
        assert data["state"] in ("done", "degraded"), data
        assert job.kind == "compare(tabular_spectrum,tabular_spectrum)"


# ─────────────────────────────────────────────────────────────────────────
# The happy path — a planted repair, read out of the real report
# ─────────────────────────────────────────────────────────────────────────


class TestComparedReport:
    def test_a_planted_repair_reaches_the_report(self, tmp_path, documents):
        """The Part C shape, in a test: a machine with a committed BPFO fault,
        then the same point with the tone gone. The report must SAY so, in the
        honest register, and must never say the machine was repaired."""
        before = tmp_path / "before.csv"
        after = tmp_path / "after.csv"
        # Both files keep a 1x line, so BOTH machines read as running and the
        # gate passes on each: the only thing that changed is the bearing tone.
        _spectrum_csv(before, peaks={_ONE_X: 0.5, _BPFO: 0.5})
        _spectrum_csv(after, peaks={_ONE_X: 0.5})
        with TestClient(_keyless_app()) as client:
            job_id = _post_compare(client, before, after).json()["job_id"]
            data = _poll_until_terminal(client, job_id)
            assert data["state"] == "degraded", data   # keyless host: no drafting

        _comparison_sections(documents)  # the heading, in BOTH documents
        for renderer, report in _documents(documents).items():
            assert "consistent with repair" in report, renderer
            assert not re.search(r"\b(was|is|been)\s+repaired\b", report, re.I), renderer
            assert "Band levels" in report, renderer
            assert "Repair verification" in report, renderer
            # The provenance line: which file was which, in the analyst's own order.
            assert "Before is the first file uploaded" in report, renderer

    def test_a_worsening_pair_says_worsened(self, tmp_path, documents):
        before = tmp_path / "before.csv"
        after = tmp_path / "after.csv"
        _spectrum_csv(before, peaks={_ONE_X: 0.5, _BPFO: 0.05})
        _spectrum_csv(after, peaks={_ONE_X: 0.5, _BPFO: 0.9})
        with TestClient(_keyless_app()) as client:
            job_id = _post_compare(client, before, after).json()["job_id"]
            _poll_until_terminal(client, job_id)
        for renderer, section in _comparison_sections(documents).items():
            assert "worsened" in section, renderer
            assert "improved" not in section, renderer

    def test_an_identical_pair_claims_nothing(self, tmp_path, documents):
        before = tmp_path / "before.csv"
        after = tmp_path / "after.csv"
        _spectrum_csv(before, peaks={_ONE_X: 0.5, _BPFO: 0.5})
        _spectrum_csv(after, peaks={_ONE_X: 0.5, _BPFO: 0.5})
        with TestClient(_keyless_app()) as client:
            job_id = _post_compare(client, before, after).json()["job_id"]
            _poll_until_terminal(client, job_id)
        for renderer, section in _comparison_sections(documents).items():
            assert "no significant change" in section, renderer
            assert "consistent with repair" not in section, renderer

    def test_the_significance_rule_is_printed(self, tmp_path, documents):
        before = tmp_path / "before.csv"
        after = tmp_path / "after.csv"
        _spectrum_csv(before, peaks={_ONE_X: 0.5, _BPFO: 0.5})
        _spectrum_csv(after, peaks={_ONE_X: 0.5, _BPFO: 0.5})
        with TestClient(_keyless_app()) as client:
            job_id = _post_compare(client, before, after).json()["job_id"]
            _poll_until_terminal(client, job_id)
        for renderer, report in _documents(documents).items():
            assert "significant at a ratio of 1.25" in report, renderer


# ─────────────────────────────────────────────────────────────────────────
# The refusals, as an analyst meets them
# ─────────────────────────────────────────────────────────────────────────


class TestRefusalsOverTheWire:
    def test_a_file_the_template_cannot_read_takes_the_ordinary_inference_lane(
        self, tmp_path
    ):
        """Compare does not change INTAKE. A .csv the template cannot read goes
        to schema inference exactly as it would uploaded alone, and the job
        pauses on the confirm card — it does not become a comparison failure
        before anyone has established there is anything wrong with the pair."""
        before = tmp_path / "before.csv"
        after = tmp_path / "after.csv"
        _spectrum_csv(before, peaks={_ONE_X: 0.5, _BPFO: 0.5})
        after.write_text("this,is,not,a,spectrum\n1,2,3,4\n")
        with TestClient(_keyless_app()) as client:
            job_id = _post_compare(client, before, after).json()["job_id"]
            data = _poll_until_terminal(client, job_id)
        assert data["state"] == "awaiting_confirm", data

    def test_only_one_readable_file_is_an_upload_failure_not_a_pairing_one(
        self, tmp_path, documents
    ):
        """`not_comparable` means "both read fine, still not a pair". When one
        side could not be read, NOTHING was compared — calling it not-comparable
        would send the analyst to fix the wrong thing."""
        before = tmp_path / "before.csv"
        after = tmp_path / "after.csv"
        _spectrum_csv(before, peaks={_ONE_X: 0.5, _BPFO: 0.5})
        after.write_text("this,is,not,a,spectrum\n1,2,3,4\n")
        with TestClient(_keyless_app()) as client:
            job_id = _post_compare(client, before, after).json()["job_id"]
            assert _poll_until_terminal(client, job_id)["state"] == "awaiting_confirm"
            # The analyst confirms what could be interpreted; the unreadable side
            # is still unreadable, so the pair never forms.
            confirm = client.post(f"/api/jobs/{job_id}/confirm", json={})
            assert confirm.status_code == 202, confirm.text
            data = _poll_until_terminal(client, job_id)
        assert data["state"] == "error", data
        assert data["failure_kind"] == "bad_upload"
        assert data["retryable"] is False
        assert "needs both files" in data["safe_message"]
        # The other arm of the property below: a refusal issues NO document at
        # all, in either renderer — there is no half-report to mistake for one.
        assert not documents

    def test_a_gate_failure_is_a_report_and_never_an_error(self, tmp_path, documents):
        """The roadmap's parenthetical put a gate fail under `not_comparable`.
        It is not: CLAUDE.md makes the insufficient-data report the ONLY valid
        downstream output of a gate fail, and that report names what to
        re-capture — strictly more useful than an error card. The comparison
        section says which reading failed and why."""
        before = tmp_path / "before.csv"
        after = tmp_path / "after.csv"
        _spectrum_csv(before, peaks={_ONE_X: 0.5, _BPFO: 0.5})
        # An all-but-silent spectrum: the machine reads as not running, which is
        # a gate FAIL, on a file that parsed perfectly.
        _spectrum_csv(after, peaks={}, floor=0.0000001)
        with TestClient(_keyless_app()) as client:
            job_id = _post_compare(client, before, after).json()["job_id"]
            data = _poll_until_terminal(client, job_id)
        assert data["state"] == "gate_fail", data
        for renderer, section in _comparison_sections(documents).items():
            assert "data-quality gate" in section, renderer
            assert "What would resolve this" in section, renderer
        for renderer, report in _documents(documents).items():
            assert "Band levels" not in report, renderer


    def test_both_readings_failing_the_gate_still_produces_the_report(
        self, tmp_path, documents
    ):
        """The other half of §S5's gate cases. Two unusable captures are two
        re-captures to ask for — and the insufficient-data report is the only
        thing that can ask."""
        before = tmp_path / "before.csv"
        after = tmp_path / "after.csv"
        _spectrum_csv(before, peaks={}, floor=0.0000001)
        _spectrum_csv(after, peaks={}, floor=0.0000001)
        with TestClient(_keyless_app()) as client:
            job_id = _post_compare(client, before, after).json()["job_id"]
            data = _poll_until_terminal(client, job_id)
        assert data["state"] == "gate_fail", data
        for renderer, section in _comparison_sections(documents).items():
            assert "Before reading" in section, renderer
            assert "After reading" in section, renderer
            assert "What would resolve this" in section, renderer


# ─────────────────────────────────────────────────────────────────────────
# Both renderers, both lanes — the Session HIST-2-FIX pins
# ─────────────────────────────────────────────────────────────────────────


#: Every pair shape this file knows how to build, as (before kwargs, after kwargs)
#: for `_spectrum_csv`. Used by the property below, which is why the entries are
#: named for the ANALYST's situation rather than the code path they exercise.
_RUNNING = {"peaks": {_ONE_X: 0.5, _BPFO: 0.5}}
_SILENT = {"peaks": {}, "floor": 0.0000001}
_COMPARE_PAIRS: dict[str, tuple[dict, dict]] = {
    "a planted repair": (_RUNNING, {"peaks": {_ONE_X: 0.5}}),
    "a worsening pair": ({"peaks": {_ONE_X: 0.5, _BPFO: 0.05}},
                         {"peaks": {_ONE_X: 0.5, _BPFO: 0.9}}),
    "an identical pair": (_RUNNING, _RUNNING),
    "the after reading fails its gate": (_RUNNING, _SILENT),
    "the before reading fails its gate": (_SILENT, _RUNNING),
    "both readings fail their gate": (_SILENT, _SILENT),
    # Same line count over ten times the span: the bins are 10x wider, so no
    # spectrum can be paired and the comparison is overall-level only. The
    # section still has to be there and still has to say so.
    "a pair whose spectra cannot be paired": (_RUNNING, {**_RUNNING, "fmax": 2000.0}),
}


class TestBothRenderersCarryTheComparison:
    """Session HIST-2-FIX. A compare job produces TWO documents — the v2 HTML
    page and the markdown one — and which of them an analyst is handed is a
    property of the host (whether weasyprint's native libs are reachable), not
    of the code. Both must carry the comparison, on every lane, or the feature
    is present only on whichever host the suite happened to be gated on.
    """

    def test_the_degraded_lane_carries_it_in_both_renderers(self, tmp_path, documents):
        before = tmp_path / "before.csv"
        after = tmp_path / "after.csv"
        _spectrum_csv(before, peaks={_ONE_X: 0.5, _BPFO: 0.5})
        _spectrum_csv(after, peaks={_ONE_X: 0.5})
        with TestClient(_keyless_app()) as client:
            job_id = _post_compare(client, before, after).json()["job_id"]
            data = _poll_until_terminal(client, job_id)
        assert data["state"] == "degraded", data
        sections = _comparison_sections(documents)
        assert set(sections) == {"html page", "markdown document"}
        for renderer, section in sections.items():
            assert "Repair verification" in section, renderer

    def test_the_drafted_lane_carries_it_in_both_renderers(self, tmp_path, documents):
        """The lane the degraded tests never reach. `drafted.html.j2` and the
        drafted markdown are a different template pair from the deterministic
        one, and the comparison is spliced into each by a different mechanism —
        a macro call on the page, `splice_comparison_markdown` on the text."""
        before = tmp_path / "before.csv"
        after = tmp_path / "after.csv"
        _spectrum_csv(before, peaks={_ONE_X: 0.5, _BPFO: 0.5})
        _spectrum_csv(after, peaks={_ONE_X: 0.5})
        app, _ = _drafting_app(after)
        with TestClient(app) as client:
            job_id = _post_compare(client, before, after).json()["job_id"]
            data = _poll_until_terminal(client, job_id)
        assert data["state"] == "done", data
        sections = _comparison_sections(documents)
        assert set(sections) == {"html page", "markdown document"}
        for renderer, section in sections.items():
            assert "Repair verification" in section, renderer
            assert "consistent with repair" in section, renderer

    def test_the_drafting_model_is_never_shown_the_comparison(self, tmp_path):
        """The load-bearing half of "the LLM never computes a delta": the
        reference report the model is handed omits the comparison entirely, so
        a narrated delta that disagrees with the arithmetic cannot be written in
        the first place. Pinned against what the client was actually sent."""
        before = tmp_path / "before.csv"
        after = tmp_path / "after.csv"
        _spectrum_csv(before, peaks={_ONE_X: 0.5, _BPFO: 0.5})
        _spectrum_csv(after, peaks={_ONE_X: 0.5})
        app, fake = _drafting_app(after)
        with TestClient(app) as client:
            job_id = _post_compare(client, before, after).json()["job_id"]
            assert _poll_until_terminal(client, job_id)["state"] == "done"
        assert fake.messages.calls, "the drafting pass never ran"
        sent = str(fake.messages.calls)
        assert _COMPARISON_TITLE not in sent
        assert "consistent with repair" not in sent

    def test_a_gate_blocked_pair_on_the_degraded_lane_still_carries_the_block(
        self, tmp_path, documents
    ):
        """The BEFORE reading fails its own gate; the AFTER reading passes. The
        job is about the After reading, so it is an ordinary degraded report —
        NOT a gate_fail — and the comparison comes back `gate_blocked`. That
        makes the section the only thing in the document that says why no delta
        was computed, on the one lane where nothing else in the report is
        insufficient-data shaped."""
        before = tmp_path / "before.csv"
        after = tmp_path / "after.csv"
        _spectrum_csv(before, peaks={}, floor=0.0000001)
        _spectrum_csv(after, peaks={_ONE_X: 0.5, _BPFO: 0.5})
        with TestClient(_keyless_app()) as client:
            job_id = _post_compare(client, before, after).json()["job_id"]
            data = _poll_until_terminal(client, job_id)
        assert data["state"] == "degraded", data
        for renderer, section in _comparison_sections(documents).items():
            assert "data-quality gate" in section, renderer
            assert "What would resolve this" in section, renderer
            assert "Band levels" not in section, renderer

    @pytest.mark.parametrize("pair", sorted(_COMPARE_PAIRS))
    def test_a_compare_job_reports_a_comparison_or_refuses(self, tmp_path, documents, pair):
        """The property, over every pair shape this file knows how to build: a
        compare job either REFUSES outright (an error card, no report at all) or
        issues a report whose comparison section is in BOTH documents. There is
        no third outcome — and in particular no report that reads like an
        ordinary single-file one, which is what a silently-dropped section
        produces and what nothing else here would catch."""
        before_kwargs, after_kwargs = _COMPARE_PAIRS[pair]
        before = tmp_path / "before.csv"
        after = tmp_path / "after.csv"
        _spectrum_csv(before, **before_kwargs)
        _spectrum_csv(after, **after_kwargs)
        with TestClient(_keyless_app()) as client:
            job_id = _post_compare(client, before, after).json()["job_id"]
            data = _poll_until_terminal(client, job_id)

        if data["state"] == "error":
            # A refusal names what is wrong and issues no document at all.
            assert data["failure_kind"] == "bad_upload", data
            assert not documents, "a refused pair must not produce a report"
            return
        assert data["state"] in ("done", "degraded", "gate_fail"), data
        assert set(_comparison_sections(documents)) == {"html page", "markdown document"}


# ─────────────────────────────────────────────────────────────────────────
# Nothing is stored between requests
# ─────────────────────────────────────────────────────────────────────────


class TestZeroPersistence:
    def test_neither_upload_survives_the_job(self, tmp_path):
        """"There is no database" stays literally true with a before/after
        feature in the product: both files arrive in ONE request, both are
        unlinked at parse time, and completion leaves only the report."""
        before = tmp_path / "before.csv"
        after = tmp_path / "after.csv"
        _spectrum_csv(before, peaks={107.03: 0.5})
        _spectrum_csv(after, peaks={})
        with TestClient(_keyless_app()) as client:
            job_id = _post_compare(client, before, after).json()["job_id"]
            _poll_until_terminal(client, job_id)
            job = client.app.state.vib.registry.get(job_id)
            left = sorted(p.name for p in job.job_dir.iterdir())
        assert not [name for name in left if name.startswith("upload")], left
        assert set(left) <= {"report.pdf", "report.md"}, left

    def test_the_history_package_stays_inert(self):
        """HIST-1's SQLite layer is still imported by nothing outside tests, and
        HIST-2 is the session most likely to have reached for it."""
        import subprocess as sp
        hits = sp.run(
            ["grep", "-rn", "--include=*.py", "vib_agent.history", "src/vib_agent/"],
            capture_output=True, text=True, cwd=_ROOT,
        )
        assert hits.stdout == "", hits.stdout


# ─────────────────────────────────────────────────────────────────────────
# The browser half — run in node against the real app.js
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_compare_mode_slot_behaviour_in_a_real_js_runtime():
    """Runs tests/js/compare_mode_tests.js: the second slot revealed, directions
    hidden, the third slot cleared, and the copy that says which side is which.

    A TestClient `files={...}` post cannot reproduce any of it — a browser posts
    an empty file part for a revealed-but-unfilled input, which is the Session E
    lesson this mode re-opens."""
    result = subprocess.run(
        [shutil.which("node"), str(_ROOT / "tests" / "js" / "compare_mode_tests.js")],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
    passed, total = map(int, re.search(r"\n(\d+)/(\d+) passed", result.stdout).groups())
    assert passed == total and total >= 7, result.stdout
