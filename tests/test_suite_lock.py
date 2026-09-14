"""Session SUITE-LOCK — law #9 made mechanical, and a hang made loud.

Law #9 (`outputs/HANDOFF_2026-09-08.md` §2) says *sessions never run the full
suite; the operator's `gate.sh` is the N/1 and it runs alone on the box.* Until
this session that was a sentence in a document, and it cost two reboots on
Sep 7 — four sessions running full suites at once put seven `_render_child`
processes into state `UE`, which `kill -9` does not clear; and then one
`gate.sh` run, ALONE on an idle box, wedged ~18 minutes on the untimed parent
wait in `report/render_proc.py` (`outputs/ROADMAP_2026-08-31.md:1750`).

Three mechanisms, three different failures:

* the **lock** stops a second full run from starting;
* the **per-test bounds** stop one run holding the box silently;
* the wait itself is bounded at source — `tests/test_render_proc.py`,
  `TestAnUnreapableChildIsAbandonedRatherThanWaitedOn`.

HOW THESE PINS MEASURE THE REAL THING. Every claim here is checked by running
a REAL pytest in a child process with `tests/conftest.py` loaded as a plugin
(`-p conftest`), never by calling the hook by hand — because what is under test
is a pytest hook's behaviour inside pytest, and a hand-called hook would answer
a question about this test file instead. The bounds are lowered through the
documented env seams so the pins can finish; the SHIPPED constants are pinned
separately, by name and value, so a lowered bound can never quietly become the
product's own (`TestTheShippedNumbers`).
"""

from __future__ import annotations

import fcntl
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

#: `tests/conftest.py` loaded BY PATH rather than by `import conftest`, which
#: is not importable under pytest 9's default import mode. The child processes
#: below load the same file as a plugin (`-p conftest`, with `tests/` on
#: PYTHONPATH); this handle exists only so `TestTheShippedNumbers` can read the
#: constants out of the one file that defines them, instead of restating them.
_spec = importlib.util.spec_from_file_location(
    "_vib_suite_conftest", _ROOT / "tests" / "conftest.py")
suite_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(suite_conftest)

#: Every knob this session added. Cleared out of a child's environment unless
#: the test sets it, so a pin can never inherit the value that makes it pass.
_KNOBS = (
    "VIB_SUITE_LOCK_PATH", "VIB_SUITE_LOCK_MIN_TESTS", "VIB_SUITE_LOCK_FD",
    "VIB_SUITE_LOCK_LABEL", "VIB_PYTEST_TIMEOUT_S", "VIB_PYTEST_BACKSTOP_S",
    "VIB_PYTEST_TIMEOUTS", "VIB_WEDGE_LOG",
)


def _probe(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body)
    return path


def _run_pytest(target: Path, *, pass_fds=(), **env_overrides) -> subprocess.CompletedProcess:
    """One real pytest, with this repo's conftest loaded as a plugin."""
    env = {k: v for k, v in os.environ.items() if k not in _KNOBS}
    env["PYTHONPATH"] = f"{_ROOT / 'src'}{os.pathsep}{_ROOT / 'tests'}"
    env.update({k: str(v) for k, v in env_overrides.items()})
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "conftest", "-o", "addopts=", str(target)],
        capture_output=True, text=True, env=env, cwd=str(_ROOT),
        timeout=120, pass_fds=pass_fds,
    )


def _all(result: subprocess.CompletedProcess) -> str:
    return result.stdout + result.stderr


# ══════════════════════════════════════════════════════════════════════════
# 1 · The lock
# ══════════════════════════════════════════════════════════════════════════

_TRIVIAL = "def test_quick():\n    assert True\n"


@pytest.fixture
def held_lock(tmp_path):
    """A lock held by somebody else, with a holder line to be named by."""
    path = tmp_path / "suite.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.ftruncate(fd, 0)
    os.pwrite(fd, b"pid 99999 (gate.sh suitelock, cwd /somewhere, started 2026-09-07T12:00:00Z)\n", 0)
    try:
        yield path, fd
    finally:
        os.close(fd)


class TestAFullRunTakesTheLock:

    def test_a_full_run_takes_it_and_records_who_holds_it(self, tmp_path):
        probe = _probe(tmp_path, "test_p.py", _TRIVIAL)
        lock = tmp_path / "suite.lock"
        r = _run_pytest(probe, VIB_SUITE_LOCK_PATH=lock, VIB_SUITE_LOCK_MIN_TESTS=1)
        assert r.returncode == 0, _all(r)
        assert "suite lock: taken" in r.stdout, _all(r)
        holder = lock.read_text()
        assert holder.startswith("pid "), holder
        assert str(_ROOT) in holder, "the holder line does not say which tree is running"

    def test_a_second_full_run_refuses_and_names_the_first(self, tmp_path, held_lock):
        """The whole point: one sentence, and it says who to go and look at."""
        lock, _fd = held_lock
        probe = _probe(tmp_path, "test_p.py", _TRIVIAL)
        r = _run_pytest(probe, VIB_SUITE_LOCK_PATH=lock, VIB_SUITE_LOCK_MIN_TESTS=1)
        assert r.returncode != 0, _all(r)
        text = _all(r)
        assert "another full test run holds" in text, text
        assert "pid 99999" in text, "the refusal did not name the other holder"
        assert "gate.sh suitelock" in text, "the refusal did not say what the holder is"
        assert "law #9" in text, text

    def test_a_targeted_run_is_unaffected_by_a_held_lock(self, tmp_path, held_lock):
        """THE NEGATIVE CONTROL for the trigger (common law #11).

        Same held lock, same child, same everything — only the collected count
        is below the threshold. If this refused too, the mechanism would be a
        blanket ban on running tests rather than on running the SUITE, and
        every session would learn to unset the variable.
        """
        lock, _fd = held_lock
        probe = _probe(tmp_path, "test_p.py", _TRIVIAL)
        r = _run_pytest(probe, VIB_SUITE_LOCK_PATH=lock)  # default threshold: 2000
        assert r.returncode == 0, _all(r)
        assert "another full test run holds" not in _all(r)
        assert "suite lock" not in r.stdout, "a targeted run touched the lock at all"

    def test_collect_only_never_takes_it(self, tmp_path, held_lock):
        """Collection runs no test and holds no renderer, so it is not a run."""
        lock, _fd = held_lock
        probe = _probe(tmp_path, "test_p.py", _TRIVIAL)
        env = {k: v for k, v in os.environ.items() if k not in _KNOBS}
        env["PYTHONPATH"] = f"{_ROOT / 'src'}{os.pathsep}{_ROOT / 'tests'}"
        env["VIB_SUITE_LOCK_PATH"] = str(lock)
        env["VIB_SUITE_LOCK_MIN_TESTS"] = "1"
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "conftest", "-o", "addopts=",
             "--collect-only", str(probe)],
            capture_output=True, text=True, env=env, cwd=str(_ROOT), timeout=120)
        assert r.returncode == 0, _all(r)
        assert "another full test run holds" not in _all(r)


class TestTheGateHandsItsOwnLockDown:
    """`gate.sh` takes the lock and THEN runs the suite, so without this the
    gate would refuse itself — the one deadlock this design has to avoid."""

    def test_a_genuinely_inherited_fd_is_accepted_without_a_second_acquisition(
        self, tmp_path, held_lock
    ):
        lock, fd = held_lock
        probe = _probe(tmp_path, "test_p.py", _TRIVIAL)
        r = _run_pytest(probe, pass_fds=(fd,), VIB_SUITE_LOCK_PATH=lock,
                        VIB_SUITE_LOCK_MIN_TESTS=1, VIB_SUITE_LOCK_FD=fd)
        assert r.returncode == 0, _all(r)
        assert "held by our caller" in r.stdout, _all(r)

    def test_the_env_var_alone_is_not_enough(self, tmp_path, held_lock):
        """THE NEGATIVE CONTROL for the handshake.

        `VIB_SUITE_LOCK_FD` names a descriptor that is NOT this lock file (it
        is not passed down at all). A bare env flag would be a bypass anybody
        could set by accident; this one is checked against the file's inode,
        so a wrong value falls through and the run refuses exactly as it
        should.
        """
        lock, _fd = held_lock
        probe = _probe(tmp_path, "test_p.py", _TRIVIAL)
        r = _run_pytest(probe, VIB_SUITE_LOCK_PATH=lock,
                        VIB_SUITE_LOCK_MIN_TESTS=1, VIB_SUITE_LOCK_FD=9)
        assert r.returncode != 0, _all(r)
        assert "another full test run holds" in _all(r)


class TestTheLockNeverVetoesADeploy:

    def test_an_unusable_lock_path_warns_and_the_run_continues(self, tmp_path):
        """`deploy/deploy.sh:42` runs this suite on the droplet as the app user
        and is "the last thing between a broken commit and production"; CI runs
        it too. A contention guard for one laptop does not get to fail a deploy
        over a permissions problem on `/tmp`, so an unusable path is a warning
        and the run proceeds."""
        probe = _probe(tmp_path, "test_p.py", _TRIVIAL)
        unusable = tmp_path / "no-such-dir" / "suite.lock"
        r = _run_pytest(probe, VIB_SUITE_LOCK_PATH=unusable, VIB_SUITE_LOCK_MIN_TESTS=1)
        assert r.returncode == 0, _all(r)
        assert "suite lock: SKIPPED" in r.stdout, _all(r)


# ══════════════════════════════════════════════════════════════════════════
# 2 · The per-test bounds
# ══════════════════════════════════════════════════════════════════════════

class TestWhyThereIsNoGracefulBound:
    """The design carried a SECOND, graceful bound — a SIGALRM that raised in
    the main thread so one test would fail and the suite would carry on. It was
    REFUTED by measurement and removed, and this is the measurement, kept as a
    pin so nobody adds it back.

    Every hang-prone test in this repo waits in `Thread.join(timeout=...)`
    (`test_render_proc.py`, `test_render_serial.py`,
    `test_charts_threading.py`), around a worker inside `@serializes_render`.
    Raising there does not interrupt the worker — it corrupts the `Thread`
    object and strands it.
    """

    def test_raising_in_a_joining_main_thread_strands_the_worker(self):
        """CPython 3.12's `Thread._wait_for_tstate_lock` has a bpo-45274
        handler that, on an exception, releases `_tstate_lock` if it is held
        and marks the thread stopped. During a *timed* join the lock is held
        for the ordinary reason — the worker is still running — so the handler
        misfires: `is_alive()` reports False for a live thread, and whatever
        lock that thread holds is never released on any timescale the suite
        can wait for.

        Consequence, and the reason this bound is gone: `assert not
        t.is_alive()` (`test_render_proc.py`, `test_render_serial.py`,
        `test_charts_threading.py`) would PASS on a stranded worker, and every
        later render would block on `render_lock`. A graceful bound that turns
        a hang into a silent pass plus a poisoned lock is worse than none.
        """
        import signal
        import threading

        class Boom(Exception):
            pass

        def handler(signum, frame):
            raise Boom()

        held = threading.Lock()
        finished = []

        def worker():
            with held:
                time.sleep(3)
            finished.append(True)

        previous = signal.signal(signal.SIGALRM, handler)
        try:
            worker_thread = threading.Thread(target=worker, name="WORKER")
            worker_thread.start()
            time.sleep(0.2)
            signal.setitimer(signal.ITIMER_REAL, 0.4)
            try:
                worker_thread.join(timeout=10)
                pytest.fail("the join was not interrupted — the probe is vacuous")
            except Boom:
                pass
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)

            # THE FINDING, in three assertions.
            assert worker_thread.is_alive() is False, (
                "if this ever reports True, CPython fixed the bpo-45274 "
                "misfire and a graceful bound can be reconsidered"
            )
            assert not finished, "the worker had not actually finished"
            assert held.locked(), (
                "the stranded worker still holds its lock — this is the half "
                "that would have blocked every later render for 420 s each"
            )
            worker_thread.join(timeout=15)
        finally:
            signal.signal(signal.SIGALRM, previous)

    def test_the_suite_ships_no_signal_based_bound(self):
        """A source pin, so re-adding it is a red test rather than a surprise."""
        text = (_ROOT / "tests" / "conftest.py").read_text()
        body = text.replace("REFUTED BY MEASUREMENT", "")
        for banned in ("setitimer(", "signal.signal(", "SIGALRM)"):
            assert banned not in body, (
                f"{banned} is back in tests/conftest.py — see "
                "TestWhyThereIsNoGracefulBound for what it does to a joining thread"
            )


class TestBoundBIsTheBackstopForAWedge:

    #: SIGALRM blocked in the main thread is what a native or kernel wedge
    #: looks like from the interpreter's side: the signal arrives and the
    #: Python handler never runs, so bound A cannot fire at all. This is the
    #: Sep 7 shape, and the only part of it a test can create portably.
    _WEDGE = (
        "import signal, time\n"
        "def test_wedges():\n"
        "    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})\n"
        "    time.sleep(60)\n"
    )

    def test_a_wedge_bound_a_cannot_reach_is_dumped_and_the_box_freed(self, tmp_path):
        wedge_log = tmp_path / "wedge.log"
        probe = _probe(tmp_path, "test_wedge.py", self._WEDGE)
        r = _run_pytest(probe, VIB_PYTEST_TIMEOUT_S=1, VIB_PYTEST_BACKSTOP_S=3,
                        VIB_WEDGE_LOG=wedge_log, VIB_SUITE_LOCK_MIN_TESTS=99999)
        assert r.returncode != 0, "the wedged run did not exit"
        assert wedge_log.exists(), "no stack dump was written"
        dump = wedge_log.read_text()
        assert "Timeout (0:00:03)!" in dump, dump[:400]
        assert "test_wedges" in dump, "the dump does not name the wedged test"

    def test_the_dump_goes_to_a_file_because_stderr_would_be_lost(self, tmp_path):
        """`exit=True` calls `_exit(1)`. Under pytest's default fd-level
        capture, fd 2 is a capture buffer that is never flushed on that path —
        so a dump sent to stderr would be lost at exactly the moment it was
        needed. Measured: the dump is in the file and not in the run's own
        output."""
        wedge_log = tmp_path / "wedge.log"
        probe = _probe(tmp_path, "test_wedge.py", self._WEDGE)
        r = _run_pytest(probe, VIB_PYTEST_TIMEOUT_S=1, VIB_PYTEST_BACKSTOP_S=3,
                        VIB_WEDGE_LOG=wedge_log, VIB_SUITE_LOCK_MIN_TESTS=99999)
        assert "Thread 0x" in wedge_log.read_text()
        assert "Thread 0x" not in _all(r), (
            "the dump reached stdout/stderr — then the file is redundant, and "
            "this test is pinning the wrong mechanism"
        )

    def test_a_clean_run_leaves_no_dump_file_behind(self, tmp_path):
        """THE NEGATIVE CONTROL: if the file were always there, its presence
        would not be evidence of anything."""
        wedge_log = tmp_path / "wedge.log"
        probe = _probe(tmp_path, "test_p.py", _TRIVIAL)
        r = _run_pytest(probe, VIB_WEDGE_LOG=wedge_log, VIB_SUITE_LOCK_MIN_TESTS=99999)
        assert r.returncode == 0, _all(r)
        assert not wedge_log.exists(), "a clean run left a wedge log behind"


# ══════════════════════════════════════════════════════════════════════════
# 3 · The shipped numbers
# ══════════════════════════════════════════════════════════════════════════

class TestTheShippedNumbers:
    """Every pin above lowers a bound so it can finish. These are the values
    the product actually ships, asserted by name, so a lowered bound in a test
    can never quietly become the default."""

    def test_the_full_run_threshold(self):
        """MEASURED, not chosen. The full suite collects ~3228; the widest
        targeted selection that could be constructed by hand — every webapp-,
        ux-, auth-, billing-, db-, account-, intake- and inference-shaped file
        at once — collects 1118. 2000 sits between them with ~1.8x clearance
        below and ~1.6x above."""
        assert suite_conftest.FULL_RUN_MIN_TESTS == 2000

    def test_the_wedge_backstop_bound(self):
        """15 minutes: it catches the Sep 7 wedge (~18 minutes before a human
        noticed) and still clears the longest worst case any test in this suite
        declares for ITSELF — 720 s, from `test_charts_threading.py`'s four
        sequential `join(timeout=180)` calls. That test is why the bound is not
        lower."""
        assert suite_conftest.WEDGE_BACKSTOP_S == 900.0
        assert suite_conftest.WEDGE_BACKSTOP_S > 720.0

    def test_the_lock_path_is_machine_wide_per_uid_and_not_tmpdir(self):
        """macOS gives each login session its own `/var/folders/...` TMPDIR, so
        a lock under `$TMPDIR` would be per-terminal and the mechanism would be
        silently inert — a false green of the shape UX-4 F-1/F-5 recorded.
        `/tmp` is shared, which is the property being used; the uid suffix and
        `O_NOFOLLOW` are what make a predictable name in a world-writable
        sticky directory safe to truncate."""
        path = suite_conftest.default_suite_lock_path()
        assert path == f"/tmp/vib-agent-suite-{os.getuid()}.lock"
        assert "TMPDIR" not in Path(path).parts
        conftext = (_ROOT / "tests" / "conftest.py").read_text()
        assert "os.O_NOFOLLOW" in conftext, "the lock open dropped O_NOFOLLOW"
        assert "0o666" not in conftext, "the lock file went world-writable again"


# ══════════════════════════════════════════════════════════════════════════
# 4 · The shared display-guard parser (UX-4 F-3)
# ══════════════════════════════════════════════════════════════════════════

class TestTheDisplayGuardParser:
    """UX-4 F-3: the UX-1 and UX-2 pins matched one bare class at column 0, so
    a GROUPED selector slipped past them — they were weaker than the
    `tests/js/harness.js` oracle they exist to protect."""

    _GROUPED = ".cta,.btn-ghost{display:inline-flex}\n.cta[hidden]{display:none}\n"

    def test_a_grouped_selector_registers_every_class_it_names(self, display_guard_audit):
        declares, guarded = display_guard_audit(self._GROUPED)
        assert declares == {"cta", "btn-ghost"}
        assert guarded == {"cta"}
        assert not declares <= guarded, "the unguarded half of a grouped rule was missed"

    def test_the_old_column_zero_parser_could_not_see_it(self):
        """THE NEGATIVE CONTROL on the fix itself: the same stylesheet through
        the parser these two pins used to carry, showing it reported nothing —
        so the fix closes a real gap rather than restating a working check."""
        import re
        old = set(re.findall(r"^\.([A-Za-z][\w-]*)\{[^}]*display:", self._GROUPED, re.M))
        assert old == set(), (
            "the old parser saw the grouped rule after all — then UX-4 F-3 was "
            "not the defect it was recorded as"
        )
