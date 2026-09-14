"""CWRU Bearing Data Center adapter (Phase 3.5 — real-data validation).

Normalizes a 12kHz Drive-End .mat recording into a Case that runs through the
exact same pipeline.run_analysis() as every other case. Mirrors the
PeakSet-boundary discipline one layer up: nothing downstream of to_case()
needs to know its data came from a .mat file rather than an NCD packet or a
synthetic spectrum.

Zero-shared-code design (Phase 3.5 plan): the single Drive-End channel is
mapped to the radial 'y' axis. classify() resolves the dominant/axial axis to
'x' (all velocities absent -> 0), so the quality gate's spectrum argument
(spectra.get(dominant_axis)) is None and every spectrum-dependent check
(including speed-sanity) correctly resolves to not_applicable — no pdm_core
or pipeline code was touched to make this work.

Pure functions except load_cwru_mat (file I/O, by necessity — this is a data
adapter, not pdm_core). No detector logic, no thresholds, no LLM.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import loadmat
from scipy.signal import butter, filtfilt, hilbert, welch

from vib_agent.models import BearingSpec, Case, CaseExpected, MachineMeta, SensorData, Spectrum

_FAULT_LABELS: dict[str, tuple[str, str | None]] = {
    "Normal": ("normal", None),
    "OR": ("outer", "bearing_outer_race"),
    "IR": ("inner", "bearing_inner_race"),
    "B": ("ball", "bearing_ball_spin"),
}

# Matches: Normal_0 | OR007@6_0 | IR014_0 | B021_0
_FILENAME_RE = re.compile(r"^(?P<label>Normal|OR|IR|B)(?P<diam>\d{3})?(?:@(?P<pos>\d+))?_(?P<suffix>\d)$")


def _parse_filename(stem: str) -> dict[str, Any]:
    m = _FILENAME_RE.match(stem)
    if m is None:
        raise ValueError(
            f"filename {stem!r} does not match the CWRU starter-set convention "
            "(Normal_N / OR0##@6_N / IR0##_N / B0##_N)"
        )
    return m.groupdict()


def filename_to_expected(stem: str, cwru_cfg: dict[str, Any]) -> dict[str, Any]:
    """Parse a CWRU filename stem into {fault_type, diameter_in, rpm, primary_fault}."""
    parts = _parse_filename(stem)
    fault_type, primary_fault = _FAULT_LABELS[parts["label"]]
    diameter_in = int(parts["diam"]) / 1000.0 if parts["diam"] else None
    rpm = float(cwru_cfg["rpm_by_load_suffix"][parts["suffix"]])
    return {
        "fault_type": fault_type,
        "diameter_in": diameter_in,
        "rpm": rpm,
        "primary_fault": primary_fault,
    }


def load_cwru_signal(path: str | Path, cwru_cfg: dict[str, Any]) -> tuple[np.ndarray, float]:
    """Load ONLY the Drive-End vibration signal + sample rate from a CWRU-format
    .mat — no filename parsing, no RPM inference.

    This is the product/upload seam: the web-upload path (adapters/uploads/cwru.py)
    takes machine context (rpm, bearing, group, support) exclusively from the form
    and never depends on the file's name, so it uses this loader. The eval path
    (load_cwru_mat, below) layers filename-derived RPM on top of the same signal.
    The error message is deliberately in user terms — it names no internal key
    and no filename.
    """
    path = Path(path)
    mat = loadmat(str(path))
    de_key = next((k for k in mat if k.endswith("_DE_time")), None)
    if de_key is None:
        raise ValueError("this .mat file has no drive-end vibration channel to analyze")
    signal = np.asarray(mat[de_key]).flatten().astype(float)
    return signal, float(cwru_cfg["fs_hz"])


def load_cwru_mat(path: str | Path, cwru_cfg: dict[str, Any]) -> tuple[np.ndarray, float, float]:
    """Load a CWRU .mat file: returns (signal, fs_hz, rpm).

    Prefers an explicit *RPM key in the .mat file (present on some files);
    falls back to the filename-family rpm map (config/cwru.json) since most
    12k-DE fault files do not carry an RPM key.
    """
    path = Path(path)
    mat = loadmat(str(path))

    de_key = next((k for k in mat if k.endswith("_DE_time")), None)
    if de_key is None:
        raise ValueError(f"no '*_DE_time' key found in {path.name} (keys: {list(mat.keys())})")
    signal = np.asarray(mat[de_key]).flatten().astype(float)

    rpm_key = next((k for k in mat if "RPM" in k.upper()), None)
    if rpm_key is not None:
        rpm = float(np.asarray(mat[rpm_key]).flatten()[0])
    else:
        parts = _parse_filename(path.stem)
        rpm = float(cwru_cfg["rpm_by_load_suffix"][parts["suffix"]])

    fs = float(cwru_cfg["fs_hz"])
    return signal, fs, rpm


def raw_spectrum(signal: np.ndarray, fs: float) -> Spectrum:
    """Welch amplitude spectrum of the raw (unfiltered) time signal — context
    for debugging / manual inspection. Not consumed by the quality gate under
    the zero-shared-code design (see module docstring).
    """
    nperseg = min(len(signal), 4096)
    freqs, psd = welch(signal, fs=fs, nperseg=nperseg)
    amplitude = np.sqrt(np.maximum(psd, 0.0))
    return Spectrum(
        freq_hz=freqs.tolist(), amplitude=amplitude.tolist(), fmax_hz=float(freqs[-1]),
        kind="raw_acceleration",
    )


def envelope_spectrum(
    signal: np.ndarray,
    fs: float,
    band_hz: tuple[float, float] = (1500.0, 5500.0),
    region_hz: tuple[float, float] = (0.0, 500.0),
) -> Spectrum:
    """Envelope (demodulated amplitude) spectrum via band-pass -> Hilbert ->
    |analytic| -> FFT — the canonical technique for rolling-element defect
    frequencies (energy at BPFO/BPFI/BSF is carried as amplitude modulation
    of the bearing's high-frequency resonance, not as discrete spectral lines
    in the raw signal).
    """
    nyquist = fs / 2.0
    low = band_hz[0] / nyquist
    high = min(band_hz[1], nyquist * 0.99) / nyquist
    b, a = butter(4, [low, high], btype="band")
    filtered = filtfilt(b, a, signal)

    analytic = hilbert(filtered)
    envelope = np.abs(analytic)
    envelope = envelope - np.mean(envelope)

    n = len(envelope)
    fft_vals = np.fft.rfft(envelope)
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    amplitude = np.abs(fft_vals) * (2.0 / n)

    mask = (freqs >= region_hz[0]) & (freqs <= region_hz[1])
    return Spectrum(
        freq_hz=freqs[mask].tolist(), amplitude=amplitude[mask].tolist(), fmax_hz=region_hz[1],
        kind="envelope",
    )


def to_case(
    path: str | Path,
    *,
    cwru_cfg: dict[str, Any],
    bearing_spec: BearingSpec,
) -> Case:
    """Build a Case from one CWRU .mat file. The Drive-End channel maps to
    the radial 'y' axis; RCA consumes its envelope spectrum via the existing
    peaks_from_spectrum() adapter. `validation_scope=["rca"]` tells the eval
    runner this case has no velocity/zone/trend ground truth to check.
    """
    path = Path(path)
    signal, fs, rpm = load_cwru_mat(path, cwru_cfg)
    expected_info = filename_to_expected(path.stem, cwru_cfg)

    band = tuple(cwru_cfg["envelope_band_hz"])
    region = tuple(cwru_cfg["envelope_region_hz"])
    envelope = envelope_spectrum(signal, fs, band_hz=band, region_hz=region)
    # Session B (B1): emit BOTH — envelope for the bearing detectors, raw for the
    # 1x-family detectors (which must never read the envelope's artifact 1x line).
    raw = raw_spectrum(signal, fs)

    rms_g = float(np.sqrt(np.mean(np.square(signal))))

    machine = MachineMeta(
        mac=f"CWRU-{path.stem}",
        name=f"CWRU {path.stem}",
        active=True,
        type="motor",
        iso_group="2",  # token metadata only — validation_scope excludes zone/severity
        iso_support="rigid",
        bearing=bearing_spec,
        axial_axis="x",
    )
    sensor_data = SensorData(rpm=rpm, y_rms_ACC_G=rms_g)

    primary_fault = expected_info["primary_fault"]
    expected = CaseExpected(
        faults=[primary_fault] if primary_fault else [],
        fault_type=expected_info["fault_type"],
        diameter_in=expected_info["diameter_in"],
    )

    return Case(
        name=path.stem,
        machine=machine,
        sensor_data=sensor_data,
        spectra={"y": envelope},
        raw_spectra={"y": raw},
        source="cwru",
        validation_scope=["rca"],
        expected=expected,
    )
