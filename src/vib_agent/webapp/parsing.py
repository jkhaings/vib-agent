"""Sandboxed subprocess runner for untrusted upload parsing (Phase 6).

Uploads are untrusted input: parsing always happens in a child process
(`_parse_child.py`) with a hard wall-clock timeout and a best-effort memory
cap, never inline in the request-handling process. Filenames and paths are
passed as argv elements only — never through a shell.

SESSION FLIP-1 — WHY THIS FILE NO LONGER CALLS `subprocess.run`
---------------------------------------------------------------
`subprocess.run(cmd, timeout=...)` looks like a bounded wait and is not one.
Read from CPython 3.12.7 in this venv rather than from the row that filed it::

    with Popen(*popenargs, **kwargs) as process:        # __exit__ waits too
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except TimeoutExpired as exc:
            process.kill()
            ...
            else:                                       # the POSIX branch
                process.wait()                          # <- NO timeout
            raise

and `Popen.__exit__` then closes the pipes and calls a second bare `self.wait()`.
So there are two unbounded waits per call site, and the moment the kernel will
not let us reap the child the timeout above them stops bounding anything — the
defect SEC-3 F-2 recorded against `report/render_proc.py`, inherited from the
stdlib rather than written here (`outputs/SESSION_SUITELOCK.md` F-1,
`outputs/PROD_READINESS.md`). Two of the three sites it named are in this file,
and this file is on the **live upload path**: the hung wait happens on a worker
thread, holding a `worker_concurrency` slot for the life of the process.

ONE CORRECTION TO THAT ROW, MEASURED HERE (SESSION_FLIP1.md F-1). It says
`subprocess.run` does "`proc.kill()` then a bare `proc.communicate()`". That is
true of `render_proc.py`'s own code and **false of `subprocess.run` on POSIX**,
which calls `proc.wait()` — `communicate()` is the Windows branch only. The
difference is not pedantry, it decides what can go wrong:

  * `communicate()` blocks until the pipes reach **EOF**, so a detached
    grandchild inheriting fds 1 and 2 hangs it even though the child is already
    dead. That is the reproduction SUITE-LOCK measured, and it is why
    `render_proc.py` was reachable.
  * `wait()` blocks until the **child is reaped**, and does not care about the
    pipes at all. Measured against this shape: with a grandchild holding the
    pipes, `subprocess.run(timeout=1)` returns in **1.0 s**. So the grandchild
    reproduction does not reproduce here.

What is left is the harder case and the one SUITE-LOCK called unreproducible in
a test: a child the kernel will not reap (state `UE` on macOS, `D` on Linux),
which is exactly what SEC-3 observed on this box — one child in `UE` for 2h11m.
So this module bounds `wait()`, **not** `communicate()`, and does it in that
order deliberately: bounding the pipe drain instead would abandon a healthy
child in the grandchild case that the stdlib handles correctly in a millisecond,
which is making the common path worse to fix the rare one.

**Waiting longer is not a fix at any bound**, because a `U`-state process cannot
be reaped by its parent at all. The only correct move is to stop waiting, close
the pipes (`Popen.__exit__` used to do that and this module must now do it
itself), keep the `Popen` so `__del__` does not warn, and say so in the log.

WHAT AN ABANDONED CHILD IS TOLD TO THE ANALYST, AND WHY IT IS NOT "BAD FILE".
A plain timeout is unchanged: the child died when we killed it, retrying the
same file gives the same answer, and it stays a `ParseError`/`SampleError` —
`bad_upload`, not retryable. An **abandoned** child is a different claim and
raises `SandboxChildAbandoned` instead, which is deliberately NOT a `ParseError`
subclass so that it travels past the three `except ParseError` handlers in
`app.py` (each of which would file it as "we could not read your file") and
reaches `app.py::_run_guarded`, which maps any escaped exception to
`internal_error` — `server_error`, `retryable=True`.

That is wire law #5 satisfied by MAPPING, and no ninth `ERROR_TAXONOMY` row —
the move RENDER-PROC and EMAIL-1 both made, for the reason RENDER-PROC states in
as many words: *"A killed renderer must not be blamed on the file."* Neither may
a parser child we could not reap: we killed it ourselves, and a failure to reap
is a condition of the box, not evidence about the upload.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

# ONE bound, ONE derivation. 10 s is ~20x the slowest thing a healthy child does
# and a reapable child dies in milliseconds, so it never fires on a working box
# (SESSION_SUITELOCK.md §2 has the measurement). Imported rather than restated
# because the two are answers to the same question — "how long do we wait for a
# child we have already SIGKILLed?" — and a second copy is a second thing to get
# wrong. It costs nothing: `webapp/worker.py` imports `report.generate`, which
# imports `render_proc` at module scope, so this module is already loaded in
# every process that has a webapp in it.
from vib_agent.report.render_proc import REAP_TIMEOUT_S

# The webapp's own logger, not a new one: `deploy/vibagent.service` routes it to
# `app.log` and `webapp/app.py` installs the handler. An abandoned child is
# otherwise INVISIBLE, which SEC-3 recorded as half of what made this class of
# bug so bad — the service lost a worker slot permanently and nothing said so.
_log = logging.getLogger("vib_agent.webapp")


class ParseError(RuntimeError):
    """Raised when the child process fails, times out, or is killed for
    exceeding its memory cap. `safe_message` is what the analyst sees —
    never a raw traceback."""

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class SampleError(RuntimeError):
    """The upload could not be sampled for inference. `safe_message` is what the
    analyst sees."""

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class SandboxChildAbandoned(RuntimeError):
    """A sandbox child outlived `REAP_TIMEOUT_S` after `SIGKILL` and was LEFT.

    NOT a `ParseError`/`SampleError` subclass, and that is the whole design.
    `app.py` has three `except ParseError` handlers and one `except SampleError`,
    and every one of them files the failure as something about the analyst's
    file — an `UnreadableChannel`, or a plan marked `status="unknown"`. This is
    not that. Travelling past them to `app.py::_run_guarded` is what turns it
    into `internal_error`: *"the analysis failed on our side; the file was
    fine"*, `server_error`, `retryable=True`, which is the truth here and is
    also the advice the analyst can act on.

    WHY NO NINTH `ERROR_TAXONOMY` ROW. Wire law #5 requires every new failure
    mode to map to the ratified taxonomy; it does not require a new code, and
    this repository's precedent is twice against one (RENDER-PROC's
    `RenderChildCrashed`, EMAIL-1's send failures). `internal_error` already
    means exactly this. The three attributes below are the mapping, declared as
    data so the pins assert it against `jobs.ERROR_TAXONOMY` itself rather than
    restating it in prose.
    """

    #: The ratified S7 code this maps to, read by the pins.
    error_code = "internal_error"
    failure_kind = "server_error"
    retryable = True

    def __init__(self, message: str, *, pid: int, returncode: int | None) -> None:
        super().__init__(message)
        self.pid = pid
        #: Expected to be `None` on this path and that is the diagnosis: the
        #: child is genuinely unreapable (state `UE` on macOS, `D` on Linux),
        #: and law #9 is explicit that a `_render_child` in `UE` means reboot,
        #: not kill — the same is true of a parse child. A NUMBER here would
        #: mean the child died between the reap bound expiring and this call,
        #: which is a race rather than a wedge and costs only a spurious
        #: WARNING. Deliberately not the grandchild-holds-the-pipe case that
        #: `render_proc.py` records: `wait()` does not block on pipes, so that
        #: case never reaches here (see this module's docstring).
        self.returncode = returncode


#: Children this process gave up on. Never emptied, for `render_proc._abandoned`'s
#: two stated reasons: the retained `Popen` is what keeps `Popen.__del__` from
#: warning about a child still running, and a leak worth logging is worth still
#: being able to count at the end of a run.
#:
#: It does NOT reserve the pid. A pid is reserved by an unreaped zombie, never by
#: a live Python object, and per the measurement above most abandoned children
#: are already reaped — so anything acting on these pids must check `returncode`
#: first, or it will signal whatever process has since inherited that number.
_abandoned: dict[int, subprocess.Popen] = {}


def abandoned_child_pids() -> list[int]:
    """Pids this process killed and then STOPPED WAITING FOR.

    Instrumentation only, and **historical rather than a live roster** — see
    `_abandoned`. Mirrors `render_proc.abandoned_child_pids()` for the reason
    that one exists: a pin that cannot read a number can only assert that
    nothing deadlocked.
    """
    return sorted(_abandoned)


def _close_pipes(proc: subprocess.Popen) -> None:
    """What `Popen.__exit__` did before this module stopped using `with`.

    Not optional bookkeeping. On the timeout path `communicate()` raises with the
    pipes still open, and `subprocess.run` only ever closed them because its
    `with` block exited -- so a `_run_bounded` that dropped the `with` and did
    not do this would leak two fds per timed-out upload, forever, on the live
    path. (`with` is not the answer: `Popen.__exit__` closes the pipes and then
    calls a bare `self.wait()`, which is the second unbounded wait on this path
    and would re-enter the hang one line after the first was fixed.)
    """
    for pipe in (proc.stdout, proc.stderr, proc.stdin):
        if pipe is not None:
            try:
                pipe.close()
            except (OSError, ValueError):  # already closed, or a broken pipe
                pass


def _abandon(proc: subprocess.Popen, what: str) -> SandboxChildAbandoned:
    """Stop waiting on a child the OS will not let us reap, say so, and build the
    exception that carries the fact out.

    Returns rather than raises so the caller's `raise ... from exc` keeps the
    reap `TimeoutExpired` as the cause.
    """
    returncode = proc.poll()
    _abandoned[proc.pid] = proc
    _close_pipes(proc)
    _log.warning(
        "sandbox child for %s (pid %s) outlived %gs after SIGKILL — ABANDONED, "
        "not waited on; returncode=%s (None = the child is genuinely unreapable, "
        "state UE/D, which law #9 says means reboot not kill)",
        what, proc.pid, REAP_TIMEOUT_S, returncode,
    )
    return SandboxChildAbandoned(
        f"sandbox child for {what} was killed and could not be reaped",
        pid=proc.pid, returncode=returncode,
    )


def _run_bounded(
    cmd: list[str], *, timeout_s: float, what: str
) -> subprocess.CompletedProcess[str]:
    """`subprocess.run(cmd, capture_output=True, timeout=timeout_s, text=True)`,
    with every wait under it bounded and the pipes closed by hand.

    Raises `subprocess.TimeoutExpired` for an ordinary timeout — so the two
    callers' existing `except subprocess.TimeoutExpired` branches, and the
    analyst-facing messages in them, are untouched — and
    `SandboxChildAbandoned` for the case that used to hang forever.

    `proc.wait`, NOT `proc.communicate`, is what is bounded, and the module
    docstring has the measurement behind that: the stdlib's unbounded call here
    is `wait()`, and bounding a pipe drain instead would abandon a healthy child
    in a case the stdlib gets right in a millisecond.
    """
    proc = subprocess.Popen(
        # DEVNULL rather than `subprocess.run`'s inherited stdin. Neither child
        # reads it, and handing an untrusted parser the server's own stdin is
        # not a property worth preserving byte-for-byte.
        cmd, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    abandoned = False
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            # THE LINE THIS SESSION EXISTS FOR. `subprocess.run` does an
            # UNBOUNDED `proc.wait()` here, which is how the bound above became
            # no bound at all whenever the child could not be reaped.
            proc.wait(timeout=REAP_TIMEOUT_S)
        except subprocess.TimeoutExpired as reap_exc:
            abandoned = True
            raise _abandon(proc, what) from reap_exc
        raise
    finally:
        # `abandoned` is a LOCAL flag and not a lookup in `_abandoned`, because
        # that dict is keyed by pid and pids are recycled: a pid abandoned an
        # hour ago would make this branch skip the work for an unrelated live
        # child. Same reason `render_proc.run_child` carries its own boolean.
        #
        # `_abandon` has already closed the pipes and already given up, and
        # re-killing there would be the unbounded wait again, one line after.
        if not abandoned:
            _close_pipes(proc)
            if proc.poll() is None:  # nothing above should leave it running
                proc.kill()
                try:
                    proc.wait(timeout=REAP_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    # Never raises out of `finally`: that would replace whatever
                    # brought us here — a TimeoutExpired carrying the real story,
                    # or a clean return — with a bare one that carries none of it.
                    _abandon(proc, what)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def sample_in_subprocess(
    upload_path: Path, *, out_path: Path, timeout_s: float, memory_mb: int
) -> str:
    """Read the bounded inference sample in the sandbox. Text files need only a
    decode, but a spreadsheet needs a workbook opened — untrusted parsing, so it
    goes behind the same cap and timeout as everything else."""
    cmd = [
        sys.executable,
        "-m",
        "vib_agent.webapp._sample_child",
        str(upload_path),
        str(out_path),
        str(memory_mb),
    ]
    try:
        result = _run_bounded(cmd, timeout_s=timeout_s, what="sample")
    except subprocess.TimeoutExpired as exc:
        raise SampleError("Reading this file took too long and was stopped.") from exc
    if result.returncode != 0:
        tail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else ""
        if tail.startswith("SAMPLE_ERROR:"):
            raise SampleError(tail[len("SAMPLE_ERROR:") :].strip())
        raise SampleError("Could not read this file as a text or spreadsheet export.")
    if not out_path.exists():
        raise SampleError("Could not read this file as a text or spreadsheet export.")
    sample = out_path.read_text()
    out_path.unlink(missing_ok=True)
    return sample


def parse_in_subprocess(
    upload_path: Path,
    form_dict: dict[str, Any],
    *,
    bearings_cfg_path: Path,
    cwru_cfg_path: Path,
    mfpt_cfg_path: Path,
    wt_cfg_path: Path,
    mafaulda_cfg_path: Path,
    out_path: Path,
    timeout_s: float,
    memory_mb: int,
    recipe_json: str | None = None,
) -> tuple[dict[str, Any], str, str]:
    """Runs the child parser and returns (case_dict, kind, conversion_note).
    Raises ParseError on any failure, timeout, or memory-cap kill.

    Session G: `recipe_json` is the validated ParseRecipe for a schema-inference
    upload (.txt/.dat/.asc). It travels as one more argv element -- the SAME
    sandbox, size caps and timeout as every other parse. The child re-validates
    it against the schema before use, so nothing that reaches the executor has
    skipped validation, whatever produced it.

    Session FLIP-1: and `SandboxChildAbandoned` when the child could not be
    reaped after being killed — see this module's docstring for why that one is
    deliberately not a `ParseError`.
    """
    cmd = [
        sys.executable,
        "-m",
        "vib_agent.webapp._parse_child",
        str(upload_path),
        json.dumps(form_dict),
        str(bearings_cfg_path),
        str(cwru_cfg_path),
        str(mfpt_cfg_path),
        str(wt_cfg_path),
        str(mafaulda_cfg_path),
        str(out_path),
        str(memory_mb),
        recipe_json or "",
    ]
    try:
        result = _run_bounded(cmd, timeout_s=timeout_s, what="parse")
    except subprocess.TimeoutExpired as exc:
        raise ParseError("Parsing took too long and was stopped.") from exc

    if result.returncode != 0:
        tail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else ""
        if tail.startswith("PARSE_ERROR:"):
            raise ParseError(tail[len("PARSE_ERROR:") :].strip())
        raise ParseError("Could not read this file — it may be corrupted or in an unexpected layout.")

    if not out_path.exists():
        raise ParseError("Parser produced no output.")

    payload = json.loads(out_path.read_text())
    return payload["case"], payload["kind"], payload["conversion_note"]
