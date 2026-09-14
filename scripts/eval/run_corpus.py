#!/usr/bin/env python3
"""EVAL-DEEP phase 3 — drive every dataset file through the deterministic pipeline.

STRICTLY READ-ONLY with respect to product code and to `data/`. This script
imports `pdm_core`/`pipeline`/the adapters exactly the way the shipped paths do
and changes nothing: no config writes, no threshold edits, no data mutation.
Every output it produces lands under `scripts/eval/results/`.

Per file it records the ground-truth label, the gate outcome, the ISO zone, the
committed fault(s) with confidence, and the differential — appended to a JSONL
**as each file completes**, so an interrupted run still leaves a usable partial
corpus (brief item 7).

Profile: `route`, named explicitly on every call. Per the threshold-profile
doctrine in CLAUDE.md a bare `load_thresholds()` resolves `active_profile`,
which is a default rather than a decision; `eval/runner.py`, `run_mafaulda.py`
and `run_wind_turbine.py` all name `route` for the same reason, and these
numbers are only comparable to the historical ones if this run does too.

GROUND-TRUTH MAPPING (documented, never invented — see EVAL_PACKET.md §3):
  cwru         filename stem -> adapters.cwru.filename_to_expected(), which reads
               config/cwru.json. Encoded by to_case into case.expected
               (fault_type: normal|outer|inner|ball, faults[0]: the primary).
  mfpt         filename stem -> config/mfpt.json rig_files[stem].fault_type
               (normal|outer|inner) or real_world_files[stem] -> "real_world".
               Encoded by to_case into case.expected.
  mafaulda     directory structure -> adapters.mafaulda.label_from_path()
               (normal | imbalance | horizontal-misalignment |
               vertical-misalignment, plus the severity dir and the nominal
               shaft Hz, which IS the filename).
  wind_turbine no per-file fault label exists. This is a 50-day run-to-failure
               timeline whose TERMINAL state is a confirmed inner-race fault;
               per-file ground truth is therefore the day index, and the dataset
               is scored as a timeline, not as a per-file classification. Files
               are processed in strict chronological order with the history
               carrying every reading up to and including the current one —
               matching scripts/run_wind_turbine.py, i.e. what production holds
               at that instant.

Usage:
    python scripts/eval/run_corpus.py --dataset all
    python scripts/eval/run_corpus.py --dataset mafaulda --out results/mafaulda.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from vib_agent.config import load_config, load_thresholds  # noqa: E402
from vib_agent.models import BearingSpec, HistoryPoint  # noqa: E402
from vib_agent.pdm_core.staging import classify_bearing_stage  # noqa: E402
from vib_agent.pipeline import run_analysis  # noqa: E402
from vib_agent.report.generate import staging_profile  # noqa: E402

_DATA = _REPO_ROOT / "data"
_RESULTS = Path(__file__).resolve().parent / "results"

DATASETS = ("cwru", "mfpt", "mafaulda", "wind_turbine")


# --------------------------------------------------------------------------
# shared extraction
# --------------------------------------------------------------------------


def _row_from_result(result, staging_cfg: dict | None = None) -> dict:
    """The per-file record. Committed = result.findings (what the report
    asserts); rca.primary_findings is recorded alongside it because the two
    can differ — synthesize() is what turns a match into a committed Finding."""
    rca = result.rca
    iso = result.iso
    findings = result.findings

    return {
        "gate": result.quality_gate.overall,
        "gate_failed_checks": [
            c.name for c in result.quality_gate.checks if c.status == "fail"
        ],
        "gate_warn_checks": [
            c.name for c in result.quality_gate.checks if c.status == "warn"
        ],
        "iso_zone": getattr(iso, "iso_zone", None) if iso else None,
        "iso_severity": getattr(iso, "iso_severity", None) if iso else None,
        "severity_rms": getattr(iso, "severity_rms", None) if iso else None,
        "not_assessable_reason": getattr(iso, "not_assessable_reason", None) if iso else None,
        # committed = the findings the report actually asserts
        "committed": [f.fault for f in findings],
        "committed_detail": [
            {"fault": f.fault, "confidence": f.confidence, "severity": f.severity}
            for f in findings
        ],
        "committed_bearing": [f.fault for f in findings if f.fault.startswith("bearing_")],
        "top_fault": findings[0].fault if findings else None,
        "top_confidence": findings[0].confidence if findings else None,
        "rca_status": rca.status if rca else None,
        "rca_primary": [
            {
                "fault": m.fault,
                "confidence": m.confidence,
                "freq_hz": m.freq_hz,
                "expected_hz": m.expected_hz,
                "axis": m.axis,
            }
            for m in (rca.primary_findings if rca else [])
        ],
        "differential": [
            {"fault": d.fault, "confidence": d.confidence, "adjudication": d.adjudication}
            for d in (rca.differential if rca else [])
        ],
        "shaft_hz": rca.shaft_freq_hz if rca else None,
        # Session R1 -- damage stage, from the same pure function the report calls.
        "stage": classify_bearing_stage(result, staging_cfg).stage,
        "recommendations": [m.technique for m in result.recommended_measurements],
        "n_recommendations": len(result.recommended_measurements),
    }


def _emit(fh, row: dict) -> None:
    """Append-and-flush so an interruption leaves a complete partial corpus."""
    fh.write(json.dumps(row) + "\n")
    fh.flush()


# --------------------------------------------------------------------------
# per-dataset drivers
# --------------------------------------------------------------------------


def run_cwru(fh, ctx: dict, limit: int | None) -> int:
    from vib_agent.adapters.cwru import to_case

    cfg = load_config("cwru")
    raw_bearing = load_config("bearings")["bearings"][cfg["bearing_key"]]
    spec = BearingSpec(**{k: v for k, v in raw_bearing.items() if not k.startswith("_")})

    files = sorted((_DATA / "cwru").glob("*.mat"))[:limit]
    n = 0
    for path in files:
        n += _one(
            fh,
            dataset="cwru",
            path=path,
            ctx=ctx,
            build=lambda p: to_case(p, cwru_cfg=cfg, bearing_spec=spec),
            label=lambda case: {
                "gt_family": (case.expected.fault_type if case.expected else None),
                "gt_fault": (
                    case.expected.faults[0]
                    if case.expected and case.expected.faults
                    else None
                ),
                "gt_severity_param": (
                    case.expected.diameter_in if case.expected else None
                ),
                "gt_source": "filename stem -> config/cwru.json (filename_to_expected)",
            },
        )
    return n


def run_mfpt(fh, ctx: dict, limit: int | None) -> int:
    from vib_agent.adapters.mfpt import to_case

    cfg = load_config("mfpt")
    bearings = load_config("bearings")

    files = sorted((_DATA / "mfpt").glob("*.mat"))[:limit]
    n = 0
    for path in files:
        n += _one(
            fh,
            dataset="mfpt",
            path=path,
            ctx=ctx,
            build=lambda p: to_case(p, mfpt_cfg=cfg, bearings_cfg=bearings),
            label=lambda case: {
                "gt_family": (case.expected.fault_type if case.expected else None),
                "gt_fault": (
                    case.expected.faults[0]
                    if case.expected and case.expected.faults
                    else None
                ),
                "gt_severity_param": (case.expected.load_lbs if case.expected else None),
                "gt_source": "filename stem -> config/mfpt.json rig_files/real_world_files",
                "embedded_orders": {
                    k: getattr(case.expected, f"embedded_{k}_order", None)
                    for k in ("ball", "cage", "outer", "inner")
                }
                if case.expected
                else {},
            },
        )
    return n


def run_mafaulda(fh, ctx: dict, limit: int | None) -> int:
    from vib_agent.adapters.mafaulda import label_from_path, to_case

    cfg = load_config("mafaulda")
    bearings = load_config("bearings")

    files = sorted((_DATA / "mafaulda").rglob("*.csv"))[:limit]
    n = 0
    for path in files:
        family, severity, nominal = label_from_path(path)
        n += _one(
            fh,
            dataset="mafaulda",
            path=path,
            ctx=ctx,
            build=lambda p: to_case(p, mafaulda_cfg=cfg, bearings_cfg=bearings),
            label=lambda case, _f=family, _s=severity, _n=nominal: {
                "gt_family": _f,
                "gt_fault": {
                    "normal": None,
                    "imbalance": "imbalance",
                    "horizontal-misalignment": "MISALIGNMENT_FAMILY",
                    "vertical-misalignment": "MISALIGNMENT_FAMILY",
                }.get(_f),
                "gt_severity_param": _s,
                "gt_nominal_hz": _n,
                "gt_source": "directory structure -> adapters.mafaulda.label_from_path",
            },
        )
    return n


def run_wind_turbine(fh, ctx: dict, limit: int | None) -> int:
    """Chronological timeline with cumulative history — see module docstring."""
    from vib_agent.adapters.wind_turbine import (
        load_wt_mat,
        overall_rms_g,
        parse_timestamp,
        to_case,
    )

    cfg = load_config("wind_turbine")
    bearings = load_config("bearings")

    files = sorted((_DATA / "wind_turbine").glob("data-*.mat"), key=parse_timestamp)[:limit]
    stamps = [parse_timestamp(f) for f in files]
    if not all(b > a for a, b in zip(stamps, stamps[1:])):
        raise RuntimeError("wind-turbine files are not strictly ordered in time")

    history: list[HistoryPoint] = []
    n = 0
    for i, path in enumerate(files):
        raw = load_wt_mat(path, wt_cfg=cfg)
        history.append(HistoryPoint(ts=stamps[i], value=overall_rms_g(raw["vibration"])))
        n += _one(
            fh,
            dataset="wind_turbine",
            path=path,
            ctx=ctx,
            build=lambda p, _h=list(history): to_case(
                p, wt_cfg=cfg, bearings_cfg=bearings, history=_h
            ),
            label=lambda case, _i=i, _t=stamps[i]: {
                "gt_family": "run_to_failure_timeline",
                # No per-file fault label exists for this dataset. The confirmed
                # terminal state is an inner-race fault; the day index is the
                # only per-file ground truth there is.
                "gt_fault": None,
                "gt_severity_param": None,
                "gt_day_index": _i,
                "gt_timestamp": _t.isoformat(),
                "gt_terminal_fault": "bearing_inner_race",
                "gt_source": "chronological day index; terminal fault from dataset README",
            },
            extra=lambda result, _n=len(history): {
                "trend_status": result.trend.status if result.trend else None,
                "trend_severity": result.trend.severity if result.trend else None,
                "trend_slope": result.trend.slope if result.trend else None,
                "trend_pct_change": result.trend.pct_change if result.trend else None,
                "trend_r_squared": result.trend.r_squared if result.trend else None,
                "trend_n_days": result.trend.n_days if result.trend else None,
                "history_len": _n,
            },
        )
    return n


# --------------------------------------------------------------------------
# the one-file worker
# --------------------------------------------------------------------------


def _one(fh, *, dataset, path, ctx, build, label, extra=None) -> int:
    rel = str(path.relative_to(_DATA))
    t0 = time.perf_counter()
    row: dict = {
        "dataset": dataset,
        "file": rel,
        "stem": path.stem,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        case = build(path)
        row.update(label(case))
        result = run_analysis(
            case,
            iso_table=ctx["iso_table"],
            thresholds=ctx["thresholds"],
            rules=ctx["rules"],
        )
        row.update(_row_from_result(result, ctx.get("staging")))
        if extra is not None:
            row.update(extra(result))
        row["validation_scope"] = case.validation_scope
        row["status"] = "ok"
    except Exception as exc:  # noqa: BLE001 — a bad file must not kill the corpus
        row["status"] = "error"
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["traceback"] = traceback.format_exc()[-1500:]
    row["elapsed_s"] = round(time.perf_counter() - t0, 3)
    _emit(fh, row)

    flag = row.get("status")
    committed = ",".join(row.get("committed", []) or []) or "-"
    print(
        f"  [{dataset:12s}] {rel:52s} gt={str(row.get('gt_family')):24s} "
        f"gate={str(row.get('gate')):5s} zone={str(row.get('iso_zone')):14s} "
        f"-> {committed}  ({flag}, {row['elapsed_s']}s)",
        flush=True,
    )
    return 1


# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="all", choices=("all",) + DATASETS)
    ap.add_argument("--out", default=None, help="JSONL path (default results/corpus_<ds>.jsonl)")
    ap.add_argument("--limit", type=int, default=None, help="first N files per dataset (smoke)")
    ap.add_argument("--profile", default="route")
    args = ap.parse_args()

    ctx = {
        "iso_table": load_config("iso_zones")["zones"],
        "thresholds": load_thresholds(args.profile),
        "rules": load_config("next_measurements"),
        # Session R1: the resolved staging profile, so the corpus records the
        # SAME stage the report would render for each file.
        "staging": staging_profile(args.profile),
    }

    targets = DATASETS if args.dataset == "all" else (args.dataset,)
    _RESULTS.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else _RESULTS / f"corpus_{args.dataset}.jsonl"
    if not out.is_absolute():
        out = _RESULTS / out

    drivers = {
        "cwru": run_cwru,
        "mfpt": run_mfpt,
        "mafaulda": run_mafaulda,
        "wind_turbine": run_wind_turbine,
    }

    print(f"profile={args.profile}  out={out}")
    total = 0
    t0 = time.perf_counter()
    with out.open("w") as fh:
        for ds in targets:
            print(f"\n=== {ds} ===", flush=True)
            try:
                total += drivers[ds](fh, ctx, args.limit)
            except Exception as exc:  # noqa: BLE001
                print(f"  DATASET-LEVEL FAILURE {ds}: {type(exc).__name__}: {exc}", flush=True)
                _emit(fh, {"dataset": ds, "status": "dataset_error", "error": str(exc)})

    print(f"\n{total} files in {time.perf_counter() - t0:.1f}s -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
