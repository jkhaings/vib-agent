"""In-memory job registry (Phase 6) -- no database holds a JOB, on any backend.
A job's directory, its state and its timing live in this process and nowhere
else, and the TTL sweep is still the only thing that ends them.

The registry is the only thing that knows a job is running, and it forgets
everything when the process does. Each job gets its own
mkdtemp() directory, emptied of everything but the report the instant the
analysis completes (`worker._keep_only_report`), deleted outright the instant
it FAILS (`worker.mark_error`, via `Job.purge` below), and removed entirely by
the TTL sweep. Downloading the report does not purge -- a browser PDF viewer
issues follow-up and ranged GETs, and each must return the same bytes.
Nothing here logs file contents or machine aliases -- only a job's own
metadata (id, state, code label, kind, size, timing, token usage).
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

JobState = Literal[
    "queued", "running", "awaiting_confirm", "done", "gate_fail", "degraded", "error"
]
# Session G: `awaiting_confirm` is the one non-terminal PAUSE in the machine.
# A schema-inference upload stops there with its interpretation on display
# until the analyst confirms it (POST /api/jobs/{id}/confirm), then resumes
# into the ordinary running -> terminal path. It is deliberately not
# terminal: the TTL sweep still reclaims it, so an upload nobody confirms
# is deleted on the same schedule as everything else.
DegradedReason = Literal["spend_budget", "draft_failure"]
# Coarse progress WITHIN the "running" state, for the UI stepline. Not a new
# state-machine state -- purely a display hint the worker advances as it moves
# from the deterministic probe to the drafting call. "verifying" is never set on
# its own: verification happens inside the drafting call, so the UI leaves that
# step pending until a terminal state, then lights it truthfully.
JobPhase = Literal["analyzing", "drafting"]

# ── S7: the terminal set, and the failure taxonomy ───────────────────────────
# Named once, here, because three separate layers now branch on it: the sweeper
# (an expired TERMINAL job is an expiry; an expired queued/running job is a job
# that stopped without finishing), the background guards in app.py, and the
# stickiness rule in `Job.__setattr__` below.
TERMINAL_STATES = frozenset({"done", "gate_fail", "degraded", "error"})

FailureKind = Literal["bad_upload", "server_error"]
# What the ERROR CARD branches on: whose problem this is, and therefore what
# advice to give. Only this and `retryable` are exposed to the browser -- the
# finer `error_code` is the operator's grep handle and stays in the log.
ErrorCode = Literal[
    "upload_unreadable",
    "interpretation_failed",
    "inference_unavailable",
    "merge_failed",
    "internal_error",
    "timeout",
    "upload_write_failed",
    "not_comparable",
]


@dataclass(frozen=True)
class FailureClass:
    """One row of the taxonomy. `retryable` answers exactly one question --
    'is submitting this again a sensible thing to do?' -- and it is a property
    of the CATEGORY, never of the exception that produced it.

    PURGE-SYNC adds `purge_at_transition`, and it is the same kind of property:
    whether a job failing in THIS category still has a writer holding its
    directory. Everything but `timeout` is answered by the thread that failed,
    sequentially, so its files can go at the moment of failure (S7-ACCEPT F-3).
    Declared here rather than tested for by name in `worker.mark_error` for the
    reason S7 made `kind` required: a new error path should not be addable
    without someone deciding what it means. The default is the privacy-safe
    answer -- omit it and the files are deleted.
    """

    kind: FailureKind
    retryable: bool
    meaning: str
    purge_at_transition: bool = True


# S7, ratified by the operator before wiring: six codes, two kinds. Categories
# an analyst would recognise, not exception class names; stable strings, because
# they are written into the operator's log and read back by scripts/beta_digest;
# and every reachable failure maps to exactly ONE of them.
#
# S7-ACCEPT added a seventh, `inference_unavailable`, because acceptance finding
# F-5 showed a reachable failure that mapped to the WRONG one: with the LLM
# unavailable (no key, or spend budget exhausted), a perfectly good file on the
# schema-inference lane was marked `interpretation_failed` -- bad_upload, not
# retryable -- and the analyst was told their format was unsupported. Both
# halves were false: nothing was wrong with the file, and retrying after the
# outage works. The rubric's "every reproduction maps to exactly one" is the
# clause that forces the split -- an outage of OUR service and a layout that
# genuinely cannot be read are different categories to the person deciding what
# to do next.
#
# Keyed by `str` rather than by `ErrorCode` on purpose: this table is what
# VALIDATES an incoming code at runtime (`worker.mark_error` raises on a miss),
# so it has to be lookup-able with an arbitrary string. The Literal above is the
# declared vocabulary, and a test asserts the two cannot drift apart.
ERROR_TAXONOMY: dict[str, FailureClass] = {
    "upload_unreadable": FailureClass(
        "bad_upload", False, "the uploaded file(s) could not be read at all"),
    "interpretation_failed": FailureClass(
        "bad_upload", False, "no layout could be inferred for any uploaded file"),
    "inference_unavailable": FailureClass(
        "server_error", True,
        "the layout-interpretation service was unavailable; the file may be fine"),
    "merge_failed": FailureClass(
        "bad_upload", False, "the uploaded channels could not be combined into one measurement"),
    "internal_error": FailureClass(
        "server_error", True, "the analysis failed on our side; the file was fine"),
    # The ONE row that does not purge at its transition, and the exception is
    # the S7 ruling, not an oversight. A `timeout` job is marked by the sweep
    # loop while the worker THREAD it abandoned is still running -- and
    # `asyncio.to_thread` gives no way to stop it, so that thread may still be
    # writing into the job directory. Deleting it here is precisely the failure
    # S7 measured: the worker recreated the directory it had just lost and wrote
    # a report into it that no registry entry pointed at any more
    # (eval_s7/transcripts/dep4b_sweep.json, part 2b/2c). Its files are
    # reclaimed by a LATER sweep once the job is terminal -- one TTL late, which
    # is the ruling's stated cost. Pinned by test_terminal_guarantee.py's
    # `job_dir.exists()` assertions and tests/test_hardening.py.
    "timeout": FailureClass(
        "server_error", True, "the analysis ran past its time limit and was stopped",
        purge_at_transition=False),
    "upload_write_failed": FailureClass(
        "server_error", True, "the upload could not be written to server storage"),
    # HIST-2, ratified by the operator before wiring (ROADMAP D-7). A two-file
    # comparison needs a way to say "these two files were both read perfectly
    # well, and they are still not Before/After of one measurement point" --
    # different measurement types, different running speeds, a reading taken
    # with the machine stopped. `merge_failed` was the near miss and is a
    # DIFFERENT promise: it means channels that could not be combined into one
    # measurement, which is what the multi-axis path does and what a comparison
    # deliberately never does. Retrying the same two files cannot change the
    # answer, so `retryable` is False, and the fault is in the pairing rather
    # than in our service, so the kind is `bad_upload`.
    #
    # A gate FAILURE is deliberately NOT here: a reading that failed its
    # data-quality gate produces the insufficient-data report that names what to
    # re-capture, which is the only valid downstream output of a gate fail
    # (CLAUDE.md) and a strictly more useful answer than an error card. See
    # SESSION_HIST2.md for that deviation from the roadmap's parenthetical.
    "not_comparable": FailureClass(
        "bad_upload", False,
        "the two uploaded files cannot be compared as before/after of one measurement point"),
}


@dataclass
class Job:
    id: str
    state: JobState = "queued"
    code_label: str = ""
    kind: str = ""
    file_size: int = 0
    created_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    finished_at: float | None = None
    duration_ms: float | None = None
    token_usage: dict[str, int] | None = None
    degraded_reason: DegradedReason | None = None
    safe_message: str | None = None
    job_dir: Path | None = None
    pdf_path: Path | None = None
    purged: bool = False
    # ── UI-only, read-only summaries (Session E / ui-v1) ──────────────────
    # Additive: set by worker.py, surfaced verbatim in GET /api/jobs/{id}, so
    # the state cards render REAL data (not raw JSON) without a new endpoint or
    # any change to the state machine, limits, or parsing.
    phase: JobPhase | None = None
    gate_summary: dict[str, Any] | None = None   # {"reasons": [...], "collect": [...]}
    result_summary: dict[str, Any] | None = None  # {"no_findings", "faults", "severity"}
    # Session E (multi-axis upload): per-channel status + optional speed warning,
    # for the UI state card. Same additive, read-only, labelled-strings pattern.
    channel_summary: dict[str, Any] | None = None  # {"channels": [...], "speed_warning": str|None}
    # Session HIST-1: the one MACHINE-READABLE result on this wire, and the
    # exception to the labelled-strings rule directly above -- deliberately its
    # own field rather than a fourth key on `result_summary`, whose contract IS
    # "labelled strings only, never raw model fields". The browser stores this
    # verbatim on the machine's trend card and posts it back as `history` on the
    # next upload, so it has to be the number, not a rendering of it.
    # {"severity_rms_mms": float, "iso_zone": "A".."D", "dominant_axis": "x|y|z"|None,
    #  "captured_at": str}. None -- and so absent from the payload -- whenever
    # there is no trendable scalar (see worker._trend_point).
    trend_point: dict[str, Any] | None = None
    # ── Session JOB-DB: the committed diagnosis, as the MODEL's own id ─────
    # `result_summary["faults"]` above carries display labels by contract, and
    # `jobs.committed` in the account schema wants the raw `Finding.fault` --
    # the string a later query can group by, not the one a card renders. So it
    # travels on its own field. Server-internal: `get_job` builds its payload
    # field by field, so nothing here reaches the wire (wire law #5).
    committed_fault: str | None = None
    # ── Session INTAKE-2: the machine as the analyst declared it ──────────
    # What kind of machine this is (PARTC F-2 -- it used to be the literal
    # "motor" for everything), and which ISO 20816-3 row the severity was
    # judged against, WITH its provenance.
    #
    # `iso_assumed` is the one that has to be here rather than inferred. A
    # report may print "Group 2 (assumed)" only when nobody stated a rating or
    # a mounting, and by the time the document is rendered the form is gone --
    # so the fact travels with the job. `report/` is REPORT-3's to open, which
    # is why this is a field and a contract entry rather than a template change:
    # `docs/contracts/machine_result.md` is what REPORT-3 builds against.
    machine_type: str | None = None
    iso_group: str | None = None
    iso_support: str | None = None
    iso_assumed: bool = False
    #: Session INTAKEFIX-1 (INTAKE-2's F-4). WHY each half of the row above was
    #: chosen, one of `rated` | `stated` | `assumed` (`IsoClass`,
    #: `adapters/uploads/common.py`). `iso_assumed` is their `or` and can only
    #: answer "was anything assumed"; the report's own words are *"(group and
    #: support class assumed)"*, which is a claim about BOTH halves made from a
    #: flag that is true when either one was. These are what make it honest, and
    #: they are named as `IsoClass` and `docs/contracts/machine_result.md`
    #: already name them so REPORT-3 finds what section 6 told it to ask for.
    group_source: str | None = None
    support_source: str | None = None
    #: The below-scope caveat from `resolve_iso_class` -- set only when the
    #: rated power is under ISO 20816-3's 15 kW floor, so a report can say the
    #: zone boundaries are a guide rather than the standard's own answer.
    iso_note: str | None = None
    #: Session INTAKEFIX-1 — the nameplate, as declared, for the two `0005` card
    #: columns `db/recorder.py` writes beside `machine_type`. SERVER-INTERNAL:
    #: `get_job` builds its payload field by field (wire law #5), so nothing
    #: here reaches the wire and neither of these is in the contract.
    rated_kw: float | None = None
    driven_rpm: float | None = None
    #: Session INTAKE-2 — one entry per measurement location OTHER than the
    #: first, in the order the form listed them. Empty on a single-location job,
    #: which is every job the product could produce before this session.
    #:
    #: Each entry is the shape `docs/contracts/machine_result.md` declares, and
    #: that file is REPORT-3's build target -- which is why this is a list of
    #: dicts on the job rather than a template change: `report/` belongs to
    #: REPORT-3 this round, so the numbers are carried and the document that
    #: renders them arrives with it.
    #:
    #: `trend_point` inside an entry is the same machine-readable scalar
    #: HIST-1 put on the wire for the first location, built by the SAME
    #: `worker._trend_point`, so the browser can file a reading per point.
    locations: list[dict[str, Any]] = field(default_factory=list)
    #: Session REPORTFIX-1 — the SAME locations, carrying the live objects the
    #: wire cannot hold: `{"label", "result": AnalysisResult, "case": Case}` per
    #: extra point, built in `app._analyse_extra_location` from the `probe` and
    #: `Case` it already computes and used to throw away on return.
    #:
    #: This is IN-PROCESS ONLY and must never be serialized. `locations` above is
    #: the contract (`docs/contracts/machine_result.md` §3) and is what
    #: `GET /api/jobs/{id}` publishes; this one exists because page 1 has to name
    #: the faulted point's frequency and shaft order, and the wire carries
    #: `committed_fault` as a bare model id with no Hz, no order and no
    #: confidence beside it. A report may not print a number no tool result
    #: produced, so the object that produced it has to reach the renderer.
    #:
    #: Safe by construction rather than by care: no code path in `webapp/` calls
    #: `asdict`, `vars` or reads `__dict__` on a Job — every payload is built
    #: field by field — so adding a field here cannot put it on the wire.
    location_docs: list[dict[str, Any]] = field(default_factory=list)
    # ── Session G (schema inference) ──────────────────────────────────────
    # `interpretation` is the confirm card's payload: OUR words, built from the
    # recipe's enumerated values -- never a phrase the model or the file wrote.
    # `recipe_json`/`pending` carry the paused job's state between the inference
    # pass and the analyst's confirmation; both are dropped once it resumes.
    interpretation: dict[str, Any] | None = None
    recipe_json: str | None = None
    pending: dict[str, Any] | None = None
    fingerprint: str | None = None
    # ── Session S7 (the terminal-state guarantee) ─────────────────────────
    # `error_code` is set at EVERY path that reaches state="error" (enforced by
    # `mark_error`'s required keyword and by tests/test_terminal_guarantee.py),
    # and indexes ERROR_TAXONOMY above for the kind and the retryable flag.
    error_code: ErrorCode | None = None
    # Monotonic stamp of when a BACKGROUND TASK actually began work on this job,
    # i.e. after it won a worker-semaphore slot. Deliberately separate from
    # `started_at` (which anchors `duration_ms` and is set inside process_job):
    # the max-runtime timeout must not count queue wait, and duration_ms must
    # not change meaning because a timeout was added.
    worker_started_at: float | None = None
    # One job, one outcome line, ever. An abandoned worker thread that finishes
    # after its job was already marked terminal must not write a second one.
    outcome_logged: bool = False
    # ── F8 (UX-WIRE): the refund hook ─────────────────────────────────────
    # Called by `worker.mark_error` when, and only when, this job actually
    # TRANSITIONS to error with a `server_error` kind. A callable rather than a
    # RateLimiter reference so `worker.py` stays ignorant of `security.py`, and
    # attached per-job at acceptance so there is still no module global here.
    # It fires at most once by construction: `claim_terminal` below hands the
    # failure transition to exactly one caller, so a late worker thread cannot
    # refund a second time.
    refund_hook: Any = None
    # ── Terminal-transition seams ─────────────────────────────────────────
    # Two optional callables the worker fires at a terminal transition, kept as
    # bare callables for exactly the reason `refund_hook` above is one: nothing
    # in `worker.py` has to import the module that would supply them.
    #
    # `db_hook` fires when the job reaches `done` or `degraded`; `credit_hook`
    # fires when it reaches an outcome that produced no deliverable. Both are
    # None in this build -- nothing attaches one -- which is what makes "this
    # job wrote nothing anywhere" a property of the object rather than a promise
    # about a branch.
    db_hook: Any = None
    credit_hook: Any = None
    # ── PURGE-SYNC: the claim on a failure transition ─────────────────────
    # Not part of the job's data -- excluded from repr (a Lock in the failure
    # output of every assertion helps nobody) and from equality.
    _terminal_lock: Any = field(default_factory=threading.Lock, repr=False, compare=False)
    _terminal_claimed: bool = field(default=False, repr=False, compare=False)

    # A terminal state is FINAL. S7 measured the alternative: the TTL sweeper
    # marked a stranded job `error`, and the worker thread it had abandoned then
    # came back and overwrote that with `degraded` -- two contradictory log lines
    # and a report that existed after the analyst was told it had failed
    # (eval_s7/transcripts/dep4b_sweep.json, part 2c). The invariant belongs on
    # the field itself rather than at each of the eight assignment sites: those
    # sites run on a thread nobody is holding any more, and there is no useful
    # place there to decide the question. Silently ignoring is deliberate -- a
    # late worker finishing its work is not an error, it is just too late to
    # change the answer.
    def __setattr__(self, name: str, value: Any) -> None:
        if name == "state" and getattr(self, "state", None) in TERMINAL_STATES:
            return
        object.__setattr__(self, name, value)

    def claim_terminal(self) -> bool:
        """Claim the right to fail this job, for exactly one caller. True to the
        winner, False to everyone after it.

        This is the claim flag `worker.mark_error` was written around and could
        not have: its guard was a check-then-set across the whole transition, and
        it noted in its own comment that the only thing keeping the window
        survivable was how few statements sat inside it -- "the proper fix is a
        claim flag on `Job` -- jobs.py, another session". This is that session,
        and PURGE-SYNC is what forces it: the F-3 purge now happens INSIDE that
        window, so an `rmtree` sits where two attribute assignments used to.

        Two callers genuinely meet here. The TTL sweep marks a stranded job
        `error(timeout)` from the event loop while the worker thread it
        abandoned -- which cannot be interrupted -- may be reaching `mark_error`
        for a failure of its own. Both used to pass the check, and both used to
        fire `refund_hook`, charging the analyst's allowance back twice for one
        job.

        `_become_terminal` deliberately does NOT claim. A successful outcome and
        a failure are decided in the same thread on every lane that can produce
        both, so there is nothing to arbitrate there, and the sticky-terminal
        rule in `__setattr__` below still has the last word on the state itself.
        Claiming there would change behaviour this session has no evidence about;
        claiming here removes a measured hazard.
        """
        with self._terminal_lock:
            if self.state in TERMINAL_STATES or self._terminal_claimed:
                return False
            self._terminal_claimed = True
            return True

    def purge(self) -> None:
        """Delete this job's directory (idempotent) and record that it is gone.

        Lives on the JOB rather than on the registry because it never needed the
        registry -- `JobRegistry.purge_job` below is now a one-line delegation
        that exists for its callers' sake. That matters for PURGE-SYNC:
        `worker.py` must be able to purge at the moment of a failure, and
        `worker.py` holds no registry reference and should not start holding
        one, for the same reason `refund_hook` is a bare callable rather than a
        `RateLimiter`.

        The registry ENTRY is untouched -- the 410-vs-404 distinction depends on
        it existing until the TTL sweep drops it.
        """
        if self.job_dir is not None and self.job_dir.exists():
            shutil.rmtree(self.job_dir, ignore_errors=True)
        self.purged = True
        self.pdf_path = None

    @property
    def failure_kind(self) -> str | None:
        row = ERROR_TAXONOMY.get(self.error_code or "")
        return row.kind if row else None

    @property
    def retryable(self) -> bool | None:
        row = ERROR_TAXONOMY.get(self.error_code or "")
        return row.retryable if row else None


class JobRegistry:
    """Owns the in-memory job dict and each job's mkdtemp() directory."""

    def __init__(self, *, ttl_minutes: float, base_tmp: Path | None = None,
                 max_runtime_s: float = 600.0) -> None:
        self._jobs: dict[str, Job] = {}
        self._ttl_seconds = ttl_minutes * 60
        self._base_tmp = base_tmp
        self._max_runtime_s = max_runtime_s

    def __len__(self) -> int:
        return len(self._jobs)

    def create(self, *, code_label: str, file_size: int) -> Job:
        job_id = uuid.uuid4().hex
        job_dir = Path(tempfile.mkdtemp(prefix=f"vibjob_{job_id}_", dir=self._base_tmp))
        job = Job(id=job_id, code_label=code_label, file_size=file_size, job_dir=job_dir)
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def all_jobs(self) -> list[Job]:
        return list(self._jobs.values())

    def purge_job(self, job: Job) -> None:
        """Delete a job's directory (idempotent). Leaves the registry entry
        in place with `purged=True` -- the 410-vs-404 distinction depends on
        the entry still existing until the TTL sweep drops it.

        The work is `Job.purge`; this stays as the name every caller outside
        `worker.py` already uses (the lazy TTL purges in `app.py`, the sweep
        below), so PURGE-SYNC moved the code without moving anybody's call."""
        job.purge()

    def is_expired(self, job: Job) -> bool:
        return (time.monotonic() - job.created_at) >= self._ttl_seconds

    def is_overrunning(self, job: Job) -> bool:
        """Has a job that a worker actually STARTED been working longer than the
        max runtime? Measured from `worker_started_at`, so a job that merely sat
        in the queue behind a full `worker_semaphore` is never killed for it."""
        if job.state not in ("queued", "running") or job.worker_started_at is None:
            return False
        return (time.monotonic() - job.worker_started_at) >= self._max_runtime_s

    def drop(self, job_id: str) -> None:
        """Remove a registry entry entirely. Used where a job must leave no
        trace because it never really existed -- see app.py's upload-write
        failure, where the entry is created six lines before the write."""
        self._jobs.pop(job_id, None)

    def sweep_expired(self) -> tuple[list[str], list[Job]]:
        """Split every reclaimable job into the two things it can actually be,
        and return them separately:

          * **removed** -- expired jobs in a TERMINAL state. These are genuine
            expiries: the analysis finished and the TTL ran out. Files purged,
            entry dropped, later polls 404. Unchanged from before S7.
          * **stranded** -- jobs that are NOT terminal and are either past the
            TTL or past the max runtime. These are NOT expiries: they are jobs
            that stopped without finishing, and returning 404 for them told the
            analyst their finished report had aged out, of which every clause
            was false (eval_s7/transcripts/dep4b_sweep.json, part 1d). The caller
            cancels their worker task and marks them `error`; the registry entry
            SURVIVES so the status endpoint can tell the truth.

        SESSION FLIP-1 MOVED ONE STATE ACROSS THAT LINE. S7 put an expired
        `awaiting_confirm` job in **removed**, reasoning that a job "paused for
        a human who never came back" genuinely did expire. But the `removed`
        branch fires neither terminal hook and produces no outcome line, so an
        analyst who uploaded a file we could not lay out, saw the confirm card,
        and closed the tab left nothing behind but one `ttl_sweep expired=1`.

        The test S7 wrote for this case is called *"the case the fix must NOT
        change"*, so this is a deliberate reversal of a ruling and not an
        oversight being tidied. Two things make it the same ruling rather than a
        new one:

          * `stranded` means *stopped without finishing*, and that is exactly
            what an abandoned confirm card is. No report was produced and the
            analysis never ran.
          * S7's own argument for keeping the entry applies here WITH FORCE. Its
            complaint was that a 404 makes the browser draw `expiredCard()`,
            which says a report *"was kept for 60 minutes after it finished,
            then deleted"*. For an abandoned confirm card nothing finished and no
            report ever existed, so every clause of that card is false here too.

        ITS FILES GO NOW, NOT ONE TTL LATER, AND THAT IS THE ONE PLACE THIS
        DIFFERS FROM A STRAND. The invariant `app.py`'s lazy purge documents is
        *"a **running** job's dir must never be deleted out from under the
        worker"*, and an `awaiting_confirm` job has no worker: the inference pass
        returned before the pause, and the only thing that reads that directory
        again is `POST /api/jobs/{id}/confirm`, which refuses an expired job and
        purges it itself (`app.py`, the 410 branch). Leaving the upload for a
        second TTL would quietly double a retention window that `/privacy` and
        the report footer both state as sixty minutes -- a disclosure change,
        which is not something a bug fix gets to make on its way past.

        Nothing here touches a stranded WORKER's files -- the paragraph above is
        the single exception, and it is an exception precisely because that lane
        has no worker. `app.py`'s lazy purge documents the invariant -- "a
        running job's dir must never be deleted out from under the worker" --
        and this method used to be the one place that broke it: it filtered on age alone, rmtree'd a live job's directory, and
        the worker then recreated that directory and wrote a report into it that
        no registry entry pointed at any more (part 2b/2c). Their files are
        reclaimed by a LATER sweep, once the caller has made them terminal.

        The ruling's full cost, stated so nobody rediscovers it (S7-ACCEPT F-3):
        what a stranded job's directory holds for that extra window includes any
        working `trace.jsonl` its abandoned thread was writing -- created during
        drafting regardless of consent, because consent gates RETENTION at
        completion, and a stranded job never completes. Up to one TTL, then gone
        with the directory. Every other terminal-`error` path purges its files
        at the moment of failure (`app.py::_run_guarded`); the strand path alone
        cannot, because its thread may still be writing into them.
        """
        removed: list[str] = []
        stranded: list[Job] = []
        for job in list(self._jobs.values()):
            # TERMINAL is the whole predicate now. It used to be the tuple
            # `("queued", "running")`, spelled out, which is what silently left
            # `awaiting_confirm` on the expiry branch -- a state added to
            # `JobState` by a later session than the one that wrote this loop.
            # Reading the frozenset the rest of the file already branches on
            # means the next state added to that Literal lands on the side that
            # accounts for it, rather than on the side that drops it.
            if job.state not in TERMINAL_STATES:
                if self.is_expired(job) or self.is_overrunning(job):
                    if job.state == "awaiting_confirm":
                        # See the docstring: no worker holds this directory, and
                        # the retention window is disclosed as sixty minutes.
                        self.purge_job(job)
                    stranded.append(job)
                continue
            if not self.is_expired(job):
                continue
            self.purge_job(job)
            del self._jobs[job.id]
            removed.append(job.id)
        return removed, stranded
