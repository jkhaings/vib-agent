"""Run the native renderers in a CHILD PROCESS, so a crash kills the child.

Session RENDER-PROC (PROD_READINESS §7 F-1, reopened by SESSION_EMAIL1.md F-6).

Three native faults are on record in this package, all observed rather than
inferred:

* **GEOM-A** — `EXIT=139`, SIGSEGV inside matplotlib's `Figure.add_axes`, on a
  `concurrent.futures` worker thread.
* **DB-1** — `EXIT=139`, SIGSEGV inside weasyprint's `write_pdf` →
  `CSS.__init__` → tinycss2's tokenizer, **during a GC pass**.
* **EMAIL-1 F-6** — `EXIT=138`, SIGBUS inside weasyprint's
  `FontConfiguration.__init__`, while another thread was garbage-collecting
  inside `anyio`.

Sessions CHARTS-AGG and RENDER-SERIAL closed the first two behind one
process-wide `threading.Lock` (`report/render_lock.py`). **F-6 is the proof
that a lock cannot close this class**: its stack has `render_lock.py:105` *in
the frame list*, so the lock was held and doing its job. A lock serializes
*renders*; it cannot serialize the *garbage collection* of cairo/pango objects,
which runs on whatever thread happens to allocate next.

So the fix is not more serialization, it is a process boundary. A native
SIGSEGV/SIGBUS kills the **interpreter** — on the droplet that is uvicorn
(`--workers 1`, jobs on `asyncio.to_thread`), so every in-flight upload dies
with it and systemd restarts the unit under `Restart=on-failure`. The failing
job is not the blast radius. Move the engines into a short-lived child and the
crash costs one job.

The lock is **kept**, for a different reason than it was written: it now bounds
how many children are in flight at once (one), which is what keeps the
`MemoryMax=1500M` cgroup in `deploy/vibagent.service` honest.

WHY `subprocess` AND NOT `multiprocessing`
------------------------------------------
The brief said "multiprocessing, spawn". Two measurements moved it, and both
are reproducible on this machine:

1. `multiprocessing`'s spawn start method re-imports the parent's `__main__` in
   the child (`multiprocessing/spawn.py::prepare` → `_fixup_main_from_path`).
   Under uvicorn, `__main__` is uvicorn's own console script — the child would
   re-run the server's entrypoint.
2. On Linux + CPython 3.12, which is what the droplet runs, `multiprocessing`'s
   **default** start method is `fork`, the one thing that must never happen
   under a threaded uvicorn. Only an explicit context avoids it, and nothing
   enforces that at a call site.

`subprocess.Popen([sys.executable, "-m", ...])` has spawn semantics
unconditionally, on every platform, and is already how this repo sandboxes
untrusted parsers (`webapp/parsing.py::parse_in_subprocess` +
`webapp/_parse_child.py`). This module follows that precedent, with **one
deliberate difference**: `parsing.py:113` tests only `returncode != 0`, so it
cannot tell a signal death from a clean refusal, and blames a killed parser on
the analyst's file. A killed *renderer* must not be blamed on the file — see
`RenderChildCrashed` below.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import vib_agent

#: Wall-clock ceiling for one child render. A module constant rather than a
#: `config/*.json` key because `config/` was closed to this session; the
#: precedent for a hardcoded render timeout is `_pandoc_to_pdf`'s
#: `subprocess.run(..., timeout=120)` in `generate.py`. Recorded in
#: outputs/SESSION_RENDERPROC.md as a finding for whoever next has config scope.
RENDER_TIMEOUT_S = 120.0

#: How long the parent waits for a child to actually die AFTER `SIGKILL`,
#: before it stops waiting and abandons it. SEC-3 F-2 / PROD_READINESS:812:
#: both waits below `proc.kill()` used to be unbounded, which turned the 120 s
#: bound above into an unbounded one whenever the kernel would not reap the
#: child -- the whole point of a timeout, lost in its own cleanup path.
#:
#: 10 s is ~20x the slowest thing a healthy child does (startup measures
#: 0.41-0.47 s, SESSION_RENDERPROC.md §7) and a reapable child dies in
#: milliseconds, so this never fires on a working box. It bounds the worst
#: case of one render at 130 s, still far inside `job_max_runtime_s: 600`.
#: A module constant for the same reason `RENDER_TIMEOUT_S` is one: `config/`
#: was closed to this session too. Re-filed in outputs/SESSION_SUITELOCK.md.
REAP_TIMEOUT_S = 10.0

#: The child's single line of structured output. Same prefix idiom as
#: `_parse_child`'s `PARSE_ERROR:` — so engine chatter on stdout (weasyprint
#: and matplotlib both log, and a C library can write to fd 1 directly) cannot
#: corrupt the channel. The parent reads the LAST line carrying the prefix.
RESULT_PREFIX = "RENDER_RESULT: "
ERROR_PREFIX = "RENDER_ERROR: "

_CHILD_MODULE = "vib_agent.report._render_child"


class RenderChildCrashed(RuntimeError):
    """A render child died from a signal, timed out, or exited nonzero.

    This is the retryable one, and it is deliberately NOT the same thing as
    "the engine is not installed here" — that stays a `None`/empty return and
    degrades to markdown-only exactly as it did before this session.

    WHY THIS CARRIES THE S7 VOCABULARY AND ADDS NO TAXONOMY ROW. Wire law #5
    (ROADMAP common law) requires every new failure mode to map to the ratified
    taxonomy in `webapp/jobs.py`. `internal_error` already reads *"the analysis
    failed on our side; the file was fine"*, is `server_error`, and is
    `retryable=True` — which is exactly this. No ninth code is proposed, the
    same move EMAIL-1 made for its send failures; `webapp/worker.py` has no
    try/except around the render calls, so this exception reaches
    `app.py::_run_guarded` and becomes `internal_error` with **zero** webapp
    edits.
    """

    #: The ratified S7 code this maps to. Read by the pins, so the mapping is
    #: asserted against `ERROR_TAXONOMY` rather than restated in prose.
    error_code = "internal_error"
    failure_kind = "server_error"
    retryable = True

    def __init__(self, message: str, *, op: str, returncode: int | None = None,
                 timed_out: bool = False, abandoned: bool = False) -> None:
        super().__init__(message)
        self.op = op
        self.returncode = returncode
        self.timed_out = timed_out
        #: True when the child outlived `REAP_TIMEOUT_S` after `SIGKILL` and
        #: was LEFT RUNNING deliberately. The wire result is unchanged -- this
        #: is still `internal_error`, still retryable -- but the box now has a
        #: process on it that this parent will never reap, and
        #: `abandoned_child_pids()` names it.
        self.abandoned = abandoned

    @property
    def signal_name(self) -> str | None:
        """`SIGSEGV` / `SIGBUS` / … when the child died from a signal."""
        if self.returncode is None or self.returncode >= 0:
            return None
        try:
            return signal.Signals(-self.returncode).name
        except ValueError:
            return None


# ── live-child instrumentation ───────────────────────────────────────────
# Mirrors `render_lock.acquisitions()`: a pin that cannot read a number can
# only assert that nothing deadlocked. This is what lets a test kill a REAL
# child mid-render — the pin the brief asks for by name — without a test-only
# branch anywhere in the product path.
_live_lock = threading.Lock()
_live: dict[int, subprocess.Popen] = {}


def live_child_pids() -> list[int]:
    """Pids of render children currently in flight. Instrumentation only."""
    with _live_lock:
        return sorted(_live)


# ── abandoned children ───────────────────────────────────────────────────
# `report/` had no logger before this session. It has one now because an
# abandoned child is otherwise INVISIBLE, and SEC-3 recorded "no log line at
# all" as half of what made F-2 so bad: the service lost a worker slot
# permanently and nothing said so. Own handler if none -- the same move
# `webapp/app.py:89-99` makes, for the same reason. `deploy/vibagent.service`
# sets `StandardError=append:/var/log/vibagent/app.log`, so this reaches the
# operator without depending on whatever logging setup uvicorn, the CLI or
# pytest happens to have installed. `propagate` is left alone, so pytest's
# caplog still sees the record.
_log = logging.getLogger("vib_agent.report")
if not _log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _log.addHandler(_handler)
_log.setLevel(logging.INFO)

#: Children this process gave up on. Deliberately never emptied: the `Popen` is
#: what keeps `Popen.__del__` from warning about a child still running, and a
#: leak worth logging is worth still being able to count at the end of a run.
#:
#: It does NOT reserve the pid, and an earlier draft of this comment said it
#: did. Measured: in the case the pin reproduces, `poll()` returns `-9` -- the
#: child IS reaped and its pid is immediately reusable; what is still on the
#: box is the grandchild holding the pipe. Only a pid whose `returncode` is
#: still `None` is a process this parent has left running, and a pid is
#: reserved by an unreaped zombie, never by a live Python object. Anything
#: acting on these pids must check `returncode` first.
_abandoned: dict[int, subprocess.Popen] = {}


def abandoned_child_pids() -> list[int]:
    """Pids this process killed and then STOPPED WAITING FOR.

    Mirrors `live_child_pids()`, and exists for the same stated reason
    (`render_lock.py:86-89`): a pin that cannot read a number can only assert
    that nothing deadlocked.

    **Historical, not a live roster.** See `_abandoned` above: a pid here whose
    `returncode` is a number is already dead and may have been recycled.
    `scripts/gate/gate.sh` cannot read this at all -- it is another
    interpreter's module dict -- and does its own `ps` sweep instead.
    """
    with _live_lock:
        return sorted(_abandoned)


def _abandon(proc: subprocess.Popen, op: str) -> None:
    """Stop waiting on a child the OS will not let us reap, and say so.

    SEC-3 F-2 / `outputs/PROD_READINESS.md:812`. Both waits under `proc.kill()`
    used to be unbounded, so a child the kernel would not reap turned the 120 s
    render bound into an unbounded wait -- on a worker thread, holding
    `render_lock` and a `worker_concurrency` slot for the life of the process.
    Observed twice: a child in state `UE` for 2h11m while the suite sat at 0.0%
    CPU (SEC-3 §8), and one `gate.sh` run wedged ~18 minutes on an otherwise
    idle box (ROADMAP FINDING, Sep 7) -- contention was not required.

    Waiting longer is not a fix at any bound, because a `U`-state process
    cannot be reaped by its parent AT ALL. So this abandons it: the pid is
    recorded, the `Popen` kept alive, the pipes closed so their fds are not
    leaked, and one WARNING carries the pid out.

    **`returncode` in that line is the diagnosis**, and the two cases want
    different responses:

    * `None` -- the child is genuinely unreapable. On this dev box that is
      state `UE`, and law #9 is explicit: *a `_render_child` in UE means
      reboot, not kill.*
    * a number (`-9` after our own `SIGKILL`) -- the child is already dead and
      it is the PIPE that is still open, held by a grandchild that inherited
      it. Nothing to reboot; the leak is the grandchild. Measured: this is the
      case the pin reproduces, and `communicate()` blocks on it just as hard.
    """
    rc = proc.poll()
    with _live_lock:
        _live.pop(proc.pid, None)
        _abandoned[proc.pid] = proc
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe is not None:
            try:
                pipe.close()
            except (OSError, ValueError):  # already closed, or a broken pipe
                pass
    _log.warning(
        "render child for %r (pid %s) outlived %gs after SIGKILL — ABANDONED, "
        "not waited on; returncode=%s (None = unreapable, state UE on this "
        "box, which means reboot not kill; a number = already dead, but a "
        "grandchild still holds its pipe)",
        op, proc.pid, REAP_TIMEOUT_S, rc,
    )


def _child_env() -> dict[str, str]:
    """Environment for the child, with `PYTHONPATH` pinned to THIS package.

    Without this a git worktree's child silently loads the main checkout's
    editable install — the trap `tests/test_render_serial.py:53` exists to
    avoid, and which is live on this machine (the worktree's `.venv` is a
    symlink to the main checkout's).
    """
    env = dict(os.environ)
    pkg_parent = str(Path(vib_agent.__file__).resolve().parents[1])
    existing = env.get("PYTHONPATH", "")
    parts = [pkg_parent, *(p for p in existing.split(os.pathsep) if p)]
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONIOENCODING"] = "utf-8"  # the child half of the pipe -- see run_child
    return env


def _child_argv(op: str) -> list[str]:
    """The exact command line. A helper so the pin can run the real thing.

    `-P` (CPython 3.11+) keeps the child's CWD off `sys.path`. Without it `-m`
    puts the cwd FIRST — ahead of the `PYTHONPATH` `_child_env` pins — so any
    directory holding a `vib_agent/` would shadow the package the parent is
    running. Measured, not assumed: a decoy package in the cwd wins without it.
    In production the cwd is the unit's `WorkingDirectory` and nothing shadows
    today; `-P` is what makes that a guarantee rather than a coincidence.
    """
    return [sys.executable, "-P", "-m", _CHILD_MODULE, op]


def run_child(op: str, payload: dict[str, Any], *,
              timeout_s: float = RENDER_TIMEOUT_S) -> dict[str, Any]:
    """Run one render op in a child process and return its result dict.

    Raises `RenderChildCrashed` if the child died from a signal, ran past
    `timeout_s`, or exited nonzero. Returns `{"status": "engine_absent"}`
    unchanged when the child found no engine — that is not a crash.
    """
    proc = subprocess.Popen(
        _child_argv(op),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        # UTF-8 on BOTH sides of the pipe, explicitly. The payload is not ASCII
        # (charts.py emits "◀ shown above"; the HTML carries — and ·), and the
        # child's default stdio encoding follows ITS locale. The systemd unit
        # sets no LANG, so on the droplet the child lands in the C locale and
        # survives only via PEP 538 coercion, which is skipped silently when
        # C.UTF-8 is absent from the image. This machine cannot reproduce that
        # failure -- macOS maps C to UTF-8 -- which is exactly why it is pinned
        # rather than left to a fallback.
        env=_child_env(), text=True, encoding="utf-8",
    )
    with _live_lock:
        _live[proc.pid] = proc
    abandoned = False
    try:
        try:
            out, err = proc.communicate(json.dumps(payload), timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            # The child must die even though the parent is giving up on it:
            # webapp/app.py records that a job abandoned at job_max_runtime_s
            # leaves a thread that cannot be interrupted, and an orphaned
            # renderer would keep its share of MemoryMax.
            proc.kill()
            try:
                # SEC-3 F-2. This call was `proc.communicate()` with NO
                # timeout, which is how the 120 s bound above became an
                # unbounded wait whenever the child could not be reaped.
                proc.communicate(timeout=REAP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                _abandon(proc, op)
                abandoned = True
            raise RenderChildCrashed(
                f"render child for {op!r} exceeded {timeout_s:g}s and was killed",
                op=op, timed_out=True, abandoned=abandoned,
            ) from exc

        if proc.returncode != 0:
            tail = ""
            for line in (err or "").strip().splitlines():
                if line.startswith(ERROR_PREFIX):
                    tail = line[len(ERROR_PREFIX):].strip()
            detail = f": {tail}" if tail else ""
            if proc.returncode < 0:
                name = signal.Signals(-proc.returncode).name if -proc.returncode in {
                    s.value for s in signal.Signals} else str(proc.returncode)
                raise RenderChildCrashed(
                    f"render child for {op!r} was killed by {name}{detail}",
                    op=op, returncode=proc.returncode,
                )
            raise RenderChildCrashed(
                f"render child for {op!r} exited {proc.returncode}{detail}",
                op=op, returncode=proc.returncode,
            )

        # SCANNED, not `.splitlines()[-1]`. weasyprint prints a multi-line
        # installation notice to STDOUT at import time when its native libs are
        # missing (`weasyprint/text/ffi.py:455`, a bare `print`) -- which is the
        # `engine_absent` path, so the noisiest case is the one a last-line read
        # would break on. `webapp/parsing.py:114` takes the last line; this
        # deliberately does not.
        for line in reversed((out or "").splitlines()):
            if line.startswith(RESULT_PREFIX):
                return json.loads(line[len(RESULT_PREFIX):])
        raise RenderChildCrashed(
            f"render child for {op!r} exited cleanly but produced no result line",
            op=op, returncode=proc.returncode,
        )
    finally:
        with _live_lock:
            _live.pop(proc.pid, None)
        # `not abandoned` first: the timeout path above has already killed this
        # child, already given up on it, and already logged it. Re-killing and
        # re-waiting here would reintroduce the same unbounded wait one line
        # after fixing it.
        if not abandoned and proc.poll() is None:  # nothing above should leave it running
            proc.kill()
            try:
                proc.wait(timeout=REAP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                # Bounded for the same reason as the site above -- and it never
                # raises out of `finally`, because that would replace whatever
                # brought us here (a RenderChildCrashed carrying the real
                # story, or a clean return) with a bare TimeoutExpired.
                _abandon(proc, op)
