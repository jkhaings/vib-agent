"""MAFAULDA (Machinery Fault Database, UFRJ) adapter — Phase 8.

Validates the two detector families that have never touched real measured data:
IMBALANCE and the MISALIGNMENT family. 8-channel, 50 kHz, 5 s recordings from a
SpectraQuest ABVT fault simulator, with real weights (6-35 g) and real shims
(0.5-2.0 mm horizontal / 0.51-1.90 mm vertical) across ~12-61 Hz shaft speeds.

Normalizes one recording into a Case that runs through the exact same
pipeline.run_analysis() as every other case. Mirrors adapters/mfpt.py's
conventions and the PeakSet-boundary discipline.

THE ONE IMPORTANT DIFFERENCE FROM CWRU/MFPT: this adapter emits a **RAW**
acceleration spectrum, not an envelope spectrum. Envelope demodulation is the
right preprocessing for BEARING defect frequencies (amplitude modulation of a
high-frequency resonance) but the WRONG input for imbalance/misalignment, which
pdm_core detects from discrete 1x/2x shaft-harmonic lines. In an envelope
spectrum, 1x energy is a demodulation artifact, not an imbalance signature —
Phase 7B showed this producing a false 'rotor imbalance' call on a healthy MFPT
baseline. See config/mafaulda.json's _spectrum_note.

Shaft speed is derived FROM THE TACHOMETER CHANNEL (spec, explicit), never the
filename; the filename's nominal speed is used only as the cross-check value.

Ground truth comes from directory structure + official docs only. Acceleration
units (g) -> validation_scope=["rca"], severity out of scope.

Pure functions except load_mafaulda_csv (file I/O, by necessity). No detector
logic, no thresholds, no LLM.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
from scipy.signal import find_peaks

from vib_agent.models import BearingSpec, Case, CaseExpected, MachineMeta, SensorData, Spectrum

# data/mafaulda/imbalance/6g/15.36.csv -> ("imbalance", "6g", 15.36)
# data/mafaulda/normal/12.288.csv      -> ("normal", None, 12.288)
_SEVERITY_DIRS = ("imbalance", "horizontal-misalignment", "vertical-misalignment")


def label_from_path(path: str | Path) -> tuple[str, str | None, float]:
    """(family, severity_param, nominal_speed_hz) from the directory structure.

    Ground truth is the directory layout + official docs, never invented
    (INVARIANT 2). The filename IS the nominal rotation frequency in Hz.
    """
    path = Path(path)
    try:
        speed = float(path.stem)
    except ValueError as exc:
        raise ValueError(
            f"{path.name!r}: MAFAULDA filenames are the nominal rotation frequency in Hz "
            "(e.g. 12.288.csv) — cannot establish the directory-implied speed"
        ) from exc

    parts = path.parts
    for i, p in enumerate(parts):
        if p in _SEVERITY_DIRS:
            severity = parts[i + 1] if i + 1 < len(parts) - 1 else None
            return p, severity, speed
        if p == "normal":
            return "normal", None, speed
    raise ValueError(
        f"{path}: not under a known MAFAULDA category directory "
        f"(normal | {' | '.join(_SEVERITY_DIRS)})"
    )


def load_mafaulda_csv(path: str | Path, *, mafaulda_cfg: dict[str, Any]) -> dict[str, np.ndarray]:
    """Load one 8-column CSV into {column_name: signal}, asserting the shape the
    official docs state. Files carry no header row."""
    path = Path(path)
    cols = list(mafaulda_cfg["columns"])
    data = np.loadtxt(path, delimiter=",")
    if data.ndim != 2 or data.shape[1] != len(cols):
        raise ValueError(
            f"this CSV does not have the {len(cols)} columns expected for a MAFAULDA recording"
        )
    expected_n = int(round(float(mafaulda_cfg["sample_rate_hz"]) * float(mafaulda_cfg["record_seconds"])))
    if data.shape[0] != expected_n:
        raise ValueError(
            f"this recording has {data.shape[0]} samples; a MAFAULDA recording should have {expected_n}"
        )
    return {name: data[:, i] for i, name in enumerate(cols)}


def shaft_hz_from_tach(
    tach: np.ndarray,
    fs: float,
    *,
    search_band_hz: tuple[float, float],
    significance: float = 0.25,
) -> float:
    """Rotation frequency from the analog tachometer channel — the FUNDAMENTAL
    (lowest significant harmonic) of its spectrum within the rig's plausible
    speed band. Spec: shaft speed comes from this channel, NOT the filename.

    NOT the dominant (loudest) peak, deliberately. The MAFAULDA tach is a
    Monarch MT-190 analog signal carrying narrow pulses (raw range roughly
    -1.3..+5.0 about a ~0 mean), one per revolution. A narrow pulse train's
    spectrum is a long harmonic series with slowly-decaying amplitude, so the
    2nd harmonic is sometimes LOUDER than the fundamental and `argmax` silently
    returns 2x the true speed: on normal/12.288.csv the 2nd harmonic measures
    0.9823 against the fundamental's 0.9605 (verified 2026-07-17), which would
    have doubled the shaft rate — and with it every derived fault frequency.
    Time-domain pulse counting is not robust either: on that same file the
    pulses carry enough sub-structure to be counted twice (121 detections over
    5 s => 24.2 Hz, against a true 12.0 Hz).

    Taking the lowest harmonic clearing `significance` x the band maximum is
    stable across the whole rig speed range: verified on all 10 normal files
    plus imbalance samples, it returns the fundamental every time, agreeing with
    the pulse-counted rate to within one FFT bin (0.2 Hz at a 5 s record).
    """
    x = np.asarray(tach, dtype=float)
    x = x - x.mean()
    n = len(x)
    w = np.hanning(n)
    spectrum = np.abs(np.fft.rfft(x * w)) * 2.0 / w.sum()
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)

    lo, hi = search_band_hz
    mask = (freqs >= lo) & (freqs <= hi)
    if not mask.any():
        raise ValueError(f"no spectral bins in the tach search band {lo}-{hi} Hz")
    band_f, band_a = freqs[mask], spectrum[mask]

    idx, _ = find_peaks(band_a, height=significance * float(band_a.max()))
    if len(idx) == 0:
        raise ValueError(
            f"no significant tach peak in {lo}-{hi} Hz — cannot derive shaft rate "
            "(do not fall back to the filename; the spec requires the tach)"
        )
    return float(band_f[idx[0]])  # lowest significant harmonic = the fundamental


def cross_check_speed(
    tach_hz: float, nominal_hz: float, *, tolerance_pct: float
) -> tuple[bool, float]:
    """Spec: 'cross-check against directory-implied speed, flag >2% disagreement'.
    Flags, never raises — a disagreement is a finding to report, not a crash."""
    delta_pct = abs(tach_hz - nominal_hz) / nominal_hz * 100.0
    return (delta_pct > tolerance_pct, delta_pct)


def raw_spectrum(signal: np.ndarray, fs: float, *, region_hz: tuple[float, float]) -> Spectrum:
    """RAW amplitude spectrum over `region_hz` — deliberately NOT an envelope.

    See this module's docstring and config/mafaulda.json's _spectrum_note: the
    imbalance/misalignment detectors key on discrete 1x/2x shaft-harmonic lines,
    which are genuine content here and artifacts in an envelope.
    """
    x = np.asarray(signal, dtype=float)
    x = x - x.mean()
    n = len(x)
    w = np.hanning(n)
    amplitude = np.abs(np.fft.rfft(x * w)) * 2.0 / w.sum()
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    mask = (freqs >= region_hz[0]) & (freqs <= region_hz[1])
    return Spectrum(
        freq_hz=freqs[mask].tolist(), amplitude=amplitude[mask].tolist(), fmax_hz=region_hz[1],
        kind="raw_acceleration",
    )


def to_case(
    path: str | Path,
    *,
    mafaulda_cfg: dict[str, Any],
    bearings_cfg: dict[str, Any],
) -> Case:
    """Build a Case from one MAFAULDA recording."""
    path = Path(path)
    family, severity, nominal_hz = label_from_path(path)
    signals = load_mafaulda_csv(path, mafaulda_cfg=mafaulda_cfg)
    fs = float(mafaulda_cfg["sample_rate_hz"])

    shaft_hz = shaft_hz_from_tach(
        signals["tachometer"], fs, search_band_hz=tuple(mafaulda_cfg["tach_search_band_hz"])
    )

    # Underhang triax is the primary measurement point; overhang is secondary
    # context only in v1 and is NOT fed to the detectors.
    axis_map: dict[str, str] = mafaulda_cfg["axis_map"]
    region = tuple(mafaulda_cfg["spectrum_region_hz"])
    # Session B (B1): MAFAULDA is RAW-only by design (envelope 1x is an artifact
    # for its 1x-family detectors). It has no calibrated envelope band, so adding
    # one would be an uncalibrated constant (forbidden in a structural B1). The
    # bearing detectors therefore read this raw spectrum too (spec: bearing accepts
    # "envelope (preferred) AND raw"); the 1x-family read it via `raw_spectra`. A
    # calibrated MAFAULDA envelope is deferred to the B3/B4 detector work.
    raw = {
        axis: raw_spectrum(signals[col], fs, region_hz=region) for col, axis in axis_map.items()
    }
    spectra = raw

    rms_by_axis = {
        axis: float(np.sqrt(np.mean(np.square(signals[col])))) for col, axis in axis_map.items()
    }

    bearing_entry = bearings_cfg["bearings"][mafaulda_cfg["bearing_key"]]
    bearing = BearingSpec(
        **{k: v for k, v in bearing_entry.items() if not k.startswith("_")},
        model=mafaulda_cfg["bearing_key"],
    )

    machine = MachineMeta(
        mac=f"MAFAULDA-{family}",
        name=f"MAFAULDA {family}" + (f" {severity}" if severity else ""),
        active=True,
        type="motor",
        iso_group="2",
        iso_support="rigid",
        bearing=bearing,
        axial_axis=mafaulda_cfg["axial_axis"],
        coupled=True,  # ABVT: motor coupled to the rotor shaft
    )

    sensor_data = SensorData(
        rpm=shaft_hz * 60.0,
        x_rms_ACC_G=rms_by_axis.get("x"),
        y_rms_ACC_G=rms_by_axis.get("y"),
        z_rms_ACC_G=rms_by_axis.get("z"),
    )

    expected_family = {
        "normal": None,
        "imbalance": "imbalance",
        "horizontal-misalignment": "misalignment_family",
        "vertical-misalignment": "misalignment_family",
    }[family]

    return Case(
        name=f"{family}_{severity or 'none'}_{nominal_hz}",
        machine=machine,
        sensor_data=sensor_data,
        spectra=spectra,
        raw_spectra=raw,  # 1x-family read the raw spectrum explicitly (same as bearing here)
        source="mafaulda",
        validation_scope=list(mafaulda_cfg["validation_scope"]),
        expected=CaseExpected(
            faults=[expected_family] if expected_family else [],
            fault_type=family,
            severity_param=severity,
            speed_hz=nominal_hz,
        ),
    )
