"""Session S7 — every job reaches a terminal state, and says which kind of
failure it was.

The hole this closes was measured before it was fixed, in `eval_s7/`: seven
concrete sites where an exception escaped into a background task nobody held a
reference to and nobody wrapped. Six of them left the job in `queued`/`running`
for **31 of 31 samples over 15 seconds** — on a path whose complete end-to-end
run is 6.2 s — with `report.pdf` answering *"still being prepared"* for a report
that was never coming, and not one line in the operator's log that could be
joined to the analyst asking about it. The seventh returned a bare `500` and
leaked a `queued` registry entry plus its `mkdtemp()` directory for the full TTL,
counted against `queue_depth_max`.

Three separate guarantees are pinned here, and they are not the same guarantee:

  * **terminal** — the job stops, visibly, at the endpoint the browser polls
    (`§2`, criteria A1–A7);
  * **attributable** — an `error` says whose problem it was (`failure_kind`) and
    whether trying again is sensible (`retryable`), because the card's old
    advice — download the CSV template, email us the export — is exactly wrong
    for a crash of ours on a good file (`§3`, B1–B4);
  * **distinguishable from an expiry** — a job that stopped without finishing is
    not a report that aged out, and the sweep must stop making them look
    identical (`§4`, C1–C5).

The reproductions in `eval_s7/run_states_raises.py` and
`eval_s7/run_dep4b_sweep.py` remain the evidence; these are the pins. The
injections below are deliberately the SAME ones those runners use, so a
regression shows up in both places or in neither.
"""

from __future__ import annotations

import logging
import re
import time
import types
from functools import partial
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fake_anthropic import FakeAnthropicClient, FakeMessage, FakeTextBlock
from tests.inference_corpus import CORPUS, RPM, write_corpus
from tests.test_webapp_e2e import _poll_until_terminal, _spectrum_csv, _status_once, _webapp_cfg
from vib_agent.webapp import app as app_module
from vib_agent.webapp.app import create_app
from vib_agent.webapp.jobs import ERROR_TAXONOMY, TERMINAL_STATES, Job
from vib_agent.webapp.security import RateLimiter
from vib_agent.webapp.worker import mark_error

CODE = "demo-code"
LABEL = "engineer-1"
_ITEM = next(item for item in CORPUS if item.name == "bare_pairs.txt")


# ── fixtures / helpers ───────────────────────────────────────────────────────
def _recipe_client() -> FakeAnthropicClient:
    """One canned reply: the correct recipe for `bare_pairs.txt`. No network,
    no key, no cost — the same pattern tests/test_inference_webapp.py uses."""
    return FakeAnthropicClient(responses=[
        FakeMessage(content=[FakeTextBlock(text=_ITEM.recipe.model_dump_json())],
                    stop_reason="end_turn")])


def _app(*, inference=True, **cfg_over):
    cfg = _webapp_cfg(**cfg_over)
    return create_app(webapp_cfg=cfg, invite_codes={CODE: LABEL},
                      contact_email="ops@example.test",
                      anthropic_client_factory=_recipe_client if inference else None)


def _form(**over) -> dict[str, str]:
    form = {"invite_code": CODE, "machine_alias": "TestPump", "rpm": str(RPM),
            "iso_group": "2", "iso_support": "rigid", "machine_type": "motor", "bearing_model": "6206"}
    form.update({k: str(v) for k, v in over.items()})
    return form


def _post(client: TestClient, path: Path, ctype: str):
    with open(path, "rb") as handle:
        return client.post("/api/jobs", files={"file": (path.name, handle, ctype)},
                           data=_form())


def _await_pause(client: TestClient, job_id: str, *, timeout_s: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout_s
    data: dict = {}
    while time.monotonic() < deadline:
        polled = _status_once(client, job_id)
        if polled is None:
            continue
        data = polled
        if data["state"] not in ("queued", "running"):
            return data
        time.sleep(0.1)
    raise AssertionError(f"job never paused or finished: {data}")


@pytest.fixture(scope="module")
def corpus_dir(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("corpus_terminal")
    write_corpus(directory)
    return directory


# ══════════════════════════════════════════════════════════════════════════
# §2 · The terminal-state guarantee — the six reachable raise sites
# ══════════════════════════════════════════════════════════════════════════
def _boom(*_a, **_k):
    raise RuntimeError("S7 test injection")


def _wrap_resume(monkeypatch, mutate):
    """Sites 4-6 fire inside `_resume_confirmed`, i.e. AFTER the analyst has
    confirmed an interpretation. Corrupt exactly the input the site reads, then
    call the real function, so the raise happens at the real site on the real
    path — not at a stub standing in for it."""
    original = app_module._resume_confirmed

    def wrapper(job, state):
        mutate(job)
        return original(job, state)

    monkeypatch.setattr(app_module, "_resume_confirmed", wrapper)


def _corrupt_recipe(job: Job) -> None:
    for plan in job.pending["channels"]:
        if plan.get("recipe"):
            plan["recipe"] = "{not-json"


# (id, needs_confirm, patch) — the same six the evidence runner drives.
_SITES = [
    ("structure_fingerprint", False,
     lambda mp, app: mp.setattr(app_module, "structure_fingerprint", _boom)),
    ("cached_recipe", False,
     lambda mp, app: mp.setattr(app.state.vib, "cached_recipe", _boom)),
    ("interpretation_payload", False,
     lambda mp, app: mp.setattr(app_module, "_interpretation_payload", _boom)),
    ("float_rpm_after_confirm", True,
     lambda mp, app: _wrap_resume(mp, lambda job: job.pending["form"].pop("rpm", None))),
    ("parse_recipe_after_confirm", True,
     lambda mp, app: _wrap_resume(mp, _corrupt_recipe)),
    ("verify_case_after_confirm", True,
     lambda mp, app: mp.setattr(app_module, "verify_case", _boom)),
]


class TestEveryRaiseSiteReachesATerminalState:
    """A1, A3, A4, E2 — and B1 for the server-side half of the taxonomy.

    The claim under test is what an ANALYST can see, so every state is read
    through `GET /api/jobs/{id}` — the browser's own endpoint — rather than off
    the registry object.
    """

    @pytest.mark.parametrize("site,needs_confirm,patch", _SITES,
                             ids=[s[0] for s in _SITES])
    def test_the_job_ends_in_error_not_in_a_spinner(
            self, site, needs_confirm, patch, corpus_dir, monkeypatch, caplog):
        txt = corpus_dir / _ITEM.name
        app = _app()
        with caplog.at_level(logging.DEBUG, logger="vib_agent.webapp"), \
                caplog.at_level(logging.DEBUG, logger="asyncio"), \
                TestClient(app) as client:
            before = app.state.vib.pending_job_count()
            if needs_confirm:
                # The site is downstream of the pause, so the job must get there
                # first — unpatched — and be confirmed by a human before the
                # injection can fire. That ordering is the point: sites 4-6 spend
                # an invite code and take a decision from the analyst BEFORE they
                # fail.
                job_id = _post(client, txt, "text/plain").json()["job_id"]
                assert _await_pause(client, job_id)["state"] == "awaiting_confirm"
                patch(monkeypatch, app)
                assert client.post(f"/api/jobs/{job_id}/confirm", json={}).status_code == 202
            else:
                patch(monkeypatch, app)
                job_id = _post(client, txt, "text/plain").json()["job_id"]

            data = _poll_until_terminal(client, job_id, timeout_s=30.0)

            # A1 / E2 — terminal, and terminal in the state the client can act on.
            # Before S7 this was `running` (or `queued`) for as long as anyone
            # cared to watch, and `stepCard` was the last thing an analyst ever saw.
            assert data["state"] == "error", f"{site} left the job non-terminal: {data}"
            # B1 — whose problem it was. The file was fine; this one is ours.
            assert data["failure_kind"] == "server_error"
            assert data["retryable"] is True
            assert data["safe_message"]
            # the finer code is the operator's, and stays out of the payload
            assert "error_code" not in data

            # A3 — the report endpoint takes the `error` branch, not the
            # "still being prepared" one: a failed job has no report and never will.
            pdf = client.get(f"/api/jobs/{job_id}/report.pdf")
            assert pdf.status_code == 409
            assert "could not be completed" in pdf.text
            assert "still being prepared" not in pdf.text

            # A4 — the slot is given back.
            assert app.state.vib.pending_job_count() == before

        messages = [record.getMessage() for record in caplog.records]
        outcomes = [m for m in messages if f"job={job_id}" in m and " outcome=" in m]

        # A2 — exactly one outcome line, and it is the CLEAN one: no traceback,
        # no filename, no machine alias, no IP. The v6-A logging rule is unchanged
        # by S7; the diagnosis goes in a separate WARNING.
        assert len(outcomes) == 1, f"expected one outcome line, got {outcomes}"
        assert "outcome=error" in outcomes[0] and "fail=internal_error" in outcomes[0]
        assert f"code={LABEL}" in outcomes[0]
        for banned in ("Traceback", "TestPump", _ITEM.name, "RuntimeError"):
            assert banned not in outcomes[0], f"the outcome line leaked {banned!r}"

        # ...and the traceback is not simply dropped in the name of that rule.
        # Our own stack frames are not request data, and they are the most useful
        # thing the operator will have.
        failures = [m for m in messages if "internal_failure" in m]
        assert len(failures) == 1, f"no internal_failure WARNING for {site}"
        assert "Traceback" in failures[0] and job_id in failures[0]

        # A2 — asyncio's orphan record carried no job id, no code label and no
        # duration, and was on the wrong logger. It must not appear at all now.
        assert not [m for m in messages if "never retrieved" in m]


class TestABadUploadIsStillTheAnalystsToFix:
    """The other half of B1: `server_error` must not swallow the case where the
    file genuinely was the problem, or the card would stop giving the one piece
    of advice that helps."""

    def test_an_unreadable_upload_is_bad_upload_and_not_retryable(self, tmp_path):
        """The model must actually EXAMINE the layout and reject it for this to
        be the analyst's problem. This test originally ran with no inference
        client at all -- which S7-ACCEPT F-5 reclassified as an OUTAGE
        (`inference_unavailable`, server_error): with the call never made,
        nothing was learned about the file, so blaming it was a guess. The
        genuine bad-upload path is the model replying UNKNOWN on both attempts
        (inference.py: `unavailable=False` on the final raise)."""
        bad = tmp_path / "notes.csv"
        bad.write_text("this is prose, not a spectrum\nand neither is this\n")
        unknown = FakeMessage(content=[FakeTextBlock(text="UNKNOWN")],
                              stop_reason="end_turn")
        app = create_app(
            webapp_cfg=_webapp_cfg(), invite_codes={CODE: LABEL},
            contact_email="ops@example.test",
            anthropic_client_factory=lambda: FakeAnthropicClient(
                responses=[unknown, unknown]))
        with TestClient(app) as client:
            job_id = _post(client, bad, "text/csv").json()["job_id"]
            data = _poll_until_terminal(client, job_id, timeout_s=30.0)
        assert data["state"] == "error"
        assert data["failure_kind"] == "bad_upload"
        assert data["retryable"] is False


# ══════════════════════════════════════════════════════════════════════════
# §2 (A7) · Site 7 — the pre-task leak
# ══════════════════════════════════════════════════════════════════════════
class TestUploadWriteFailureLeavesNothingBehind:
    """A7. `registry.create` runs six lines before the write, and `create()` is
    what produces the directory the write targets — so the write cannot simply
    move ahead of it. Before S7 an `OSError` here left a `queued` entry and its
    `mkdtemp()` dir behind for the full TTL, counted against `queue_depth_max`:
    twenty of them and the service answers "Server is busy" to everybody for an
    hour, with the wrong cause."""

    @pytest.mark.parametrize("errno_code,label", [(28, "ENOSPC"), (13, "EACCES")])
    def test_the_entry_is_dropped_and_the_response_says_storage(
            self, errno_code, label, tmp_path, monkeypatch, caplog):
        # EACCES matters as much as ENOSPC and is likelier: the disk guard above
        # already refuses below 500 MB free and one upload is capped at 25 MiB,
        # so a plain full disk is the LEAST likely way to reach this line. A
        # read-only remount or a permissions change on the scratch dir is not.
        spec = tmp_path / "spec.csv"
        _spectrum_csv(spec)
        real_write = Path.write_bytes

        def refuse(self, data):
            if self.name.startswith("upload"):
                raise OSError(errno_code, f"injected {label}")
            return real_write(self, data)

        monkeypatch.setattr(Path, "write_bytes", refuse)
        app = _app(inference=False)
        with caplog.at_level(logging.DEBUG, logger="vib_agent.webapp"), \
                TestClient(app) as client:
            before = app.state.vib.pending_job_count()
            response = _post(client, spec, "text/csv")
            after = app.state.vib.pending_job_count()
            left_behind = app.state.vib.registry.all_jobs()

        # not a bare 500 — and the same words the disk guard uses, because to the
        # analyst this is the same condition arriving a few lines later.
        assert response.status_code == 503
        assert "storage is temporarily full" in response.json()["detail"]
        assert after == before == 0
        assert left_behind == [], f"a phantom job survived: {left_behind}"

        messages = [record.getMessage() for record in caplog.records]
        outcomes = [m for m in messages if " outcome=error" in m]
        assert len(outcomes) == 1 and "fail=upload_write_failed" in outcomes[0]
        # the operator gets the errno; the analyst never does
        assert any(f"upload_write_failed code={LABEL} errno={errno_code}" in m
                   for m in messages)


# ══════════════════════════════════════════════════════════════════════════
# §4 · The sweep — an expiry and a job that stopped without finishing
# ══════════════════════════════════════════════════════════════════════════
def _age_past_ttl(app, job: Job) -> None:
    """Exactly what 60 real minutes would do: `created_at` is `time.monotonic()`
    and `is_expired` compares against `_ttl_seconds`. No patched clock, no change
    to the registry's own logic."""
    job.created_at -= app.state.vib.registry._ttl_seconds + 1.0


class TestSweepDistinguishesStrandedFromExpired:
    """C1–C3. Before S7 `sweep_expired` filtered on age alone: a crashed job and
    a finished one that aged out both ended as `404`, the client renders
    `expiredCard()` for a 404 and a 410 alike, and that card says the report was
    *kept for 60 minutes after it finished, then deleted* — of which, for the
    crashed job, every clause is false."""

    def test_a_stranded_job_survives_the_sweep_and_says_so(self, tmp_path, caplog):
        app = _app(inference=False)
        with caplog.at_level(logging.DEBUG, logger="vib_agent.webapp"), \
                TestClient(app) as client:
            registry = app.state.vib.registry
            stranded = registry.create(code_label=LABEL, file_size=1)   # still `queued`
            finished = registry.create(code_label=LABEL, file_size=1)
            finished.state = "done"
            job_dir = stranded.job_dir
            _age_past_ttl(app, stranded)
            _age_past_ttl(app, finished)

            removed, still_live = registry.sweep_expired()
            for job in still_live:                    # what the loop does with them
                mark_error(job, "stopped without finishing", code="timeout")

            # C1 — no longer indistinguishable.
            assert finished.id in removed and stranded.id not in removed
            crashed_status = client.get(f"/api/jobs/{stranded.id}")
            expired_status = client.get(f"/api/jobs/{finished.id}")
            assert crashed_status.status_code == 200
            assert crashed_status.json()["state"] == "error"
            assert crashed_status.json()["failure_kind"] == "server_error"
            assert expired_status.status_code == 404

            # C2 / the ruling — the sweeper never touches a live job's files.
            # `app.py`'s lazy purge documents the invariant ("a running job's dir
            # must never be deleted out from under the worker"); this method used
            # to be the one place that broke it.
            assert job_dir.exists()

    def test_an_expired_awaiting_confirm_job_keeps_its_schedule_and_changes_lane(self):
        """C3, REVERSED IN PART BY SESSION FLIP-1, and deliberately so.

        S7 wrote this case as *"the case the fix must NOT change"* and put an
        expired `awaiting_confirm` job in `removed` — files purged, entry
        dropped, later polls 404. That reasoning predates BILL-1. A credit is
        held when a job is ACCEPTED, and `Job.credit_hook` fires from exactly two
        places, `worker.mark_error` and `worker._become_terminal`. The `removed`
        branch reaches neither, so an analyst who closed the tab on a confirm
        card was charged for no report and refunded nothing, with one
        `ttl_sweep expired=1` to show for it (LEGAL-1 F-1).

        What C3 was actually protecting is the SCHEDULE — *"its upload must still
        be deleted on the same schedule as everything else"* — and that half is
        asserted here unchanged. What moves is the lane: the job comes back as
        stranded so `_strand_job` can take it through `mark_error`.

        S7's own argument now cuts this way. Its complaint about 404 was that the
        browser draws `expiredCard()`, which says a report *"was kept for 60
        minutes after it finished, then deleted"* — and for an abandoned confirm
        card nothing finished and no report ever existed, so every clause of that
        card is as false here as it was for a crashed job.
        """
        app = _app(inference=False)
        with TestClient(app) as client:
            registry = app.state.vib.registry
            job = registry.create(code_label=LABEL, file_size=1)
            job.state = "awaiting_confirm"
            job_dir = job.job_dir
            _age_past_ttl(app, job)

            removed, still_live = registry.sweep_expired()

            # The lane, changed.
            assert job.id not in removed and still_live == [job]
            # The schedule, unchanged — this is C3's real content. A true strand
            # keeps its directory for another TTL because a worker thread may
            # still be writing into it; this job has no worker, and its upload
            # goes at the same moment it always did.
            assert not job_dir.exists()
            # The entry survives, which is what gives the transition somewhere to
            # be recorded. `tests/test_flip1_confirm_credit.py` drives the loop
            # and asserts the credit comes back.
            assert client.get(f"/api/jobs/{job.id}").status_code == 200


class TestMaxRuntimeTimeout:
    """The timeout half of the ruling: cancel the worker task FIRST, then mark
    `error(timeout)`. Not cosmetic — the other order was measured. The sweeper
    marked a stranded job, its still-live worker then finished, overwrote the
    state with `degraded`, recreated the very directory the sweeper had deleted,
    and wrote a report into it that no registry entry pointed at any more."""

    def test_a_hung_job_is_stopped_and_cannot_be_un_failed(self, tmp_path, caplog):
        import threading

        spec = tmp_path / "spec.csv"
        _spectrum_csv(spec)
        entered, release = threading.Event(), threading.Event()
        real_process_job = app_module.process_job

        def parked(job, case, **kwargs):
            entered.set()
            release.wait(timeout=30.0)
            return real_process_job(job, case, **kwargs)

        app = _app(inference=False, sweep_interval_s=0.05, job_max_runtime_s=0.5)
        try:
            app_module.process_job = parked
            with caplog.at_level(logging.DEBUG, logger="vib_agent.webapp"), \
                    TestClient(app) as client:
                job_id = _post(client, spec, "text/csv").json()["job_id"]
                assert entered.wait(timeout=30.0), "worker never started"
                job = app.state.vib.registry.get(job_id)
                job_dir = job.job_dir

                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline and job.state not in TERMINAL_STATES:
                    time.sleep(0.05)

                assert job.state == "error" and job.error_code == "timeout"
                body = client.get(f"/api/jobs/{job_id}").json()
                assert body["state"] == "error"
                assert body["failure_kind"] == "server_error" and body["retryable"] is True
                # the ruling: its files are NOT deleted while a worker holds them
                assert job_dir.exists()

                # Now let the abandoned worker finish. It cannot be interrupted —
                # `asyncio.to_thread` gives no way to stop a running thread — so
                # the guarantee has to be that it can no longer change the answer.
                release.set()
                time.sleep(1.5)
                assert job.state == "error", "a late worker un-failed a timed-out job"
        finally:
            app_module.process_job = real_process_job

        # C4 — one line per stranded job, and only one, however late the worker is.
        outcomes = [r.getMessage() for r in caplog.records
                    if f"job={job_id}" in r.getMessage() and " outcome=" in r.getMessage()]
        assert len(outcomes) == 1, f"expected one outcome line, got {outcomes}"
        assert "outcome=error" in outcomes[0] and "fail=timeout" in outcomes[0]

    def test_a_long_think_on_the_confirm_card_is_not_runtime_either(self, corpus_dir):
        """The subtle half of the same rule. The FIRST background task stamps the
        clock when it runs the inference pass; the job then pauses, possibly for
        a long time, waiting for a human. If that stamp survived into the
        `queued` state the confirm handler puts the job back into, a job
        confirmed after a twenty-minute think would be killed for overrunning
        while it was still waiting for a worker slot."""
        txt = corpus_dir / _ITEM.name
        app = _app()
        with TestClient(app) as client:
            job_id = _post(client, txt, "text/plain").json()["job_id"]
            assert _await_pause(client, job_id)["state"] == "awaiting_confirm"
            job = app.state.vib.registry.get(job_id)
            assert job.worker_started_at is not None      # the first pass ran
            job.worker_started_at -= 3600                 # the analyst went to lunch
            assert client.post(f"/api/jobs/{job_id}/confirm", json={}).status_code == 202
            # the stale clock is cleared at the moment the job re-enters `queued`
            assert not app.state.vib.registry.is_overrunning(job)
            assert _poll_until_terminal(client, job_id, timeout_s=30.0)["state"] != "error"

    def test_queue_wait_is_not_runtime(self):
        """The clock starts when a job WINS a worker-semaphore slot, never at
        upload. A job that merely sat behind a full queue must never be killed
        for someone else's slowness — which is why this is `worker_started_at`
        and not `created_at`, and why it is not `started_at` either (that one
        anchors `duration_ms` and must not change meaning)."""
        app = _app(inference=False, job_max_runtime_s=0.01)
        registry = app.state.vib.registry
        job = registry.create(code_label=LABEL, file_size=1)   # queued, never started
        time.sleep(0.05)
        assert registry.is_overrunning(job) is False
        job.worker_started_at = time.monotonic() - 1.0
        assert registry.is_overrunning(job) is True


# ══════════════════════════════════════════════════════════════════════════
# §5 · The two 503 guards leave a record — UXD Q3's precondition
# ══════════════════════════════════════════════════════════════════════════
class TestGuardRefusalsAreLogged:
    """D1–D3. Both 503 branches used to raise in silence, which is why the
    prototype's *"It has been reported"* was an open question rather than a
    statement. It is a statement now."""

    def _post_one(self, client):
        return client.post("/api/jobs", files={"file": ("s.csv", b"freq_hz,amplitude\n1,0.1\n",
                                                        "text/csv")}, data=_form())

    def test_the_disk_guard_logs_once_with_no_request_data(self, monkeypatch, caplog):
        monkeypatch.setattr(app_module.shutil, "disk_usage",
                            lambda _p: types.SimpleNamespace(total=10**9, used=10**9, free=1024))
        with caplog.at_level(logging.WARNING, logger="vib_agent.webapp"):
            with TestClient(_app(inference=False)) as client:
                assert self._post_one(client).status_code == 503
        lines = [r.getMessage() for r in caplog.records if "disk_guard" in r.getMessage()]
        assert len(lines) == 1
        assert "free_mb=0" in lines[0] and "min_mb=500" in lines[0]
        assert f"code={LABEL}" in lines[0] and "outcome=refused" in lines[0]
        # the v6-A rule, unchanged: no addresses, no filename, no machine alias.
        assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", lines[0])
        assert "s.csv" not in lines[0] and "TestPump" not in lines[0]
        assert CODE not in lines[0], "the raw invite code must never be logged"

    def test_the_queue_guard_logs_too(self, caplog):
        app = _app(inference=False, queue_depth_max=1)
        with caplog.at_level(logging.WARNING, logger="vib_agent.webapp"):
            with TestClient(app) as client:
                app.state.vib.registry.create(code_label=LABEL, file_size=1)  # fills the queue
                assert self._post_one(client).status_code == 503
        lines = [r.getMessage() for r in caplog.records if "queue_guard" in r.getMessage()]
        assert len(lines) == 1
        assert "pending=1 max=1" in lines[0] and "outcome=refused" in lines[0]

    def test_repeat_refusals_inside_one_window_produce_one_line(self, monkeypatch, caplog):
        """A full disk refuses EVERY request. At 60 posts/hour/IP across N
        clients this line would otherwise dominate the log — so it is emitted at
        most once per window, carrying the count of the ones it stands for."""
        monkeypatch.setattr(app_module.shutil, "disk_usage",
                            lambda _p: types.SimpleNamespace(total=10**9, used=10**9, free=1024))
        app = _app(inference=False)
        with caplog.at_level(logging.WARNING, logger="vib_agent.webapp"):
            with TestClient(app) as client:
                for _ in range(10):
                    assert self._post_one(client).status_code == 503
        lines = [r.getMessage() for r in caplog.records if "disk_guard" in r.getMessage()]
        assert len(lines) == 1, f"repeat suppression failed: {len(lines)} lines"
        assert "suppressed" not in lines[0]      # the first line stands for itself
        # the nine it stands for are counted, not lost: the next window's line
        # carries them as `suppressed=9`.
        assert app.state.vib.guard_log["disk_guard"][1] == 9


# ══════════════════════════════════════════════════════════════════════════
# §3 · The taxonomy itself
# ══════════════════════════════════════════════════════════════════════════
class TestTheTaxonomy:
    def test_the_declared_vocabulary_and_the_table_cannot_drift(self):
        """`ErrorCode` declares the vocabulary; `ERROR_TAXONOMY` carries what each
        code MEANS. A code in one and not the other is either an unusable literal
        or a category with no name."""
        from typing import get_args

        from vib_agent.webapp.jobs import ErrorCode
        assert set(get_args(ErrorCode)) == set(ERROR_TAXONOMY)

    def test_every_row_is_complete_and_the_set_is_small(self):
        assert len(ERROR_TAXONOMY) <= 8, "the taxonomy is meant to stay small"
        for code, row in ERROR_TAXONOMY.items():
            assert code.islower() and " " not in code
            assert row.kind in ("bad_upload", "server_error")
            assert isinstance(row.retryable, bool), f"{code} is not flagged retryable or not"
            assert row.meaning and not row.meaning.endswith("."), code
            # PURGE-SYNC: the third thing a row has to say — whether a job
            # failing in this category still has a writer holding its directory,
            # and therefore whether its files can go at the moment of failure.
            assert isinstance(row.purge_at_transition, bool), (
                f"{code} does not say whether it purges at its transition")
        # Only the strand path defers, and only because its abandoned thread
        # cannot be stopped. Spelled out here rather than left to the default so
        # that adding a ninth code with `purge_at_transition=False` has to
        # explain itself. See tests/test_purge_order.py for the behaviour.
        assert {c for c, r in ERROR_TAXONOMY.items() if not r.purge_at_transition} == {"timeout"}
        # user-meaningful categories, never exception class names
        for code in ERROR_TAXONOMY:
            assert "error" not in code or code == "internal_error"
            assert not code.endswith("Error")

    def test_mark_error_refuses_a_code_that_is_not_in_the_taxonomy(self):
        job = Job(id="x")
        with pytest.raises(ValueError, match="unknown error code"):
            mark_error(job, "boom", code="whatever_went_wrong")
        assert job.state == "queued", "a rejected code must not half-mark the job"

    def test_mark_error_will_not_overwrite_a_terminal_state(self):
        """`_finish` runs on completion; a failure raised after a job legitimately
        reached `done` must not rewrite a good outcome into an error."""
        job = Job(id="x", state="done")
        assert mark_error(job, "too late", code="internal_error") is False
        assert job.state == "done" and job.error_code is None and job.safe_message is None

    def test_a_terminal_state_is_sticky_on_the_job_itself(self):
        """The invariant lives on the field, not at the eight assignment sites:
        the site that would break it runs on a thread nobody is holding any more,
        and there is no useful place there to decide the question."""
        job = Job(id="x")
        job.state = "running"           # non-terminal transitions are unaffected
        assert job.state == "running"
        job.state = "error"
        job.state = "degraded"          # a late worker, finishing its own work
        assert job.state == "error"

    def test_every_error_path_in_the_webapp_names_its_category(self):
        """B4. `mark_error`'s `code` is keyword-only and required, so a new error
        path cannot be added without naming its category — but a direct
        `job.state = "error"` would sidestep that, so scan for one."""
        for name in ("app.py", "worker.py"):
            source = (Path(app_module.__file__).parent / name).read_text()
            # `(?<!def )` so the definition itself is not mistaken for a call site.
            for match in re.finditer(r"(?<!def )mark_error\((.*?)\)\n", source, re.S):
                assert "code=" in match.group(1), f"{name}: mark_error without a code"
            # `mark_error`'s own body is the ONE place allowed to assign it --
            # that is what makes it the single enforcement point -- so cut the
            # function out before scanning for anyone else doing it.
            without_helper = re.sub(r"^def mark_error\(.*?(?=^\S)", "", source, flags=re.S | re.M)
            direct = re.findall(r'^\s*job\.state = "error"', without_helper, re.M)
            assert not direct, (
                f'{name}: sets state="error" directly, bypassing mark_error and the taxonomy')

    def test_the_terminal_set_is_named_once(self):
        assert TERMINAL_STATES == frozenset({"done", "gate_fail", "degraded", "error"})


# ══════════════════════════════════════════════════════════════════════════
# §6 · The UX-side acceptance test
# ══════════════════════════════════════════════════════════════════════════
class TestTheUnknownFrameIsGoneAndWasNeverBuilt:
    """E1, in the two parts the evidence session found it needed.

    (a) `design/webapp_v2_proto/screens/processing.html` no longer contains the
    `p-unknown` article. Its own footer named the exit condition — once a failure
    kind distinguishes "the analysis failed" from "your file was bad" and the
    sweep stops purging non-terminal jobs, the frame is deleted and the ordinary
    error card shown instead.

    (b) `app.js` still contains no long-running branch. This is the sharper half:
    `p-unknown` was never IMPLEMENTED. The live client had no elapsed counter and
    no UNKNOWN row — a stuck job just kept `stepCard` and `setTimeout(poll)`
    forever, because `POLL_MAX_FAILURES` counts poll failures, not stuck states.
    So the failure mode is not leaving the frame in, it is SHIPPING it: a
    permanent "we cannot tell whether this is alive" card would mean a terminal
    state was available and the client chose to hedge instead.
    """

    def _proto(self) -> Path:
        return (Path(__file__).resolve().parents[1]
                / "design/webapp_v2_proto/screens/processing.html")

    def _app_js(self) -> Path:
        return Path(app_module.__file__).parent / "static/app.js"


    def test_no_design_page_still_links_to_it(self):
        proto_root = self._proto().parents[1]
        dangling = [str(f) for f in proto_root.rglob("*")
                    if f.suffix in (".html", ".md") and f != self._proto()
                    and "#p-unknown" in f.read_text()]
        assert not dangling, f"dead anchor to the deleted frame: {dangling}"

    def test_the_client_never_grew_the_long_running_branch(self):
        source = self._app_js().read_text()
        for banned in ("UNKNOWN", "ran long", "long-running"):
            assert banned not in source, (
                f"app.js gained {banned!r} — a permanent 'we cannot tell' card is S7 "
                "not having landed, not S7 shipping a nicer spinner")

    def test_the_elapsed_clock_is_display_only_and_never_selects_a_card(self):
        """UX-WIRE U1 narrowed this pin, deliberately, and here is the reason.

        S7 banned the literal token `elapsed` from app.js because the frame it
        was guarding against — `p-unknown`, "we cannot tell whether this is
        alive" — had an elapsed counter as its distinguishing feature. The word
        was standing in for the property.

        U1 then added an elapsed clock for the opposite reason: it is one of the
        three honest things that REPLACE the fake percentage bar (an
        indeterminate sweep, the state in words, and a client-side clock
        measured from the moment this page POSTed). It shows from second zero on
        the ordinary working card. It is not a hedge; it is the one honest
        number the browser actually holds.

        So the token ban is replaced by the property it was protecting: the
        elapsed value may be DISPLAYED and must never be BRANCHED ON. The moment
        a threshold on it picks a different card, that is the long-running frame
        arriving under another name, and this test fails."""
        source = self._app_js().read_text()
        touching = [ln.strip() for ln in source.splitlines()
                    if ("runStartMs" in ln or "elapsedText" in ln) and not ln.strip().startswith("//")]
        assert touching, "the elapsed clock vanished — U1 §5.1 requires it"
        for line in touching:
            assert "show(" not in line, f"the elapsed value selects a card: {line}"
            assert not re.search(r"(runStartMs|elapsed\w*)\s*[<>]", line), (
                f"the elapsed value is compared against a threshold: {line}")
        # ...and it is measured from this page's own POST, not from a new field
        # invented on the wire.
        assert "function markRunStart() { runStartMs = Date.now(); }" in source

    def test_the_client_branches_the_error_card_on_whose_problem_it_was(self):
        """B2. The CSV/XLSX template links are the `bad_upload` advice and must
        not be rendered for a crash of ours on a good file."""
        source = self._app_js().read_text()
        start = source.index("function errorCard(")
        body = source[start:source.index("\n}", start)]
        assert "failure_kind" not in body      # the branch is on the parameter
        assert "server_error" in body
        server_half = body[body.index("const advice"):body.index("</p>`\n    : `")]
        assert "/sample.csv" not in server_half and "/sample.xlsx" not in server_half
        # ...and the live poll actually passes it through
        assert "errorCard(data.safe_message, data.failure_kind, data.retryable)" in source


# ══════════════════════════════════════════════════════════════════════════
# §6 · S7-ACCEPT — the three acceptance findings fixed at the app layer
#
# outputs/S7_ACCEPTANCE.md measured these on the live server. F-1 (the healthy
# control's imbalance commit) is pdm_core and latched — deliberately NOT pinned
# here; the acceptance doc carries it.
# ══════════════════════════════════════════════════════════════════════════
class TestServerFailuresAreRefunded:
    """F8, decided by the operator: a failure on OUR side does not spend the
    analyst's daily allowance; a `bad_upload` still does.

    The counter is spent at ACCEPTANCE (app.py, `check_and_record`), before any
    work happens — the right moment to charge for a job that is going to run,
    and the wrong moment to have charged for one that then crashed inside our
    own service. From where the analyst sits those are indistinguishable: they
    uploaded a good file and got nothing."""

    def _limits(self, app) -> tuple[int, int]:
        rl = app.state.vib.rate_limiter
        return rl._per_code["engineer-1"], rl._global

    def test_a_server_error_gives_the_job_back(self):
        app = _app()
        job = app.state.vib.registry.create(code_label="engineer-1", file_size=1)
        app.state.vib.rate_limiter.check_and_record("engineer-1")
        job.refund_hook = partial(app.state.vib.rate_limiter.refund, "engineer-1")
        assert self._limits(app) == (1, 1)

        assert mark_error(job, "boom", code="internal_error") is True
        assert job.failure_kind == "server_error"
        assert self._limits(app) == (0, 0), "a crash of ours still spent the allowance"

    def test_a_bad_upload_still_counts(self):
        """The service did the work it was asked to do and the answer was
        "this file cannot be read". That is a real result, not a failure of
        ours, and refunding it would make the daily cap unenforceable against
        anyone willing to upload garbage."""
        app = _app()
        job = app.state.vib.registry.create(code_label="engineer-1", file_size=1)
        app.state.vib.rate_limiter.check_and_record("engineer-1")
        job.refund_hook = partial(app.state.vib.rate_limiter.refund, "engineer-1")

        assert mark_error(job, "unreadable", code="upload_unreadable") is True
        assert job.failure_kind == "bad_upload"
        assert self._limits(app) == (1, 1), "a bad upload was wrongly refunded"

    def test_a_late_worker_cannot_refund_a_second_time(self):
        """Idempotent BY CONSTRUCTION rather than by bookkeeping: `mark_error`
        returns early on an already-terminal job, so the abandoned thread that
        S7 measured coming back after the sweep marked its job cannot hand out
        a second free job on the way past."""
        app = _app()
        job = app.state.vib.registry.create(code_label="engineer-1", file_size=1)
        for _ in range(2):
            app.state.vib.rate_limiter.check_and_record("engineer-1")
        job.refund_hook = partial(app.state.vib.rate_limiter.refund, "engineer-1")

        assert mark_error(job, "boom", code="internal_error") is True
        assert self._limits(app) == (1, 1)
        for _ in range(3):
            assert mark_error(job, "boom again", code="timeout") is False
        assert self._limits(app) == (1, 1), "a second refund escaped"

    def test_a_refund_never_pushes_a_counter_below_zero(self):
        """What makes it safe across the local-day rollover: a job accepted at
        23:59 and failed at 00:01 would otherwise refund into a FRESH day's
        counter and hand out a job that was never charged. After the rollover
        the counters are zero and the floor leaves them there — the analyst
        keeps the new day's full allowance either way, which is right, because
        the day they lost the job to is over."""
        rl = RateLimiter(per_code_daily_jobs=5, global_daily_jobs=9)
        rl.refund("engineer-1")                       # nothing was ever spent
        assert rl._per_code["engineer-1"] == 0 and rl._global == 0
        rl.check_and_record("engineer-1")
        for _ in range(4):
            rl.refund("engineer-1")
        assert rl._per_code["engineer-1"] == 0 and rl._global == 0

    def test_the_hook_is_actually_attached_by_the_upload_path(self, tmp_path):
        """The tests above attach `refund_hook` by hand, which proves the
        MECHANISM and not the WIRING. This drives a real upload through the API
        so a missing line in app.py cannot pass everything else.

        A garbage file is a `bad_upload`, so the correct end state is that the
        allowance stays SPENT — and that the hook is nonetheless present, ready
        for the failure that would have been ours."""
        garbage = tmp_path / "noise.txt"
        garbage.write_bytes(bytes(range(256)) * 16)
        app = _app()
        with TestClient(app) as client:
            job_id = _post(client, garbage, "text/plain").json()["job_id"]
            data = _poll_until_terminal(client, job_id, timeout_s=30.0)
            assert data["state"] == "error" and data["failure_kind"] == "bad_upload"

            job = app.state.vib.registry.get(job_id)
            assert job.refund_hook is not None, "app.py never attached the refund hook"
            rl = app.state.vib.rate_limiter
            assert rl._global == 1, "a bad upload was refunded by the live path"

            # ...and the hook it attached is bound to THIS job's code label
            job.refund_hook()
            assert rl._global == 0

    def test_the_card_states_the_policy_in_the_analysts_words(self):
        """The analyst can otherwise only discover the refund by counting their
        remaining uploads, and the point of the refund is that they should not
        have to."""
        js = (Path(app_module.__file__).parent / "static/app.js").read_text()
        card = js.partition("function errorCard(")[2].partition("\n}")[0]
        server_half = card[card.index("const advice"):card.index("</p>`\n    : `")]
        assert "not been counted against your daily limit" in server_half
        # ...and the bad_upload half must NOT claim it
        bad_half = card[card.index("</p>`\n    : `"):]
        assert "daily limit" not in bad_half


class TestErrorJobsKeepNoFiles:
    """F-3. /privacy: "Your uploaded file is unlinked as soon as it has been
    read", and working files go "when the analysis completes". A rejected
    upload's analysis IS complete — in failure — so its files must go then, not
    a TTL later. (U4 reworded that page; the behaviour asserted here is
    unchanged, and it is what makes the new wording true.) The registry entry survives so the error card stays
    queryable, and report.pdf keeps its honest 409 (the `error` branch now wins
    over `purged`, or the purge would flip a truthful "could not be completed"
    into a false "has been deleted")."""

    def test_a_rejected_upload_is_deleted_at_the_moment_of_failure(self, tmp_path):
        garbage = tmp_path / "noise.txt"
        garbage.write_bytes(bytes(range(256)) * 16)  # unreadable as text
        app = _app()
        with TestClient(app) as client:
            job_id = _post(client, garbage, "text/plain").json()["job_id"]
            data = _poll_until_terminal(client, job_id, timeout_s=30.0)
            assert data["state"] == "error"
            assert data["failure_kind"] == "bad_upload"

            job = app.state.vib.registry.get(job_id)
            assert job.purged, "the error job's files were not purged"
            assert job.job_dir is None or not any(job.job_dir.glob("*")), (
                "the analyst's raw upload survived its own rejection")

            # ...and purging did not eat the honest answers.
            assert _status_once(client, job_id)["state"] == "error"
            pdf = client.get(f"/api/jobs/{job_id}/report.pdf")
            assert pdf.status_code == 409
            assert "could not be completed" in pdf.text
            assert "has been deleted" not in pdf.text

    def test_internal_error_purges_too(self, corpus_dir, monkeypatch):
        txt = corpus_dir / _ITEM.name
        app = _app()
        with TestClient(app) as client:
            monkeypatch.setattr(app_module, "structure_fingerprint", _boom)
            job_id = _post(client, txt, "text/plain").json()["job_id"]
            assert _poll_until_terminal(client, job_id, timeout_s=30.0)["state"] == "error"
            job = app.state.vib.registry.get(job_id)
            assert job.purged
            assert job.job_dir is None or not any(job.job_dir.glob("*"))

    def test_the_pause_is_not_a_failure_and_keeps_its_files(self, corpus_dir):
        """The uploads MUST survive `awaiting_confirm` — the recipe is a
        hypothesis and the confirmed parse still needs the bytes."""
        txt = corpus_dir / _ITEM.name
        app = _app()
        with TestClient(app) as client:
            job_id = _post(client, txt, "text/plain").json()["job_id"]
            assert _await_pause(client, job_id)["state"] == "awaiting_confirm"
            job = app.state.vib.registry.get(job_id)
            assert not job.purged and any(job.job_dir.glob("upload*"))


class TestJobDirCreateFailureIsTheSame503:
    """F-4. `registry.create`'s mkdtemp() is the FIRST filesystem touch, and a
    permissions change on the scratch directory fails there — measured on
    acceptance as a bare 500 because S7's OSError guard started one line below
    it. Same condition, same 503, same storage message; and since mkdtemp
    raises before the entry joins the registry, nothing exists to leak."""

    def test_an_unwritable_scratch_dir_gets_the_storage_503(self, tmp_path, caplog):
        app = _app()
        with caplog.at_level(logging.DEBUG, logger="vib_agent.webapp"), \
                TestClient(app) as client:
            def denied(**_k):
                raise PermissionError(13, "Permission denied")
            app.state.vib.registry.create = denied
            before = len(app.state.vib.registry.all_jobs())

            spec = tmp_path / "spec.csv"
            _spectrum_csv(spec)
            with open(spec, "rb") as handle:
                response = client.post(
                    "/api/jobs", files={"file": ("spec.csv", handle, "text/csv")},
                    data=_form())

            assert response.status_code == 503
            assert "storage" in response.json()["detail"].lower()
            assert len(app.state.vib.registry.all_jobs()) == before
            assert app.state.vib.pending_job_count() == 0
        warnings = [r.getMessage() for r in caplog.records
                    if "upload_write_failed" in r.getMessage()]
        assert any(f"code={LABEL}" in w and "errno=13" in w for w in warnings)


class TestAnOutageIsNotTheAnalystsFault:
    """F-5. With the inference call unavailable (no key, or budget exhausted) a
    good file on the .txt lane used to be marked `interpretation_failed` —
    bad_upload, don't retry — telling the analyst their format was unsupported
    and that retrying was pointless, both false. The outage is OURS and a retry
    after it genuinely works: `inference_unavailable`, server_error, retryable."""

    @staticmethod
    def _no_client():
        raise RuntimeError("no API key on this host")

    def _outage_app(self):
        return create_app(webapp_cfg=_webapp_cfg(), invite_codes={CODE: LABEL},
                          contact_email="ops@example.test",
                          anthropic_client_factory=self._no_client)

    def test_no_key_on_the_inference_lane_is_server_error_and_retryable(
            self, corpus_dir, caplog):
        txt = corpus_dir / _ITEM.name
        app = self._outage_app()
        with caplog.at_level(logging.DEBUG, logger="vib_agent.webapp"), \
                TestClient(app) as client:
            job_id = _post(client, txt, "text/plain").json()["job_id"]
            data = _poll_until_terminal(client, job_id, timeout_s=30.0)

            assert data["state"] == "error"
            assert data["failure_kind"] == "server_error", (
                "an outage of OUR service was blamed on the analyst's file")
            assert data["retryable"] is True
            # the message says whose problem it was, not "unsupported format"
            assert "unavailable" in data["safe_message"]

            # F-3 holds on this path too.
            #
            # This assertion is what CI run 33999047017 failed, and the next run
            # on the same commit passed: the purge was a hop behind the response,
            # so a poll could be answered `error` with the upload still on disk.
            # Left exactly as it was — it is a real customer-shaped assertion and
            # PURGE-SYNC's fix is what should make it pass. The pin underneath it
            # is tests/test_purge_order.py, which reads the ORDER directly rather
            # than waiting for a poll to get unlucky.
            job = app.state.vib.registry.get(job_id)
            assert job.purged
        outcomes = [r.getMessage() for r in caplog.records
                    if f"job={job_id}" in r.getMessage() and " outcome=" in r.getMessage()]
        assert len(outcomes) == 1
        assert "fail=inference_unavailable" in outcomes[0]

    def test_a_layout_that_genuinely_resists_reading_is_still_bad_upload(self, tmp_path):
        """The split must not swallow the other half: garbage bytes with a
        WORKING inference client stay `interpretation_failed`."""
        garbage = tmp_path / "noise.txt"
        garbage.write_bytes(bytes(range(256)) * 16)
        app = _app()  # working fake client
        with TestClient(app) as client:
            job_id = _post(client, garbage, "text/plain").json()["job_id"]
            data = _poll_until_terminal(client, job_id, timeout_s=30.0)
            assert data["state"] == "error"
            assert data["failure_kind"] == "bad_upload"
            assert data["retryable"] is False

    def test_an_outage_mid_call_is_the_same_outage(self, corpus_dir, caplog):
        """The mechanism the live no-key server actually exhibits: the SDK
        resolves auth PER-REQUEST, so `anthropic.Anthropic()` constructs fine
        and the failure surfaces inside `messages.create` — wrapped by
        `infer_recipe` into InferenceError. The factory-site guard alone never
        sees it (measured: S7-ACCEPT A6). It must map to the same code."""
        class _DeadMessages:
            @staticmethod
            def create(**_k):
                raise TypeError("Could not resolve authentication method.")

        class _DeadClient:
            messages = _DeadMessages()

        txt = corpus_dir / _ITEM.name
        app = create_app(webapp_cfg=_webapp_cfg(), invite_codes={CODE: LABEL},
                         contact_email="ops@example.test",
                         anthropic_client_factory=_DeadClient)
        with caplog.at_level(logging.DEBUG, logger="vib_agent.webapp"), \
                TestClient(app) as client:
            job_id = _post(client, txt, "text/plain").json()["job_id"]
            data = _poll_until_terminal(client, job_id, timeout_s=30.0)
            assert data["state"] == "error"
            assert data["failure_kind"] == "server_error"
            assert data["retryable"] is True
            assert "unavailable" in data["safe_message"]
        outcomes = [r.getMessage() for r in caplog.records
                    if f"job={job_id}" in r.getMessage() and " outcome=" in r.getMessage()]
        assert len(outcomes) == 1 and "fail=inference_unavailable" in outcomes[0]
