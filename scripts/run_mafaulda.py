"""Phase 8 — run the MAFAULDA stratified subset through the frozen pipeline.

Parts A (normal, hard line), B (imbalance), C (misalignment family), plus the
gradient / speed-tracking / interaction-rule logs the spec requires regardless
of pass/fail.

Emits outputs/mafaulda_timeline.json (machine-readable, one row per file).
Reads config and calls pdm_core; changes neither.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from vib_agent.adapters.mafaulda import (  # noqa: E402
    cross_check_speed,
    label_from_path,
    to_case,
)
from vib_agent.config import load_config, load_thresholds  # noqa: E402
from vib_agent.pipeline import run_analysis  # noqa: E402

DATA = _REPO_ROOT / "data" / "mafaulda"
OUT = _REPO_ROOT / "outputs" / "mafaulda_timeline.json"

_MISALIGNMENT_FAMILY = {
    "angular_misalignment",
    "parallel_misalignment",
    "severe_misalignment",
    "misalignment_general",
    "bent_shaft",
}


def main() -> int:
    cfg = load_config("mafaulda")
    bearings = load_config("bearings")
    iso = load_config("iso_zones")["zones"]
    # "route" EXPLICITLY, matching eval/runner.py's CWRU and MFPT suites
    # (runner.py:105, :385). load_thresholds() with no argument would silently
    # return config/thresholds.json's active_profile, which is "streaming" —
    # a different rca.tolerance_pct (5.0 vs route's 3.0) and therefore a
    # different benchmark. The two profiles are otherwise identical.
    th = load_thresholds("route")
    rules = load_config("next_measurements")

    files = sorted(DATA.rglob("*.csv"))
    if not files:
        print(f"no data in {DATA} — run scripts/fetch_mafaulda.py first", file=sys.stderr)
        return 2
    print(f"{len(files)} files\n")

    rows = []
    for path in files:
        family, severity, nominal = label_from_path(path)
        case = to_case(path, mafaulda_cfg=cfg, bearings_cfg=bearings)
        result = run_analysis(case, iso_table=iso, thresholds=th, rules=rules)

        shaft_hz = case.sensor_data.rpm / 60.0
        flagged, delta = cross_check_speed(
            shaft_hz, nominal, tolerance_pct=float(cfg["shaft_cross_check_tolerance_pct"])
        )

        primaries = [(f.fault, f.confidence, f.freq_hz) for f in (result.rca.primary_findings if result.rca else [])]
        differential = [
            (d.fault, d.disposition if hasattr(d, "disposition") else None,
             getattr(d, "reason", None) or getattr(d, "adjudication", None))
            for d in (result.rca.differential if result.rca else [])
        ]

        # 1x amplitude on the loudest radial axis, for the gradient check
        one_x_amp = 0.0
        one_x_hz = None
        for ax in ("y", "z"):  # radial + tangential (axial is x)
            sp = case.spectra[ax]
            fr = np.asarray(sp.freq_hz)
            am = np.asarray(sp.amplitude)
            m = (fr >= shaft_hz * 0.97) & (fr <= shaft_hz * 1.03)
            if m.any() and float(am[m].max()) > one_x_amp:
                one_x_amp = float(am[m].max())
                one_x_hz = float(fr[m][int(np.argmax(am[m]))])

        rows.append({
            "file": str(path.relative_to(DATA)),
            "family": family,
            "severity_param": severity,
            "nominal_hz": nominal,
            "tach_shaft_hz": shaft_hz,
            "speed_delta_pct": delta,
            "speed_flagged": flagged,
            "gate": result.quality_gate.overall,
            "iso_zone": result.iso.iso_zone,
            "severity_rms": result.iso.severity_rms,
            "rca_primary": primaries,
            "differential": [d[0] for d in differential],
            "one_x_amp": one_x_amp,
            "one_x_hz": one_x_hz,
            "n_recommendations": len(result.recommended_measurements),
        })
        pf = ",".join(f"{a}({b})" for a, b, _ in primaries) or "-"
        print(f"  {str(path.relative_to(DATA)):46s} shaft={shaft_hz:6.2f} "
              f"(nom {nominal:7.3f}, {delta:+5.2f}%{'!' if flagged else ' '})  -> {pf}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, indent=2))

    # ---------------- scoring ----------------
    print("\n" + "=" * 74)
    normal = [r for r in rows if r["family"] == "normal"]
    imb = [r for r in rows if r["family"] == "imbalance"]
    mis = [r for r in rows if r["family"].endswith("misalignment")]

    clean = [r for r in normal if not r["rca_primary"]]
    print(f"PART A — normal, HARD LINE (zero fault findings)")
    print(f"  {len(clean)}/{len(normal)} clean  -> {'PASS' if len(clean)==len(normal) else 'FAIL'}")
    for r in normal:
        if r["rca_primary"]:
            print(f"    FALSE POSITIVE {r['file']}: {r['rca_primary']}")

    hit_imb = [r for r in imb if any(f == "imbalance" for f, _, _ in r["rca_primary"])]
    pct = 100.0 * len(hit_imb) / len(imb) if imb else 0.0
    print(f"\nPART B — imbalance (bar: >=90% primary)")
    print(f"  {len(hit_imb)}/{len(imb)} = {pct:.1f}%  -> {'PASS' if pct >= 90 else 'MISS'}")

    hit_mis = [r for r in mis if any(f in _MISALIGNMENT_FAMILY for f, _, _ in r["rca_primary"])]
    pctm = 100.0 * len(hit_mis) / len(mis) if mis else 0.0
    print(f"\nPART C — misalignment FAMILY (bar: >=80% primary)")
    print(f"  {len(hit_mis)}/{len(mis)} = {pctm:.1f}%  -> {'PASS' if pctm >= 80 else 'MISS'}")

    print(f"\nSPEED CROSS-CHECK (>2% flags)")
    flg = [r for r in rows if r["speed_flagged"]]
    deltas = [r["speed_delta_pct"] for r in rows]
    print(f"  flagged {len(flg)}/{len(rows)}; delta min={min(deltas):.2f}% "
          f"max={max(deltas):.2f}% mean={np.mean(deltas):.2f}%")

    print(f"\nWhat fired instead (all families seen):")
    seen: dict[str, int] = {}
    for r in rows:
        for f, _, _ in r["rca_primary"]:
            seen[f] = seen.get(f, 0) + 1
    for k, v in sorted(seen.items(), key=lambda kv: -kv[1]):
        print(f"  {k:28s} {v}")
    print(f"  (no primary finding)        {sum(1 for r in rows if not r['rca_primary'])}")

    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
