"""Session WORKER-CASE — a job's two documents come from one context.

`_render_degraded` and `_render_fail_closed` rendered `report.md` with no
`case=` and no `thresholds=`, then `_finalize_markdown_and_pdf` rendered the
PDF *with* both. Two documents, two contexts, and the markdown was the poorer
one — it is also the agent's reference document and what a drafted narrative is
written from.

Measured on `before.csv` with no bearing model, the two kwarg sets the worker
actually used:

    no case  ->  report.md 8544 B, no 107 Hz, 5 "not recorded" rows,
                 charts = frequency_map.svg + badge
    with     ->  report.md 8954 B, names 107 Hz, 0 "not recorded",
                 charts = spectrum_y.svg + badge

The charts half is the sharp end: `render_report` hands its manifest back, the
worker forwards it as `charts=`, and `render_pdf` redraws only when that is
None. So the case-less manifest was what the PDF rendered with, and the PDF an
analyst downloaded on a spend-capped or draft-failed job showed the no-spectrum
fallback figure — captioned "The spectrum array was not retained with this
analysis", which was false.

`report.md` cannot be read after a job (the purge deletes it, which is the
privacy promise working), so both documents are captured where they are handed
on — the `tests/test_compare_webapp.py::documents` idiom.
"""

from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path

import pytest

from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.models import Check
from vib_agent.pipeline import run_analysis
from vib_agent.report.generate import render_report
from vib_agent.report import charts as charts_mod
from vib_agent.webapp import worker as worker_mod
from vib_agent.webapp.jobs import Job
from vib_agent.webapp.spend import SpendGuard
from vib_agent.webapp.worker import process_job

RPM = 1800.0
SHAFT_HZ = RPM / 60.0          # 30 Hz
UNCLAIMED_HZ = 107.0           # the loudest line; BPFO for a bearing nobody named
MARKER = "Unexplained dominant peak"

#: `outputs/hist2_sample/before.csv`, re-minted — that directory is untracked.
_BEFORE = {SHAFT_HZ: 0.9, UNCLAIMED_HZ: 1.4, 2 * UNCLAIMED_HZ: 0.6}


def _spectrum_csv(path: Path, peaks: dict[float, float], *, n: int = 800,
                  fmax: float = 400.0, floor: float = 0.001) -> Path:
    freqs = [i * fmax / n for i in range(n)]
    amp = [floor] * n
    for hz, a in peaks.items():
        amp[min(range(n), key=lambda i: abs(freqs[i] - hz))] = a
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["freq_hz", "amplitude"])
        for f, a in zip(freqs, amp):
            w.writerow([f, a])
    return path


def _trend_csv(path: Path, *, n: int = 30) -> Path:
    """A reading with NO spectra at all — the narrowest case the fix touches."""
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 1, 31, tzinfo=timezone.utc)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "overall_rms"])
        for i in range(n):
            w.writerow([(now - timedelta(days=n - i)).isoformat(), 2.0 + 1.5 * i / (n - 1)])
    return path


@pytest.fixture(scope="module")
def bearings():
    from vib_agent.config import load_config

    return load_config("bearings")


def _case(path: Path, bearings, *, bearing_model=None, mode="spectrum"):
    form = dict(machine_alias="WC", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor", mode=mode)
    if bearing_model:
        form["bearing_model"] = bearing_model
    case, _kind, _note = parse_upload(path, UploadForm(**form), bearings_cfg=bearings)
    return case


# ── capturing both documents ─────────────────────────────────────────────


class _Captured:
    """What each renderer was handed. `markdown` is the job's report.md at the
    moment it is passed on; `html` is what the PDF is rendered from."""

    def __init__(self) -> None:
        self.markdown: list[str] = []
        self.html: list[str] = []
        self.report_kwargs: list[dict] = []
        self.pdf_kwargs: list[dict] = []


@pytest.fixture
def documents(monkeypatch) -> _Captured:
    from vib_agent.report import generate

    cap = _Captured()

    original_from_html = generate._pdf_from_html

    def _spy_html(html, out_dir, *args, **kwargs):
        cap.html.append(html)
        return original_from_html(html, out_dir, *args, **kwargs)

    monkeypatch.setattr(generate, "_pdf_from_html", _spy_html)

    # The worker's OWN names: worker.py imports these directly, so patching
    # generate.render_pdf would be a no-op (test_compare_webapp.py:188).
    original_report = worker_mod.render_report

    def _spy_report(*args, **kwargs):
        cap.report_kwargs.append(kwargs)
        return original_report(*args, **kwargs)

    monkeypatch.setattr(worker_mod, "render_report", _spy_report)

    for name in ("render_pdf", "render_drafted_pdf"):
        original = getattr(worker_mod, name)

        def _spy_pdf(*args, _original=original, **kwargs):
            cap.pdf_kwargs.append(kwargs)
            text = kwargs.get("markdown_text")
            if text is not None:
                cap.markdown.append(text)
            return _original(*args, **kwargs)

        monkeypatch.setattr(worker_mod, name, _spy_pdf)

    return cap


def _run_job(job_dir, case, iso_table, thresholds, rules, **over) -> Job:
    """`drafting_available=False` lands on `_render_degraded` for any case whose
    gate passes — the tests/test_s12_failed_analysis.py idiom, no app, no HTTP."""
    job = Job(id="wc", code_label="engineer-1", job_dir=job_dir)
    kwargs = dict(
        iso_table=iso_table, thresholds=thresholds, rules=rules,
        spend_guard=SpendGuard(daily_token_budget=10**9),
        conversion_note="", client=None, drafting_available=False,
    )
    kwargs.update(over)
    process_job(job, case, **kwargs)
    return job


# ── the defect ───────────────────────────────────────────────────────────


class TestBothDocumentsCarryTheCaseDependentSection:
    def test_degraded_lane_names_the_unclaimed_line_in_both(
        self, tmp_path, bearings, documents, iso_table, thresholds, rules
    ):
        case = _case(_spectrum_csv(tmp_path / "u.csv", _BEFORE), bearings)
        job = _run_job(tmp_path / "job", case, iso_table, thresholds, rules)
        assert job.state == "degraded", job.state
        assert documents.markdown and documents.html, "neither document was captured"
        assert MARKER in documents.markdown[0], "report.md lost the unexplained-peak line"
        assert "107.0" in documents.markdown[0]
        assert MARKER in documents.html[0], "the PDF's page lost the unexplained-peak line"
        assert "107.0" in documents.html[0]

    def test_the_pdf_embeds_the_measured_spectrum_not_the_fallback_figure(
        self, tmp_path, bearings, documents, iso_table, thresholds, rules
    ):
        """The charts half. `render_pdf` reuses the manifest `render_report`
        produced, so a case-less markdown render also decided what the PDF drew
        — the fault-frequency-map fallback, whose caption says the spectrum
        array was not retained. It was."""
        case = _case(_spectrum_csv(tmp_path / "u.csv", _BEFORE), bearings)
        _run_job(tmp_path / "job", case, iso_table, thresholds, rules)
        html = documents.html[0]
        assert "spectrum_y.svg" in html, "the PDF still draws the no-spectrum fallback"
        assert "frequency_map.svg" not in html
        assert "spectrum array was not retained" not in html

    def test_geometry_claims_the_line_and_both_documents_stay_silent(
        self, tmp_path, bearings, documents, iso_table, thresholds, rules
    ):
        case = _case(_spectrum_csv(tmp_path / "u.csv", _BEFORE), bearings, bearing_model="6205")
        _run_job(tmp_path / "job", case, iso_table, thresholds, rules)
        for doc, label in ((documents.markdown[0], "markdown"), (documents.html[0], "html")):
            assert MARKER not in doc, f"{label}: fired with geometry supplied"

    def test_the_fail_closed_lane_has_the_same_parity(
        self, tmp_path, bearings, documents, iso_table, thresholds, rules
    ):
        """`_render_fail_closed` is the other divergent call site. Driving it
        directly with a forced check skips the multi-axis assembly entirely —
        nothing in the suite reached this function before."""
        case = _case(_spectrum_csv(tmp_path / "u.csv", _BEFORE), bearings)
        job = _run_job(
            tmp_path / "job", case, iso_table, thresholds, rules,
            forced_fail_checks=[Check(name="cross_channel_speed_agreement", status="fail",
                                      reason="channels disagreed on running speed")],
        )
        assert job.state == "gate_fail", job.state
        report_kw = documents.report_kwargs[0]
        pdf_kw = documents.pdf_kwargs[0]
        assert report_kw["case"] is pdf_kw["case"]
        assert report_kw["thresholds"] is pdf_kw["thresholds"]


# ── the divergence cannot come back ──────────────────────────────────────


class TestOneContextForBothDocuments:
    SHARED = ("case", "thresholds", "comparison")

    def test_both_renders_receive_the_same_objects(
        self, tmp_path, bearings, documents, iso_table, thresholds, rules
    ):
        case = _case(_spectrum_csv(tmp_path / "u.csv", _BEFORE), bearings)
        _run_job(tmp_path / "job", case, iso_table, thresholds, rules)
        report_kw = documents.report_kwargs[0]
        pdf_kw = documents.pdf_kwargs[0]
        for key in self.SHARED:
            assert key in report_kw, f"the markdown render lost {key!r}"
            assert key in pdf_kw, f"the PDF render lost {key!r}"
            assert report_kw[key] is pdf_kw[key], f"{key!r} differs between the two documents"

    def test_every_render_report_call_site_splats_the_shared_bundle(self):
        """Source-level, the tests/test_terminal_guarantee.py idiom. The runtime
        test above proves the lanes that RUN are equal; this proves the shape
        that keeps a third lane equal when someone adds one."""
        source = Path(worker_mod.__file__).read_text()
        calls = re.findall(r"(?<!def )render_report\((.*?)\)\n", source, re.S)
        assert calls, "render_report call sites vanished from worker.py"
        for args in calls:
            assert "**document_ctx" in args, (
                f"a render_report call site builds its own context: {args.strip()!r}"
            )

    def test_the_pdf_bundle_is_built_from_the_same_helper(self):
        body = (Path(worker_mod.__file__).read_text()
                .partition("def _finalize_markdown_and_pdf(")[2].partition("\ndef ")[0])
        assert body, "_finalize_markdown_and_pdf is gone"
        assert "_document_context(" in body, (
            "the PDF bundle no longer starts from the shared helper, so the two "
            "documents can drift again"
        )


# ── the delta this introduces, enumerated ────────────────────────────────


class TestTheDeltaIsExactlyWhatWasBeingWithheld:
    """Byte-identity is not achievable and the measurement says why: passing
    `thresholds` necessarily adds two Analysis-parameters rows. On the narrowest
    case the fix touches — a reading with NO spectra — that is the WHOLE delta,
    and two of the three figures are byte-identical."""

    def _renders(self, tmp_path, bearings, iso_table, thresholds, rules):
        case = _case(_trend_csv(tmp_path / "t.csv"), bearings, mode="trend")
        assert case.spectra is None, "fixture is meant to carry no spectra"
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        before = tmp_path / "before"
        after = tmp_path / "after"
        # exactly what the worker used to pass, and what it passes now
        render_report(result, case.machine, before, pdf=False, comparison=None)
        render_report(result, case.machine, after, pdf=False, comparison=None,
                      case=case, thresholds=thresholds)
        return before, after

    def test_the_markdown_gains_exactly_two_rows(
        self, tmp_path, bearings, iso_table, thresholds, rules
    ):
        before, after = self._renders(tmp_path, bearings, iso_table, thresholds, rules)
        old = (before / "report.md").read_text().splitlines()
        new = (after / "report.md").read_text().splitlines()
        assert [line for line in new if line not in old] == [
            "| Match tolerance | ±3% |",
            "| Amplitude floor | 12.73× spectrum mean |",
        ]
        assert [line for line in old if line not in new] == []

    def test_the_figures_that_do_not_depend_on_the_case_are_byte_identical(
        self, tmp_path, bearings, iso_table, thresholds, rules
    ):
        before, after = self._renders(tmp_path, bearings, iso_table, thresholds, rules)

        def _hashes(d):
            return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in charts_mod.chart_files(d)}

        old, new = _hashes(before), _hashes(after)
        assert old.keys() == new.keys()
        for name in ("status_badge.svg", "frequency_map.svg"):
            assert old[name] == new[name], f"{name} moved and had no reason to"
        # trend.svg is the one that legitimately changes: with the Case it plots
        # the 30 measured readings instead of the two-point aggregate fallback.
        assert old["trend.svg"] != new["trend.svg"]
