"""One process-wide lock for every native renderer this package drives.

Session RENDER-SERIAL (PROD_READINESS §7, row F-1). Two libraries in `report/`
are C extensions that do not support being driven from several threads at
once, and both are reached from webapp worker THREADS (`webapp/app.py` runs
`worker.process_job` through `asyncio.to_thread` under a `worker_concurrency`
semaphore, and documents that an abandoned thread cannot be interrupted, so
transiently there can be more):

* **matplotlib**, in `report/charts.py` — GEOM-A observed `EXIT=139` inside
  `Figure.add_axes` on a figure that thread had just created.
* **weasyprint**, in `report/generate.py` — DB-1 observed `EXIT=139` inside
  `write_pdf` → `CSS.__init__` → tinycss2's tokenizer, during a GC pass.

Both crashes are native SIGSEGVs, so they kill the interpreter: uvicorn and
every in-flight upload go down together, not one job. Session CHARTS-AGG closed
the first half behind a lock local to `charts.py`; this module replaced that
lock with a shared one and extended it over the PDF engine, which runs on the
same threads moments later. **`worker_concurrency` is deliberately untouched —
a crash is not fixed by lowering throughput.**

WHAT THIS LOCK IS FOR NOW — Session RENDER-PROC
-----------------------------------------------
It is no longer what stops those crashes reaching the interpreter, and the
reason is worth keeping: SESSION_EMAIL1.md F-6 hit the SAME weasyprint fault
(a SIGBUS in `FontConfiguration.__init__`) **with `render_lock.py` in the frame
list** — the lock was held and doing its job. A lock serializes *renders*; it
cannot serialize the *garbage collection* of the cairo/pango objects a finished
render leaves in the heap, which runs on whatever thread allocates next. So a
lock was never able to close this class, and RENDER-SERIAL's closure was scoped
to a mechanism rather than to the symptom.

`report/render_proc.py` closes it properly, by running both engines in a child
process. This lock is KEPT, for two narrower jobs that are still real:

1. **One child in flight at a time.** `deploy/vibagent.service` sets
   `MemoryMax=1500M` on the unit, and systemd's cgroup covers the parent plus
   its children — so "at most one child" is a memory bound, not only a
   concurrency one. Pinned by `test_render_proc.py`.
2. **The `acquisitions() == 1` property still means something**, but note what
   it now counts: one acquisition per document around the *spawn*, not around a
   renderer. `tests/test_render_serial.py` reads that number.

Three properties, and each is pinned in `tests/test_render_serial.py`:

1. **ONE lock, both libraries.** Not one per module: a thread drawing a figure
   and a thread laying out a PDF must exclude each other too, not merely their
   own kind.

2. **Entered exactly ONCE per document.** `render_report` calls `render_charts`
   AND `render_pdf`, and `render_pdf` is *also* a public entry point the webapp
   worker calls directly — so the natural boundaries nest. The thread-local
   guard below makes an inner acquisition a pass-through, which is why a whole
   document render acquires once instead of the six times its call graph would
   otherwise produce.

3. **NOT reentrant.** `_LOCK` is a plain `threading.Lock`, chosen over the
   `RLock` CHARTS-AGG used. An `RLock` silently absorbs accidental nesting,
   which is exactly the bug shape this module exists to make impossible; a
   plain `Lock` plus the guard means nesting cannot happen rather than merely
   not mattering.

**Cost.** A full five-figure render measures 0.47–0.53 s (CHARTS-AGG) and a PDF
2.7–3.8 s, so at `worker_concurrency: 2` a second job waits a few seconds inside
a job that already spends tens of seconds in the LLM draft. The span also covers
`_pandoc_to_pdf`, whose `subprocess.run(timeout=120)` is the worst case — but
that fallback is only reached on a host where weasyprint is absent, which is a
host with no weasyprint crash to serialize against in the first place; where the
product actually runs, `_pdf_from_markdown` returns before pandoc.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Iterator
from contextlib import contextmanager

#: The one lock. Plain, not reentrant — see property 3 above.
_LOCK = threading.Lock()

#: Per-thread "this thread is already inside a serialized render". What makes
#: the acquisition happen once per document rather than once per nested call.
_state = threading.local()

#: How many times `_LOCK` has actually been taken. Instrumentation for the
#: pins: "entered exactly once per document" is a claim about a number, and a
#: test that cannot read the number can only assert it did not deadlock.
_acquisitions = 0


@contextmanager
def serialized_render() -> Iterator[bool]:
    """Hold the render lock for this block, unless this thread already holds it.

    Yields True if this block took the lock, False if it was already held by an
    enclosing render on the same thread. Callers ignore the value; the pins do
    not.
    """
    if getattr(_state, "held", False):
        yield False  # an enclosing document render owns it — do not re-enter
        return
    with _LOCK:
        global _acquisitions
        _acquisitions += 1
        _state.held = True
        try:
            yield True
        finally:
            # Cleared even if the render raised: a stranded flag would let the
            # NEXT render on this thread skip the lock entirely.
            _state.held = False


def serializes_render(fn):
    """Decorator form of `serialized_render` — the whole call under the lock.

    Applied at two altitudes, both in this package: the document entry points in
    `generate.py`, which is where the once-per-document acquisition happens, and
    the figure builders in `charts.py`, which is what protects a caller that
    reaches the renderer without going through a document.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with serialized_render():
            return fn(*args, **kwargs)

    return wrapper


def holds_render_lock() -> bool:
    """True if the calling thread is inside a serialized render."""
    return bool(getattr(_state, "held", False))


def acquisitions() -> int:
    """Times `_LOCK` has been taken since the last `reset_acquisitions()`."""
    return _acquisitions


def reset_acquisitions() -> None:
    global _acquisitions
    _acquisitions = 0
