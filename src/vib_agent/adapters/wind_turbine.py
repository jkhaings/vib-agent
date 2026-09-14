"""Wind-turbine high-speed-shaft bearing run-to-failure adapter (Phase 7B —
the TIME dimension: Layer 2 Welford anomaly + Layer 4 trend on real degradation).

50 daily 6-second vibration recordings from a commercial 2 MW turbine's
high-speed shaft, ending in a confirmed inner-race fault. Normalizes each
recording into a Case that runs through the exact same pipeline.run_analysis()
as every other case. Mirrors adapters/mfpt.py's conventions and the
PeakSet-boundary discipline.

Per file this emits BOTH:
  - overall RMS (acceleration, g) -> the 50-point history feeding Layers 2/4
  - an envelope spectrum -> Layer 5 via peaks_from_spectrum

Shaft rate is DERIVED per file from that file's own tachometer pulse times
(the repo documents no shaft speed) and cross-checked against the raw
spectrum's 1x peak — see config/wind_turbine.json's _pulses_per_rev_note for
the two independent lines of evidence establishing 2 pulses/rev. This is a
real variable-speed turbine; the rate is never hardcoded.

validation_scope = ["rca", "trend_relative", "anomaly"]: acceleration units
mean ISO zones and the velocity-based trend alarm limit are OUT of scope;
slope/R2/pct_change and z-scores are unit-relative and IN scope.

BEARING GEOMETRY IS BLOCKED (see BLOCKED_phase7b_bearing_geometry.md): the
SKF 32222 J2's roller count/diameter are unobtainable within this run's rules,
so config's bearing_key is null and MachineMeta.bearing is None. pdm_core
handles that by design (bearing_rca.py:332) — the bearing detectors simply do
not fire, rather than firing on a fabricated geometry.

Pure functions except load_wt_mat (file I/O, by necessity). No detector logic,
no thresholds, no LLM.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import loadmat
from scipy.signal import find_peaks

from vib_agent.adapters.cwru import envelope_spectrum, raw_spectrum
from vib_agent.models import (
    BearingSpec,
    Case,
    CaseExpected,
    HistoryPoint,
    MachineMeta,
    SensorData,
)

# data-20130307T015746Z.mat -> 2013-03-07 01:57:46 UTC
_TS_RE = re.compile(r"^data-(\d{8}T\d{6})Z$")


def parse_timestamp(path: str | Path) -> datetime:
    """Extract the recording timestamp from the filename. The dataset encodes
    it as data-YYYYMMDDTHHMMSSZ.mat (trailing Z = UTC). Chronology is this
    phase's core invariant, so a name that doesn't parse is an error, never a
    silently-defaulted 'now'."""
    stem = Path(path).stem
    m = _TS_RE.match(stem)
    if m is None:
        raise ValueError(
            f"{stem!r} does not match the dataset's data-YYYYMMDDTHHMMSSZ naming — "
            "cannot establish chronology (Phase 7B INVARIANT 2)"
        )
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


def load_wt_mat(path: str | Path, *, wt_cfg: dict[str, Any]) -> dict[str, Any]:
    """Load one recording's {vibration, tach} into a plain dict, asserting the
    record length implied by the configured sample rate. Every file in this
    dataset holds exactly these two variables (verified across all 50).

    `timestamp` is BEST-EFFORT here and is None when the filename doesn't
    carry one. That is deliberate: the webapp deliberately discards a user's
    original filename (privacy model — the upload is stored as `upload`), and
    an uploaded snapshot has no chronology to establish anyway. INVARIANT 2
    (chronology honesty) is enforced where ordering actually happens — the
    caller, scripts/run_wind_turbine.py, calls parse_timestamp() directly and
    asserts strict ordering, so a mis-named file still cannot slip into the
    chronological run.
    """
    path = Path(path)
    mat = loadmat(str(path), squeeze_me=True)
    for key in ("vibration", "tach"):
        if key not in mat:
            raise ValueError(f"this recording is missing its {key!r} channel")

    vibration = np.asarray(mat["vibration"], dtype=float).flatten()
    tach = np.asarray(mat["tach"], dtype=float).flatten()
    fs = float(wt_cfg["sample_rate_hz"])

    expected_n = int(round(fs * float(wt_cfg["record_seconds"])))
    if len(vibration) != expected_n:
        raise ValueError(
            f"{path.name}: {len(vibration)} vibration samples, expected {expected_n} "
            f"({wt_cfg['record_seconds']}s @ {fs}Hz). Sample rate or record length "
            "disagrees with config/wind_turbine.json — do not assume, re-inspect the source."
        )
    try:
        timestamp: datetime | None = parse_timestamp(path)
    except ValueError:
        timestamp = None  # an upload, not a dataset file — see docstring

    return {
        "vibration": vibration,
        "tach": tach,
        "fs": fs,
        "timestamp": timestamp,
    }


def shaft_hz_from_tach(tach: np.ndarray, *, pulses_per_rev: int) -> float:
    """Average shaft rate from tachometer pulse TIMES (seconds, monotonic).

    tach is a vector of pulse instants, not a sampled waveform — the mean
    inter-pulse interval gives the pulse rate, and pulse rate / pulses_per_rev
    is the shaft rate. See config/wind_turbine.json's _pulses_per_rev_note for
    the evidence fixing pulses_per_rev = 2 on this dataset.
    """
    if len(tach) < 2:
        raise ValueError(f"tach has {len(tach)} pulses — need >= 2 to derive a rate")
    diffs = np.diff(tach)
    if not np.all(diffs > 0):
        raise ValueError("tach pulse times are not monotonically increasing — not a pulse-time vector")
    return float(1.0 / diffs.mean() / pulses_per_rev)


def one_x_from_spectrum(signal: np.ndarray, fs: float, *, expected_hz: float, search_pct: float) -> tuple[float, float]:
    """Locate the raw spectrum's 1x peak near `expected_hz`, for the spec's
    mandated cross-check of the tach-derived rate. Returns (freq, amplitude).
    Searches a window around the expectation rather than globally — a wind
    turbine spectrum's global maximum sits in the bearing-resonance region
    (~4 kHz), not at 1x.
    """
    n = len(signal)
    window = np.hanning(n)
    spectrum = np.abs(np.fft.rfft((signal - signal.mean()) * window)) * 2.0 / window.sum()
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)

    lo = expected_hz * (1.0 - search_pct / 100.0)
    hi = expected_hz * (1.0 + search_pct / 100.0)
    mask = (freqs >= lo) & (freqs <= hi)
    if not mask.any():
        raise ValueError(f"no spectral bins in {lo:.2f}-{hi:.2f} Hz to search for 1x")

    band_freqs, band_amps = freqs[mask], spectrum[mask]
    idx, _ = find_peaks(band_amps)
    if len(idx) == 0:
        i = int(np.argmax(band_amps))  # no local maximum — fall back to the band's loudest bin
    else:
        i = int(idx[np.argmax(band_amps[idx])])
    return float(band_freqs[i]), float(band_amps[i])


def cross_check_shaft_rate(
    tach_hz: float, spectrum_1x_hz: float, *, tolerance_pct: float, source_name: str
) -> tuple[bool, float]:
    """Spec: 'cross-check against the 1x peak of an early file's raw spectrum;
    flag >3% disagreement'. Returns (flagged, delta_pct) — this FLAGS, it does
    not raise: a variable-speed turbine legitimately drifts, and the phase's
    job is to report disagreement, not to crash on it.
    """
    delta_pct = abs(tach_hz - spectrum_1x_hz) / spectrum_1x_hz * 100.0
    return (delta_pct > tolerance_pct, delta_pct)


def overall_rms_g(vibration: np.ndarray) -> float:
    """Overall RMS acceleration in g — the scalar feeding the 50-point history
    that Layers 2 (Welford) and 4 (trend) consume."""
    return float(np.sqrt(np.mean(np.square(vibration))))


def _resolve_bearing(wt_cfg: dict[str, Any], bearings_cfg: dict[str, Any]) -> BearingSpec | None:
    """Bearing geometry is BLOCKED for this dataset (see
    BLOCKED_phase7b_bearing_geometry.md) — bearing_key is null, so this returns
    None and pdm_core's bearing detectors correctly do not fire. Wired to work
    the instant a real key is supplied."""
    key = wt_cfg.get("bearing_key")
    if key is None:
        return None
    entry = bearings_cfg["bearings"][key]
    return BearingSpec(**{k: v for k, v in entry.items() if not k.startswith("_")})


def to_case(
    path: str | Path,
    *,
    wt_cfg: dict[str, Any],
    bearings_cfg: dict[str, Any],
    history: list[HistoryPoint] | None = None,
) -> Case:
    """Build a Case from one wind-turbine recording.

    `history` is supplied BY THE CALLER and must contain only readings at or
    before this file's own timestamp — the adapter never reads other files.
    That keeps INVARIANT 2 (chronology honesty; no peeking at later files)
    enforceable at the call site, where the ordering actually lives:
    see scripts/run_wind_turbine.py.
    """
    path = Path(path)
    data = load_wt_mat(path, wt_cfg=wt_cfg)

    shaft_hz = shaft_hz_from_tach(data["tach"], pulses_per_rev=int(wt_cfg["tach_pulses_per_rev"]))
    envelope = envelope_spectrum(
        data["vibration"],
        data["fs"],
        band_hz=tuple(wt_cfg["envelope_band_hz"]),
        region_hz=tuple(wt_cfg["envelope_region_hz"]),
    )
    raw = raw_spectrum(data["vibration"], data["fs"])  # B1: 1x-family reads raw
    rms_g = overall_rms_g(data["vibration"])

    machine = MachineMeta(
        mac="WT-HS-BEARING",
        name="Wind turbine high-speed shaft bearing",
        active=True,
        type="motor",
        iso_group="2",
        iso_support="rigid",
        bearing=_resolve_bearing(wt_cfg, bearings_cfg),
        axial_axis="x",
    )
    sensor_data = SensorData(rpm=shaft_hz * 60.0, y_rms_ACC_G=rms_g)

    return Case(
        name=path.stem,
        machine=machine,
        sensor_data=sensor_data,
        spectra={"y": envelope},
        raw_spectra={"y": raw},
        history=history,
        source="wind_turbine",
        validation_scope=list(wt_cfg["validation_scope"]),
        expected=CaseExpected(
            faults=[],
            # The dataset's only ground truth: it ends in a confirmed inner-race
            # fault. NOT a per-file label — early files are not known-healthy, and
            # the spec forbids auto-failing early bearing calls on that assumption.
            fault_type="run_to_failure_inner_race_endpoint",
        ),
    )
