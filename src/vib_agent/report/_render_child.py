"""Child-process entrypoint for the native renderers (Session RENDER-PROC).

Run only via `python -m vib_agent.report._render_child <op>` (see
`report/render_proc.py`) — never imported by the parent. This is the ONLY
place under `src/` where weasyprint is imported or a `write_pdf` happens, and
the only place a matplotlib figure is ever constructed; that is the property
that closes EMAIL-1 F-6, because it means the uvicorn process never holds a
cairo/pango object for a later GC to fault on.

Protocol, deliberately the same shape as `webapp/_parse_child.py`:

* the payload arrives as one JSON object on **stdin** — not argv, because
  `render_html` produces a ~120 KB string (it inlines the whole stylesheet and
  six `file:///` `@font-face` URLs) and Linux caps a single argv element at
  128 KB; and not a temp file, because `tests/test_purge_order.py` pins that a
  finished job directory holds nothing but the report;
* the result leaves as one `RENDER_RESULT: {json}` line on **stdout**, so
  engine chatter cannot corrupt the channel;
* an internal failure prints one `RENDER_ERROR: <message>` line to stderr and
  exits nonzero.

`status` is `ok` or `engine_absent`. **`engine_absent` is not a failure** — it
is the pre-existing "no weasyprint / no matplotlib here" degrade, and the
parent turns it back into the same `None` / empty `ChartSet` it returned
before this session. Only a signal death, a timeout, or a nonzero exit is a
crash, and only those raise.

No `RLIMIT_AS` cap, unlike `_parse_child`. That child parses untrusted uploads;
this one renders our own HTML and our own JSON, both already through that
sandbox. A cap here would convert working renders into failures, and the limit
that actually binds in production is the unit's `MemoryMax=1500M`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from vib_agent.report.render_lock import serialized_render
from vib_agent.report.render_proc import ERROR_PREFIX, RESULT_PREFIX


def _emit(result: dict[str, Any], channel) -> None:
    channel.write(RESULT_PREFIX + json.dumps(result) + "\n")
    channel.flush()


def _op_pdf(payload: dict[str, Any]) -> dict[str, Any]:
    """HTML in, `report.pdf` out. The frame DB-1 and EMAIL-1 F-6 died in.

    The PDF is written to `report.pdf.part` and renamed as the LAST act, so a
    crash mid-`write_pdf` can never leave a truncated `report.pdf` where
    `webapp/worker.py`'s `pdf.exists()` would find it and hand it to an analyst.
    """
    out_dir = Path(payload["out_dir"])
    pdf_path = out_dir / "report.pdf"
    part_path = out_dir / "report.pdf.part"
    try:
        from weasyprint import HTML  # type: ignore
    except ImportError:
        return {"status": "engine_absent", "reason": "import"}
    except OSError:
        # weasyprint is installed but its native libs (pango/gobject/cairo)
        # aren't reachable -- the package imports partially, then dies deep
        # inside its FFI layer with OSError rather than ImportError. RENDER-
        # SERIAL §6: keep BOTH handlers.
        return {"status": "engine_absent", "reason": "oserror"}

    try:
        # base_url resolves the RELATIVE chart paths (charts/*.png) against the
        # report directory -- without it every figure silently drops out.
        #
        # The lock is uncontended here (one render, one child, one thread). It
        # is written anyway: `tests/test_render_serial.py` proves by AST that
        # every `write_pdf` under `src/` is lexically under it, and that pin is
        # still the right one to keep passing -- a `write_pdf` reached without
        # it is a `write_pdf` someone can reach from two threads again.
        with serialized_render():
            HTML(string=payload["html"], base_url=str(out_dir)).write_pdf(str(part_path))
    except OSError:
        part_path.unlink(missing_ok=True)
        return {"status": "engine_absent", "reason": "oserror"}

    os.replace(part_path, pdf_path)
    return {"status": "ok", "pdf": str(pdf_path)}


def _op_charts(payload: dict[str, Any]) -> dict[str, Any]:
    """The analysis in, `charts/*.png` + the ChartSet manifest out.

    GEOM-A's SIGSEGV was inside figure CONSTRUCTION (`Figure.add_axes`), which
    is why the whole figure set is built here rather than only saved here.
    """
    from vib_agent.models import AnalysisResult, Case, MachineMeta
    from vib_agent.report import charts as charts_mod

    if not charts_mod.matplotlib_available():
        return {"status": "engine_absent", "reason": "import"}

    result = AnalysisResult.model_validate_json(payload["result"])
    machine = MachineMeta.model_validate_json(payload["machine"])
    case = Case.model_validate_json(payload["case"]) if payload.get("case") else None
    chart_set = charts_mod.render_charts_inprocess(
        result, machine, Path(payload["out_dir"]),
        case=case,
        thresholds=payload.get("thresholds"),
        fault_labels=payload.get("fault_labels"),
    )
    return {"status": "ok", "charts": charts_mod.chartset_to_dict(chart_set)}


_OPS = {"pdf": _op_pdf, "charts": _op_charts}


def _claim_stdout():
    """Take fd 1 for the protocol, then point fd 1 itself at stderr.

    The result line has to be the ONLY thing on the channel, and a Python-level
    convention cannot guarantee that: weasyprint prints an installation notice
    straight to stdout at import time (`weasyprint/text/ffi.py:455`), and the C
    libraries under it — fontconfig, cairo, pango — can write to fd 1 directly,
    with no newline, mid-line. After this, everything any of them emits lands on
    stderr where it is harmless, and nothing but `_emit` can reach the parent.
    """
    channel = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    return channel


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in _OPS:
        print(f"{ERROR_PREFIX}bad child invocation {argv!r}", file=sys.stderr)
        return 2
    channel = _claim_stdout()
    try:
        payload = json.loads(sys.stdin.read())
        _emit(_OPS[argv[0]](payload), channel)
    except Exception as exc:  # noqa: BLE001 -- the parent gets a class name, never a traceback
        print(f"{ERROR_PREFIX}{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
