#!/usr/bin/env python3
"""EVAL-DEEP phase 4 — the report layer at scale.

Drives a stratified ~25-file sample across all four datasets and across
severities through the FULL report path (charts -> markdown -> PDF) and
measures, per file:

  * figures  — every figure the ChartSet produced exists on disk as a non-empty
               PNG, every image reference in report.md resolves, and the count
               of image XObjects actually embedded in the rendered PDF.
  * numeric  — `agent.consistency.check_numeric_quotes` over the rendered
               markdown. The deterministic template emits a real
               `## Executive Summary`, so this check is substantive rather than
               vacuously green; whether the section was found is recorded
               per-file so a green cannot be mistaken for a skipped check.
  * timing   — charts+markdown wall-clock and PDF wall-clock separately. The
               merge packet's flag #7 ("tectonic is much slower on
               figure-bearing PDFs") wants exactly this distribution.

Nothing is fixed here — failures are quantified and recorded (brief item 4).
No product file is touched; artifacts land outside the repo by default.

PDF IMAGE COUNTING, and why it is sound without pypdf/poppler: an image is a
PDF *stream* object, and the PDF spec forbids stream-bearing objects from
living inside a compressed object stream (ObjStm). Every embedded image's
`/Subtype /Image` dictionary is therefore in the file uncompressed, and a byte
scan counts them reliably. Verified against the known figure count per report.

Usage:
    python scripts/eval/run_report_layer.py
    python scripts/eval/run_report_layer.py --artifacts /tmp/report_layer
"""

from __future__ import annotations

import argparse
import tempfile
import json
import re
import sys
import time
import traceback
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from vib_agent.agent.consistency import check_numeric_quotes  # noqa: E402
from vib_agent.config import load_config, load_thresholds  # noqa: E402
from vib_agent.models import BearingSpec, HistoryPoint  # noqa: E402
from vib_agent.pipeline import run_analysis  # noqa: E402
from vib_agent.report.charts import matplotlib_available  # noqa: E402
from vib_agent.report.generate import render_pdf, render_report  # noqa: E402

_DATA = _REPO_ROOT / "data"
_RESULTS = Path(__file__).resolve().parent / "results"

_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_PDF_IMG_RE = re.compile(rb"/Subtype\s*/Image")
_EXEC_RE = re.compile(r"^#{1,4}[ \t]*Executive Summary[ \t]*$", re.MULTILINE | re.IGNORECASE)

# --------------------------------------------------------------------------
# THE STRATIFIED SAMPLE — hand-selected, fixed, and documented rather than
# randomly drawn, so the composition is auditable and the run is reproducible
# without carrying a seed. Spans all four datasets and, within each, the
# severity/outcome range: healthy, known-miss, committed-fault, and (for the
# wind turbine) points along the run-to-failure trend.
# --------------------------------------------------------------------------
SAMPLE: list[tuple[str, str, str]] = [
    # (dataset, relative path under data/, why this file is in the sample)
    ("cwru", "cwru/Normal_0.mat", "healthy baseline"),
    ("cwru", "cwru/OR007@6_0.mat", "outer 0.007in — committed, historical PASS"),
    ("cwru", "cwru/OR014@6_0.mat", "outer 0.014in — the documented 4-file miss"),
    ("cwru", "cwru/OR021@6_0.mat", "outer 0.021in — largest graded outer defect"),
    ("cwru", "cwru/IR007_0.mat", "inner 0.007in — committed"),
    ("cwru", "cwru/IR021_3.mat", "inner 0.021in, highest load suffix"),
    ("cwru", "cwru/B021_0.mat", "ball 0.021in — ungated/informational family"),
    ("cwru", "cwru/IR028_0.mat", "0.028in — out of graded scope, blind-test-A case"),
    ("mfpt", "mfpt/baseline_1.mat", "healthy baseline"),
    ("mfpt", "mfpt/baseline_3.mat", "healthy — the historical false positive"),
    ("mfpt", "mfpt/outer_270_1.mat", "outer race, 270 lbs"),
    ("mfpt", "mfpt/outer_vload_7.mat", "outer race, highest variable load"),
    ("mfpt", "mfpt/inner_vload_1.mat", "inner race, lowest variable load"),
    ("mfpt", "mfpt/real_world_oil_pump_bearing.mat", "real-world field machine"),
    ("mafaulda", "mafaulda/normal/12.288.csv", "healthy — low speed, historical FP"),
    # NOTE: outputs/session_b_results.md names `50.176` as the one clean normal
    # file, but no such file exists in the local mirror (the normal/ set runs
    # 12.288 … 61.44 with 49.5616 in that slot). Using 49.5616, which this
    # session's own corpus run measured as clean. Flagged in EVAL_PACKET.md.
    ("mafaulda", "mafaulda/normal/49.5616.csv", "healthy — high speed, measured clean"),
    ("mafaulda", "mafaulda/imbalance/6g/15.36.csv", "imbalance, lightest mass"),
    ("mafaulda", "mafaulda/imbalance/35g/55.0912.csv", "imbalance, heaviest mass, high speed"),
    ("mafaulda", "mafaulda/horizontal-misalignment/0.5mm/15.36.csv", "h-misalign, smallest shim"),
    ("mafaulda", "mafaulda/horizontal-misalignment/2.0mm/54.4768.csv", "h-misalign, largest shim"),
    ("mafaulda", "mafaulda/vertical-misalignment/1.90mm/15.1552.csv", "v-misalign, largest shim"),
    ("wind_turbine", "wind_turbine/data-20130307T015746Z.mat", "run-to-failure day 0"),
    ("wind_turbine", "wind_turbine/data-20130331T193818Z.mat", "run-to-failure mid-span"),
    ("wind_turbine", "wind_turbine/data-20130424T215514Z.mat", "run-to-failure day 48"),
    ("wind_turbine", "wind_turbine/data-20130425T232202Z.mat", "run-to-failure final day"),
]


def build_case(dataset: str, path: Path, cfgs: dict):
    if dataset == "cwru":
        from vib_agent.adapters.cwru import to_case

        return to_case(path, cwru_cfg=cfgs["cwru"], bearing_spec=cfgs["cwru_bearing"])
    if dataset == "mfpt":
        from vib_agent.adapters.mfpt import to_case

        return to_case(path, mfpt_cfg=cfgs["mfpt"], bearings_cfg=cfgs["bearings"])
    if dataset == "mafaulda":
        from vib_agent.adapters.mafaulda import to_case

        return to_case(path, mafaulda_cfg=cfgs["mafaulda"], bearings_cfg=cfgs["bearings"])
    if dataset == "wind_turbine":
        # The trend figure needs the timeline, so rebuild the cumulative history
        # up to and including this recording — exactly as run_wind_turbine.py does.
        from vib_agent.adapters.wind_turbine import (
            load_wt_mat,
            overall_rms_g,
            parse_timestamp,
            to_case,
        )

        files = sorted((_DATA / "wind_turbine").glob("data-*.mat"), key=parse_timestamp)
        history: list[HistoryPoint] = []
        for f in files:
            raw = load_wt_mat(f, wt_cfg=cfgs["wind_turbine"])
            history.append(HistoryPoint(ts=parse_timestamp(f), value=overall_rms_g(raw["vibration"])))
            if f == path:
                break
        return to_case(
            path,
            wt_cfg=cfgs["wind_turbine"],
            bearings_cfg=cfgs["bearings"],
            history=history,
        )
    raise ValueError(dataset)


def one(dataset: str, rel: str, why: str, cfgs: dict, ctx: dict, artifacts: Path) -> dict:
    path = _DATA / Path(rel)
    out_dir = artifacts / rel.replace("/", "__").replace(".", "_")
    row: dict = {"dataset": dataset, "file": rel, "sample_reason": why}
    try:
        t_build = time.perf_counter()
        case = build_case(dataset, path, cfgs)
        row["build_s"] = round(time.perf_counter() - t_build, 3)

        t_an = time.perf_counter()
        result = run_analysis(
            case, iso_table=ctx["iso_table"], thresholds=ctx["thresholds"], rules=ctx["rules"]
        )
        row["analysis_s"] = round(time.perf_counter() - t_an, 3)

        # --- charts + markdown (no PDF yet, so the two costs stay separable) --
        t_md = time.perf_counter()
        written = render_report(
            result,
            case.machine,
            out_dir,
            pdf=False,
            case=case,
            thresholds=ctx["thresholds"],
            profile="route",
            figures=True,
        )
        row["charts_markdown_s"] = round(time.perf_counter() - t_md, 3)

        md_path = written["markdown"]
        md_text = md_path.read_text()
        row["markdown_bytes"] = len(md_text.encode())

        # --- figures on disk -------------------------------------------------
        charts_dir = out_dir / "charts"
        pngs = sorted(charts_dir.glob("*.png")) if charts_dir.exists() else []
        row["png_files"] = [p.name for p in pngs]
        row["png_count"] = len(pngs)
        row["png_empty"] = [p.name for p in pngs if p.stat().st_size == 0]

        refs = _IMG_RE.findall(md_text)
        row["md_image_refs"] = len(refs)
        row["md_image_refs_missing"] = [
            r for r in refs if not (out_dir / r).exists()
        ]

        # --- numeric-quote check --------------------------------------------
        row["exec_summary_present"] = bool(_EXEC_RE.search(md_text))
        mismatches = check_numeric_quotes(md_text, result)
        row["numeric_quote_mismatches"] = mismatches
        row["numeric_quote_ok"] = not mismatches

        # --- PDF -------------------------------------------------------------
        t_pdf = time.perf_counter()
        pdf_path = render_pdf(md_text, out_dir)
        row["pdf_s"] = round(time.perf_counter() - t_pdf, 3)
        if pdf_path is None:
            row["pdf"] = None
            row["pdf_images"] = None
            row["figures_in_pdf_ok"] = False
            row["pdf_note"] = "no PDF engine produced output"
        else:
            data = pdf_path.read_bytes()
            row["pdf"] = str(pdf_path.relative_to(artifacts))
            row["pdf_bytes"] = len(data)
            row["pdf_images"] = len(_PDF_IMG_RE.findall(data))
            # Every figure referenced by the markdown must survive into the PDF.
            row["figures_in_pdf_ok"] = row["pdf_images"] >= row["md_image_refs"]

        row["total_s"] = round(
            row["build_s"] + row["analysis_s"] + row["charts_markdown_s"] + row["pdf_s"], 3
        )
        row["committed"] = [
            f.fault for f in result.findings if f.fault != "no_significant_findings"
        ]
        row["iso_zone"] = getattr(result.iso, "iso_zone", None) if result.iso else None
        row["gate"] = result.quality_gate.overall
        row["status"] = "ok"
    except Exception as exc:  # noqa: BLE001
        row["status"] = "error"
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["traceback"] = traceback.format_exc()[-1500:]
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--artifacts",
        default=str(Path(tempfile.gettempdir()) / "vib_report_layer"),
        help="where the rendered reports go (kept out of the repo)",
    )
    ap.add_argument("--out", default="report_layer.jsonl")
    args = ap.parse_args()

    artifacts = Path(args.artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    _RESULTS.mkdir(parents=True, exist_ok=True)
    out = _RESULTS / args.out

    cfgs = {
        "cwru": load_config("cwru"),
        "mfpt": load_config("mfpt"),
        "mafaulda": load_config("mafaulda"),
        "wind_turbine": load_config("wind_turbine"),
        "bearings": load_config("bearings"),
    }
    raw_b = cfgs["bearings"]["bearings"][cfgs["cwru"]["bearing_key"]]
    cfgs["cwru_bearing"] = BearingSpec(**{k: v for k, v in raw_b.items() if not k.startswith("_")})

    ctx = {
        "iso_table": load_config("iso_zones")["zones"],
        "thresholds": load_thresholds("route"),
        "rules": load_config("next_measurements"),
    }

    print(f"matplotlib available: {matplotlib_available()}")
    print(f"{len(SAMPLE)} files -> {artifacts}\n")

    t0 = time.perf_counter()
    with out.open("w") as fh:
        for i, (ds, rel, why) in enumerate(SAMPLE, 1):
            row = one(ds, rel, why, cfgs, ctx, artifacts)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            if row["status"] == "ok":
                print(
                    f"  {i:2d}/{len(SAMPLE)} [{ds:12s}] {rel:52s} "
                    f"figs={row['png_count']} refs={row['md_image_refs']} "
                    f"pdfimg={row['pdf_images']} numeric={'OK' if row['numeric_quote_ok'] else 'MISMATCH'} "
                    f"md={row['charts_markdown_s']}s pdf={row['pdf_s']}s",
                    flush=True,
                )
            else:
                print(f"  {i:2d}/{len(SAMPLE)} [{ds:12s}] {rel:52s} ERROR {row['error']}", flush=True)

    print(f"\n{len(SAMPLE)} files in {time.perf_counter() - t0:.1f}s -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
