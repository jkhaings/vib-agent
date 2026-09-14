"""Session FLIP-1 — the sandbox's post-kill wait is bounded, on the LIVE upload path.

`outputs/SESSION_SUITELOCK.md` F-1 and `outputs/PROD_READINESS.md`: CPython's
`subprocess.run(cmd, timeout=...)` kills the child and then waits for it with no
bound, and `Popen.__exit__` waits a second time. So both calls in
`webapp/parsing.py` carried the SEC-3 F-2 defect inherited from the stdlib, and
unlike the `report/generate.py` twin the operator ruled filed-not-fixed, these
two are reached by every upload: the hang lands on a worker thread holding a
`worker_concurrency` slot for the life of the process.

The bound and its derivation are REUSED, not re-chosen — `parsing.REAP_TIMEOUT_S`
is `render_proc.REAP_TIMEOUT_S`, asserted below by identity.

ONE CORRECTION TO THE ROW, MEASURED WHILE WRITING THESE PINS. It says
`subprocess.run` does "`proc.kill()` then a bare `proc.communicate()`". On POSIX
it calls `proc.wait()`; `communicate()` is the Windows branch. `wait()` blocks on
REAPING and not on the pipes, so SUITE-LOCK's grandchild reproduction — which
hangs `render_proc.py` — does **not** hang `subprocess.run`: measured at **1.0 s**
on this box. Two consequences run through everything below:

  * the product bounds `wait()`, not `communicate()`. Bounding the pipe drain
    would abandon a healthy child in the grandchild case the stdlib handles
    correctly in a millisecond — worse on the common path to fix the rare one.
    `TestTheGrandchildCaseIsNotARegression` is that guard, with a real child.
  * the case that DOES hang is a child the kernel will not reap (state `UE`/`D`),
    which SEC-3 observed on this box for 2h11m and which SUITE-LOCK stated
    plainly cannot be created by a test. So that one pin doubles `Popen.wait`
    and nothing else: the bound, the `finally`, `_abandon`, the pipe close and
    the exception are all the shipped code. It is labelled as a double rather
    than dressed up as a reproduction.

Four things are pinned, and the third is the one that makes this more than a
timeout change:

  1. a child that cannot be reaped is abandoned at the bound, with the pid in a
     WARNING — and the negative controls are a child that reaps normally and a
     child whose grandchild holds the pipes, neither of which may be abandoned;
  2. an ordinary timeout is UNCHANGED — still `ParseError`/`SampleError`, still
     "we could not read your file", because retrying a file that timed out gives
     the same answer;
  3. an ABANDONED child is not blamed on the analyst's file. It raises
     `SandboxChildAbandoned`, which is deliberately not a `ParseError`, travels
     past `app.py`'s four handlers, and becomes `internal_error` —
     `server_error`, `retryable=True` — at `_run_guarded`. Wire law #5 by
     MAPPING, no ninth taxonomy row, exactly as RENDER-PROC and EMAIL-1 did;
  4. no `subprocess.run` and no `with Popen` survive in the module, and the fds
     they used to close are closed by hand instead.
"""

from __future__ import annotations

import ast
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_webapp_e2e import _poll_until_terminal, _spectrum_csv, _webapp_cfg
from vib_agent.report import render_proc
from vib_agent.webapp import app as app_module
from vib_agent.webapp import parsing
from vib_agent.webapp.jobs import ERROR_TAXONOMY
from vib_agent.webapp.parsing import (
    REAP_TIMEOUT_S,
    ParseError,
    SampleError,
    SandboxChildAbandoned,
    parse_in_subprocess,
    sample_in_subprocess,
)

REPO = Path(__file__).resolve().parents[1]
_MODULE = ast.parse((REPO / "src/vib_agent/webapp/parsing.py").read_text())


def _dotted(node: ast.AST) -> tuple[str, ...] | None:
    """`subprocess.run` -> `("subprocess", "run")`; anything else -> None."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return (node.value.id, node.attr)
    return None


def _calls(tree: ast.AST, dotted: tuple[str, ...]) -> list[ast.Call]:
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and _dotted(n.func) == dotted]

#: Ported verbatim in shape from `tests/test_render_proc.py::_FAKE_CHILD`, and
#: for the reason its comment gives: SIGKILL cannot be caught, blocked or ignored
#: in userspace, so "a child that ignores SIGKILL" cannot be written. `hold=1`
#: spawns a DETACHED GRANDCHILD inheriting fds 1 and 2 — the parent's stdout and
#: stderr pipes — so after our SIGKILL the child is dead (`poll()` is `-9`) and
#: `communicate()` still blocks, because the pipe's write end is open somewhere
#: else. That is the observed symptom, and it needs no `U`-state process.
_FAKE_CHILD = """
import os, subprocess, sys, time

pidfile = os.environ["FAKE_CHILD_PIDFILE"]
if os.environ.get("FAKE_CHILD_HOLD") == "1":
    grandchild = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(90)"],
        stdout=1, stderr=2, start_new_session=True,
    )
    open(pidfile, "w").write(str(grandchild.pid))
else:
    open(pidfile, "w").write("")
time.sleep(90)
"""


@pytest.fixture
def fake_child(tmp_path, monkeypatch):
    """The script, plus the cleanup that puts this box back the way it was.

    `returncode is None` is checked FIRST for the reason `test_render_proc.py`
    states: an abandoned child is usually already reaped, so its pid is free for
    the kernel to reuse, and a blind `os.kill` would signal whatever process now
    owns that number under the operator's own uid.
    """
    script = tmp_path / "fake_child.py"
    script.write_text(_FAKE_CHILD)
    pidfile = tmp_path / "grandchild.pid"
    monkeypatch.setenv("FAKE_CHILD_PIDFILE", str(pidfile))
    parsing._abandoned.clear()
    try:
        yield [sys.executable, str(script)], pidfile
    finally:
        for pid, proc in list(parsing._abandoned.items()):
            if proc.returncode is None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        text = pidfile.read_text().strip() if pidfile.exists() else ""
        if text:
            try:
                os.kill(int(text), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass
        parsing._abandoned.clear()


# ══════════════════════════════════════════════════════════════════════════
# 1 · the bound itself
# ══════════════════════════════════════════════════════════════════════════
class TestTheOneBoundIsShared:

    def test_the_reap_bound_is_render_procs_own_constant(self):
        """One derivation, one number. SESSION_SUITELOCK §2 measured it against
        child startup (0.41–0.47 s) and a reapable child's death (milliseconds);
        a second copy here would be a second thing to get wrong."""
        assert parsing.REAP_TIMEOUT_S is render_proc.REAP_TIMEOUT_S

    def test_the_module_no_longer_calls_subprocess_run(self):
        """The defect is a SHAPE, and this is the shape. A source pin rather than
        a behavioural one because the next person to add a third child here will
        reach for `subprocess.run` — it is the obvious call — and get a red test
        instead of a silent re-import of the hang.

        READ THROUGH THE AST, not as a substring. The module's own docstring
        quotes `subprocess.run(cmd, timeout=...)` while explaining why it is
        gone, and a `not in source` pin fails on the prose that documents the
        fix — which is the kind of pin that gets loosened in a hurry and stops
        meaning anything (`test_bill1_packs.py` records the same lesson about the
        word "reach")."""
        assert _calls(_MODULE, ("subprocess", "run")) == [], (
            "subprocess.run's TimeoutExpired branch does an unbounded proc.wait() "
            "-- that is SEC-3 F-2 inherited from the stdlib"
        )

    def test_the_with_popen_form_is_avoided_too(self):
        """`Popen.__exit__` calls a bare `self.wait()`. That is the SAME defect a
        second time on the same path -- the twin `render_proc.py`'s `finally` had,
        which SEC-3's row named only one half of."""
        popens = [
            node for node in ast.walk(_MODULE)
            if isinstance(node, ast.With)
            for item in node.items
            if isinstance(item.context_expr, ast.Call)
            and _dotted(item.context_expr.func) == ("subprocess", "Popen")
        ]
        assert popens == []

    def test_those_two_pins_can_fail(self):
        """Non-vacuity: the matcher finds what it is looking for when it is
        there. Without this, a `_dotted` that returned `None` for everything
        would make both pins above pass forever."""
        decoy = ast.parse(
            "import subprocess\n"
            "with subprocess.Popen(['x']) as p:\n"
            "    subprocess.run(['y'], timeout=1)\n"
        )
        assert len(_calls(decoy, ("subprocess", "run"))) == 1
        assert any(
            isinstance(node, ast.With)
            and _dotted(node.items[0].context_expr.func) == ("subprocess", "Popen")
            for node in ast.walk(decoy)
        )


class _UnreapableProc:
    """A `Popen` whose child never dies — the ONE thing a test cannot make the OS
    do (SIGKILL cannot be caught, and `UE`/`D` state needs real blocked I/O).

    Everything else in `_run_bounded` runs for real against this: the first
    `communicate` timeout, the `kill()`, the bounded `wait()`, the `finally`,
    `_abandon`'s pipe close and log line, and the exception that comes out. Only
    `wait()` lies, and it lies in exactly the way a `UE` child makes it behave.
    """

    def __init__(self) -> None:
        self.pid = 999_999
        self.stdin = None
        self.stdout = _FakePipe()
        self.stderr = _FakePipe()
        self.killed = False
        self.waits: list[float | None] = []

    def communicate(self, timeout=None):
        raise subprocess.TimeoutExpired(cmd=["fake"], timeout=timeout)

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout=None):
        self.waits.append(timeout)
        # An unreapable child: `wait` never returns. It must not sleep for the
        # full bound here or the pin would take REAP_TIMEOUT_S of wall clock to
        # prove a branch; raising what the kernel would make `wait` raise at the
        # bound is the same fact, measured by the product's own `timeout=` value.
        assert timeout == REAP_TIMEOUT_S, (
            f"the reap wait was given timeout={timeout!r}, not the shared bound"
        )
        raise subprocess.TimeoutExpired(cmd=["fake"], timeout=timeout)

    def poll(self):
        return None                      # still running, forever

    @property
    def returncode(self):
        return None


class _FakePipe:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class TestAnUnreapableChildIsAbandonedRatherThanWaitedOn:
    """The failure that used to hang the parent forever."""

    def test_it_is_abandoned_at_the_bound_and_not_waited_on(self, monkeypatch, caplog):
        fake = _UnreapableProc()
        monkeypatch.setattr(parsing.subprocess, "Popen", lambda *a, **k: fake)
        parsing._abandoned.clear()
        try:
            with caplog.at_level(logging.WARNING, logger="vib_agent.webapp"):
                with pytest.raises(SandboxChildAbandoned) as caught:
                    parsing._run_bounded(["fake"], timeout_s=1.0, what="parse")

            assert fake.killed, "the child must be killed before we give up on it"
            # ONE bounded wait, and no second one after the abandon: re-waiting
            # in the `finally` is how `render_proc.py`'s twin defect worked.
            assert fake.waits == [REAP_TIMEOUT_S], fake.waits
            assert fake.stdout.closed and fake.stderr.closed, (
                "the pipes were left open -- that is two fds per event, and "
                "`Popen.__exit__` is no longer here to close them"
            )
            assert caught.value.pid == fake.pid
            assert caught.value.returncode is None
            assert caught.value.pid in parsing.abandoned_child_pids()

            warnings = [r.getMessage() for r in caplog.records
                        if r.levelno >= logging.WARNING]
            assert any("ABANDONED" in m and str(fake.pid) in m for m in warnings), (
                "an abandoned child with no log line is exactly what SEC-3 called "
                f"half the defect; got {warnings}"
            )
        finally:
            parsing._abandoned.clear()

    def test_the_double_is_what_master_would_hang_on(self, monkeypatch):
        """NON-VACUITY, and the honest form of it. The claim is not "the fix
        returns faster" — it is that the shipped stdlib call has no bound at
        this point at all. Asserted against CPython's own source rather than by
        hanging a test for two hours."""
        import inspect

        source = inspect.getsource(subprocess.run)
        posix_branch = source[source.index("POSIX _communicate"):]
        assert "process.wait()" in posix_branch.split("raise")[0], (
            "subprocess.run's POSIX timeout branch no longer waits unbounded -- "
            "re-read it and re-derive this module's reason for existing"
        )
        exit_source = inspect.getsource(subprocess.Popen.__exit__)
        assert "self.wait()" in exit_source, (
            "Popen.__exit__ no longer waits unbounded -- same"
        )


class TestTheHealthyPathsAreNotMadeWorse:
    """Two negative controls with REAL children. Both must take the ordinary
    branch: `TimeoutExpired`, nothing abandoned, back well inside the bound.

    Without these, a `_run_bounded` that abandoned every timeout — or that
    bounded `communicate()` instead of `wait()` — would pass every pin above
    while making the live path slower and mislabelling healthy failures as
    server errors."""

    def _measure(self, cmd) -> float:
        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            parsing._run_bounded(cmd, timeout_s=1.0, what="parse")
        return time.monotonic() - started

    def test_a_child_that_reaps_normally(self, fake_child, monkeypatch):
        cmd, _pidfile = fake_child
        monkeypatch.delenv("FAKE_CHILD_HOLD", raising=False)
        elapsed = self._measure(cmd)
        assert elapsed < parsing.REAP_TIMEOUT_S, (
            f"a reapable child took {elapsed:.1f}s -- the abandon path is firing "
            "on a healthy child"
        )
        assert parsing.abandoned_child_pids() == []

    def test_a_child_whose_grandchild_holds_the_pipes(self, fake_child, monkeypatch):
        """THE REGRESSION GUARD. This is SUITE-LOCK's reproduction, and against
        `wait()` it is not a wedge at all: the child dies on SIGKILL and is
        reaped at once while the grandchild keeps the pipe. Measured against the
        stdlib at 1.0 s, so anything near the reap bound here means this module
        started bounding the pipe drain and is now abandoning healthy children."""
        cmd, _pidfile = fake_child
        monkeypatch.setenv("FAKE_CHILD_HOLD", "1")
        elapsed = self._measure(cmd)
        assert elapsed < parsing.REAP_TIMEOUT_S, (
            f"took {elapsed:.1f}s -- a grandchild holding a pipe is being treated "
            "as an unreapable child"
        )
        assert parsing.abandoned_child_pids() == []


# ══════════════════════════════════════════════════════════════════════════
# 2 · what each failure is called on the wire
# ══════════════════════════════════════════════════════════════════════════
class TestAnOrdinaryTimeoutIsUnchanged:
    """Retrying a file that timed out gives the same answer, so it stays
    `bad_upload` and keeps the sentence it already had. This session widened
    nothing it did not have evidence to widen."""

    def test_parse(self, tmp_path):
        upload = tmp_path / "upload.csv"
        upload.write_text("frequency_hz,amplitude_mm_s\n10,1.0\n")
        with pytest.raises(ParseError) as caught:
            parse_in_subprocess(
                upload, {"rpm": 1800},
                bearings_cfg_path=tmp_path, cwru_cfg_path=tmp_path,
                mfpt_cfg_path=tmp_path, wt_cfg_path=tmp_path,
                mafaulda_cfg_path=tmp_path, out_path=tmp_path / "out.json",
                timeout_s=0.001, memory_mb=256,
            )
        assert caught.value.safe_message == "Parsing took too long and was stopped."

    def test_sample(self, tmp_path):
        upload = tmp_path / "upload.txt"
        upload.write_text("10 1.0\n20 2.0\n")
        with pytest.raises(SampleError) as caught:
            sample_in_subprocess(
                upload, out_path=tmp_path / "sample.txt",
                timeout_s=0.001, memory_mb=256,
            )
        assert caught.value.safe_message == "Reading this file took too long and was stopped."


class TestAnAbandonedChildIsNotBlamedOnTheFile:

    def test_it_is_not_a_ParseError_or_a_SampleError(self):
        """The entire mechanism. `app.py` has three `except ParseError` handlers
        and one `except SampleError`, and every one of them files the failure as
        something about the analyst's upload. Making this a subclass would put a
        stranded process on the box and tell the analyst their file was bad."""
        assert not issubclass(SandboxChildAbandoned, ParseError)
        assert not issubclass(SandboxChildAbandoned, SampleError)

    @pytest.mark.parametrize("call", ["parse", "sample"])
    def test_it_travels_out_of_both_public_helpers(self, call, tmp_path, monkeypatch):
        def boom(cmd, *, timeout_s, what):
            raise SandboxChildAbandoned("wedged", pid=4242, returncode=None)

        monkeypatch.setattr(parsing, "_run_bounded", boom)
        upload = tmp_path / "upload.csv"
        upload.write_text("x\n")
        with pytest.raises(SandboxChildAbandoned):
            if call == "parse":
                parse_in_subprocess(
                    upload, {"rpm": 1800},
                    bearings_cfg_path=tmp_path, cwru_cfg_path=tmp_path,
                    mfpt_cfg_path=tmp_path, wt_cfg_path=tmp_path,
                    mafaulda_cfg_path=tmp_path, out_path=tmp_path / "out.json",
                    timeout_s=5.0, memory_mb=256,
                )
            else:
                sample_in_subprocess(
                    upload, out_path=tmp_path / "s.txt", timeout_s=5.0, memory_mb=256,
                )

    def test_the_mapping_is_asserted_against_the_ratified_taxonomy(self):
        """Wire law #5, checked against `ERROR_TAXONOMY` itself rather than
        restated. No ninth row is proposed -- `internal_error` already means
        'the analysis failed on our side; the file was fine'."""
        row = ERROR_TAXONOMY[SandboxChildAbandoned.error_code]
        assert SandboxChildAbandoned.error_code == "internal_error"
        assert row.kind == SandboxChildAbandoned.failure_kind == "server_error"
        assert row.retryable is SandboxChildAbandoned.retryable is True

    def test_through_the_product_path_it_becomes_a_retryable_server_error(
        self, tmp_path, monkeypatch
    ):
        """The mapping is only worth anything if it survives the four handlers
        between `parsing.py` and the wire. Driven through `POST /api/jobs`, with
        the sandbox raising -- so `_run_guarded`, `mark_error` and the status
        payload are all the shipped ones."""
        def wedged(*args, **kwargs):
            raise SandboxChildAbandoned("wedged", pid=4242, returncode=None)

        monkeypatch.setattr(app_module, "parse_in_subprocess", wedged)
        upload = tmp_path / "upload.csv"
        _spectrum_csv(upload)
        app = app_module.create_app(
            webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "engineer-1"},
            contact_email="ops@example.test", base_tmp=tmp_path,
        )
        with TestClient(app) as client:
            response = client.post(
                "/api/jobs",
                data={"invite_code": "demo-code", "rpm": "1800", "machine_alias": "Pump A",
                      "measurement_location": "Motor DE", "iso_group": "2",
                      "iso_support": "rigid", "machine_type": "motor", "velocity_unit": "mm_s",
                      "detection_type": "rms", "mode": "spectrum"},
                files={"file": ("upload.csv", upload.read_bytes(), "text/csv")},
            )
            assert response.status_code == 202
            state = _poll_until_terminal(client, response.json()["job_id"])

        assert state["state"] == "error"
        # The two fields the browser branches on. `bad_upload` here would tell
        # the analyst to change a file that was never read.
        assert state["failure_kind"] == "server_error"
        assert state["retryable"] is True
