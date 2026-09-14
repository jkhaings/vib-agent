"""Session CHARTS-AGG — the chart renderer is safe to call from worker threads.

GEOM-A recorded a native segfault (`EXIT=139`) at 45 % of a full-suite run,
inside a webapp worker THREAD: `process_job` -> `_render_fail_closed` ->
`render_report` -> `render_charts` -> `_render_status_badge` ->
`Figure.add_axes` -> `transforms.set_children`. A segfault there kills the
interpreter, so it takes the whole service down, not one job. The webapp runs
`worker.process_job` through `asyncio.to_thread` under a semaphore of
`worker_concurrency` (2), and `webapp/app.py` documents that an abandoned thread
cannot be interrupted, so transiently there can be more than that.

Two properties close it, and this module pins both:

1. **The backend is never switched.** `matplotlib.use()` on an already-imported
   pyplot calls `switch_backend`, which closes every figure in the process —
   including one another thread is drawing into.
2. **No two threads are inside matplotlib at once.** matplotlib does not support
   concurrent drawing; the crash above was inside figure *construction*, which
   no amount of registry hygiene protects.

The first two tests read the source and run whether or not matplotlib is
installed. The third actually renders from several threads at once and requires
it.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path

import pytest

from vib_agent.pipeline import run_analysis
from vib_agent.report import charts as charts_mod
from vib_agent.report.generate import render_report
from vib_agent.synth.generator import make_case, make_history

_SRC = Path(__file__).resolve().parents[1] / "src" / "vib_agent"


# ── source discipline ────────────────────────────────────────────────────
#
# Read with `ast`, not with a text grep: this file's own prose, and charts.py's,
# has to be free to NAME `matplotlib.use(force=True)` and `switch_backend` while
# explaining why they are banned. A grep over stripped text can be defeated by a
# docstring; a syntax tree cannot see a comment at all.


def _dotted(node: ast.AST) -> str:
    """`matplotlib.pyplot.switch_backend` from the Attribute/Name chain."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _forces_a_backend_switch(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = _dotted(node.func)
    if name.split(".")[-1] == "switch_backend":
        return True
    if name.split(".")[-1] != "use":
        return False
    return any(
        kw.arg == "force" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in node.keywords
    )


def _imports_pyplot(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(a.name == "matplotlib.pyplot" for a in node.names)
    if isinstance(node, ast.ImportFrom):
        if node.module == "matplotlib.pyplot":
            return True
        return node.module == "matplotlib" and any(a.name == "pyplot" for a in node.names)
    return False


def _source_files() -> list[Path]:
    files = sorted(_SRC.rglob("*.py"))
    assert files, f"no source found under {_SRC}"
    return files


class TestBackendDiscipline:
    def test_nothing_under_src_forces_a_backend_switch(self):
        """`matplotlib.use(..., force=True)` on an imported pyplot switches the
        backend, and switching closes every open figure PROCESS-WIDE. Under
        `asyncio.to_thread` that reaches into a figure another job is drawing.
        The backend is selected once, guarded on `sys.modules`, and never
        switched — see report/charts.py::_init_matplotlib."""
        offenders = [
            f"{path.relative_to(_SRC.parents[1])}:{node.lineno}"
            for path in _source_files()
            for node in ast.walk(ast.parse(path.read_text()))
            if _forces_a_backend_switch(node)
        ]
        assert not offenders, (
            "a forced backend switch closes every figure in the process, "
            f"including ones other threads are drawing: {offenders}"
        )

    def test_charts_never_imports_pyplot(self):
        """Figures are built through the object API (`Figure` +
        `FigureCanvasAgg`). pyplot keeps a process-global registry of every
        figure it creates, which is what makes someone else's `close("all")`
        able to free ours; the object API registers nothing, so there is no
        shared registry and nothing to close."""
        tree = ast.parse((_SRC / "report" / "charts.py").read_text())
        offenders = [node.lineno for node in ast.walk(tree) if _imports_pyplot(node)]
        assert not offenders, f"charts.py imports pyplot at line(s) {offenders}"


# ── the renderer under concurrency ───────────────────────────────────────


class TestConcurrentRendering:
    def test_four_threads_render_the_same_case_byte_identically(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """N threads through one barrier, then all of them into `render_charts`
        at once. Two claims: every thread COMPLETES (the segfault this session
        closes did not), and every PNG is byte-identical to the same case
        rendered serially — concurrency changes the schedule, never the
        picture."""
        pytest.importorskip("matplotlib")
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        case = case.model_copy(
            update={"history": make_history(30, 1.2, 5.2, noise_pct=0.12,
                                            cadence="daily", seed=7)}
        )
        # ONE result rendered many times: `make_case` stamps the wall clock and
        # the figure header prints it, so a second case would differ in its
        # INPUT, not in the renderer.
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)

        def render(out: Path) -> dict[str, bytes]:
            render_report(result, case.machine, out, pdf=False, case=case,
                          thresholds=thresholds, profile="route")
            return {p.name: p.read_bytes() for p in charts_mod.chart_files(out)}

        reference = render(tmp_path / "serial")
        assert reference, "no figures rendered — the comparison would be vacuous"

        n = 4
        barrier = threading.Barrier(n)
        failures: list[str] = []

        def work(i: int) -> None:
            try:
                barrier.wait(timeout=60)  # nobody starts until everybody can
                got = render(tmp_path / f"thread{i}")
                assert got.keys() == reference.keys(), f"thread {i}: {sorted(got)}"
                for name, blob in reference.items():
                    assert got[name] == blob, f"thread {i}: {name} differs from the serial render"
            except BaseException as exc:  # noqa: BLE001 -- reported, not swallowed
                failures.append(f"thread {i}: {exc!r}")

        threads = [threading.Thread(target=work, args=(i,), name=f"chart{i}") for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=180)

        assert not [t for t in threads if t.is_alive()], "a chart render never finished"
        assert not failures, failures
