"""Job processing (Phase 6): pipeline -> draft/degrade -> PDF.

Every step after the sandboxed parse (webapp/parsing.py) is deterministic
Python already proven by the CLI path (pipeline.run_analysis,
agent.loop.run_agent_analysis, report.generate) — this module's job is
orchestration, footer post-processing (the units-conversion note + the
privacy footer line), and turning any failure into the right job state
instead of an unhandled crash. `process_job` never raises.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
import traceback
from pathlib import Path
from typing import Any, Sequence

import anthropic

from vib_agent.agent.loop import AgentAnalysisError, run_agent_analysis
from vib_agent.models import (
    AnalysisResult,
    Case,
    Check,
    ComparisonResult,
    QualityGateResult,
    RecommendedMeasurement,
)
from vib_agent.pdm_core.compare import compare_readings
from vib_agent.pipeline import run_analysis
from vib_agent.report.generate import (
    FAULT_LABELS,
    _UNRATED_SEVERITY_DISPLAY,
    render_drafted_pdf,
    render_pdf,
    render_report,
    splice_comparison_markdown,
)
from vib_agent.webapp.jobs import ERROR_TAXONOMY, TERMINAL_STATES, Job
from vib_agent.webapp.spend import SpendGuard

PROCESSED_AND_DELETED_NOTE = "Processed and deleted — no raw data retained."
DEGRADED_NOTE = "Drafted narrative unavailable — deterministic report issued."

# Session R3-DIFF (item 3). The degrade path is CORRECT — a job must never hard
# fail because the drafting pass did — but until now it was also SILENT: both
# handlers below caught and discarded the exception, so the only trace a
# degraded job left was the note in the PDF. Production showed the narrative
# unavailable on 3 of 3 cause-bearing reports and 0 of 2 without, and there was
# no way to tell from the logs whether that was an API error, a spend guard, or
# a consistency hard-fail — three different bugs with one symptom.
#
# Same logger as the per-job outcome line, so it lands in the same place with
# the same handler. The job id and the traceback are all it carries: the id is
# already in the outcome line, and a traceback is our own stack frames. Nothing
# derived from the upload, the machine alias, the invite code or the client
# reaches it — deliberately, and asserted in tests/test_draft_failure_logging.py.
_log = logging.getLogger("vib_agent.webapp")


def _log_draft_failure(job: Job, exc: BaseException) -> None:
    """Record WHY a job degraded. Never raises: a logging failure must not turn
    a degraded job into a crashed one."""
    try:
        _log.warning(
            "job=%s draft_failed exc=%s.%s\n%s",
            job.id,
            type(exc).__module__,
            type(exc).__qualname__,
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip(),
        )
    except Exception:  # noqa: BLE001 -- logging is never the reason a job fails
        pass

_SENTINEL_FAULT = "no_significant_findings"


def _result_summary(result: AnalysisResult) -> dict[str, Any]:
    """Plain-language snapshot of a finished analysis for the READY state card.
    Uses the report's own fault labels and severity display, so the card matches
    the PDF. Never exposes raw model fields -- labelled strings only."""
    committed = [f for f in result.findings if f.fault != _SENTINEL_FAULT]
    if result.iso is not None and result.iso.iso_zone == "not_assessable":
        severity = _UNRATED_SEVERITY_DISPLAY
    elif result.iso is not None:
        severity = f"ISO Zone {result.iso.iso_zone}"
    else:
        severity = None
    return {
        "no_findings": not committed,
        "faults": [
            {"label": FAULT_LABELS.get(f.fault, f.fault.replace("_", " ").capitalize()),
             "confidence": f.confidence}
            for f in committed
        ],
        "severity": severity,
    }


def _committed_fault(result: AnalysisResult) -> str | None:
    """The diagnosis this analysis committed to, as the model's own fault id.

    Session JOB-DB. `_result_summary` above deliberately renders labels -- its
    contract is "labelled strings only, never raw model fields" -- and the
    account schema's `jobs.committed` wants the opposite: the id a later query
    can group by. Taking it from `result_summary` would mean storing whatever
    `FAULT_LABELS` happened to say in the release that ran, which is a display
    decision leaking into a record.

    None when nothing was committed. The sentinel finding IS the clean bill, so
    it is skipped rather than stored: "we looked and found nothing" and "we
    committed to `no_significant_findings`" are the same fact, and NULL is how
    this schema already says it (`db/models.py`).
    """
    for finding in result.findings:
        if finding.fault != _SENTINEL_FAULT:
            return finding.fault
    return None


def _trend_point(result: AnalysisResult) -> dict[str, Any] | None:
    """The one measurement the browser's trend card stores, machine-readable.

    Session HIST-1, operator ruling D-5: the scalar is `severity_rms` in mm/s
    exactly as the reading reports it — the axis-max value the ISO zone was
    classified from — stamped with that zone at capture. Nothing is rounded or
    relabelled here: the card posts these numbers back as `Case.history` and
    the trend regression runs on them, so a display rounding would become a
    measurement error one upload later.

    None (and therefore absent from the wire, and no save offer in the browser)
    whenever there is no trendable scalar:

      * `severity_rms is None` — the reading carried no velocity at all. Every
        acceleration-only lane lands here (CWRU, MFPT, an unscaled WAV), and a
        trend of "no value" is not a trend.
      * `iso_zone == "not_assessable"` — a velocity exists but was withheld,
        which today means implausible units (pipeline.py). A point with no zone
        cannot badge, and a reading we refused to classify is the last one that
        should anchor a baseline.

    `captured_at` is the analysis timestamp, which is the only clock this path
    has: no upload lane collects a measurement time. The report and the privacy
    page say "when it was analysed" rather than implying otherwise.
    """
    iso = result.iso
    if iso is None or iso.severity_rms is None or iso.iso_zone == "not_assessable":
        return None
    return {
        "severity_rms_mms": iso.severity_rms,
        "iso_zone": iso.iso_zone,
        "dominant_axis": iso.dominant_axis,
        "captured_at": result.ts,
    }


def _gate_summary(result: AnalysisResult) -> dict[str, Any]:
    """Plain-language reasons a reading was stopped before diagnosis, plus what
    to collect to resolve it -- both already human-readable in the AnalysisResult
    (Check.reason, RecommendedMeasurement.technique). For the STOPPED card."""
    reasons = [
        c.reason for c in result.quality_gate.checks
        if c.status == "fail" and c.reason
    ]
    if not reasons:
        reasons = [
            c.reason for c in result.quality_gate.checks
            if c.status == "warn" and c.reason
        ]
    return {
        "reasons": reasons,
        "collect": [m.technique for m in result.recommended_measurements],
    }

_MACHINE_DETAILS_RE = re.compile(r"(## Machine Details\n\n(?:\|.*\n)+)", re.MULTILINE)


def _insert_note_after_machine_details(markdown_text: str, note: str) -> str:
    """Insert a plain-English note (units conversion, degraded mode, ...)
    right after the Machine Details table when that heading exists;
    otherwise just before the trailing DRAFT footer so it's never silently
    dropped."""
    if not note:
        return markdown_text
    match = _MACHINE_DETAILS_RE.search(markdown_text)
    if match:
        insert_at = match.end()
        return markdown_text[:insert_at] + f"\n_{note}_\n" + markdown_text[insert_at:]
    idx = markdown_text.rfind("\nDRAFT")
    if idx == -1:
        return markdown_text.rstrip() + f"\n\n_{note}_\n"
    return markdown_text[:idx] + f"\n_{note}_\n" + markdown_text[idx:]


_SEVERITY_COVERAGE_RE = re.compile(r"(## Severity & Coverage\n)")


def _insert_note_after_severity_coverage(markdown_text: str, note: str) -> str:
    """Insert the multi-axis next-tier coverage line into the Severity & Coverage
    section when it exists (assessable reports have no such section), else fall
    back to the Machine Details hook so the line is never dropped."""
    if not note:
        return markdown_text
    match = _SEVERITY_COVERAGE_RE.search(markdown_text)
    if match:
        insert_at = match.end()
        return markdown_text[:insert_at] + f"\n_{note}_\n" + markdown_text[insert_at:]
    return _insert_note_after_machine_details(markdown_text, note)


def _append_privacy_footer(markdown_text: str) -> str:
    return markdown_text.rstrip() + f"\n\n{PROCESSED_AND_DELETED_NOTE}\n"


def _sum_trace_usage(trace_path: Path) -> dict[str, int]:
    totals = {"input_tokens": 0, "output_tokens": 0}
    if not trace_path.exists():
        return totals
    for line in trace_path.read_text().splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        usage = entry.get("usage")
        if isinstance(usage, dict):
            totals["input_tokens"] += int(usage.get("input_tokens") or 0)
            totals["output_tokens"] += int(usage.get("output_tokens") or 0)
    return totals


def markdown_with_notes(
    text: str, notes: Sequence[str], *, coverage_note: str | None = None
) -> str:
    """`report.md` with the webapp's own notes inserted and the privacy footer
    appended — the placement it has always had.

    Split out of `_finalize_markdown_and_pdf` in Session V2-WIRE so the note
    ORDERING can be tested without a job, a result and a PDF renderer. The
    ordering is the subtle part: each note is inserted at the same anchor, so
    the caller passes them reversed to get them out top-to-bottom.
    """
    for note in notes:
        text = _insert_note_after_machine_details(text, note)
    if coverage_note:
        text = _insert_note_after_severity_coverage(text, coverage_note)
    return _append_privacy_footer(text)


def _render_roster(
    job: Job, case: Case, result: Any = None, charts: Any = None
) -> list[dict[str, Any]] | None:
    """The roster the renderer takes: EVERY measured point, in route order.

    Session REPORTFIX-1. `job.location_docs` is the contract's `locations[]` —
    which is "the OTHER points" (contract §5 rule 4: location 1 is the
    document's own subject and is not repeated in the list). The renderer's
    roster is the whole machine, so location 1's own entry is prepended here.
    Without that the document's own subject would be missing from its own page
    1 and from Evidence, and `len(locations) + 1` would silently become
    `len(locations)`.

    Location 1's label is `case.machine.location` — the string the analyst typed
    into `measurement_location`, which is the same field
    `locations.location_form_dict` substitutes per point, so all N labels come
    from one source and read as one route sheet.

    None for a single-location job, which is every job that measured one point:
    `report/generate.py::location_roster` renders nothing below a roster of two,
    so the single-location document stays byte-identical.
    """
    if not job.location_docs:
        return None
    first: dict[str, Any] = {"label": case.machine.location or "Location 1"}
    # Optional enrichment, and only what this caller actually holds — the
    # markdown pass runs before charts exist, and a roster entry is allowed to
    # carry less rather than carry a placeholder.
    if result is not None:
        first["result"] = result
        first["case"] = case
    if charts is not None:
        first["charts"] = charts
    return [first, *job.location_docs]


def _document_context(
    job: Job, case: Case, thresholds: dict[str, Any] | None,
    comparison: ComparisonResult | None, *, result: Any = None, charts: Any = None,
) -> dict[str, Any]:
    """The kwargs that decide what a document SAYS — built once per job and
    handed to BOTH renders.

    Session WORKER-CASE. `_render_degraded` and `_render_fail_closed` rendered
    `report.md` without `case` or `thresholds` and then rendered the PDF with
    them, so a job's two documents were built from two different contexts and
    the markdown was the poorer one. It is also the agent's reference document.
    Measured on the HIST-2 sample: the case-less markdown lost the
    unexplained-dominant-peak line and five Analysis-parameters rows, and —
    because `render_report` hands its chart manifest to `render_pdf`, which
    redraws only when it gets None — the PDF drew the no-spectrum FALLBACK
    figure, whose caption says the spectrum array was not retained. It was.

    Deliberately NOT in here, and each for its own reason:

      * `notes` / `deletion_footer` — measured inert for markdown. They are
        HTML-side context (`render_report` forwards them only to `render_pdf`);
        the markdown gets its notes from this module's text insertion, which is
        the placement V2-WIRE froze.
      * `charts` — asymmetric by construction: the markdown render PRODUCES the
        manifest the PDF render CONSUMES.
      * `markdown_text` — PDF-only, the no-weasyprint fallback payload.
      * `profile` — `process_job` never receives one, and neither does the
        drafted lane, so it is None on every webapp document today. Consistent,
        not divergent; threading it would reach outside this module.
    """
    return {"case": case, "thresholds": thresholds, "comparison": comparison,
            # Session REPORTFIX-1 -- in here, not beside it, for the reason this
            # whole bundle exists: a multi-location job's two documents must be
            # built from ONE context or they come to say different things about
            # which points were measured.
            "locations": _render_roster(job, case, result=result, charts=charts),
            "iso_assumed": job.iso_assumed or None}


def _finalize_markdown_and_pdf(
    job: Job, notes: list[str], *, coverage_note: str | None = None,
    result: AnalysisResult, case: Case,
    thresholds: dict[str, Any] | None = None, charts: Any = None,
    narrative: str | None = None, comparison: ComparisonResult | None = None,
    # Session REPORTFIX-1. Accepted so the two callers that splat one
    # `document_ctx` into both the markdown render and this one still bind, and
    # then deliberately NOT used: the bundle rebuilt below is the richer one,
    # because by this point location 1's own result and charts exist and the
    # markdown pass's did not. One source of truth, `_render_roster`, called
    # twice with different amounts in hand.
    locations: list[dict[str, Any]] | None = None,
    iso_assumed: bool | None = None,
) -> None:
    """Insert the webapp's notes into report.md, then render the final PDF.

    Session V2-WIRE. The two halves now take different routes, and that is the
    point:

      * `report.md` keeps the existing text insertion, so its bytes and the
        placement of every note are exactly what they were. On this path it is
        an INTERMEDIATE — `_keep_only_report` deletes it at completion — but the
        CLI writes the same file, and moving its notes would have changed a
        document for no gain.
      * **the PDF is rendered from the analysis**, not from that text. The notes
        travel to it as DATA (`notes=`, `deletion_footer=`) rather than as a
        regex match against a markdown heading, which is what §12.1/§12.2/§12.4
        depended on before.

    `narrative` distinguishes the two documents: None means the report is a
    render of the analysis, and a string is the model's UN-spliced prose, which
    goes into the v2 page in place of the deterministic prose while every
    evidence block around it is still rendered from the analysis.
    """
    report_md = job.job_dir / "report.md"
    text = markdown_with_notes(report_md.read_text(), notes, coverage_note=coverage_note)
    # HIST-2. On the deterministic paths `render_report` already wrote the
    # comparison section, and this splice is an idempotent no-op. On the DRAFTED
    # path it is the only way the section reaches report.md: that file is
    # written inside agent/loop.py, which this session does not touch — and on a
    # host without weasyprint, report.md IS the PDF (render_pdf falls back to it).
    text = splice_comparison_markdown(text, comparison)
    report_md.write_text(text)

    # Visual order, top to bottom — the same order the reader meets them in
    # report.md, without the reversal the insert-at-one-anchor hook needs.
    html_notes = [n for n in notes if n]
    if coverage_note:
        html_notes.append(coverage_note)
    # Starts from the SAME bundle the markdown render was given, so the two
    # documents cannot be built from two contexts (Session WORKER-CASE). Only
    # the decoration is added here.
    common = dict(
        **_document_context(job, case, thresholds, comparison,
                            result=result, charts=charts),
        charts=charts, notes=html_notes,
        deletion_footer=PROCESSED_AND_DELETED_NOTE,
        # The markdown just written, notes and footer included, so the
        # no-weasyprint fallback issues the same document rather than
        # re-deriving one without them.
        markdown_text=text,
    )
    if narrative is None:
        render_pdf(result, case.machine, job.job_dir, **common)
    else:
        render_drafted_pdf(narrative, result, job.job_dir, machine=case.machine, **common)
    pdf = job.job_dir / "report.pdf"
    # render is best-effort (may write markdown only, e.g. pandoc timeout or no
    # engine) -- never point pdf_path at a file that was not actually written.
    job.pdf_path = pdf if pdf.exists() else None


def _keep_only_report(job: Job, *, keep_trace: bool) -> None:
    """At completion, delete the raw upload and EVERY intermediate (report.md,
    analysis.json, any temp), leaving ONLY report.pdf — plus trace.jsonl when the
    analyst consented — in the job dir until the TTL sweep removes the dir itself.

    Decouples deletion of intermediates from the report download: the report stays
    downloadable (idempotently) within its TTL, so a browser PDF viewer's follow-up
    or ranged requests all succeed. Never touches `purged`/`pdf_path`. Idempotent."""
    if job.job_dir is None or not job.job_dir.exists():
        return
    keep = {"report.pdf"} | ({"trace.jsonl"} if keep_trace else set())
    if not (job.job_dir / "report.pdf").exists():
        # PDF render was unavailable (best-effort) -- report.md is the only
        # surviving report artifact; never destroy the report content itself.
        keep.add("report.md")
    for entry in job.job_dir.iterdir():
        if entry.name in keep:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


def _render_degraded(
    job: Job, result: Any, case: Case, conversion_note: str, *, reason: str, keep_trace: bool,
    assembly_notes: Sequence[str] = (), coverage_note: str | None = None,
    thresholds: dict[str, Any] | None = None, comparison: ComparisonResult | None = None,
) -> None:
    # `result=` is not optional here, and the markdown document is why: without
    # it location 1 reaches the roster as a bare label, so the point the
    # document is ABOUT renders with no zone and no overall while every other
    # point renders with both. That is the two-documents-from-two-contexts
    # failure this bundle exists to prevent, and it is invisible on a
    # weasyprint host, where the PDF comes from the HTML pass that does have it.
    document_ctx = _document_context(job, case, thresholds, comparison, result=result)
    written = render_report(result, case.machine, job.job_dir, pdf=False, **document_ctx)
    # assembly_notes render top-to-bottom after Machine Details; the hook inserts
    # each right after the table (reversing list order), so reverse to preserve it.
    _finalize_markdown_and_pdf(
        job, [conversion_note, DEGRADED_NOTE, *reversed(list(assembly_notes))],
        coverage_note=coverage_note, result=result,
        charts=written.get("charts"), **document_ctx,
    )
    job.degraded_reason = reason  # type: ignore[assignment]
    # the deterministic report still carries findings — surface them for the card
    job.result_summary = _result_summary(result)
    # Session HIST-1: set beside result_summary on BOTH lanes a finished
    # analysis can reach. A degraded job's numbers are the same computed
    # numbers -- only the narrative is missing -- so it is trendable too.
    # gate_fail is deliberately absent: no diagnosis was made there.
    job.trend_point = _trend_point(result)
    job.committed_fault = _committed_fault(result)
    _become_terminal(job, "degraded", keep_trace=keep_trace)


# Follow-up measurements for the two Session-E fail-closed paths (the probe passed
# the gate, so its recommendations don't cover the cross-file failure — name the
# re-capture that resolves it, honestly, through the same channel).
_FAIL_CLOSED_RECS: dict[str, RecommendedMeasurement] = {
    "cross_channel_speed_agreement": RecommendedMeasurement(
        technique="Re-acquire all channels in a single session at one operating condition",
        purpose="Confirm every channel is from the same machine and running state so the "
        "directional analysis can be trusted.",
        trigger="Uploaded channels disagreed on running speed.",
        priority="high",
    ),
    "per_channel_quality": RecommendedMeasurement(
        technique="Re-capture each channel with the machine running and an adequate signal",
        purpose="Provide at least one channel that passes the data-quality gate so a diagnosis can be made.",
        trigger="Every uploaded channel failed the data-quality gate.",
        priority="high",
    ),
}


def _render_fail_closed(
    job: Job, probe: AnalysisResult, case: Case, conversion_note: str, checks: list[Check], *,
    assembly_notes: Sequence[str], coverage_note: str | None, keep_trace: bool,
    thresholds: dict[str, Any] | None = None, comparison: ComparisonResult | None = None,
) -> None:
    """Session E: a cross-file integrity failure (speed disagreement, or every
    channel failing its gate) yields an insufficient-data report, deterministically,
    with the LLM never invoked — reusing the whole gate-fail template machinery by
    forcing the gate to fail. `error` stays reserved for unparseable uploads."""
    forced_gate = QualityGateResult(
        overall="fail",
        checks=[*checks, *probe.quality_gate.checks],
        train_baseline=probe.quality_gate.train_baseline,
        train_reasons=probe.quality_gate.train_reasons,
    )
    recs = [_FAIL_CLOSED_RECS[c.name] for c in checks if c.name in _FAIL_CLOSED_RECS] \
        or probe.recommended_measurements
    forced = probe.model_copy(
        update={"quality_gate": forced_gate, "findings": [], "rca": None, "recommended_measurements": recs}
    )
    document_ctx = _document_context(job, case, thresholds, comparison, result=forced)
    written = render_report(forced, case.machine, job.job_dir, pdf=False, **document_ctx)
    _finalize_markdown_and_pdf(
        job, [conversion_note, *reversed(list(assembly_notes))], coverage_note=coverage_note,
        result=forced, charts=written.get("charts"), **document_ctx,
    )
    job.gate_summary = _gate_summary(forced)
    _become_terminal(job, "gate_fail", keep_trace=keep_trace)


def _finish(job: Job) -> None:
    job.finished_at = time.monotonic()
    if job.started_at is not None:
        job.duration_ms = round((job.finished_at - job.started_at) * 1000, 1)
    # Re-anchor the TTL clock at completion: the promise is "kept for 60 minutes
    # AFTER analysis", so queue + processing time must not eat into the window.
    job.created_at = time.monotonic()


#: The two terminal states that produced a diagnosis, and so have something to
#: record. `gate_fail` is absent for the reason `_trend_point` is not set there:
#: no diagnosis was made, and a row saying an analysis happened without saying
#: what it found is a record of nothing. `error` is absent because there is no
#: analysis at all.
_RECORDED_STATES = ("done", "degraded")


def _record_to_store(job: Job, state: str) -> None:
    """Copy a finished analysis into the account store. Never raises.

    Runs INSIDE the terminal transition, before the state flip, on PURGE-SYNC's
    rule: the instant `job.state` holds a terminal value the analyst has been
    told the analysis is over and their browser acts on it, so everything that
    claim implies must already be true.

    A no-op wherever `db_hook` is None, which is every job in this build -- a
    property of the job object rather than a branch anyone has to remember to
    write.

    A no-op too on a job that is ALREADY terminal, which is not the same check
    and is the one a reader will not expect. See the guard below.

    A HOOK FAILURE NEVER CHANGES THE OUTCOME. The analysis succeeded and the
    report exists; turning that into `error` would purge a report the analyst is
    entitled to over a bookkeeping failure, and `degraded` is a closed
    vocabulary whose third value would be a wire change. So the job keeps the
    outcome it earned and the record is lost.

    NO NEW `ERROR_TAXONOMY` CODE, and that is wire law #5 satisfied rather than
    dodged: the wire carries `failure_kind`/`retryable`, both derived from
    `error_code`, and this failure produces neither a failed job nor a changed
    response. There is nothing for the operator to ratify because there is
    nothing an analyst can be told. EMAIL-1 made the same call for a send that
    produces no job.

    THE LOG LINE CARRIES A CLASS NAME AND NOTHING ELSE -- no message, no
    traceback, no statement. A SQLAlchemy `OperationalError` can carry the
    connection string, and a `StatementError` can carry the bound parameters,
    which on this path means the analyst's machine alias. Either would put in
    `app.log`, for fourteen days, precisely what the per-job outcome line is
    careful never to write. EMAIL-1's sender logs a status code or a class name
    for the same reason.
    """
    if job.state in TERMINAL_STATES:
        # A LATE FIRE, and the one case where the argument lies. A job the
        # max-runtime sweep stranded was cancelled at the task and marked
        # `error`/`timeout` by `_strand_job` -- but `asyncio.to_thread` cannot
        # interrupt the THREAD, so the worker it abandoned runs on and arrives
        # here with `state="done"` in its hand. The sticky-terminal rule
        # (`Job.__setattr__`) drops the flip that follows, so today that thread
        # only deletes files nobody was reading. It must not also record a row:
        # the analyst has been told this job failed, the wire says `error`, and
        # their daily allowance has already been refunded. `job.state` is what
        # actually happened; the parameter is only what this thread intended.
        return
    if state not in _RECORDED_STATES or job.db_hook is None:
        return
    try:
        job.db_hook(job)
    except Exception as exc:  # noqa: BLE001 -- see the docstring: never the reason a job fails
        try:
            _log.warning(
                "job=%s store_write_failed exc=%s.%s",
                job.id, type(exc).__module__, type(exc).__qualname__,
            )
        except Exception:  # noqa: BLE001 -- logging is never the reason either
            pass


def _return_credit(job: Job, state: str) -> None:
    """Give back the credit held for a job that produced no deliverable. Never raises.

    The rule in one line: `done` and `degraded` return immediately, everything
    else fires the hook. A degraded job still produced the deterministic report
    and its PDF -- which IS the deliverable; a `gate_fail` and every `error`
    code produced no report anybody would sign.

    A no-op wherever `credit_hook` is None, which is every job in this build --
    a property of the job object rather than a branch anyone has to remember to
    write, exactly as `db_hook is None` is for the store hook.

    CALLED FROM BOTH LANES, AT THE POINT EACH LANE ALREADY USES, AND THE
    PLACEMENT DIFFERS. In `_become_terminal` it runs BEFORE the state flip, on
    PURGE-SYNC's rule -- that transition is sequential and honouring the rule
    there is free. In `mark_error` it sits with the daily-allowance refund, AFTER
    the flip, which is that lane's existing shape and is defensible for the same
    reason `_finish` is: no wire field and no client reads a balance off the job
    payload, so nothing observes it early. Splitting the two refunds across the
    flip would have been the change worth arguing about.

    A REFUND FAILURE NEVER CHANGES THE OUTCOME, on `_record_to_store`'s argument:
    the report exists and the analyst is entitled to it, and turning a
    bookkeeping failure into `error` would purge it. The cost is real and is
    written up rather than hidden -- a database outage inside this window loses
    the refund and leaves a charge standing against a failed job.

    The log line carries an exception's class name and nothing else, for the
    reason `_record_to_store` gives: a SQLAlchemy error can carry the connection
    string or the bound parameters, and `app.log` is kept fourteen days.
    """
    if state in _RECORDED_STATES or job.credit_hook is None:
        return
    try:
        job.credit_hook()
    except Exception as exc:  # noqa: BLE001 -- never the reason a job fails
        try:
            _log.warning(
                "job=%s credit_refund_failed exc=%s.%s",
                job.id, type(exc).__module__, type(exc).__qualname__,
            )
        except Exception:  # noqa: BLE001 -- logging is never the reason either
            pass


def _become_terminal(job: Job, state: str, *, keep_trace: bool) -> None:
    """Make every promise a terminal state carries TRUE, and only then announce it.

    `job.state` is the ONLY thing a client polls, and the instant it holds a
    terminal value the analyst's browser acts on it: it stops polling, reads
    `degraded_reason` / `gate_summary` / `result_summary` off that same payload,
    and follows the report link. Until this session every one of those promises
    was established AFTER the flip -- so a poll landing inside the window saw a
    job that had announced an outcome it had not finished producing:

      * the job directory still held `analysis.json`, `report.md` and the trace,
        contradicting /privacy's "everything but the report is deleted when the
        analysis completes" (this is what CI caught on master `8d8494f`:
        `test_pre_exhausted_budget_degrades_without_calling_llm` read the dir one
        statement too early and found the intermediates still there — the same
        test passes on a slower machine, which is what makes it a race and not a
        wrong assertion);
      * `degraded_reason` was unset, so a degraded card could render with no
        reason at all;
      * `gate_summary` / `result_summary` were unset, so the card had no numbers;
      * `created_at` had not been re-anchored, so `get_job`'s lazy TTL check
        (`app.py`) judged the job against its CREATION time rather than its
        completion time and could answer 410 -- "report has expired and been
        deleted" -- about a report that was about to be perfectly valid.

    The window is genuinely small; that is exactly why it is worth closing in one
    place rather than trusting four call sites to keep the order. This mirrors how
    `jobs.Job.__setattr__` handles the sticky-terminal rule: the invariant lives
    where it cannot be forgotten, not at each assignment.

    Callers set their own state-specific fields (`degraded_reason`,
    `gate_summary`, `result_summary`, `token_usage`) BEFORE calling this, for the
    same reason.
    """
    _keep_only_report(job, keep_trace=keep_trace)
    _record_to_store(job, state)
    _return_credit(job, state)
    _finish(job)
    job.state = state


def mark_error(job: Job, safe_message: str, *, code: str) -> bool:
    """Public helper so app.py can report a parse-stage failure (before
    process_job is ever called) through the same terminal-state + timing
    bookkeeping as every other failure path.

    `code` is REQUIRED and must be a key of `jobs.ERROR_TAXONOMY`: an `error`
    job with no code is one the error card cannot give advice for and the
    operator cannot grep, so S7 makes it impossible to add a new error path
    without naming its category. Keyword-only so it can never be passed by
    accident in the safe_message slot.

    Returns False and changes nothing if the job is ALREADY terminal, or if
    another caller has already claimed the failure. That is the "mark only if
    still non-terminal" rule, enforced in the one place every caller goes
    through rather than at each call site: a failure raised after a job
    legitimately reached `done` must not rewrite a good outcome, and a worker
    thread abandoned by the timeout sweep must not un-fail its own job.

    PURGE-SYNC. The F-3 deletion happens HERE, before the state flip, and no
    longer one event-loop hop later in `app.py::_run_guarded`. That hop was a
    real window, not a theoretical one: this function runs on the WORKER THREAD,
    and the continuation that purged ran on the event loop, so a status poll
    already queued there could be served first -- answering `state: "error"`
    while the analyst's raw upload was still on disk, against /privacy's
    "deleted the moment analysis finished". CI run 33999047017 caught it
    (`state='error'`, `purged` False, the outcome line already written) and the
    next run on the same commit passed, which is what makes it a race and not a
    wrong assertion. `_run_guarded`'s purge is left in place upstream and is now
    an idempotent no-op.

    `timeout` is the one code that does NOT purge here, and the taxonomy row
    says so rather than this function testing for the name. See
    `jobs.ERROR_TAXONOMY`: that job's abandoned thread may still be writing.
    """
    if code not in ERROR_TAXONOMY:
        raise ValueError(f"unknown error code {code!r} -- add it to jobs.ERROR_TAXONOMY")
    # One caller wins the transition, and it is decided under a lock rather than
    # by a check-then-set race. This function used to keep the window between the
    # guard and the state write down to two attribute assignments BECAUSE that
    # narrowness was the refund's only protection against firing twice -- the
    # timeout sweeper and an abandoned worker thread can both arrive for one job.
    # The purge below is an rmtree, which would have widened that window by
    # orders of magnitude, so PURGE-SYNC took the fix the old comment named as
    # owed instead of accepting the bad trade. `claim_terminal` also subsumes the
    # terminal-state guard: an already-terminal job cannot be claimed.
    if not job.claim_terminal():
        return False
    job.started_at = job.started_at or time.monotonic()
    # The state goes LAST here, for the reason `_become_terminal` states: a poll
    # landing between the flip and the lines above it saw `state: "error"` with
    # no `safe_message` and no `failure_kind`/`retryable` (both derived from
    # `error_code`) -- an error card with nothing to say and no advice to give,
    # and a `/report.pdf` navigation answering with the generic "The file could
    # not be analysed." instead of the real reason (`app.py`'s error branch reads
    # `job.safe_message`). The purge joins them for exactly the same reason: the
    # instant `state` holds a terminal value, the analyst has been told the
    # analysis is over, and the files must already match that claim.
    #
    # `_finish` stays AFTER the flip on this lane, deliberately, and it is the one
    # asymmetry with `_become_terminal`. Its TTL re-anchor is read only by the
    # lazy-expiry checks in `get_job` and `download_report`, and both are scoped
    # to ("done", "gate_fail", "degraded") -- never `error` -- while its
    # `duration_ms` is read by `_log_job_outcome`, which every caller runs after
    # this function returns. So nothing observes it early.
    job.error_code = code
    job.safe_message = safe_message
    if ERROR_TAXONOMY[code].purge_at_transition:
        # Safe precisely because this point is sequential with the work on every
        # lane that reaches it: each of the ten call sites returns immediately
        # afterwards, nothing reads `job_dir` again, and the thread that failed
        # is the one running this line. `timeout` -- the one lane where that is
        # NOT true -- is the row that opts out.
        job.purge()
    job.state = "error"
    _finish(job)
    # F8. A failure on OUR side does not spend the analyst's daily allowance.
    # `bad_upload` still counts: the service did the work it was asked to do
    # and the answer was "this file cannot be read", which is a real result.
    # This sits inside the terminal-state transition ON PURPOSE -- everything
    # above already ran exactly once for this job, because the claim at the top
    # of this function makes a second call a no-op. That is what makes the refund
    # idempotent without this function tracking anything.
    if job.failure_kind == "server_error" and job.refund_hook is not None:
        try:
            job.refund_hook()
        except Exception:  # noqa: BLE001 -- a refund is never the reason a job fails
            pass
    # Session BILL-1, and DELIBERATELY NOT UNDER THE CONDITION ABOVE. The daily
    # allowance comes back only for `server_error`, on F8's stated argument that
    # a `bad_upload` "still counts: the service did the work it was asked to do
    # and the answer was 'this file cannot be read', which is a real result".
    # That argument is about a free quota and does not survive being applied to a
    # paid one: a credit is a report an analyst intends to put their name on, and
    # charging one for a file we could not read is not defensible at any price.
    # So EVERY error code returns the credit, and the two refunds disagree on
    # purpose rather than by omission.
    #
    # Idempotent: `claim_terminal` above makes a second call here unreachable.
    _return_credit(job, "error")
    return True


#: What the analyst is told when two files are not a Before/After pair. The
#: reason comes from pdm_core.compare and names the two files' OWN declared
#: properties (measurement type, running speed, gate verdict) — never a filename
#: and never a value read out of either file's contents.
def _not_comparable_message(comparison: ComparisonResult) -> str:
    lines = [comparison.reason or
             "These two files cannot be compared as before/after of one measurement point."]
    lines.extend(comparison.what_to_collect)
    return " ".join(lines)


def process_compare_job(
    job: Job,
    *,
    before_case: Case,
    after_case: Case,
    iso_table: dict[str, Any],
    thresholds: dict[str, Any],
    rules: dict[str, Any] | None,
    spend_guard: SpendGuard,
    conversion_note: str,
    retain_trace: bool = False,
    client: anthropic.Anthropic | None = None,
    assembly_notes: Sequence[str] = (),
    coverage_note: str | None = None,
    forced_fail_checks: Sequence[Check] = (),
    channel_summary: dict[str, Any] | None = None,
    drafting_available: bool = True,
) -> None:
    """Session HIST-2 — `mode=compare`: two files in, one report out, nothing
    stored between requests.

    Both readings run the ORDINARY pipeline, unchanged and independently; the
    delta is computed by `pdm_core.compare`; and the document the analyst gets
    is the AFTER analysis — the machine's condition NOW — carrying the
    comparison as an added section. Never raises, like everything on this path.

    Zero persistence is structural rather than promised: both Cases live in
    local variables for the length of this call, both uploads were already
    unlinked at parse time, and `_keep_only_report` deletes every intermediate
    at completion. Nothing about the Before reading survives the job, and that
    is still true after DB-1: the account schema in `webapp/db/` is inert on the
    default backend, this module does not import it on any backend, and no
    reading, spectrum or report from this path is written anywhere.

    Session COMPARE-FWD — this function is a FORWARDER, and its parameter list
    is `process_job`'s. Everything it accepts is passed straight through as an
    explicit keyword argument; exactly two of `process_job`'s parameters are
    NOT forwarded because this function derives them itself:

      * `case` — `after_case`. The report is the machine's condition NOW, with
        the delta added (see the `comparison` note on `process_job`).
      * `comparison` — computed below by `compare_readings`.

    Everything else is a pass-through, `coverage_note`/`forced_fail_checks`/
    `channel_summary` included. Those three were silently dropped until this
    session: they simply fell back to their defaults, so `_render_fail_closed`
    was unreachable from the compare lane and `job.channel_summary` was always
    None there. The list is not kept by hand — `tests/test_compare_forward.py`
    asserts signature parity against `process_job` and reads this module's AST
    to prove the forward is explicit, so a 16th parameter fails loudly instead
    of vanishing. Do not "simplify" the forward to `**kwargs`; the explicitness
    IS the fix.
    """
    job.started_at = job.started_at or time.monotonic()
    job.state = "running"
    job.phase = "analyzing"

    try:
        before = run_analysis(before_case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        after = run_analysis(after_case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    except Exception:  # noqa: BLE001 -- malformed upload content must not crash the worker
        mark_error(
            job,
            "Could not analyze this data — please check the files match the documented schema.",
            code="upload_unreadable",
        )
        return

    # S12FIX, on BOTH readings. `run_rca` swallows every exception and returns
    # status="error", so a crashed screen looks like a successful probe from
    # here. A comparison computed from one would report "no new frequencies"
    # about a spectrum nothing ever screened — and `compare_readings` refuses to
    # do it. This branch exists so the analyst gets the right category rather
    # than an unhandled ValueError: the analysis failed on OUR side, their files
    # were fine, and the allowance is refunded (F8).
    for label, probe in (("before", before), ("after", after)):
        if probe.rca is not None and probe.rca.status == "error":
            _log.warning("job=%s rca_error side=%s reason=%s", job.id, label, probe.rca.reason)
            mark_error(
                job,
                "The analysis did not complete on our side. Your files were fine — "
                "please try again.",
                code="internal_error",
            )
            return

    try:
        comparison = compare_readings(
            before, after, before_case=before_case, after_case=after_case,
            cfg=thresholds.get("compare"),
        )
    except Exception:  # noqa: BLE001 -- the loop above covers the known raise; this is the backstop
        _log.warning("job=%s compare_failed", job.id, exc_info=True)
        mark_error(
            job,
            "The comparison did not complete on our side. Your files were fine — "
            "please try again.",
            code="internal_error",
        )
        return

    if comparison.status == "not_comparable":
        # The ratified HIST-2 code. Both files were read perfectly well; they
        # are still not two readings of one measurement point, and no retry of
        # the same pair changes that.
        mark_error(job, _not_comparable_message(comparison), code="not_comparable")
        return

    # Everything else — including `gate_blocked`, where a reading failed its own
    # data-quality gate — goes through the ordinary funnel. That is deliberate:
    # a gate fail's only valid output is the insufficient-data report naming what
    # to re-capture, and process_job already produces exactly that. The
    # comparison section carries the reason and the re-capture list alongside it.
    process_job(
        job,
        after_case,
        iso_table=iso_table,
        thresholds=thresholds,
        rules=rules,
        spend_guard=spend_guard,
        conversion_note=conversion_note,
        retain_trace=retain_trace,
        client=client,
        assembly_notes=assembly_notes,
        coverage_note=coverage_note,
        forced_fail_checks=forced_fail_checks,
        channel_summary=channel_summary,
        drafting_available=drafting_available,
        comparison=comparison,
    )


def process_job(
    job: Job,
    case: Case,
    *,
    iso_table: dict[str, Any],
    thresholds: dict[str, Any],
    rules: dict[str, Any] | None,
    spend_guard: SpendGuard,
    conversion_note: str,
    retain_trace: bool = False,
    client: anthropic.Anthropic | None = None,
    assembly_notes: Sequence[str] = (),
    coverage_note: str | None = None,
    forced_fail_checks: Sequence[Check] = (),
    channel_summary: dict[str, Any] | None = None,
    drafting_available: bool = True,
    comparison: ComparisonResult | None = None,
) -> None:
    """Runs the deterministic pipeline once (probe, for gate/spend routing),
    then either the drafting pass or a degraded/gate-fail deterministic
    report, writes report.pdf into job.job_dir, and sets job.state /
    job.safe_message / job.degraded_reason / job.token_usage. Never raises.

    Session E: `assembly_notes`/`coverage_note` are multi-axis report lines
    (empty/None ⇒ byte-identical to the single-file path); `forced_fail_checks`
    routes cross-file integrity failures to an insufficient-data report;
    `channel_summary` is the UI-only per-channel status.

    Session HIST-2: `comparison` is the computed Before/After result when this
    job is a compare-mode job, in which case `case` is the AFTER reading — the
    machine's current condition, which is the report an analyst wants, with the
    comparison added to it. None on every ordinary job, and then every render
    below is byte-identical to before. The comparison is NEVER handed to the
    drafting model: it reaches the document deterministically, so a narrated
    delta cannot disagree with the arithmetic because none is ever narrated.

    Session F2: `drafting_available=False` means the drafting client could not
    even be constructed (e.g. ANTHROPIC_API_KEY unset on the host). That is the
    LLM-path-unavailable case the product contract already covers -- degrade to
    the deterministic report with the visible note, exactly like a spend-capped
    or failed draft. It must never leave the job stuck in `running`.
    """
    job.started_at = job.started_at or time.monotonic()
    job.state = "running"
    job.phase = "analyzing"  # deterministic pipeline running (UI stepline hint)
    job.channel_summary = channel_summary  # set once, carried into every terminal state

    try:
        probe = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    except Exception:  # noqa: BLE001 -- malformed upload content must not crash the worker
        mark_error(
            job,
            "Could not analyze this data — please check the file matches the documented schema.",
            code="upload_unreadable",
        )
        return

    # S12FIX (HANDOFF-08-27 §5.1, ruling D-3): `run_rca` swallows every
    # exception and returns status="error", so the probe above SUCCEEDS on a
    # crashed analysis -- the `except` clause never fires and the job would sail
    # on to a report asserting nothing was found. This is the one place that can
    # tell the difference, and it must come BEFORE the degrade branches: an RCA
    # failure is an error, never a degraded report (degraded means "the analysis
    # is sound, the narrative is missing" -- here the analysis itself is gone).
    #
    # `internal_error` is the ratified code for exactly this and no new code is
    # added: server_error, retryable, "the analysis failed on our side; the file
    # was fine" -- which also refunds the analyst's allowance (F8).
    if probe.rca is not None and probe.rca.status == "error":
        _log.warning("job=%s rca_error reason=%s", job.id, probe.rca.reason)
        mark_error(
            job,
            "The analysis did not complete on our side. Your file was fine — "
            "please try again.",
            code="internal_error",
        )
        return

    # Session E cross-file integrity failure -> insufficient-data, LLM never invoked.
    if forced_fail_checks:
        _render_fail_closed(
            job, probe, case, conversion_note, list(forced_fail_checks),
            assembly_notes=assembly_notes, coverage_note=coverage_note, keep_trace=retain_trace,
            thresholds=thresholds, comparison=comparison,
        )
        return

    # A gate-fail case never reaches the model anyway (run_agent_analysis returns
    # the deterministic insufficient-data report before it builds a client), so
    # both no-draft routes below are conditioned on the gate not having failed.
    if probe.quality_gate.overall != "fail" and (spend_guard.exceeded() or not drafting_available):
        reason = "spend_budget" if spend_guard.exceeded() else "draft_failure"
        _render_degraded(job, probe, case, conversion_note, reason=reason, keep_trace=retain_trace,
                         assembly_notes=assembly_notes, coverage_note=coverage_note,
                         thresholds=thresholds, comparison=comparison)
        return

    job.phase = "drafting"  # handing the computed result to the drafting pass
    drafted: dict[str, Any] = {}
    try:
        result = run_agent_analysis(
            case,
            iso_table=iso_table,
            thresholds=thresholds,
            rules=rules,
            out_dir=job.job_dir,
            pdf=False,
            client=client,
            report_out=drafted,
        )
    except AgentAnalysisError as exc:
        # A consistency contract refused the draft (twice — the loop already
        # retried). Distinct from an API error and worth telling apart in the log.
        _log_draft_failure(job, exc)
        _render_degraded(job, probe, case, conversion_note, reason="draft_failure", keep_trace=retain_trace,
                         assembly_notes=assembly_notes, coverage_note=coverage_note,
                         thresholds=thresholds, comparison=comparison)
        return
    except Exception as exc:  # noqa: BLE001 -- API/network errors degrade rather than fail the job
        _log_draft_failure(job, exc)
        _render_degraded(job, probe, case, conversion_note, reason="draft_failure", keep_trace=retain_trace,
                         assembly_notes=assembly_notes, coverage_note=coverage_note,
                         thresholds=thresholds, comparison=comparison)
        return

    if result.quality_gate.overall == "fail":
        # A gate-fail case never reached the model — `run_agent_analysis` returns
        # the DETERMINISTIC insufficient-data report before it builds a client —
        # so this document is a render of the analysis and takes the v2 page like
        # any other deterministic report.
        _finalize_markdown_and_pdf(job, [conversion_note, *reversed(list(assembly_notes))],
                                   coverage_note=coverage_note, result=result, case=case,
                                   thresholds=thresholds, charts=drafted.get("charts"),
                                   comparison=comparison)
        job.gate_summary = _gate_summary(result)
        _become_terminal(job, "gate_fail", keep_trace=retain_trace)
        return

    _finalize_markdown_and_pdf(job, [conversion_note, *reversed(list(assembly_notes))],
                               coverage_note=coverage_note, result=result, case=case,
                               thresholds=thresholds, charts=drafted.get("charts"),
                               narrative=drafted.get("narrative"), comparison=comparison)
    job.result_summary = _result_summary(result)
    # Session HIST-1: set beside result_summary on BOTH lanes a finished
    # analysis can reach. A degraded job's numbers are the same computed
    # numbers -- only the narrative is missing -- so it is trendable too.
    # gate_fail is deliberately absent: no diagnosis was made there.
    job.trend_point = _trend_point(result)
    job.committed_fault = _committed_fault(result)

    trace_path = job.job_dir / "trace.jsonl"
    usage = _sum_trace_usage(trace_path)  # must read the trace BEFORE cleanup
    job.token_usage = usage
    spend_guard.record(usage["input_tokens"] + usage["output_tokens"])
    _become_terminal(job, "done", keep_trace=retain_trace)
