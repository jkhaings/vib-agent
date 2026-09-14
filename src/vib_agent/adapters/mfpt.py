"""MFPT Bearing Fault Dataset adapter (Phase 7 — cross-rig validation +
real-world field test).

Normalizes an MFPT .mat recording (lab rig or real-world) into a Case that
runs through the exact same pipeline.run_analysis() as every other case.
Mirrors adapters/cwru.py's conventions and the PeakSet-boundary discipline.

Rig files (20): known labels (normal/outer/inner), all use the MFPT_NICE
lab-rig bearing (config/bearings.json), a single acceleration channel
mapped to the radial 'y' axis exactly like CWRU, envelope band/region
reused VERBATIM from config/cwru.json (zero new DSP tuning — see
config/mfpt.json's _envelope_band_note).

Real-world files (3): NOT officially labeled by fault location. Each
carries its own embedded fault-frequency ORDERS (ball/cage/outer/inner,
multiples of the file's own shaft rate) — read directly from the file,
never hardcoded, never treated as ground truth beyond what Part B's
two-outcome scoring rule (eval/runner.py::run_mfpt_suite) uses them for.
equivalent_bearing_from_frequencies() derives a synthetic BearingSpec from
the embedded outer/inner orders alone (exact algebraic reproduction of
BPFO/BPFI; BSF/FTF are close approximations — see its docstring) so the
SAME frozen pdm_core.bearing_rca detectors run unmodified on these files
too, through the real product path (Parts B and C).

Pure functions except load_mfpt_mat (file I/O, by necessity). No detector
logic, no thresholds, no LLM.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import loadmat

from vib_agent.adapters.cwru import envelope_spectrum, raw_spectrum
from vib_agent.models import BearingSpec, Case, CaseExpected, MachineMeta, SensorData


def _unwrap(struct: np.ndarray, field: str) -> Any:
    """scipy.io.loadmat(squeeze_me=True) wraps struct fields in 0-d object
    arrays — .item() unwraps to the actual scalar/array/string."""
    return struct[field].item()


def load_mfpt_mat(path: str | Path) -> dict[str, Any]:
    """Load one MFPT .mat file's 'bearing' struct into a plain dict. Every
    file has {sr, gs, load, rate}; real-world files additionally have
    {ball, cage, outer, inner} (dimensionless fault-frequency ORDERS,
    multiples of `rate`). Never guesses a missing field."""
    path = Path(path)
    mat = loadmat(str(path), squeeze_me=True)
    if "bearing" not in mat:
        raise ValueError("this .mat file has no recognized bearing recording to analyze")
    b = mat["bearing"]

    result: dict[str, Any] = {
        "sr": float(_unwrap(b, "sr")),
        "gs": np.asarray(_unwrap(b, "gs"), dtype=float).flatten(),
        "rate": float(_unwrap(b, "rate")),
    }

    load_field = b["load"]
    try:
        load_raw = load_field.item()
    except ValueError:
        load_raw = None  # some files (e.g. PlanetBearing) carry an empty load field
    if load_raw is not None and getattr(load_raw, "size", 1) == 0:
        load_raw = None  # empty array in either (0,) or (0,0) shape -- no load recorded
    result["load"] = str(load_raw) if load_raw is not None and str(load_raw) != "" else None

    for order_field in ("ball", "cage", "outer", "inner"):
        if order_field in b.dtype.names:
            result[order_field] = float(_unwrap(b, order_field))
    return result


def assert_shaft_rate(rate_hz: float, *, expected_hz: float, tolerance_pct: float, source_name: str) -> None:
    """The Feb-2013 dataset fix corrected a wrong shaft-rate field (50Hz vs
    the true 25Hz) on rig files. Guards against ever silently using a
    pre-fix copy."""
    delta_pct = abs(rate_hz - expected_hz) / expected_hz * 100.0
    if delta_pct > tolerance_pct:
        raise ValueError(
            f"{source_name}: shaft rate {rate_hz:.4f} Hz is {delta_pct:.2f}% off the expected "
            f"{expected_hz} Hz — possible pre-Feb-2013 dataset copy (known 50Hz-vs-25Hz bug). "
            "Re-download from the source in scripts/fetch_mfpt.py."
        )


def equivalent_bearing_from_frequencies(outer_order: float, inner_order: float) -> BearingSpec:
    """Derive a synthetic BearingSpec that reproduces a real-world file's
    own embedded BPFO/BPFI orders EXACTLY, so pdm_core.bearing_rca's frozen,
    unmodified detectors can run against it through the real product path.

    From bearing_frequencies()'s own formulas:
        BPFO/shaft = (n/2)(1-r),  BPFI/shaft = (n/2)(1+r)
        where r = (ball_dia/pitch_dia) * cos(contact_angle)
    Summing and differencing:
        n = BPFO_order + BPFI_order   (rounded to the nearest integer —
                                        a real bearing has an integer ball
                                        count)
        r = (BPFI_order - BPFO_order) / (BPFI_order + BPFO_order)
    contact_angle is assumed 0 (unknown for these files; also
    NiceBearing.m's own assumption for the lab rig). Absolute ball/pitch
    diameters are not individually recoverable from ratios alone and are
    not needed — bearing_frequencies() depends only on their ratio r for
    BPFO/BPFI, and BSF depends on r alone too, so a 100mm reference pitch
    diameter (ball_dia_mm = r * 100mm) reproduces the same r. FTF and BSF
    are then close approximations, not exact reproductions — see
    config/mfpt.json's geometry_source note on why exact FTF reproduction
    isn't attempted (Bechhoefer's own cage-frequency formula doesn't
    satisfy the BPFO = n*FTF identity that must hold when the outer race
    is fixed, so there is no single "correct" FTF ratio to target here).
    """
    n_balls = round(outer_order + inner_order)
    if n_balls < 4:
        raise ValueError(
            f"derived n_balls={n_balls} from outer={outer_order}, inner={inner_order} "
            "is implausibly low for a rolling-element bearing"
        )
    r = (inner_order - outer_order) / (inner_order + outer_order)
    pitch_dia_mm = 100.0
    ball_dia_mm = r * pitch_dia_mm
    return BearingSpec(
        n_balls=n_balls,
        ball_dia_mm=ball_dia_mm,
        pitch_dia_mm=pitch_dia_mm,
        contact_angle_deg=0.0,
    )


def to_case(
    path: str | Path,
    *,
    mfpt_cfg: dict[str, Any],
    bearings_cfg: dict[str, Any],
) -> Case:
    """Build a Case from one MFPT .mat file. Dispatches on whether the
    file's stem is a known rig file (config/mfpt.json rig_files) or
    real-world file (real_world_files) — the stem convention is set by
    scripts/fetch_mfpt.py's save names.
    """
    path = Path(path)
    stem = path.stem
    rig_meta = mfpt_cfg["rig_files"].get(stem)
    rw_meta = mfpt_cfg["real_world_files"].get(stem)
    if rig_meta is None and rw_meta is None:
        raise ValueError(
            f"{stem!r} is not a known MFPT filename (see config/mfpt.json rig_files / real_world_files)"
        )

    data = load_mfpt_mat(path)
    envelope = envelope_spectrum(
        data["gs"],
        data["sr"],
        band_hz=tuple(mfpt_cfg["envelope_band_hz"]),
        region_hz=tuple(mfpt_cfg["envelope_region_hz"]),
    )
    raw = raw_spectrum(data["gs"], data["sr"])  # B1: 1x-family reads raw, not envelope
    rms_g = float(np.sqrt(np.mean(np.square(data["gs"]))))
    load_lbs: float | None = None
    if data.get("load") is not None:
        try:
            load_lbs = float(data["load"])
        except ValueError:
            load_lbs = None  # non-numeric load field -- leave unset rather than guess

    if rig_meta is not None:
        assert_shaft_rate(
            data["rate"],
            expected_hz=mfpt_cfg["expected_shaft_rate_hz"],
            tolerance_pct=mfpt_cfg["shaft_rate_tolerance_pct"],
            source_name=path.name,
        )
        bearing_entry = bearings_cfg["bearings"][mfpt_cfg["bearing_key"]]
        bearing = BearingSpec(**{k: v for k, v in bearing_entry.items() if not k.startswith("_")})
        fault_type = rig_meta["fault_type"]
        primary_fault = {
            "outer": "bearing_outer_race",
            "inner": "bearing_inner_race",
            "normal": None,
        }[fault_type]
        expected = CaseExpected(
            faults=[primary_fault] if primary_fault else [],
            fault_type=fault_type,
            load_lbs=load_lbs,
        )
    else:
        for required in ("outer", "inner"):
            if required not in data:
                raise ValueError(f"this recording is missing embedded order data ('{required}') needed to analyze it")
        bearing = equivalent_bearing_from_frequencies(data["outer"], data["inner"])
        expected = CaseExpected(
            faults=[],
            fault_type="real_world",
            embedded_ball_order=data.get("ball"),
            embedded_cage_order=data.get("cage"),
            embedded_outer_order=data.get("outer"),
            embedded_inner_order=data.get("inner"),
            load_lbs=load_lbs,
        )

    machine = MachineMeta(
        mac=f"MFPT-{stem}",
        name=f"MFPT {stem}",
        active=True,
        type="motor",
        iso_group="2",
        iso_support="rigid",
        bearing=bearing,
        axial_axis="x",
    )
    sensor_data = SensorData(rpm=data["rate"] * 60.0, y_rms_ACC_G=rms_g)

    return Case(
        name=stem,
        machine=machine,
        sensor_data=sensor_data,
        spectra={"y": envelope},
        raw_spectra={"y": raw},
        source="mfpt",
        validation_scope=["rca"],
        expected=expected,
    )
