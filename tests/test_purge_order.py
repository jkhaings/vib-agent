"""Session PURGE-ORDER — a terminal state is announced only after it is true.

`job.state` is the one field a client polls, and the instant it holds a terminal
value the analyst's browser acts on it: it stops polling, renders the card from
`degraded_reason` / `gate_summary` / `result_summary` in that same payload, and
follows the report link. Every one of those promises used to be established
AFTER the flip, so a poll landing in the window saw a job announcing an outcome
it had not finished producing.

That window was not theoretical. CI on master `8d8494f` failed
`tests/test_webapp_e2e.py::TestSpendGuard::test_pre_exhausted_budget_degrades_without_calling_llm`
with `analysis.json` still in the job directory — the same test passes on a
slower host, which is what makes it a race rather than a wrong assertion. That
test is left exactly as it was; it is a real customer-shaped assertion and the
fix is what should make it pass. This file is the pin underneath it: it does not
wait for a poll to get unlucky, it inspects the ORDER directly, so the ordering
cannot regress silently on a fast machine again.

Three properties, across every lane that can go terminal:

  1. `_keep_only_report` is never called on a job whose state is already
     terminal — i.e. the directory is put in the state the announcement promises
     BEFORE the announcement (the literal pin PURGE-ORDER owed).
  2. Each terminal state's companion fields are written before `state`, so no
     payload can carry a terminal state with the fields that explain it missing.
  3. **Session PURGE-SYNC.** The F-3 deletion is part of the transition, not a
     consequence of it. On an `error` lane the whole directory is gone and
     `purged` is True at the instant `state` is written; on the three
     report-producing lanes the raw upload and every intermediate are gone by
     then and `purged` stays False, because those jobs have a report to serve.
     `timeout` is the one documented exception, and it is the taxonomy row that
     says so. Same failure shape as (1), one hop further out: PURGE-ORDER fixed
     an ordering inside the worker thread, PURGE-SYNC an ordering ACROSS the
     thread and the event loop (CI run 33999047017 — see the section at the
     bottom of this file).

Zero real API calls: every app here is handed a fake client or a factory that
raises.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.fake_anthropic import FakeAnthropicClient, build_consistent_echo, draft_message
from tests.inference_corpus import RPM, write_corpus
from tests.test_webapp_e2e import (
    _off_csv,
    _poll_until_terminal,
    _post_upload,
    _spectrum_csv,
    _webapp_cfg,
)
from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.agent.consistency import TITLE_TEMPLATE
from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.webapp import app as app_module
from vib_agent.webapp import worker as worker_mod
from vib_agent.webapp.app import create_app
from vib_agent.webapp.jobs import ERROR_TAXONOMY, TERMINAL_STATES, Job

_CODES = {"demo-code": "engineer-1"}
#: The corpus file the inference lane runs on — the same one
#: tests/test_terminal_guarantee.py drives, so a regression shows up in both
#: places or in neither.
_INFERENCE_ITEM = "bare_pairs.txt"


def _boom(*_a, **_k):
    """The S7 injection, reused verbatim so the crash happens at a real site."""
    raise RuntimeError("PURGE-SYNC test injection")


# ── the instrument ────────────────────────────────────────────────────────
#
# One ordered event log per job, recording (a) every attribute that lands on the
# Job and (b) every `_keep_only_report` call, interleaved. Assertions then read
# the order directly instead of racing a poll for it.
#
# `Job.__setattr__` is patched on the CLASS because Python resolves dunders on
# the type: an instance-level override would never be consulted. The spy records
# only writes that actually LAND — `jobs.Job.__setattr__` silently drops a state
# write on an already-terminal job (the sticky-terminal rule), and recording a
# dropped write would invent an ordering violation that did not happen.


class _Recorder:
    def __init__(self) -> None:
        self.events: dict[str, list[tuple[str, Any]]] = {}

    def _log(self, job: Job, kind: str, value: Any = None) -> None:
        job_id = getattr(job, "id", None)
        if job_id is None:
            return
        self.events.setdefault(job_id, []).append((kind, value))

    def for_state(self, state: str) -> list[list[tuple[str, Any]]]:
        """Event logs of every job that reached `state`."""
        return [
            log for log in self.events.values()
            if any(k == "attr:state" and v == state for k, v in log)
        ]

    @staticmethod
    def snapshot(log: list[tuple[str, Any]], state: str) -> dict[str, Any] | None:
        """What the job's FILES looked like at the instant `state` was written.

        PURGE-SYNC's pin, and the reason it is taken here rather than by a poll:
        `get_job` (`app.py`) builds its payload from `job.state`, so the earliest
        moment any client can learn a job is terminal is the moment that write
        lands. A snapshot taken INSIDE that write is therefore exactly what the
        first possible reader sees — no sleep, no polling luck, no window to lose.
        """
        for kind, value in log:
            if kind == "terminal_snapshot" and value["state"] == state:
                return value
        return None

    @staticmethod
    def index(log: list[tuple[str, Any]], kind: str, value: Any = None) -> int:
        """Index of the FIRST matching event, or -1."""
        for i, (k, v) in enumerate(log):
            if k == kind and (value is None or v == value):
                return i
        return -1

    @staticmethod
    def settled(log: list[tuple[str, Any]], kind: str) -> int:
        """Index of the LAST write of `kind` that put a real value there, or -1.

        `Job` is a dataclass, so `__init__` assigns EVERY field — including the
        ones a terminal state is supposed to fill in later — before the job has
        done anything at all. A first-match search therefore finds
        `degraded_reason = None` at index 0 and cheerfully reports that the
        reason was in place before the state was announced. It was not; it was
        `None`, and `get_job` (`app.py`) omits a falsy field from the payload
        entirely, so the client sees the same nothing either way.

        The question worth asking is when the field's FINAL, meaningful value
        landed relative to the announcement — which is this. (This bit: the
        first version of these assertions passed against the unfixed code, which
        is the one result a pin must never produce.)
        """
        found = -1
        for i, (k, v) in enumerate(log):
            if k == kind and v is not None and v != "":
                found = i
        return found


@pytest.fixture
def recorder(monkeypatch) -> _Recorder:
    rec = _Recorder()
    original_setattr = Job.__setattr__
    original_keep = worker_mod._keep_only_report

    def spy_setattr(self: Job, name: str, value: Any) -> None:
        before = getattr(self, name, None) if name == "state" else None
        original_setattr(self, name, value)
        if name == "state" and getattr(self, "state", None) == before:
            return  # the sticky rule dropped it; it never happened
        rec._log(self, f"attr:{name}", value)
        # PURGE-SYNC. Appended AFTER the `attr:state` event, so every relative
        # ordering assertion already in this file reads exactly as it did.
        if name == "state" and value in TERMINAL_STATES:
            job_dir = getattr(self, "job_dir", None)
            rec._log(self, "terminal_snapshot", {
                "state": value,
                "purged": getattr(self, "purged", None),
                "dir_exists": job_dir is not None and job_dir.exists(),
                "names": (sorted(p.name for p in job_dir.iterdir())
                          if job_dir is not None and job_dir.exists() else []),
            })

    def spy_keep(job: Job, **kwargs: Any) -> None:
        rec._log(job, "keep_only_report", job.state)
        return original_keep(job, **kwargs)

    monkeypatch.setattr(Job, "__setattr__", spy_setattr)
    monkeypatch.setattr(worker_mod, "_keep_only_report", spy_keep)
    return rec


# ── the lanes ─────────────────────────────────────────────────────────────


def _app(fake: Any = None, **cfg_overrides):
    return create_app(webapp_cfg=_webapp_cfg(**cfg_overrides), invite_codes=dict(_CODES),
                      anthropic_client_factory=(lambda: fake) if fake is not None else None)


def _keyless_app(**cfg_overrides):
    def _no_client():
        raise RuntimeError("no ANTHROPIC_API_KEY in this test")

    return create_app(webapp_cfg=_webapp_cfg(**cfg_overrides), invite_codes=dict(_CODES),
                      anthropic_client_factory=_no_client)


def _consistent_fake(path: Path, cfg_overrides: dict[str, Any] | None = None) -> FakeAnthropicClient:
    """A draft whose echo matches the analysis this exact file produces, so the
    job reaches `done` rather than degrading on a consistency refusal."""
    cfg = _webapp_cfg(**(cfg_overrides or {}))
    form = UploadForm(machine_alias="TestPump", rpm=1800.0, iso_group="2",
                      iso_support="rigid", machine_type="motor", bearing_model="6206")
    case, _kind, _note = parse_upload(path, form, bearings_cfg=load_config("bearings"))
    result = run_analysis(case, iso_table=load_config("iso_zones")["zones"],
                          thresholds=load_thresholds(cfg["analysis_profile"]),
                          rules=load_config("next_measurements"))
    narrative = (f"{TITLE_TEMPLATE.format(machine_name='TestPump')}\n\nBody.\n\n"
                 "DRAFT -- prepared by automated analysis, pending analyst review.")
    return FakeAnthropicClient(responses=[draft_message(narrative, build_consistent_echo(result))])


def _bad_csv(path: Path) -> Path:
    """Unparseable — the `error` lane, without needing the sandbox to crash."""
    path.write_text("this file is not a spectrum and never was\n")
    return path


def _post_compare(client: TestClient, before: Path, after: Path):
    with open(before, "rb") as first, open(after, "rb") as second:
        return client.post(
            "/api/jobs",
            files={"file": (before.name, first, "text/csv"),
                   "file_2": (after.name, second, "text/csv")},
            data={"invite_code": "demo-code", "machine_alias": "TestPump", "rpm": "1800",
                  "iso_group": "2", "iso_support": "rigid", "machine_type": "motor", "bearing_model": "6206",
                  "mode": "compare"},
        )


def _post_text(client: TestClient, path: Path):
    """A `.txt` upload, i.e. the schema-INFERENCE lane. `_post_upload` posts
    everything as `text/csv`, which is a different code path."""
    with open(path, "rb") as handle:
        return client.post(
            "/api/jobs", files={"file": (path.name, handle, "text/plain")},
            data={"invite_code": "demo-code", "machine_alias": "TestPump", "rpm": str(RPM),
                  "iso_group": "2", "iso_support": "rigid", "machine_type": "motor", "bearing_model": "6206"})


def _run(app, post, recorder: _Recorder) -> dict:
    with TestClient(app) as client:
        response = post(client)
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        data = _poll_until_terminal(client, job_id)
        # The lane's own job id, so a per-lane assertion reads THAT job's event
        # log rather than guessing which of several `error` jobs it meant.
        return {**data, "_job_id": job_id}


@pytest.fixture
def lanes(tmp_path, recorder, monkeypatch):
    """Drive every lane that can go terminal, once, and hand back the recorder.

    Module-scoping this would be wrong: the recorder patches `Job.__setattr__`
    for the whole class, and that has to be undone between tests.
    """
    spec = _spectrum_csv(tmp_path / "spec.csv") or tmp_path / "spec.csv"
    off = tmp_path / "off.csv"
    _off_csv(off)
    before = tmp_path / "before.csv"
    _spectrum_csv(before)
    bad = _bad_csv(tmp_path / "bad.csv")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    write_corpus(corpus)
    txt = corpus / _INFERENCE_ITEM

    seen = {}

    # done — a drafting pass that succeeds
    seen["done"] = _run(_app(_consistent_fake(spec)), lambda c: _post_upload(c, spec), recorder)
    # degraded — the CI case: the daily budget is already spent, so no LLM call
    degraded_app = _app(FakeAnthropicClient(responses=[]), daily_token_budget=1)
    degraded_app.state.vib.spend_guard.record(1_000_000)
    seen["degraded"] = _run(degraded_app, lambda c: _post_upload(c, spec), recorder)
    # gate_fail — a machine-off reading; the model is never reached
    seen["gate_fail"] = _run(_app(FakeAnthropicClient(responses=[])),
                             lambda c: _post_upload(c, off), recorder)
    # error — an unreadable upload
    seen["error"] = _run(_app(FakeAnthropicClient(responses=[])),
                         lambda c: _post_upload(c, bad), recorder)
    # compare — two readings of one point, on a keyless host (so it degrades)
    seen["compare"] = _run(_keyless_app(), lambda c: _post_compare(c, before, spec), recorder)

    # ── PURGE-SYNC additions ──────────────────────────────────────────────
    # error(inference_unavailable) — THE lane CI 33999047017 failed on: a good
    # `.txt` on a keyless host, so the interpretation call cannot be made.
    seen["outage"] = _run(_keyless_app(), lambda c: _post_text(c, txt), recorder)
    # error(internal_error) — the OTHER side of the boundary. Every lane above
    # transitions inside the worker thread; this one raises before `mark_error`
    # is reached at all, so `app.py::_run_guarded` catches it and marks the job
    # from the EVENT LOOP. Both orders have to hold, and only one of them is the
    # one the old code got right. Patched around this run alone — leaking the
    # injection into the fixture's other lanes would break them.
    with monkeypatch.context() as patched:
        patched.setattr(app_module, "structure_fingerprint", _boom)
        seen["crash"] = _run(_app(FakeAnthropicClient(responses=[])),
                             lambda c: _post_text(c, txt), recorder)
    return seen, recorder


class TestEveryLaneReachesTheStateItShould:
    """The fixture is only evidence if each lane actually went where it claims."""

    def test_lanes_are_what_they_say(self, lanes):
        seen, _rec = lanes
        assert seen["done"]["state"] == "done", seen["done"]
        assert seen["degraded"]["state"] == "degraded", seen["degraded"]
        assert seen["degraded"]["degraded_reason"] == "spend_budget"
        assert seen["gate_fail"]["state"] == "gate_fail", seen["gate_fail"]
        assert seen["error"]["state"] == "error", seen["error"]
        # a keyless host degrades; the compare section rides on that document
        assert seen["compare"]["state"] in ("degraded", "gate_fail"), seen["compare"]
        # PURGE-SYNC's two: the outage is server_error/retryable (the F-5 split),
        # and the crash is the one that transitions from the event loop.
        assert seen["outage"]["state"] == "error", seen["outage"]
        assert seen["outage"]["failure_kind"] == "server_error", seen["outage"]
        assert seen["crash"]["state"] == "error", seen["crash"]


# ── property 1 · the directory is right before the state says so ──────────


class TestKeepOnlyReportRunsBeforeTheAnnouncement:
    """The pin this session owes, stated exactly: every `_keep_only_report` call
    happens while the job is still NON-TERMINAL.

    If it ever runs after the flip, there is a window in which a client has been
    told the job is finished while `analysis.json`, `report.md` and the working
    trace are still on disk — which is both the CI failure and a contradiction
    of the retention promise the finished card prints.
    """

    def test_no_call_ever_sees_a_terminal_job(self, lanes):
        _seen, rec = lanes
        offenders = [
            (job_id, state)
            for job_id, log in rec.events.items()
            for kind, state in log
            if kind == "keep_only_report" and state in TERMINAL_STATES
        ]
        assert not offenders, (
            f"_keep_only_report ran on an already-terminal job: {offenders}. The state was "
            f"announced before the directory matched it."
        )

    def test_it_actually_ran_on_the_lanes_that_produce_a_report(self, lanes):
        """Guard against the previous test passing vacuously. `done`,
        `degraded` and `gate_fail` all produce a report and therefore all clean
        up; `error` produces none and is purged in `app.py` instead."""
        _seen, rec = lanes
        for state in ("done", "degraded", "gate_fail"):
            logs = rec.for_state(state)
            assert logs, f"no job reached {state}"
            assert any(rec.index(log, "keep_only_report") > -1 for log in logs), (
                f"the {state} lane never called _keep_only_report — this test would pass "
                f"for the wrong reason"
            )

    @pytest.mark.parametrize("state", ["done", "degraded", "gate_fail"])
    def test_the_call_precedes_the_state_write(self, state, lanes):
        _seen, rec = lanes
        for log in rec.for_state(state):
            keep_at = rec.index(log, "keep_only_report")
            state_at = rec.index(log, "attr:state", state)
            assert keep_at > -1 and state_at > -1
            assert keep_at < state_at, (
                f"{state}: _keep_only_report ran at {keep_at}, after the state write at "
                f"{state_at} — {log}"
            )


# ── property 2 · the payload's explaining fields precede the state ────────


class TestCompanionFieldsPrecedeTheAnnouncement:
    """`get_job` builds one payload from `state` and the fields that explain it.
    A client that polls between the flip and those writes gets a terminal state
    with nothing to render: a degraded card with no reason, a gate-fail card
    with no summary, an error card with no message and no `failure_kind`.
    """

    @pytest.mark.parametrize("state,companions", [
        ("degraded", ["attr:degraded_reason", "attr:result_summary"]),
        ("gate_fail", ["attr:gate_summary"]),
        ("done", ["attr:result_summary"]),
        # error_code is what `failure_kind` and `retryable` are derived from,
        # and safe_message is what both the card and /report.pdf print.
        ("error", ["attr:error_code", "attr:safe_message"]),
    ])
    def test_each_state_is_explained_by_the_time_it_is_announced(
        self, state, companions, lanes
    ):
        _seen, rec = lanes
        logs = rec.for_state(state)
        assert logs, f"no job reached {state}"
        for log in logs:
            state_at = rec.index(log, "attr:state", state)
            for companion in companions:
                at = rec.settled(log, companion)
                assert at > -1, f"{state}: {companion} was never given a value — {log}"
                assert at < state_at, (
                    f"{state}: {companion} was written at {at}, after the state at "
                    f"{state_at} — a poll in that window sees a state it cannot explain"
                )


class TestTtlIsReanchoredBeforeTheAnnouncement:
    """`_finish` re-anchors `created_at` so the TTL is measured from completion,
    not from creation. `get_job` and `download_report` both check expiry LAZILY
    and only for ("done", "gate_fail", "degraded") — so on those three lanes the
    re-anchor must land before the flip, or a poll in the window is judged
    against creation time and can be told 410, "report has expired and been
    deleted", about a report that is about to be perfectly valid.

    `error` is deliberately exempt: neither expiry check covers it, so nothing
    can observe its `created_at` early (see the comment in `mark_error`).
    """

    @pytest.mark.parametrize("state", ["done", "degraded", "gate_fail"])
    def test_created_at_is_re_anchored_first(self, state, lanes):
        _seen, rec = lanes
        for log in rec.for_state(state):
            finished_at = rec.settled(log, "attr:finished_at")
            created_at = rec.settled(log, "attr:created_at")
            state_at = rec.index(log, "attr:state", state)
            assert finished_at > -1 and created_at > -1
            assert created_at < state_at and finished_at < state_at, (
                f"{state}: the TTL was re-anchored at {created_at} and the job finished at "
                f"{finished_at}, both after the state write at {state_at}"
            )


# ── the source-level guard ────────────────────────────────────────────────


class TestTheOrderingLivesInOnePlace:
    """The four report-producing lanes go terminal through one helper, so the
    ordering cannot be got wrong by adding a fifth. Asserted at the source as
    well as at runtime, because this is a REMOVAL: the runtime tests prove the
    current lanes are ordered, and this proves the shape that keeps them so.
    """

    def _source(self) -> str:
        from pathlib import Path as _Path

        return _Path(worker_mod.__file__).read_text()

    def test_become_terminal_does_the_work_then_announces(self):
        body = self._source().partition("def _become_terminal(")[2].partition("\ndef ")[0]
        assert body, "_become_terminal is gone"
        keep = body.index("_keep_only_report(job")
        finish = body.index("_finish(job)")
        flip = body.index("job.state = state")
        assert keep < flip and finish < flip, (
            "_become_terminal announces the state before it has kept its promises"
        )

    def test_no_report_lane_assigns_a_terminal_state_directly(self):
        """Every terminal assignment outside `_become_terminal` must be
        `mark_error`'s, which is the one lane that produces no report and whose
        ordering is pinned above instead."""
        source = self._source()
        stray = [
            line.strip() for line in source.splitlines()
            if "job.state = " in line
            and any(f'"{s}"' in line for s in ("done", "degraded", "gate_fail"))
        ]
        assert stray == [], f"a terminal state is assigned outside _become_terminal: {stray}"

    def test_mark_error_writes_its_message_before_its_state(self):
        body = self._source().partition("def mark_error(")[2].partition("\ndef ")[0]
        code_at = body.index("job.error_code = code")
        message_at = body.index("job.safe_message = safe_message")
        flip_at = body.index('job.state = "error"')
        assert code_at < flip_at and message_at < flip_at, (
            "mark_error announces `error` before the message and code that explain it"
        )


# ══════════════════════════════════════════════════════════════════════════
# PURGE-SYNC · the F-3 purge happens BEFORE the wire can answer
# ══════════════════════════════════════════════════════════════════════════
#
# CI run 33999047017 failed `tests/test_terminal_guarantee.py::
# TestAnOutageIsNotTheAnalystsFault::test_no_key_on_the_inference_lane_is_
# server_error_and_retryable` at `assert job.purged`, on a job whose repr
# already read `state='error'` and whose outcome line was already in the log;
# the next run on the same commit passed. The race was not in the test.
# `mark_error` ran on the WORKER THREAD and the purge ran one event-loop hop
# later, in `app.py::_run_guarded`, after `await asyncio.to_thread(...)` — so a
# status poll already queued on that loop could be served first and answer
# `state: "error"` while the analyst's raw upload was still on disk.
#
# These pins do not wait for a poll to get unlucky. They read the snapshot the
# recorder takes INSIDE the state write, which is the earliest instant any
# reader can learn the job is terminal (`get_job` builds its payload from
# `job.state`). No sleeps, and no dependence on which thread happens to win.


class TestThePurgeIsSynchronousWithTheTerminalTransition:
    """One pin per terminal path, each asserting that path's REAL F-3 promise.

    The promise is not the same on every lane, and pretending it is would break
    the product. A job that produced a report keeps `report.pdf` — `purged` is
    what `download_report` returns 410 for, so setting it on `done` would delete
    every finished report. What `done`/`gate_fail`/`degraded` owe is that the
    raw upload and every intermediate are already gone; `error` owes the whole
    directory. Both are the same promise underneath — *the files match what the
    state claims, before the state is readable* — and both are pinned here.
    """

    @pytest.mark.parametrize("lane", ["error", "outage", "crash"])
    def test_an_error_is_purged_before_it_is_announced(self, lane, lanes):
        """The F-3 lane. `purged` is True and the directory is gone at the
        instant `state` becomes `error` — not one hop afterwards."""
        seen, rec = lanes
        assert seen[lane]["state"] == "error", seen[lane]
        log = rec.events[seen[lane]["_job_id"]]

        purge_at = rec.index(log, "attr:purged", True)
        state_at = rec.index(log, "attr:state", "error")
        assert purge_at > -1, f"{lane}: the error job was never purged at all — {log}"
        assert purge_at < state_at, (
            f"{lane}: purged at {purge_at}, AFTER the state write at {state_at}. The wire "
            f"could answer `error` with the analyst's upload still on disk."
        )

        snap = rec.snapshot(log, "error")
        assert snap["purged"] is True, (
            f"{lane}: the first possible reader of `state: error` saw purged={snap['purged']}"
        )
        assert not snap["dir_exists"], (
            f"{lane}: the job directory still held {snap['names']} when the failure was announced"
        )

    @pytest.mark.parametrize("state", ["done", "gate_fail", "degraded"])
    def test_a_report_lane_has_already_cleaned_up_and_keeps_its_report(self, state, lanes):
        """The other three. Nothing but the report (and a consented trace) is on
        disk by the time the state says the analysis finished — and `purged`
        stays False, because these jobs have a report to serve and
        `download_report` answers 410 for a purged one.
        """
        _seen, rec = lanes
        logs = rec.for_state(state)
        assert logs, f"no job reached {state} — this test would pass for the wrong reason"
        for log in logs:
            snap = rec.snapshot(log, state)
            assert snap is not None, f"{state}: no snapshot was taken"
            assert snap["purged"] is False, (
                f"{state}: announced as purged — every finished report would 410"
            )
            assert set(snap["names"]) <= {"report.pdf", "report.md", "trace.jsonl"}, (
                f"{state}: the raw upload or an intermediate survived the announcement: "
                f"{snap['names']}"
            )
            assert not any(n.startswith("upload") for n in snap["names"]), (
                f"{state}: the analyst's raw upload was on disk when the job said it had "
                f"finished — {snap['names']}"
            )

    def test_the_report_lanes_are_still_downloadable(self, lanes):
        """The wire half of the pin above: cleaning up early must not have cost
        the report. Guards against 'fixing' this session by purging everything."""
        seen, _rec = lanes
        assert seen["done"]["state"] == "done"
        # `get_job` 410s a purged done/gate_fail/degraded job; a 200 with the
        # state on it is proof the entry is still serving.
        for lane in ("done", "gate_fail", "degraded"):
            assert "state" in seen[lane], seen[lane]


class TestTheTimeoutLaneIsTheOneDocumentedException:
    """`timeout` does NOT purge, and that is a ruling rather than an oversight.

    A stranded job is marked by the sweep loop while the worker THREAD it
    abandoned is still running — `asyncio.to_thread` gives no way to stop it —
    so deleting the directory is exactly the S7 failure: the worker recreated it
    and wrote a report into it that no registry entry pointed at any more
    (eval_s7/transcripts/dep4b_sweep.json, part 2b/2c).

    `tests/test_terminal_guarantee.py` and `tests/test_hardening.py` assert
    `job_dir.exists()` after a strand. This says WHY, at the taxonomy, so the
    exception cannot be deleted as an inconsistency by someone tidying up.
    """

    def test_timeout_is_the_only_row_that_does_not_purge(self):
        deferred = {code for code, row in ERROR_TAXONOMY.items()
                    if not row.purge_at_transition}
        assert deferred == {"timeout"}, (
            f"{deferred} opt out of the transition purge. Only `timeout` may: it is the one "
            f"failure marked by a caller that is NOT the thread holding the job directory."
        )

    def test_a_timed_out_job_keeps_its_files(self, tmp_path):
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        (job_dir / "upload.csv").write_text("still being written by an abandoned thread\n")
        job = Job(id="strand", job_dir=job_dir)

        assert worker_mod.mark_error(job, "stopped without finishing", code="timeout") is True
        assert job.state == "error" and job.error_code == "timeout"
        assert job.purged is False, "the strand path purged; the ruling says it must not"
        assert (job_dir / "upload.csv").exists(), (
            "a directory a live worker thread may still be writing into was deleted"
        )


class TestEveryCodeInTheTaxonomyObeysItsOwnRow:
    """Route-driving all eight codes end to end is not worth the wall clock, and
    a lane-by-lane test would leave whichever code nobody thought of uncovered.
    This closes the taxonomy: every row, at the source, in order, with no app,
    no HTTP, no threads and no sleeps — so an eighth code cannot be added
    without this failing or the author deciding what it does.
    """

    @pytest.mark.parametrize("code", sorted(ERROR_TAXONOMY))
    def test_the_row_decides_and_the_purge_precedes_the_flip(self, code, recorder, tmp_path):
        job_dir = tmp_path / code
        job_dir.mkdir()
        (job_dir / "upload.csv").write_text("the analyst's raw file\n")
        job = Job(id=f"tax-{code}", job_dir=job_dir)

        assert worker_mod.mark_error(job, "a message", code=code) is True
        log = recorder.events[job.id]
        purges = ERROR_TAXONOMY[code].purge_at_transition

        snap = recorder.snapshot(log, "error")
        assert snap is not None
        assert snap["purged"] is purges, (
            f"{code}: purge_at_transition={purges} but the announcement carried "
            f"purged={snap['purged']}"
        )
        assert snap["dir_exists"] is not purges, (
            f"{code}: the directory state at the announcement contradicts its row — {snap}"
        )
        if purges:
            state_at = recorder.index(log, "attr:state", "error")
            purge_at = recorder.index(log, "attr:purged", True)
            assert -1 < purge_at < state_at, f"{code}: purged at {purge_at}, flip at {state_at}"
            assert not job_dir.exists()

    def test_a_second_call_changes_nothing(self):
        """The claim makes the transition one-shot, so a late worker thread
        cannot re-run the purge, re-stamp the timing, or refund twice."""
        refunds = []
        job = Job(id="twice")
        job.refund_hook = lambda: refunds.append(1)

        assert worker_mod.mark_error(job, "first", code="internal_error") is True
        assert worker_mod.mark_error(job, "second", code="upload_unreadable") is False
        assert job.error_code == "internal_error" and job.safe_message == "first"
        assert refunds == [1], f"the allowance was refunded {len(refunds)} times for one job"


class TestTwoThreadsCannotBothFailOneJob:
    """The hazard PURGE-SYNC's claim closes, and the reason it was closed here
    rather than deferred again.

    `mark_error`'s old guard was a check-then-set, and its own comment said the
    only thing making that survivable was how few statements sat between the
    check and the write — "the refund's only protection against firing twice
    when the timeout sweeper and an abandoned worker thread both reach this
    function for one job", with the proper fix named as a claim flag on `Job`.
    Moving an `rmtree` inside that window would have widened it by orders of
    magnitude, so this session took the fix instead of the trade.
    """

    def test_one_transition_and_one_refund_under_contention(self, tmp_path):
        import threading

        for attempt in range(40):
            job_dir = tmp_path / f"race{attempt}"
            job_dir.mkdir()
            (job_dir / "upload.csv").write_text("x\n")
            job = Job(id=f"race{attempt}", job_dir=job_dir)
            refunds: list[int] = []
            job.refund_hook = lambda: refunds.append(1)

            results: list[bool] = []
            start = threading.Barrier(2)

            def race(code: str) -> None:
                start.wait(timeout=10.0)
                results.append(worker_mod.mark_error(job, code, code=code))

            threads = [threading.Thread(target=race, args=(code,))
                       for code in ("timeout", "internal_error")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10.0)

            assert sorted(results) == [False, True], (
                f"attempt {attempt}: both callers claimed the transition — {results}"
            )
            assert len(refunds) == 1, (
                f"attempt {attempt}: one job refunded {len(refunds)} times — both are "
                f"server_error, and both used to pass the old check-then-set guard"
            )
