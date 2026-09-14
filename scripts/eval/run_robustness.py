#!/usr/bin/env python3
"""EVAL-DEEP phase 5 (stretch) — robustness sweep.

For ~10 representative files, sweep TRUNCATION / ADDED NOISE FLOOR /
DOWNSAMPLING and record where the committed diagnosis degrades.

**Every perturbation is written to a TEMP COPY outside the repo. `data/` is
opened read-only and never written, moved, or deleted.** The temp copy is then
driven through the *real adapter* (`to_case`) and the real pipeline, so what is
measured is the shipped path's behaviour on degraded input — not a bypass.

Two adapter-level facts shape what this sweep can measure, and both are
reported rather than worked around:

  * `adapters.cwru.load_cwru_signal` reads whatever length the `*_DE_time` key
    holds and takes `fs` from `config/cwru.json`. Truncation and added noise are
    therefore faithfully testable. DECIMATION IS NOT testable through the file:
    the adapter would still believe fs = 12 kHz, so a decimated file is not
    "the same signal sampled slower", it is a mislabelled sample rate — a
    different experiment. Decimation is applied at the SIGNAL level instead,
    rebuilding the spectra through the adapter's own `envelope_spectrum` /
    `raw_spectrum` with the correctly reduced fs, and is labelled as such.
  * `adapters.mafaulda.load_mafaulda_csv` HARD-ASSERTS exactly
    sample_rate_hz x record_seconds rows and the full column count. Truncation
    and decimation of a MAFAULDA CSV are therefore rejected outright. That
    fail-closed behaviour is the measurement, and is recorded as
    `rejected` rather than treated as an error of this harness.

Noise is deterministic: a fixed seed per (file, level).

Usage:
    python scripts/eval/run_robustness.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import zlib
import sys
import tempfile
import time
import traceback
from pathlib import Path

import numpy as np
from scipy.io import loadmat, savemat

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from vib_agent.config import load_config, load_thresholds  # noqa: E402
from vib_agent.models import BearingSpec  # noqa: E402
from vib_agent.pipeline import run_analysis  # noqa: E402

_DATA = _REPO_ROOT / "data"
_RESULTS = Path(__file__).resolve().parent / "results"

# 10 representative files spanning both detector families and both outcomes.
FILES: list[tuple[str, str, str]] = [
    ("cwru", "cwru/Normal_0.mat", "healthy"),
    ("cwru", "cwru/OR007@6_0.mat", "outer 0.007in — committed"),
    ("cwru", "cwru/OR021@6_0.mat", "outer 0.021in — committed"),
    ("cwru", "cwru/IR007_0.mat", "inner 0.007in — committed"),
    ("cwru", "cwru/IR021_0.mat", "inner 0.021in — committed"),
    ("mafaulda", "mafaulda/normal/49.5616.csv", "healthy — measured clean"),
    ("mafaulda", "mafaulda/normal/12.288.csv", "healthy — known false commit"),
    ("mafaulda", "mafaulda/imbalance/35g/55.0912.csv", "imbalance, heaviest mass"),
    ("mafaulda", "mafaulda/horizontal-misalignment/2.0mm/54.4768.csv", "h-misalign, largest shim"),
    ("mafaulda", "mafaulda/vertical-misalignment/1.90mm/15.1552.csv", "v-misalign, largest shim"),
]

TRUNCATIONS = (0.75, 0.50, 0.25, 0.10)   # fraction of samples retained
NOISE_LEVELS = (0.01, 0.05, 0.10, 0.25, 0.50)  # noise RMS as a fraction of signal RMS
DECIMATIONS = (2, 4, 8)


# --------------------------------------------------------------------------


def _temp_copy_path(tmp_root: Path, condition: str, rel: str) -> Path:
    """Temp copy that PRESERVES the original directory layout and filename.

    Both adapters derive ground truth from the path — CWRU parses the filename
    stem (`OR007@6_0`), MAFAULDA reads the category directory and parses the
    stem as the nominal shaft Hz. A flat temp dir with decorated names makes
    every perturbed file unparseable, which reads as "the pipeline rejected
    degraded data" when it is really "the harness renamed the file". Each
    condition therefore gets its own subtree and the file keeps its own name.
    """
    dst = tmp_root / condition / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    return dst


def _cwru_write(src: Path, dst: Path, transform) -> None:
    """Copy a CWRU .mat, applying `transform` to every time-series channel.
    Non-signal keys (e.g. the RPM scalar) are preserved verbatim."""
    mat = loadmat(str(src))
    out = {}
    for k, v in mat.items():
        if k.startswith("__"):
            continue
        if k.endswith("_time"):
            sig = np.asarray(v).flatten().astype(float)
            out[k] = transform(sig).reshape(-1, 1)
        else:
            out[k] = v
    savemat(str(dst), out)


def _mafaulda_write(src: Path, dst: Path, transform, accel_cols) -> None:
    data = np.loadtxt(src, delimiter=",")
    data = transform(data, accel_cols)
    np.savetxt(dst, data, delimiter=",", fmt="%.6g")


def _analyse(dataset: str, path: Path, cfgs: dict, ctx: dict) -> dict:
    if dataset == "cwru":
        from vib_agent.adapters.cwru import to_case

        case = to_case(path, cwru_cfg=cfgs["cwru"], bearing_spec=cfgs["cwru_bearing"])
    else:
        from vib_agent.adapters.mafaulda import to_case

        case = to_case(path, mafaulda_cfg=cfgs["mafaulda"], bearings_cfg=cfgs["bearings"])
    result = run_analysis(
        case, iso_table=ctx["iso_table"], thresholds=ctx["thresholds"], rules=ctx["rules"]
    )
    return _summarise(result)


def _summarise(result) -> dict:
    return {
        "gate": result.quality_gate.overall,
        "committed": [f.fault for f in result.findings if f.fault != "no_significant_findings"],
        "committed_detail": [
            {"fault": f.fault, "confidence": f.confidence}
            for f in result.findings
            if f.fault != "no_significant_findings"
        ],
        "rca_primary": [m.fault for m in (result.rca.primary_findings if result.rca else [])],
        "top_confidence": next(
            (f.confidence for f in result.findings if f.fault != "no_significant_findings"), None
        ),
        "shaft_hz": round(result.rca.shaft_freq_hz, 4) if result.rca else None,
    }


def _decimate_signal_level(dataset: str, path: Path, factor: int, cfgs: dict, ctx: dict) -> dict:
    """Decimation with fs correctly reduced — signal level, since the file
    adapters take fs from config and would otherwise misread the result as a
    frequency shift. Rebuilds the spectra with the adapter's own DSP."""
    if dataset != "cwru":
        raise NotImplementedError("mafaulda decimation changes the asserted row count")

    from vib_agent.adapters.cwru import envelope_spectrum, load_cwru_mat, raw_spectrum
    from vib_agent.models import Case, MachineMeta, SensorData

    signal, fs, rpm = load_cwru_mat(path, cfgs["cwru"])
    sig = signal[::factor]
    new_fs = fs / factor
    cfg = cfgs["cwru"]

    # A PHYSICAL limit, not a defect. `envelope_spectrum` CLAMPS the band's high
    # edge to 0.99 x Nyquist but does not move its low edge, so decimation
    # progressively NARROWS the usable envelope band until the low edge reaches
    # Nyquist, at which point demodulation is impossible. Mirrored here exactly
    # so the sweep reports the real limit rather than a filter-design exception
    # — and so it does not over-reject a rate the shipped code handles.
    band_lo, band_hi = tuple(cfg["envelope_band_hz"])
    nyq = new_fs / 2.0
    effective_hi = min(band_hi, nyq * 0.99)
    if band_lo >= effective_hi:
        raise ValueError(
            f"envelope band low edge {band_lo:.0f} Hz is at or above Nyquist "
            f"({nyq:.0f} Hz) after {factor}x decimation — envelope demodulation is "
            f"not physically possible at this sample rate"
        )

    env = envelope_spectrum(
        sig, new_fs,
        band_hz=tuple(cfg["envelope_band_hz"]),
        region_hz=tuple(cfg["envelope_region_hz"]),
    )
    raw = raw_spectrum(sig, new_fs)
    machine = MachineMeta(
        mac=f"CWRU-{path.stem}", name=f"CWRU {path.stem}", active=True, type="motor",
        iso_group="2", iso_support="rigid", bearing=cfgs["cwru_bearing"], axial_axis="x",
    )
    case = Case(
        name=path.stem, machine=machine,
        sensor_data=SensorData(rpm=rpm, y_rms_ACC_G=float(np.sqrt(np.mean(sig**2)))),
        spectra={"y": env}, raw_spectra={"y": raw},
        source="cwru", validation_scope=["rca"],
    )
    result = run_analysis(
        case, iso_table=ctx["iso_table"], thresholds=ctx["thresholds"], rules=ctx["rules"]
    )
    row = _summarise(result)
    row["effective_fs_hz"] = new_fs
    row["nyquist_hz"] = nyq
    row["effective_envelope_band_hz"] = [band_lo, round(effective_hi, 1)]
    return row


# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="robustness.jsonl")
    args = ap.parse_args()

    cfgs = {
        "cwru": load_config("cwru"),
        "mafaulda": load_config("mafaulda"),
        "bearings": load_config("bearings"),
    }
    raw_b = cfgs["bearings"]["bearings"][cfgs["cwru"]["bearing_key"]]
    cfgs["cwru_bearing"] = BearingSpec(**{k: v for k, v in raw_b.items() if not k.startswith("_")})
    ctx = {
        "iso_table": load_config("iso_zones")["zones"],
        "thresholds": load_thresholds("route"),
        "rules": load_config("next_measurements"),
    }
    maf_cols = list(cfgs["mafaulda"]["columns"])
    # noise goes on the accelerometer channels only — perturbing the tachometer
    # would corrupt speed extraction and conflate two separate effects.
    accel_cols = [i for i, c in enumerate(maf_cols)
                  if "tach" not in c.lower() and "mic" not in c.lower()]

    _RESULTS.mkdir(parents=True, exist_ok=True)
    out = _RESULTS / args.out
    tmp_root = Path(tempfile.mkdtemp(prefix="vib_robustness_"))
    print(f"temp copies in {tmp_root}  (data/ is never written)")
    print(f"mafaulda noise channels: {[maf_cols[i] for i in accel_cols]}\n")

    n = 0
    t0 = time.perf_counter()
    try:
        with out.open("w") as fh:
            for dataset, rel, why in FILES:
                src = _DATA / Path(rel)
                stem = rel.replace("/", "__")
                print(f"=== {rel} ({why}) ===", flush=True)

                def emit(cond: str, param, body):
                    nonlocal n
                    row = {
                        "dataset": dataset, "file": rel, "why": why,
                        "condition": cond, "param": param,
                    }
                    try:
                        row.update(body())
                        row["status"] = "ok"
                    except Exception as exc:  # noqa: BLE001
                        row["status"] = "rejected"
                        row["error"] = f"{type(exc).__name__}: {exc}"
                        row["traceback"] = traceback.format_exc()[-600:]
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
                    n += 1
                    tag = (
                        ",".join(row.get("committed", []) or []) or "-"
                        if row["status"] == "ok"
                        else f"REJECTED ({row['error'][:60]})"
                    )
                    print(f"   {cond:12s} {str(param):6s} -> {tag}", flush=True)

                # baseline (unperturbed, straight from data/, read-only)
                emit("baseline", "-", lambda: _analyse(dataset, src, cfgs, ctx))

                # ---- truncation ------------------------------------------
                for frac in TRUNCATIONS:
                    dst = _temp_copy_path(tmp_root, f"trunc{int(frac * 100)}", rel)

                    def body(frac=frac, dst=dst):
                        if dataset == "cwru":
                            _cwru_write(src, dst, lambda s: s[: max(1, int(len(s) * frac))])
                        else:
                            _mafaulda_write(
                                src, dst,
                                lambda d, _c, frac=frac: d[: max(1, int(len(d) * frac))],
                                accel_cols,
                            )
                        return _analyse(dataset, dst, cfgs, ctx)

                    emit("truncate", f"{int(frac * 100)}%", body)
                    dst.unlink(missing_ok=True)

                # ---- additive noise floor --------------------------------
                for lvl in NOISE_LEVELS:
                    dst = _temp_copy_path(tmp_root, f"noise{int(lvl * 100)}", rel)
                    seed = zlib.crc32(f"{rel}:{lvl}".encode())

                    def body(lvl=lvl, dst=dst, seed=seed):
                        rng = np.random.default_rng(seed)
                        if dataset == "cwru":
                            def tf(s):
                                return s + rng.normal(0, lvl * float(np.sqrt(np.mean(s**2))), len(s))
                            _cwru_write(src, dst, tf)
                        else:
                            def tf(d, cols):
                                d = d.copy()
                                for c in cols:
                                    rms = float(np.sqrt(np.mean(d[:, c] ** 2)))
                                    d[:, c] = d[:, c] + rng.normal(0, lvl * rms, d.shape[0])
                                return d
                            _mafaulda_write(src, dst, tf, accel_cols)
                        return _analyse(dataset, dst, cfgs, ctx)

                    emit("noise", f"{int(lvl * 100)}%", body)
                    dst.unlink(missing_ok=True)

                # ---- downsampling ----------------------------------------
                for k in DECIMATIONS:
                    emit(
                        "decimate", f"{k}x",
                        lambda k=k: _decimate_signal_level(dataset, src, k, cfgs, ctx),
                    )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
        print(f"\ntemp dir removed: {tmp_root}")

    print(f"{n} runs in {time.perf_counter() - t0:.1f}s -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
