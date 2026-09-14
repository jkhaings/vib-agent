"""Session RENDER-SERIAL — one lock over both native renderers in `report/`.

Session CHARTS-AGG serialized matplotlib and left the PDF engine, which runs on
the SAME webapp worker threads moments later, unserialized. DB-1 then observed
the other half of the same bug: a full-suite gate died `EXIT=139`,
`Fatal Python error: Segmentation fault`, no summary line, with the faulting
stack in a `concurrent.futures.thread` worker —

    Garbage-collecting -> tinycss2 parse_component_value_list
      -> weasyprint CSS.__init__ -> get_all_computed_styles -> document._render
      -> weasyprint write_pdf -> report/generate.py::_pdf_from_html
      -> ::render_drafted_pdf -> webapp/worker.py:319 -> webapp/app.py:1149

An immediate re-run of the identical tree was clean — a race, not a
deterministic failure, and the same signature GEOM-A reported for matplotlib. A
native segfault kills the interpreter, so it takes uvicorn and every in-flight
upload with it, not one job.

Three properties close it, and this module pins each:

1. **Every call into either native renderer is under the lock** — read from the
   syntax tree, not from a caller's good intentions.
2. **A whole document enters the lock exactly ONCE** — the call graph nests
   (`render_report` calls `render_pdf`, which the webapp worker ALSO calls
   directly), and a lock that quietly re-enters is the bug shape this session
   exists to remove.
3. **No two threads are inside a renderer at the same time** — measured, once
   against fake engines (so it runs anywhere) and once against the real ones.

`tests/test_charts_threading.py` is deliberately NOT edited: its render-twice
byte-equality and four-thread barrier are the regression net for this edit, and
a pin you edit while changing what it pins is not a pin.
"""

from __future__ import annotations

import ast
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

import vib_agent
from vib_agent.models import AnalysisResult, Case
from vib_agent.report import charts as charts_mod
from vib_agent.report import render_proc
from vib_agent.report import generate as gen
from vib_agent.report import render_lock
from vib_agent.pipeline import run_analysis
from vib_agent.synth.generator import make_case

#: Resolved from the IMPORTED package, not from this file's location. A worktree
#: borrows the main checkout's editable install, so `parents[1]/src` would read
#: this branch's source no matter which tree `PYTHONPATH` actually selected —
#: and the "these pins fail on master" evidence would be a tautology.
_SRC = Path(vib_agent.__file__).resolve().parent


# ── reading the source ───────────────────────────────────────────────────
#
# With `ast`, not a text grep, for the reason test_charts_threading.py gives:
# this module's prose and generate.py's both have to be free to NAME
# `write_pdf` while explaining how it is guarded. A grep over stripped text is
# defeated by a docstring; a syntax tree cannot see a comment at all.

#: The context manager that takes the lock, and the decorator form of it.
_LOCK_CONTEXT = "serialized_render"
_LOCK_DECORATORS = {"serializes_render", "_serialized"}


def _dotted(node: ast.AST) -> str:
    """`weasyprint.HTML.write_pdf` from the Attribute/Name chain.

    Stops at anything that is not an Attribute or a Name, so the receiver of
    `HTML(...).write_pdf(...)` — a Call — still yields `write_pdf`.
    """
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _tail(node: ast.AST) -> str:
    return _dotted(node).split(".")[-1]


def _takes_the_lock(item: ast.withitem) -> bool:
    expr = item.context_expr
    return isinstance(expr, ast.Call) and _tail(expr.func) == _LOCK_CONTEXT


def _unprotected(tree: ast.AST, wanted: str) -> list[int]:
    """Line numbers of every `wanted(...)` call NOT under the render lock.

    "Under the lock" is either form the codebase uses: lexically inside a
    `with serialized_render():`, or anywhere inside a function carrying the
    decorator form. Both take the same lock in `report/render_lock.py`.
    """
    offenders: list[int] = []

    def visit(node: ast.AST, protected: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            protected = protected or any(
                _tail(dec) in _LOCK_DECORATORS for dec in node.decorator_list
            )
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            if any(_takes_the_lock(item) for item in node.items):
                # The body is protected; the context expressions themselves are
                # evaluated before the lock is held, so they are not.
                for item in node.items:
                    visit(item.context_expr, protected)
                for child in node.body:
                    visit(child, True)
                return

        if isinstance(node, ast.Call) and _tail(node.func) == wanted and not protected:
            offenders.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            visit(child, protected)

    visit(tree, False)
    return offenders


def _source_files() -> list[Path]:
    files = sorted(_SRC.rglob("*.py"))
    assert files, f"no source found under {_SRC}"
    return files


def _scan(wanted: str) -> list[str]:
    return [
        f"{path.relative_to(_SRC.parent)}:{line}"
        for path in _source_files()
        for line in _unprotected(ast.parse(path.read_text()), wanted)
    ]


class TestEveryRendererCallIsUnderTheLock:
    """The source-discipline half. Runs whether or not either engine is
    installed, because it reads files rather than rendering."""

    def test_every_write_pdf_call_site_is_under_the_lock(self):
        """weasyprint's `write_pdf` is the frame DB-1's segfault died in.
        Serializing it in the CALLER would work today and break on the next
        refactor, so the guard sits at the call itself — and this reads the
        syntax tree to prove there is no second, unguarded one.

        Scoped to `src/`: `tests/test_report_html.py` calls `write_pdf`
        directly to check the stylesheet, single-threaded, and is not an
        offender."""
        offenders = _scan("write_pdf")
        assert not offenders, (
            "weasyprint is not thread-safe and a segfault inside it kills the "
            f"interpreter, not the job — unguarded write_pdf at: {offenders}"
        )

    def test_every_figure_is_built_under_the_lock(self):
        """The matplotlib half, stated the same way. `_new_figure` is the only
        way a figure is created in `charts.py` and its docstring claims it is
        only ever called from a serialized function; this is that claim checked
        rather than trusted."""
        offenders = _scan("_new_figure")
        assert not offenders, (
            "matplotlib does not support concurrent drawing and GEOM-A's crash "
            f"was inside figure CONSTRUCTION — unguarded _new_figure at: {offenders}"
        )


class TestTheLockItself:
    def test_the_render_lock_is_not_reentrant(self):
        """A plain `Lock`, not the `RLock` CHARTS-AGG used.

        An RLock makes accidental nesting harmless, which sounds like a virtue
        and is the reason the old lock could be taken four times per document
        without anyone noticing. The thread-local guard is what prevents
        nesting here; a non-reentrant lock is what stops that guard from being
        quietly optional."""
        assert isinstance(render_lock._LOCK, type(threading.Lock())), (
            "the render lock must not be reentrant — see render_lock.py"
        )

    def test_nesting_passes_through_instead_of_re_entering(self):
        """The guard, at its narrowest: three nested blocks, one acquisition,
        and the lock free again at the end."""
        render_lock.reset_acquisitions()
        with render_lock.serialized_render() as outer:
            with render_lock.serialized_render() as inner:
                assert render_lock.holds_render_lock()
        assert (outer, inner) == (True, False)
        assert render_lock.acquisitions() == 1
        assert not render_lock.holds_render_lock()
        assert not render_lock._LOCK.locked()

    def test_the_flag_is_cleared_when_a_render_raises(self):
        """A stranded thread-local would let the NEXT render on this thread
        skip the lock altogether — a silent loss of the whole property."""
        with pytest.raises(RuntimeError):
            with render_lock.serialized_render():
                raise RuntimeError("render blew up")
        assert not render_lock.holds_render_lock()
        assert not render_lock._LOCK.locked()


# ── rendering under concurrency ──────────────────────────────────────────


class _Engines:
    """Counts how many threads are inside a native renderer at once."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self.inside = 0
        self.entries: list[str] = []
        self.overlaps: list[str] = []

    @contextmanager
    def inside_engine(self, who: str):
        with self._guard:
            self.inside += 1
            self.entries.append(who)
            if self.inside > 1:
                self.overlaps.append(f"{who} (with {self.inside - 1} other(s))")
        try:
            # Widen the window. Without this the two threads can miss each
            # other by luck and the test passes for the wrong reason — which is
            # exactly how 80 concurrent renders on unmodified master came back
            # clean (SESSION_CHARTSAGG.md §3).
            time.sleep(0.05)
            yield
        finally:
            with self._guard:
                self.inside -= 1


def _run_on_two_threads(work) -> list[str]:
    """Both threads released together through one barrier. Returns failures."""
    barrier = threading.Barrier(2)
    failures: list[str] = []

    def target(i: int) -> None:
        try:
            barrier.wait(timeout=60)
            work(i)
        except BaseException as exc:  # noqa: BLE001 -- reported, not swallowed
            failures.append(f"thread {i}: {exc!r}")

    threads = [threading.Thread(target=target, args=(i,), name=f"render{i}") for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)
    assert not [t for t in threads if t.is_alive()], "a render never finished"
    return failures


@pytest.fixture
def analysis(iso_table, thresholds, rules):
    """One case, one result, rendered many times. `make_case` stamps the wall
    clock and the figure header prints it, so a second case would differ in its
    INPUT rather than in the renderer."""
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    return case, run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)


class TestConcurrentRendering:
    def test_two_threads_never_overlap_in_the_render_children(
        self, tmp_path, monkeypatch, analysis, thresholds
    ):
        """Two threads are never inside a render child at the same time.

        REPLACES two tests, and the reason is recorded rather than quietly
        dropped (outputs/SESSION_RENDERPROC.md §5). Session RENDER-PROC moved
        both native engines into a child process, and BOTH old pins instrumented
        the parent:

        * `test_two_threads_never_overlap_in_a_fake_engine` injected a fake
          `weasyprint` into `sys.modules`. A child does not inherit a
          `sys.modules` patch, so the fake stopped being reached — the test kept
          passing on its `render_charts` half alone, which is worse than
          failing.
        * `test_two_threads_never_overlap_in_the_real_engines` monkeypatched
          `charts._new_figure` and `weasyprint.HTML.write_pdf` in the parent and
          asserted it had entered them. After the boundary it cannot, and it
          failed honestly.

        The property they were both written for is unchanged, so it is asserted
        where the work now happens: at `run_child`. This is ALSO engine-
        independent in a way the fake-engine pin was only approximately —
        `run_child` is entered whether or not weasyprint and matplotlib are
        installed, because the parent no longer knows.
        """
        case, result = analysis
        engines = _Engines()
        real_run_child = render_proc.run_child

        def counted_run_child(op, payload, **kwargs):
            with engines.inside_engine(f"child:{op}"):
                return real_run_child(op, payload, **kwargs)

        monkeypatch.setattr(render_proc, "run_child", counted_run_child)

        def work(i: int) -> None:
            out = tmp_path / f"thread{i}"
            out.mkdir()
            gen.render_report(
                result, case.machine, out, pdf=True, case=case,
                thresholds=thresholds, profile="route",
            )
            assert (out / "report.md").read_bytes(), "empty report.md"

        failures = _run_on_two_threads(work)
        assert not failures, failures
        assert "child:charts" in engines.entries, "no charts child — the test is vacuous"
        assert "child:pdf" in engines.entries, "no pdf child — the test is vacuous"
        assert not engines.overlaps, (
            "two threads had a render child in flight at the same time: "
            f"{engines.overlaps} (entries: {engines.entries})"
        )

    def test_only_one_render_child_is_ever_in_flight(
        self, tmp_path, monkeypatch, analysis, thresholds
    ):
        """The same claim read off the child registry rather than a wrapper.

        This is the one that matters for `MemoryMax=1500M` in
        `deploy/vibagent.service`: the cgroup holds the parent plus whatever
        children exist, so "at most one child" is a memory bound, not only a
        concurrency one.
        """
        case, result = analysis
        seen: list[int] = []
        real_run_child = render_proc.run_child

        def watching_run_child(op, payload, **kwargs):
            seen.append(len(render_proc.live_child_pids()))
            return real_run_child(op, payload, **kwargs)

        monkeypatch.setattr(render_proc, "run_child", watching_run_child)

        def work(i: int) -> None:
            out = tmp_path / f"thread{i}"
            out.mkdir()
            gen.render_report(result, case.machine, out, pdf=True, case=case,
                              thresholds=thresholds, profile="route")

        failures = _run_on_two_threads(work)
        assert not failures, failures
        assert seen, "no child was ever spawned — the test is vacuous"
        assert max(seen) == 0, f"a child was already in flight at spawn time: {seen}"
        assert render_proc.live_child_pids() == [], "a child outlived its render"


class TestOneAcquisitionPerDocument:
    """The "no nested acquisition" claim, stated as a number rather than as an
    absence of deadlock. On master the same render takes the lock four times
    (once per figure builder) and never takes it around `write_pdf` at all."""

    def _count(self, fn) -> int:
        render_lock.reset_acquisitions()
        fn()
        return render_lock.acquisitions()

    def test_a_deterministic_document_enters_the_lock_once(
        self, tmp_path, analysis, thresholds
    ):
        case, result = analysis
        taken = self._count(lambda: gen.render_report(
            result, case.machine, tmp_path / "det", pdf=True, case=case,
            thresholds=thresholds, profile="route",
        ))
        assert taken == 1, f"render_report took the render lock {taken} times, want 1"

    def test_a_drafted_document_enters_the_lock_once(self, tmp_path, analysis, thresholds):
        case, result = analysis
        taken = self._count(lambda: gen.write_drafted_report(
            "# Draft\n\nNarrative prose.\n", result, tmp_path / "drafted", pdf=True,
            machine=case.machine, case=case, thresholds=thresholds, profile="route",
        ))
        assert taken == 1, f"write_drafted_report took the render lock {taken} times, want 1"

    def test_the_workers_own_entry_points_enter_the_lock_once(
        self, tmp_path, analysis, thresholds
    ):
        """`webapp/worker.py` calls `render_pdf` and `render_drafted_pdf`
        directly on its degraded and drafted lanes — they are entry points in
        their own right, not just callees of `render_report`."""
        case, result = analysis
        out = tmp_path / "worker"
        out.mkdir()
        taken = self._count(lambda: gen.render_pdf(
            result, case.machine, out, case=case, thresholds=thresholds,
            profile="route", markdown_text="# Fallback\n",
        ))
        assert taken == 1, f"render_pdf took the render lock {taken} times, want 1"

        taken = self._count(lambda: gen.render_drafted_pdf(
            "# Draft\n", result, out, machine=case.machine, case=case,
            thresholds=thresholds, profile="route", markdown_text="# Draft\n",
        ))
        assert taken == 1, f"render_drafted_pdf took the render lock {taken} times, want 1"
