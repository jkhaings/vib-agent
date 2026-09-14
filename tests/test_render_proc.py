"""Session RENDER-PROC — the native renderers run in a child process.

What this file pins, and why each claim exists:

* PROD_READINESS §7 F-1 was booked CLOSED by RENDER-SERIAL behind one
  `threading.Lock`. SESSION_EMAIL1.md F-6 reopened it: a `Fatal Python error:
  Bus error` inside weasyprint's `FontConfiguration.__init__` **with
  `report/render_lock.py:105` in the frame list**. The lock was held. A lock
  serializes renders; it cannot serialize the garbage collection of the
  cairo/pango objects a render leaves behind, which runs on whatever thread
  allocates next.
* So the claim under test is no longer "renders do not overlap" (that is still
  pinned, in `test_render_serial.py`) but "**a native fault cannot reach the
  interpreter that serves requests**" — which is a claim about processes.

The load-bearing test is `TestAChildCrashDoesNotKillTheParent`: it kills a REAL
child mid-render with the two signals actually observed in production, SIGSEGV
(GEOM-A, DB-1) and SIGBUS (EMAIL-1 F-6).
"""

from __future__ import annotations

import ast
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import vib_agent
from vib_agent.pipeline import run_analysis
from vib_agent.report import charts as charts_mod
from vib_agent.report import generate as gen
from vib_agent.report import render_lock
from vib_agent.report import render_proc
from vib_agent.report.charts import (
    ChannelFigures, ChartFigure, ChartSet, FigureGeometry,
    chartset_from_dict, chartset_to_dict,
)
from vib_agent.report.render_proc import RenderChildCrashed
from vib_agent.synth.generator import make_case

#: Resolved from the IMPORTED package, for the same reason
#: `test_render_serial.py:53` does it: a worktree borrows the main checkout's
#: editable install, so a path-derived root would make the evidence a tautology.
_SRC = Path(vib_agent.__file__).resolve().parent


@pytest.fixture
def analysis(iso_table, thresholds, rules):
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    return case, run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)


def _kill_the_live_child(sig: int, *, timeout_s: float = 30.0) -> int:
    """Wait for a render child, kill it with `sig`, return its pid.

    Polls the registry rather than sleeping a fixed time: the window is the
    child's whole startup-plus-render (~0.5 s at minimum, since it must import
    the engine), so this is wide, but a fixed sleep would be a flake waiting to
    happen on a loaded box.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pids = render_proc.live_child_pids()
        if pids:
            os.kill(pids[0], sig)
            return pids[0]
        time.sleep(0.005)
    raise AssertionError("no render child was ever in flight — the test is vacuous")


#: RULED D-27 (Sep 8, operator): the SIGSEGV and SIGBUS pins do not run on macOS.
#:
#: They are not flaky — they PASS and then poison the box. Killing a live
#: matplotlib/weasyprint child with a native fault signal on this laptop leaves
#: the process in state `UE` (uninterruptible), reparented to init. `kill -9`
#: does not clear one; only a reboot does. The gate's own BOX AFTER sweep and
#: `scripts/gate/gate.sh`'s pre-flight both refuse the box afterwards, so the
#: cost of one such pin is not one red test but **every gate until a reboot**:
#: on Sep 8 the legal1 gate left pids 40167 (`charts`) and 40460 (`pdf`) wedged,
#: and the ci1 and ux5 gates then refused to start — THREE GATES LOST IN ONE DAY
#: to a 29-minute suite that had already passed the assertion.
#:
#: The cascade is visible in that run: `[11]` (SIGSEGV) passed and wedged the
#: child; `[10]` (SIGBUS), `[9]` (SIGKILL), the timeout pin and the PDF-crash
#: pin then all failed against a stale pid that `_kill_the_live_child` found
#: first. Four of the five failures measured the wedge, not the code.
#:
#: SUITE-LOCK F-6 is the reason no in-tree fix closes this: that session proved
#: the parent CANNOT reap an uninterruptible child (`render_proc.py` abandons it
#: after a bounded wait and logs the pid — which is the correct behaviour, and
#: the child still sits there). The wedge is a kernel state on Darwin, below
#: anything this repo can reach.
#:
#: SIGKILL keeps running on every platform: it is delivered by the kernel with
#: no core dump and no native fault handler, and it has never wedged a child
#: here. The claim these pins exist to defend — a native fault cannot reach the
#: interpreter that serves requests — is a claim about the LINUX droplet, and
#: CI-1's Linux workflow is where SIGSEGV and SIGBUS are now measured.
_D27_NOT_ON_MACOS = pytest.mark.skipif(
    sys.platform == "darwin",
    reason=(
        "RULED D-27: a native-fault kill wedges the render child in state UE on "
        "macOS (reboot, not kill) — three gates lost in one day to it on Sep 8, "
        "and SUITE-LOCK F-6 proved the parent cannot reap one. SIGSEGV/SIGBUS "
        "are measured on Linux CI; SIGKILL still runs here."
    ),
)


class TestAChildCrashDoesNotKillTheParent:
    """The point of the session, stated as an experiment rather than a design."""

    @pytest.mark.skipif(
        not charts_mod.matplotlib_available(), reason="matplotlib not available here"
    )
    @pytest.mark.parametrize(
        "sig",
        [
            pytest.param(signal.SIGSEGV, marks=_D27_NOT_ON_MACOS),
            pytest.param(signal.SIGBUS, marks=_D27_NOT_ON_MACOS),
            signal.SIGKILL,
        ],
    )
    def test_a_signal_killed_child_raises_and_the_parent_lives(
        self, tmp_path, analysis, thresholds, sig
    ):
        """SIGSEGV is GEOM-A's and DB-1's signal; SIGBUS is EMAIL-1 F-6's.

        Before this session each of these killed the interpreter — on the
        droplet, uvicorn and every in-flight upload, with systemd restarting
        the unit. Here the parent is still running at the end of the test,
        which is the whole claim.
        """
        case, result = analysis
        parent_pid = os.getpid()
        box: dict[str, BaseException] = {}

        def work() -> None:
            try:
                charts_mod.render_charts(result, case.machine, tmp_path,
                                         case=case, thresholds=thresholds)
            except BaseException as exc:  # noqa: BLE001 -- captured, then asserted
                box["exc"] = exc

        t = threading.Thread(target=work)
        t.start()
        killed = _kill_the_live_child(sig)
        t.join(timeout=60)
        assert not t.is_alive(), "the render never returned after the child died"

        exc = box.get("exc")
        assert isinstance(exc, RenderChildCrashed), f"want RenderChildCrashed, got {exc!r}"
        assert exc.returncode == -sig, f"want returncode {-sig}, got {exc.returncode}"
        assert exc.signal_name == sig.name
        assert os.getpid() == parent_pid, "the parent process did not survive"
        assert killed not in render_proc.live_child_pids(), "the dead child is still registered"

    def test_a_timeout_raises_and_leaves_no_child_behind(self, tmp_path, analysis, thresholds):
        """`app.py` records that a job abandoned at `job_max_runtime_s` leaves a
        thread that cannot be interrupted. If that thread is inside
        `communicate()`, the child must still die — an orphaned renderer would
        keep its share of the unit's `MemoryMax`."""
        case, result = analysis
        with pytest.raises(RenderChildCrashed) as caught:
            render_proc.run_child(
                "charts",
                {
                    "result": result.model_dump_json(),
                    "machine": case.machine.model_dump_json(),
                    "case": case.model_dump_json(),
                    "out_dir": str(tmp_path),
                    "thresholds": thresholds,
                    "fault_labels": None,
                },
                timeout_s=0.01,
            )
        assert caught.value.timed_out is True
        assert render_proc.live_child_pids() == [], "the timed-out child was not reaped"

    # D-27: this one crashes its child with SIGSEGV (below), so it takes the
    # same mark. The timeout pin above does NOT — it never sends a fault signal.
    @_D27_NOT_ON_MACOS
    @pytest.mark.skipif(
        not charts_mod.matplotlib_available(), reason="matplotlib not available here"
    )
    def test_a_crashed_pdf_child_leaves_no_truncated_report(
        self, tmp_path, analysis, thresholds
    ):
        """`webapp/worker.py` decides `job.pdf_path` from `report.pdf.exists()`.
        A PDF half-written by a child that died mid-`write_pdf` would be handed
        to an analyst as a finished report, so the child renders to
        `report.pdf.part` and renames only as its last act."""
        case, result = analysis
        out = tmp_path / "doc"
        out.mkdir()
        # Charts first, in a child that is allowed to finish, so the crash under
        # test is the PDF one and not a missing figure.
        charts = charts_mod.render_charts(result, case.machine, out,
                                          case=case, thresholds=thresholds)
        box: dict[str, BaseException] = {}

        def work() -> None:
            try:
                gen.render_pdf(result, case.machine, out, charts=charts,
                               case=case, thresholds=thresholds, profile="route")
            except BaseException as exc:  # noqa: BLE001
                box["exc"] = exc

        t = threading.Thread(target=work)
        t.start()
        _kill_the_live_child(signal.SIGSEGV)
        t.join(timeout=60)

        assert isinstance(box.get("exc"), RenderChildCrashed)
        assert not (out / "report.pdf").exists(), "a truncated report.pdf survived the crash"
        assert not (out / "report.pdf.part").exists(), "the child's scratch file survived"


#: A stand-in render child, for the pin below. It never renders anything; its
#: whole job is to be hard to reap.
#:
#: `hold=1` spawns a DETACHED GRANDCHILD that inherits fds 1 and 2 -- the
#: parent's stdout/stderr pipes -- and outlives the child. That is what makes
#: the parent's post-kill `communicate()` block: the child is dead, but the
#: pipe's write end is still open somewhere, so EOF never arrives.
#:
#: WHY NOT "a child that ignores SIGKILL", which is what the brief asked for:
#: SIGKILL cannot be caught, blocked or ignored in userspace, so no such child
#: can be written. This reproduces the OBSERVED symptom instead -- a parent
#: stuck in cleanup after the child is already gone -- and measurement says it
#: is the more faithful of the two: SEC-3's wedged children were in state `UE`,
#: which no test can create portably, but the parent's stack was identical.
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
    """Point `run_child` at `_FAKE_CHILD` and clean up everything it leaves.

    Monkeypatches `_child_argv`, which is the same seam
    `test_the_childs_cwd_cannot_shadow_this_package` already drives the real
    argv through -- so the PARENT half under test is entirely real: real
    `Popen`, real `communicate`, real timeout, real `kill`, real bounded reap.
    """
    script = tmp_path / "fake_child.py"
    script.write_text(_FAKE_CHILD)
    pidfile = tmp_path / "grandchild.pid"
    monkeypatch.setenv("FAKE_CHILD_PIDFILE", str(pidfile))
    monkeypatch.setattr(render_proc, "_child_argv",
                        lambda op: [sys.executable, str(script)])
    render_proc._abandoned.clear()
    try:
        yield pidfile
    finally:
        # Whatever the assertions did, this box goes back to clean: an
        # abandoned child is a real leaked process, and leaving one behind
        # would trip the gate's own box check on the next run.
        #
        # `returncode is None` FIRST, and it is not a nicety. An abandoned
        # child is usually already reaped (measured: `-9`), so its pid is free
        # for the kernel to reuse — and a blind `os.kill` here would signal
        # whatever process now owns that number, under the operator's own uid,
        # part-way through a 3228-test run.
        for pid, proc in list(render_proc._abandoned.items()):
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
        render_proc._abandoned.clear()


class TestAnUnreapableChildIsAbandonedRatherThanWaitedOn:
    """SEC-3 F-2 / `outputs/PROD_READINESS.md:812` — the defect this closes.

    `run_child` used to do `proc.kill()` and then a bare `proc.communicate()`
    with no timeout, and its `finally` did a bare `proc.wait()`. Two unbounded
    waits on one path, so the 120 s render bound became an unbounded wait
    whenever the child could not be reaped — on a worker thread, holding
    `render_lock` and a `worker_concurrency` slot for the life of the process,
    **with no log line at all**.

    Observed twice: a child in state `UE` for 2h11m at 0.0% CPU (SEC-3 §8), and
    one `gate.sh` run wedged ~18 minutes on an otherwise idle box (ROADMAP
    FINDING, Sep 7) — where contention was not required and the untimed parent
    wait was the whole defect.
    """

    def _run_against_the_fake_child(self, tmp_path, thresholds, analysis):
        case, result = analysis
        return render_proc.run_child(
            "charts",
            {
                "result": result.model_dump_json(),
                "machine": case.machine.model_dump_json(),
                "case": case.model_dump_json(),
                "out_dir": str(tmp_path),
                "thresholds": thresholds,
                "fault_labels": None,
            },
            timeout_s=0.5,
        )

    def test_a_child_whose_pipe_outlives_it_is_abandoned_within_the_bound(
        self, tmp_path, analysis, thresholds, monkeypatch, fake_child
    ):
        """The claim is about a NUMBER, so the test reads one.

        `REAP_TIMEOUT_S` is monkeypatched down here rather than waited out, so
        what is pinned is that the constant GOVERNS the wait — which is the
        actual claim, and is stronger than watching the shipped 10 s elapse.
        The shipped value is pinned separately below.
        """
        monkeypatch.setenv("FAKE_CHILD_HOLD", "1")
        monkeypatch.setattr(render_proc, "REAP_TIMEOUT_S", 1.5)

        started = time.monotonic()
        with pytest.raises(RenderChildCrashed) as caught:
            self._run_against_the_fake_child(tmp_path, thresholds, analysis)
        elapsed = time.monotonic() - started

        assert caught.value.timed_out is True
        assert caught.value.abandoned is True, (
            "the child outlived the reap bound but was not recorded as abandoned"
        )
        # Before the fix this call did not return at all. The upper bound is
        # what the whole session is about; the lower one proves the reap was
        # actually attempted rather than skipped.
        assert 1.5 <= elapsed < 8.0, f"reap was not bounded by REAP_TIMEOUT_S: {elapsed:.2f}s"

    def test_the_abandoned_pid_is_named_and_no_longer_counted_as_in_flight(
        self, tmp_path, analysis, thresholds, monkeypatch, fake_child
    ):
        monkeypatch.setenv("FAKE_CHILD_HOLD", "1")
        monkeypatch.setattr(render_proc, "REAP_TIMEOUT_S", 1.5)
        with pytest.raises(RenderChildCrashed):
            self._run_against_the_fake_child(tmp_path, thresholds, analysis)

        abandoned = render_proc.abandoned_child_pids()
        assert len(abandoned) == 1, f"want exactly one abandoned pid, got {abandoned}"
        assert abandoned[0] not in render_proc.live_child_pids(), (
            "an abandoned child is still counted as in flight — `render_lock` "
            "bounds children to one, so this would block every later render"
        )

    def test_the_abandon_is_logged_with_the_pid(
        self, tmp_path, analysis, thresholds, monkeypatch, fake_child, caplog
    ):
        """SEC-3 called the silence half of what made F-2 so bad: the service
        lost a worker slot permanently and nothing said so."""
        monkeypatch.setenv("FAKE_CHILD_HOLD", "1")
        monkeypatch.setattr(render_proc, "REAP_TIMEOUT_S", 1.5)
        with caplog.at_level("WARNING", logger="vib_agent.report"):
            with pytest.raises(RenderChildCrashed):
                self._run_against_the_fake_child(tmp_path, thresholds, analysis)

        pid = render_proc.abandoned_child_pids()[0]
        assert any(str(pid) in r.getMessage() and "ABANDONED" in r.getMessage()
                   for r in caplog.records), (
            f"no WARNING naming pid {pid}; records={[r.getMessage() for r in caplog.records]}"
        )

    def test_a_reapable_child_is_still_reaped_and_never_abandoned(
        self, tmp_path, analysis, thresholds, monkeypatch, fake_child
    ):
        """THE NEGATIVE CONTROL (common law #11).

        Identical child, identical kill, identical bound — only the grandchild
        holding the pipe is gone. If this also reported "abandoned", the pin
        above would be measuring the timeout rather than the wedge, and the
        whole mechanism could be inert without a single test noticing.
        """
        monkeypatch.setenv("FAKE_CHILD_HOLD", "0")
        monkeypatch.setattr(render_proc, "REAP_TIMEOUT_S", 1.5)

        started = time.monotonic()
        with pytest.raises(RenderChildCrashed) as caught:
            self._run_against_the_fake_child(tmp_path, thresholds, analysis)
        elapsed = time.monotonic() - started

        assert caught.value.timed_out is True
        assert caught.value.abandoned is False, "a reapable child was abandoned"
        assert render_proc.abandoned_child_pids() == []
        # The reap itself, with the 0.5 s first timeout and the child's own
        # interpreter spawn subtracted. Deliberately generous: this file's own
        # `_kill_the_live_child` warns that a fixed wall-clock window is "a
        # flake waiting to happen on a loaded box", and this runs alongside
        # 3227 other tests. The claim that matters is `abandoned is False`
        # above; this only says the reap did not quietly sit out the bound.
        assert elapsed < 1.5 + 3.0, (
            f"a killable child took {elapsed:.2f}s — it should die in "
            "milliseconds, and if it did not, the bound above proves nothing"
        )

    def test_the_render_lock_is_free_after_an_abandon(
        self, tmp_path, analysis, thresholds, monkeypatch, fake_child
    ):
        """`render_lock` bounds children in flight to ONE, so a wedged child
        that kept the lock would block every later render process-wide — which
        is exactly what PROD_READINESS:812 says made this worse, not better.

        Nothing releases the lock explicitly; the exception propagating out of
        `serialized_render`'s `finally` is what does it. This asserts the
        outcome rather than trusting the mechanism.
        """
        monkeypatch.setenv("FAKE_CHILD_HOLD", "1")
        monkeypatch.setattr(render_proc, "REAP_TIMEOUT_S", 1.5)

        with pytest.raises(RenderChildCrashed):
            with render_lock.serialized_render():
                self._run_against_the_fake_child(tmp_path, thresholds, analysis)

        assert not render_lock.holds_render_lock()
        with render_lock.serialized_render() as took_it:
            assert took_it, "the lock was not free after an abandoned child"

    def test_the_shipped_bound_is_ten_seconds(self):
        """A reapable child dies in milliseconds and child STARTUP measures
        0.41-0.47 s (SESSION_RENDERPROC.md §7), so 10 s is ~20x the slowest
        thing a healthy child does and never fires on a working box. It bounds
        one render's worst case at 130 s, still far inside
        `job_max_runtime_s: 600`."""
        assert render_proc.REAP_TIMEOUT_S == 10.0
        assert render_proc.RENDER_TIMEOUT_S == 120.0

    def test_an_abandoned_child_is_still_the_ratified_retryable_code(self):
        """Wire law #5: `abandoned` is a new FACT about the failure, not a new
        failure mode. No ninth taxonomy row, same mapping as every other
        `RenderChildCrashed`."""
        from vib_agent.webapp.jobs import ERROR_TAXONOMY

        exc = RenderChildCrashed("x", op="pdf", timed_out=True, abandoned=True)
        assert exc.error_code in ERROR_TAXONOMY
        assert exc.failure_kind == "server_error"
        assert exc.retryable is True


class TestTheCrashMapsToTheRatifiedTaxonomy:
    """Wire law #5, satisfied by mapping rather than by a ninth code."""

    def test_the_exception_carries_a_code_that_exists_in_the_taxonomy(self):
        from vib_agent.webapp.jobs import ERROR_TAXONOMY

        code = RenderChildCrashed.error_code
        assert code in ERROR_TAXONOMY, (
            f"{code!r} is not a ratified S7 code — wire law #5 requires a new one "
            "to be proposed to the operator, not invented at a call site"
        )
        row = ERROR_TAXONOMY[code]
        assert row.kind == RenderChildCrashed.failure_kind == "server_error"
        assert row.retryable is RenderChildCrashed.retryable is True, (
            "a render crash is our fault and the analyst's file was fine — it must be retryable"
        )

    def test_no_new_taxonomy_row_was_added(self):
        """The session adds a failure MODE, not a failure CODE. Stated here so
        that adding one later trips this file as well as
        `test_terminal_guarantee.py`'s `len(ERROR_TAXONOMY) <= 8`."""
        from vib_agent.webapp.jobs import ERROR_TAXONOMY

        assert len(ERROR_TAXONOMY) == 8, (
            f"the taxonomy moved to {len(ERROR_TAXONOMY)} rows; RENDER-PROC added none"
        )

    def test_the_worker_does_not_catch_it(self):
        """The mapping only works because `worker.py` lets it through to
        `app.py::_run_guarded`. `worker.py` is read-only to this session, so
        this reads its source rather than changing it — if a future session
        wraps those calls in a try/except, a render crash silently stops being
        retryable and this says so."""
        worker = (_SRC / "webapp" / "worker.py").read_text()
        tree = ast.parse(worker)
        finalize = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_finalize_markdown_and_pdf"
        )
        handlers = [n for n in ast.walk(finalize) if isinstance(n, ast.ExceptHandler)]
        assert not handlers, (
            "_finalize_markdown_and_pdf now catches exceptions; a RenderChildCrashed "
            "may no longer reach app.py::_run_guarded and become internal_error"
        )


class TestTheParentNeverTouchesTheEngines:
    """F-6's actual requirement: nothing for a later GC to fault on.

    Serializing the render was not enough because the crash happened during
    COLLECTION of objects a finished render had left in the heap. The only way
    to have nothing to collect is to never allocate it here.
    """

    def test_a_whole_document_render_imports_neither_engine_in_the_parent(
        self, tmp_path, analysis, thresholds
    ):
        """Measured in a FRESH interpreter, not in the pytest process.

        The pytest process has already imported matplotlib — this very file's
        skip-guards call `matplotlib_available()`, and so do four other test
        modules — so asking `sys.modules` here would answer a question about
        the test runner rather than about a render. The subprocess does nothing
        but render one document and report what it loaded.
        """
        case, result = analysis
        (tmp_path / "case.json").write_text(case.model_dump_json())
        (tmp_path / "result.json").write_text(result.model_dump_json())
        (tmp_path / "thresholds.json").write_text(json.dumps(thresholds))
        probe = """
import json, sys
from pathlib import Path
from vib_agent.models import AnalysisResult, Case
from vib_agent.report.generate import render_report

d = Path(sys.argv[1])
case = Case.model_validate_json((d / "case.json").read_text())
result = AnalysisResult.model_validate_json((d / "result.json").read_text())
written = render_report(result, case.machine, d / "doc", pdf=True, case=case,
                        thresholds=json.loads((d / "thresholds.json").read_text()),
                        profile="route")
leaked = sorted(m for m in sys.modules
                if m.split(".")[0] in {"weasyprint", "matplotlib"})
print(json.dumps({"leaked": leaked,
                  "md": (d / "doc" / "report.md").exists(),
                  "charts": bool(written.get("charts") and written["charts"].available)}))
"""
        out = subprocess.run([sys.executable, "-c", probe, str(tmp_path)],
                             env=render_proc._child_env(), capture_output=True,
                             text=True, timeout=300)
        assert out.returncode == 0, out.stderr
        report = json.loads(out.stdout.strip().splitlines()[-1])
        assert report["md"], "the probe did not actually render a document"
        if charts_mod.matplotlib_available():
            assert report["charts"], "the probe rendered no figures — it proves nothing"
        assert not report["leaked"], (
            "the parent imported a native renderer during a document render — "
            f"{report['leaked']}. A module in the parent means objects in the parent, "
            "and objects mean a GC pass there, which is exactly how EMAIL-1 F-6 died."
        )

    def test_write_pdf_happens_only_in_the_child_entrypoint(self):
        """RENDER-SERIAL pinned that every `write_pdf` is under the lock. That
        pin still holds and still lives in `test_render_serial.py`; this is the
        stronger claim the boundary makes possible — there is exactly ONE
        module under `src/` where it can happen at all."""
        offenders = sorted(_calls_named("write_pdf") - {"report/_render_child.py"})
        assert not offenders, f"write_pdf outside the render child: {offenders}"

    def test_figures_are_built_only_where_charts_builds_them(self):
        offenders = sorted(_calls_named("_new_figure") - {"report/charts.py"})
        assert not offenders, f"_new_figure outside report/charts.py: {offenders}"

    def test_weasyprint_is_imported_only_in_the_child_entrypoint(self):
        offenders = sorted(_imports_of("weasyprint") - {"report/_render_child.py"})
        assert not offenders, f"weasyprint imported outside the render child: {offenders}"


def _py_files() -> list[Path]:
    return sorted(_SRC.rglob("*.py"))


def _rel(p: Path) -> str:
    return str(p.relative_to(_SRC))


def _calls_named(func: str) -> set[str]:
    """Files under `src/` containing a call whose callee is named `func`."""
    found: set[str] = set()
    for path in _py_files():
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name == func:
                found.add(_rel(path))
    return found


def _imports_of(module: str) -> set[str]:
    found: set[str] = set()
    for path in _py_files():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                if any(a.name.split(".")[0] == module for a in node.names):
                    found.add(_rel(path))
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] == module:
                    found.add(_rel(path))
    return found


class TestTheChildRunsThisPackage:
    def test_the_child_env_pins_the_imported_package(self):
        """A worktree's `.venv` is a symlink to the main checkout's, so a child
        that inherited only the ambient environment would render the OTHER
        tree's code and every measurement in this session would be a lie."""
        env = render_proc._child_env()
        first = env["PYTHONPATH"].split(os.pathsep)[0]
        assert Path(first).resolve() == _SRC.parent, (
            f"child PYTHONPATH starts at {first!r}, not this package's parent {_SRC.parent}"
        )

    def test_the_child_actually_imports_this_package(self):
        out = subprocess.run(
            [sys.executable, "-c", "import vib_agent; print(vib_agent.__file__)"],
            env=render_proc._child_env(), capture_output=True, text=True, timeout=60,
        )
        assert out.returncode == 0, out.stderr
        assert Path(out.stdout.strip()).resolve() == _SRC / "__init__.py"

    def test_the_childs_cwd_cannot_shadow_this_package(self, tmp_path):
        """`-m` puts the cwd on sys.path AHEAD of PYTHONPATH, so a directory
        holding a `vib_agent/` would win.

        Exercised by running the REAL child, with the REAL argv, from a cwd
        holding a broken decoy — not by asserting a flag is present. The decoy
        raises on import, so if it shadowed, the child could not start.
        """
        work = tmp_path / "work"
        work.mkdir()
        decoy = work / "vib_agent"
        decoy.mkdir()
        (decoy / "__init__.py").write_text("raise ImportError('decoy package won')\n")

        out = subprocess.run(
            render_proc._child_argv("pdf"), cwd=work, env=render_proc._child_env(),
            input=json.dumps({"html": "<html><body>x</body></html>",
                              "out_dir": str(tmp_path)}),
            capture_output=True, text=True, timeout=120,
        )
        assert out.returncode == 0, (
            "the child could not start from a cwd containing a decoy vib_agent/ — "
            f"the decoy shadowed this package.\nstderr: {out.stderr}"
        )
        assert render_proc.RESULT_PREFIX in out.stdout

    def test_engine_chatter_on_stdout_cannot_corrupt_the_result(self, tmp_path):
        """weasyprint prints an installation notice straight to stdout at import
        time when its native libs are missing (`weasyprint/text/ffi.py:455`), and
        the C libraries under it can write to fd 1 directly. The child claims fd
        1 for the protocol and points fd 1 itself at stderr, so none of that can
        reach the parent's channel."""
        probe = (
            "import os, sys\n"
            "from vib_agent.report._render_child import _claim_stdout, _emit\n"
            "ch = _claim_stdout()\n"
            "print('a python print that must not reach the parent')\n"
            "os.write(1, b'raw fd-1 bytes with no newline')\n"
            "sys.stdout.write('more noise')\n"
            "_emit({'status': 'ok', 'probe': True}, ch)\n"
        )
        out = subprocess.run([sys.executable, "-P", "-c", probe],
                             env=render_proc._child_env(), capture_output=True,
                             text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        lines = out.stdout.splitlines()
        assert len(lines) == 1, f"noise reached the protocol channel: {lines}"
        assert json.loads(lines[0][len(render_proc.RESULT_PREFIX):]) == {
            "status": "ok", "probe": True}
        assert "raw fd-1 bytes" in out.stderr, "the noise vanished instead of moving to stderr"

    def test_the_child_is_spawned_never_forked(self):
        """`subprocess` + `-m` has spawn semantics unconditionally. This pins
        that the repo has not acquired a `multiprocessing` renderer since — on
        Linux/3.12, which the droplet runs, that module's DEFAULT start method
        is `fork`, and forking a threaded uvicorn is how you turn one crash
        into a deadlock."""
        offenders = sorted(_imports_of("multiprocessing"))
        assert not offenders, (
            f"multiprocessing imported under src/: {offenders}. The render child is a "
            "subprocess for a reason — see report/render_proc.py."
        )


class TestEngineAbsentIsNotACrash:
    """The pre-existing degrade must survive the boundary unchanged: a host with
    no weasyprint still gets markdown, and no error."""

    def test_the_pdf_path_returns_none_when_the_child_finds_no_engine(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            render_proc, "run_child",
            lambda op, payload, **kw: {"status": "engine_absent", "reason": "import"},
        )
        assert gen._pdf_from_html("<html></html>", tmp_path) is None
        assert not (tmp_path / "report.pdf").exists()

    def test_charts_returns_an_empty_set_when_the_child_finds_no_engine(
        self, tmp_path, monkeypatch, analysis, thresholds
    ):
        case, result = analysis
        monkeypatch.setattr(
            render_proc, "run_child",
            lambda op, payload, **kw: {"status": "engine_absent", "reason": "import"},
        )
        charts = charts_mod.render_charts(result, case.machine, tmp_path,
                                          case=case, thresholds=thresholds)
        assert charts == ChartSet()
        assert charts.available is False

    def test_a_crash_is_not_confused_with_an_absent_engine(self, tmp_path, monkeypatch):
        """The distinction `webapp/parsing.py:113` cannot make: it tests only
        `returncode != 0`, so a signal-killed parser is reported as a bad file.
        A signal-killed RENDERER must never be reported as a missing engine —
        that would turn a repeating crash into silent markdown-only output."""
        def boom(op, payload, **kw):
            raise RenderChildCrashed("boom", op=op, returncode=-signal.SIGSEGV)

        monkeypatch.setattr(render_proc, "run_child", boom)
        with pytest.raises(RenderChildCrashed):
            gen._pdf_from_html("<html></html>", tmp_path)


class TestTheChartSetSurvivesTheBoundary:
    """The manifest carries relative paths and presentation text — never image
    bytes — so this is a total encoding, and it is checked as one."""

    def _figure(self, key: str) -> ChartFigure:
        return ChartFigure(
            key=key, rel_path=f"charts/{key}.png", title="T", alt="A", caption="C",
            header=(("Speed", "1800 rpm"), ("Fmax", "400 Hz")),
            geometry=FigureGeometry(fmax=400.0, focus_max=220.0, has_context_strip=True,
                                    label_rows=2, collisions=0, n_markers=5, n_matched=3),
        )

    def test_a_populated_manifest_round_trips_to_equality(self):
        original = ChartSet(
            status=self._figure("status_badge"),
            channels=[ChannelFigures(axis="y", label="Y (radial)",
                                     figures=(self._figure("spectrum_y"),))],
            trend=self._figure("trend"),
            available=True,
        )
        encoded = chartset_to_dict(original)
        # json.dumps on the ENCODED form, not only the round trip: a future
        # FigureGeometry field arriving as a numpy scalar round-trips fine
        # through Python objects and raises TypeError here, which is where a
        # boundary failure should surface.
        restored = chartset_from_dict(json.loads(json.dumps(encoded)))
        assert restored == original

    def test_tuples_come_back_as_tuples(self):
        """JSON has one sequence type and these dataclasses are frozen. A list
        where a tuple belongs makes `ChartFigure` unhashable and compares
        unequal to the thing it was built from."""
        restored = chartset_from_dict(json.loads(json.dumps(chartset_to_dict(
            ChartSet(status=self._figure("s"),
                     channels=[ChannelFigures(axis="y", label="Y",
                                              figures=(self._figure("f"),))]),
        ))))
        assert isinstance(restored.status.header, tuple)
        assert all(isinstance(row, tuple) for row in restored.status.header)
        assert isinstance(restored.channels[0].figures, tuple)
        hash(restored.status)  # frozen dataclass must still be hashable

    def test_an_empty_manifest_round_trips(self):
        assert chartset_from_dict(json.loads(json.dumps(chartset_to_dict(ChartSet())))) == ChartSet()

    def test_the_computed_property_is_not_serialized_but_still_works(self):
        """`focus_pct` is a property, not a field. `asdict` must not write it,
        and the decoder must not need it."""
        payload = chartset_to_dict(ChartSet(status=self._figure("s")))
        assert "focus_pct" not in payload["status"]["geometry"]
        restored = chartset_from_dict(json.loads(json.dumps(payload)))
        assert restored.status.geometry.focus_pct == pytest.approx(55.0)
