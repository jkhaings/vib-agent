"""Phase 7B — chronological run over the wind-turbine run-to-failure dataset.

Processes all 50 daily recordings in STRICT timestamp order, carrying Welford
state and trend history forward exactly as production would (INVARIANT 2:
chronology honesty — no peeking at later files to inform earlier calls).

Layer 2 (Welford) is NOT part of pipeline.run_analysis() — that function sets
zscore=None by design ("no streaming baseline in a single-file analysis"), so
this script drives compute_zscore() itself and threads WelfordState across
files, honoring each reading's own train_baseline from the quality gate and
each file's REAL timestamp as now_ms (Welford decays observations on a
half-life, so wall-clock time here would silently corrupt the weighting).

Emits outputs/wind_turbine_timeline.json — the phase's core artifact, rendered
into the results doc's timeline table.

Frozen constants: this script reads config and calls pdm_core; it changes
neither.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from vib_agent.adapters.wind_turbine import (  # noqa: E402
    cross_check_shaft_rate,
    load_wt_mat,
    one_x_from_spectrum,
    overall_rms_g,
    parse_timestamp,
    shaft_hz_from_tach,
    to_case,
)
from vib_agent.config import load_config, load_thresholds  # noqa: E402
from vib_agent.models import HistoryPoint  # noqa: E402
from vib_agent.pdm_core.anomaly import compute_zscore, init_welford_state  # noqa: E402
from vib_agent.pipeline import run_analysis  # noqa: E402

DATA_DIR = _REPO_ROOT / "data" / "wind_turbine"
OUT_PATH = _REPO_ROOT / "outputs" / "wind_turbine_timeline.json"


def _gear_content(vibration: np.ndarray, fs: float, shaft_hz: float, teeth: int) -> dict:
    """GEAR-CONTENT HONESTY (spec work-item 3): a 20-tooth pinion means
    gear-mesh content is expected and we have NO gear detector. Measure it
    from the RAW spectrum and log it — never feed it to a detector."""
    n = len(vibration)
    w = np.hanning(n)
    sp = np.abs(np.fft.rfft((vibration - vibration.mean()) * w)) * 2.0 / w.sum()
    fr = np.fft.rfftfreq(n, 1.0 / fs)
    noise = float(np.median(sp[(fr > 5) & (fr < 500)]))

    def amp_near(f, tol=3.0):
        m = (fr >= f - tol) & (fr <= f + tol)
        if not m.any():
            return 0.0, f
        i = int(np.argmax(sp[m]))
        return float(sp[m][i]), float(fr[m][i])

    gmf = shaft_hz * teeth
    out = {"noise_floor": noise, "gmf_predicted_hz": gmf, "harmonics": []}
    for k in (1, 2, 3):
        a, f = amp_near(gmf * k, tol=4.0)
        out["harmonics"].append(
            {"k": k, "predicted_hz": gmf * k, "observed_hz": f, "amp": a,
             "x_noise": (a / noise) if noise > 0 else 0.0}
        )
    return out


def _envelope_families(case_spectrum, shaft_hz: float, top_n: int = 5) -> list[dict]:
    """Geometry-independent substitute for the BLOCKED BPFI check
    (BLOCKED_phase7b_bearing_geometry.md): report the loudest envelope peaks
    and their ORDER relative to shaft rate, plus whether +/-1x shaft sidebands
    flank them (the structural inner-race signature). Descriptive only — this
    fits nothing and asserts nothing."""
    fr = np.asarray(case_spectrum.freq_hz, float)
    amp = np.asarray(case_spectrum.amplitude, float)
    from scipy.signal import find_peaks

    idx, _ = find_peaks(amp)
    if len(idx) == 0:
        return []
    order = idx[np.argsort(amp[idx])[::-1]][:top_n]
    mean_amp = float(amp.mean())
    fams = []
    for i in order:
        f0 = float(fr[i])
        if f0 < 1.0:
            continue  # near-DC bin, not a fault family
        sb = []
        for sign in (-1, 1):
            ft = f0 + sign * shaft_hz
            m = (fr >= ft - 1.0) & (fr <= ft + 1.0)
            sb.append(float(np.max(amp[m])) if m.any() else 0.0)
        fams.append({
            "hz": f0,
            "amp": float(amp[i]),
            "order_x_shaft": f0 / shaft_hz,
            "x_mean": float(amp[i]) / mean_amp if mean_amp > 0 else 0.0,
            "sideband_lo_amp": sb[0],
            "sideband_hi_amp": sb[1],
            "sidebands_present": bool(sb[0] > mean_amp and sb[1] > mean_amp),
        })
    return fams


def main() -> int:
    wt_cfg = load_config("wind_turbine")
    bearings_cfg = load_config("bearings")
    iso_table = load_config("iso_zones")["zones"]
    # "route" EXPLICITLY, matching eval/runner.py's CWRU/MFPT suites
    # (runner.py:105, :385). load_thresholds() with no argument silently
    # returns config/thresholds.json's active_profile — currently "streaming".
    # For THIS phase the two profiles are numerically identical everywhere it
    # matters (zscore and trend sections are byte-identical; they differ only in
    # rca.tolerance_pct, 5.0 vs 3.0, and this phase's RCA is geometry-blocked),
    # so naming the profile explicitly changes no result here — it just stops
    # the run from silently depending on which profile happens to be active.
    thresholds = load_thresholds("route")
    rules = load_config("next_measurements")

    files = sorted(DATA_DIR.glob("data-*.mat"), key=parse_timestamp)
    if not files:
        print(f"no data in {DATA_DIR} — run the clone first", file=sys.stderr)
        return 2
    print(f"{len(files)} recordings, {parse_timestamp(files[0]).date()} -> {parse_timestamp(files[-1]).date()}")

    # INVARIANT 2 proof: assert strict chronological ordering up front.
    stamps = [parse_timestamp(f) for f in files]
    assert all(b > a for a, b in zip(stamps, stamps[1:])), "files are not strictly ordered in time"

    state = init_welford_state(now_ms=stamps[0].timestamp() * 1000.0)
    history: list[HistoryPoint] = []
    rows = []

    for i, path in enumerate(files):
        ts = stamps[i]
        now_ms = ts.timestamp() * 1000.0

        raw = load_wt_mat(path, wt_cfg=wt_cfg)
        shaft_hz = shaft_hz_from_tach(raw["tach"], pulses_per_rev=int(wt_cfg["tach_pulses_per_rev"]))
        rms = overall_rms_g(raw["vibration"])

        # History carries readings up to AND INCLUDING this one — exactly what
        # production holds at this instant. Never any later file.
        history.append(HistoryPoint(ts=ts, value=rms))

        case = to_case(path, wt_cfg=wt_cfg, bearings_cfg=bearings_cfg, history=list(history))
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)

        state, z = compute_zscore(
            state,
            case.sensor_data,
            case.machine.mac,
            case.machine.name,
            thresholds["zscore"],
            train_baseline=result.quality_gate.train_baseline,
            now_ms=now_ms,
        )

        # 1x cross-check on the first file only (spec: "an early file")
        cross = None
        if i == 0:
            f1x, a1x = one_x_from_spectrum(
                raw["vibration"], raw["fs"],
                expected_hz=shaft_hz, search_pct=float(wt_cfg["one_x_search_pct"]),
            )
            flagged, delta = cross_check_shaft_rate(
                shaft_hz, f1x,
                tolerance_pct=float(wt_cfg["shaft_cross_check_tolerance_pct"]),
                source_name=path.name,
            )
            cross = {"tach_hz": shaft_hz, "spectrum_1x_hz": f1x, "amp_1x": a1x,
                     "delta_pct": delta, "flagged": flagged}

        primary = [f.fault_type for f in (result.rca.primary_findings if result.rca else [])]
        conf = [f.confidence for f in (result.rca.primary_findings if result.rca else [])]

        rows.append({
            "day": i + 1,
            "date": ts.isoformat(),
            "file": path.name,
            "overall_g": rms,
            "shaft_hz": shaft_hz,
            "gate": result.quality_gate.overall,
            "train_baseline": result.quality_gate.train_baseline,
            "z_y": z.axes["y"].z,
            "z_status": z.axes["y"].status,
            "z_n": z.axes["y"].n,
            "z_mean": z.axes["y"].mean,
            "z_std": z.axes["y"].std,
            "trend_status": result.trend.status if result.trend else None,
            "trend_severity": result.trend.severity if result.trend else None,
            "trend_slope": result.trend.slope if result.trend else None,
            "trend_pct_change": result.trend.pct_change if result.trend else None,
            "trend_r2": result.trend.r_squared if result.trend else None,
            "rca_primary": primary,
            "rca_confidence": conf,
            "cross_check": cross,
            "gear": _gear_content(raw["vibration"], raw["fs"], shaft_hz, int(wt_cfg["gear_pinion_teeth"])),
            "envelope_families": _envelope_families(case.spectra["y"], shaft_hz),
        })
        print(f"  day {i+1:2d} {ts.date()}  rms={rms:.4f}g  shaft={shaft_hz:.2f}Hz  "
              f"z={z.axes['y'].z:6.2f} ({z.axes['y'].status:10s} n={z.axes['y'].n:5.2f})  "
              f"trend={result.trend.status if result.trend else '-':16s}  rca={primary}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(rows, indent=2))
    print(f"\nwrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
