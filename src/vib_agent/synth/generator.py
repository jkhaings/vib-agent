"""Deterministic synthetic vibration data with seeded faults.

Amendment B1 (packet-first): the synthetic spectrum is generated first, and
the NCD-shaped packet's ranked peak triplets are *derived* from it — the
same top-3-by-amplitude selection rule peaks_from_spectrum() uses — so both
PeakSet adapters see equivalent input by construction. This also mirrors
what the real NCD sensor firmware does: it reports only its own onboard
peak-picking result, not a raw spectrum.

Every fault case's `expected` block (zone, faults, gate verdict) is derived
by actually running the pipeline once at generation time — quality_gate →
iso_classify → bearing_rca — rather than hand-authored, so a case's
"expected" outcome can never drift from what its own data actually produces.

All randomness goes through numpy.random.default_rng(seed): same seed,
identical output.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import numpy as np
from scipy.signal import find_peaks

from vib_agent.models import (
    Axis,
    BearingSpec,
    BeltSpec,
    Case,
    CaseExpected,
    HistoryPoint,
    LifecycleState,
    MachineMeta,
    SensorData,
    Spectrum,
)
from vib_agent.pipeline import analysis_to_expected, run_analysis

_AXES: tuple[Axis, ...] = ("x", "y", "z")


# ─────────────────────────────────────────────────────────────────────────
# Machine templates
# ─────────────────────────────────────────────────────────────────────────


def _pump() -> MachineMeta:
    return MachineMeta(
        mac="SYN-PUMP-01", name="Synthetic Pump 01", active=True, type="pump",
        iso_group="2", iso_support="rigid",
    )


def _pump_with_blades(blades: int) -> MachineMeta:
    return MachineMeta(
        mac="SYN-PUMP-02", name="Synthetic Pump 02", active=True, type="pump",
        iso_group="2", iso_support="rigid", blades=blades,
    )


def _comp_with_bearing() -> MachineMeta:
    return MachineMeta(
        mac="SYN-COMP-01", name="Synthetic Compressor 01", active=True, type="compressor",
        iso_group="2", iso_support="rigid",
        bearing=BearingSpec(n_balls=9, ball_dia_mm=9.53, pitch_dia_mm=46.0, contact_angle_deg=0.0, model="6206"),
    )


def _motor() -> MachineMeta:
    return MachineMeta(
        mac="SYN-MOTOR-01", name="Synthetic Motor 01", active=True, type="motor",
        iso_group="2", iso_support="rigid",
    )


def _motor_with_belt(freq_hz: float) -> MachineMeta:
    return MachineMeta(
        mac="SYN-MOTOR-02", name="Synthetic Motor 02", active=True, type="motor",
        iso_group="2", iso_support="rigid", belt=BeltSpec(freq_hz=freq_hz),
    )


def _fan_uncoupled() -> MachineMeta:
    return MachineMeta(
        mac="SYN-FAN-01", name="Synthetic Fan 01", active=True, type="fan",
        iso_group="2", iso_support="rigid", coupled=False,
    )


# ─────────────────────────────────────────────────────────────────────────
# Spectrum + packet primitives
# ─────────────────────────────────────────────────────────────────────────


def _bin_index(freq_hz: float, fmax: float, lines: int) -> int:
    idx = round(freq_hz / fmax * lines)
    return max(0, min(lines - 1, idx))


def _add_peak(amp: list[float], target_freq: float, target_amp: float, fmax: float, lines: int) -> None:
    """Adds a narrow peak (monotonically decaying over ±2 bins) so a single
    clean local maximum lands at target_freq — no spurious secondary peaks.
    """
    idx = _bin_index(target_freq, fmax, lines)
    for offset, scale in ((-2, 0.15), (-1, 0.35), (0, 1.0), (1, 0.35), (2, 0.15)):
        i = idx + offset
        if 0 <= i < lines:
            amp[i] = max(amp[i], target_amp * scale)


def make_spectrum(
    peaks_by_axis: dict[Axis, list[tuple[float, float]]],
    *,
    fmax: float = 500.0,
    lines: int = 2000,
    noise_level: float = 0.001,
    seed: int,
) -> dict[Axis, Spectrum]:
    """Per-axis synthetic FFT amplitude spectrum: a low broadband noise
    floor plus narrow peaks at the given (freq_hz, amplitude) pairs.
    """
    rng = np.random.default_rng(seed)
    freq_hz = [i * fmax / lines for i in range(lines)]
    spectra: dict[Axis, Spectrum] = {}
    for axis in _AXES:
        amp = (rng.uniform(0.3, 1.0, lines) * noise_level).tolist()
        for target_freq, target_amp in peaks_by_axis.get(axis, []):
            _add_peak(amp, target_freq, target_amp, fmax, lines)
        spectra[axis] = Spectrum(freq_hz=freq_hz, amplitude=amp, fmax_hz=fmax)
    return spectra


def _as_velocity(spectra: dict[Axis, Spectrum]) -> dict[Axis, Spectrum]:
    """The raw/velocity twin of a synthetic (envelope-kind) spectrum, for B1's
    kind routing. Synthetic tones are idealized — no demodulation artifact — so
    the 1x-family detectors read the SAME tones the bearing detectors do; only the
    `kind` label differs. Emitting both keeps every generator diagnosis byte-
    identical to pre-B1 while satisfying "1x-family never reads envelope"."""
    return {ax: s.model_copy(update={"kind": "velocity"}) for ax, s in spectra.items()}


def _flat_spectrum(fmax: float = 500.0, lines: int = 2000, level: float = 0.001) -> dict[Axis, Spectrum]:
    """A perfectly flat (zero-variance) spectrum on every axis — for the
    quality gate's spectrum_non_flat violation case. No noise, no peaks:
    a genuinely dead/disconnected sensor signal.
    """
    freq_hz = [i * fmax / lines for i in range(lines)]
    amp = [level] * lines
    return {axis: Spectrum(freq_hz=list(freq_hz), amplitude=list(amp), fmax_hz=fmax) for axis in _AXES}


def _clipped_spectrum(
    peaks_by_axis: dict[Axis, list[tuple[float, float]]],
    clip_freq_hz: float,
    *,
    fmax: float = 500.0,
    lines: int = 2000,
    noise_level: float = 0.001,
    plateau_width: int = 6,
    seed: int,
) -> dict[Axis, Spectrum]:
    """Like make_spectrum(), but every axis additionally has a flat-topped
    plateau of `plateau_width` bins pinned at the same amplitude around
    clip_freq_hz — simulating ADC clipping — for the quality gate's
    clipping violation case.
    """
    spectra = make_spectrum(peaks_by_axis, fmax=fmax, lines=lines, noise_level=noise_level, seed=seed)
    idx = _bin_index(clip_freq_hz, fmax, lines)
    clip_level = 0.5
    for axis in _AXES:
        amp = spectra[axis].amplitude
        for offset in range(plateau_width):
            i = idx + offset
            if 0 <= i < lines:
                amp[i] = clip_level
        spectra[axis] = Spectrum(freq_hz=spectra[axis].freq_hz, amplitude=amp, fmax_hz=fmax)
    return spectra


def packet_from_spectra(
    spectra: dict[Axis, Spectrum],
    velocities_mms: dict[str, float],
    rpm: float,
    *,
    temperature: float = 23.7,
    max_peaks_per_axis: int = 3,
) -> SensorData:
    """Derive an NCD-shaped packet from a synthetic spectrum. The ranked
    peak triplets are the top-3 local maxima per axis — the identical
    selection rule bearing_rca.peaks_from_spectrum() uses — so both PeakSet
    adapters see equivalent input by construction (Amendment B1).

    Acceleration is a simple velocity-proportional estimate (not a physical
    simulation): calibrated so ordinary running velocities clear
    min_running_g and near-zero (machine-off) velocities don't.
    """
    shaft_hz = rpm / 60.0 if rpm else 0.0
    fields: dict[str, Any] = {"rpm": rpm, "temperature": temperature, "mode": 0, "msg_type": "regular"}

    for axis in _AXES:
        v = velocities_mms.get(axis, 0.0)
        fields[f"{axis}_velocity_mm_sec"] = v
        rms_acc_g = v * 0.08
        fields[f"{axis}_rms_ACC_G"] = rms_acc_g
        fields[f"{axis}_max_ACC_G"] = rms_acc_g * 2.2
        fields[f"{axis}_displacement_mm"] = v / (2 * math.pi * shaft_hz) if shaft_hz > 0 else 0.0

        spectrum = spectra.get(axis)
        if spectrum is not None and spectrum.amplitude:
            indices, _ = find_peaks(spectrum.amplitude)
            ranked = sorted(indices, key=lambda i: spectrum.amplitude[i], reverse=True)[:max_peaks_per_axis]
            suffixes = ("peak_one_Hz", "peak_two_Hz", "peak_three_Hz")
            for rank, idx in enumerate(ranked):
                fields[f"{axis}_{suffixes[rank]}"] = spectrum.freq_hz[idx]

    return SensorData(**fields)


# ─────────────────────────────────────────────────────────────────────────
# Expected-outcome derivation (self-verifying: run the real pipeline once)
# ─────────────────────────────────────────────────────────────────────────


def _derive_expected(
    sensor_data: SensorData,
    machine: MachineMeta,
    iso_table: dict[str, dict[str, float]],
    thresholds: dict[str, Any],
    *,
    lifecycle: LifecycleState | None = None,
    battery_percent: float | None = None,
    spectra: dict[Axis, Spectrum] | None = None,
    raw_spectra: dict[Axis, Spectrum] | None = None,
) -> CaseExpected:
    """Project a case's expected outcome by running the one canonical pipeline
    (pipeline.run_analysis) and reading off its verdict — no second
    orchestration path. Intent is still independently pinned by the B2/B5
    generator tests, so 'expected' is not blindly trusting the pipeline.
    """
    case = Case(
        name="_derive_expected",
        machine=machine,
        sensor_data=sensor_data,
        spectra=spectra,
        raw_spectra=raw_spectra,
        lifecycle=lifecycle,
        battery_percent=battery_percent,
    )
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds)
    return analysis_to_expected(result)


# ─────────────────────────────────────────────────────────────────────────
# Fault menu (Amendment B2 — full detector menu, axis-aware)
#
# Where a fault has a reference T-fixture twin, its peak frequencies and
# velocities are reused exactly (proven collision-safe for that rpm by the
# reference's own test harness) — see tests/fixtures.py for the originals.
# Amendment B5 cross-checks each twin pair.
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class _FaultRecipe:
    machine: MachineMeta
    rpm: float
    peaks_by_axis: dict[Axis, list[tuple[float, float]]]
    velocities_mms: dict[str, float]
    primary_fault: str | None  # None for the healthy case
    twin: str | None = None  # reference test name, if one exists


_FAULT_RECIPES: dict[str, _FaultRecipe] = {
    "healthy": _FaultRecipe(
        machine=_pump(), rpm=1800.0,
        peaks_by_axis={
            "x": [(41.2, 0.05), (53.7, 0.03), (84.1, 0.02)],
            "y": [(49.5, 0.06), (81.3, 0.02), (264.4, 0.01)],
            "z": [(52.8, 0.05), (184.6, 0.02), (234.2, 0.01)],
        },
        velocities_mms={"x": 0.25, "y": 0.3, "z": 0.22},
        primary_fault=None, twin="T01",
    ),
    "imbalance": _FaultRecipe(
        machine=_pump(), rpm=1740.0,
        peaks_by_axis={
            "x": [(41.2, 0.03), (53.7, 0.02), (84.1, 0.015)],
            "y": [(29.0, 0.12), (81.3, 0.02), (264.4, 0.01)],
            "z": [(29.0, 0.13), (184.6, 0.02), (234.2, 0.01)],
        },
        velocities_mms={"x": 0.4, "y": 6.5, "z": 6.8},
        primary_fault="imbalance", twin="T07",
    ),
    "angular_misalignment": _FaultRecipe(
        machine=_motor(), rpm=1800.0,
        peaks_by_axis={
            "x": [(30.0, 0.13), (41.2, 0.03), (53.7, 0.02)],
            "y": [(30.0, 0.05), (81.3, 0.02), (264.4, 0.01)],
            "z": [(30.0, 0.045), (184.6, 0.02), (234.2, 0.01)],
        },
        velocities_mms={"x": 6.5, "y": 2.5, "z": 2.3},
        primary_fault="angular_misalignment", twin="T09",
    ),
    "parallel_misalignment": _FaultRecipe(
        machine=_pump(), rpm=1800.0,
        peaks_by_axis={
            "x": [(30.0, 0.02), (41.2, 0.015), (53.7, 0.01)],
            "y": [(60.0, 0.21), (30.0, 0.05), (264.4, 0.01)],
            "z": [(60.0, 0.22), (30.0, 0.05), (234.2, 0.01)],
        },
        velocities_mms={"x": 0.5, "y": 5.5, "z": 5.8},
        primary_fault="parallel_misalignment", twin="T10",
    ),
    "bent_shaft": _FaultRecipe(
        machine=_fan_uncoupled(), rpm=1200.0,
        peaks_by_axis={
            "x": [(20.0, 0.083), (17.3, 0.032), (56.2, 0.028)],
            "y": [(20.0, 0.032), (24.7, 0.02), (84.1, 0.01)],
            "z": [(20.0, 0.028), (27.5, 0.018), (64.2, 0.01)],
        },
        velocities_mms={"x": 6.5, "y": 2.5, "z": 2.2},
        primary_fault="bent_shaft", twin="T08",
    ),
    "severe_misalignment": _FaultRecipe(
        machine=_comp_with_bearing(), rpm=1800.0,
        peaks_by_axis={
            "x": [(30.0, 0.11), (60.0, 0.11), (90.0, 0.03)],
            "y": [(30.0, 0.10), (60.0, 0.10), (120.0, 0.03)],
            "z": [(30.0, 0.09), (60.0, 0.09), (150.0, 0.03)],
        },
        velocities_mms={"x": 5.8, "y": 5.2, "z": 4.9},
        primary_fault="severe_misalignment", twin="T13",
    ),
    "looseness": _FaultRecipe(
        machine=_pump(), rpm=1800.0,
        peaks_by_axis={
            "x": [(15.0, 0.034), (30.0, 0.03), (45.0, 0.02)],
            "y": [(30.0, 0.073), (60.0, 0.03), (90.0, 0.01)],
            "z": [(15.0, 0.031), (45.0, 0.02), (60.0, 0.015)],
        },
        velocities_mms={"x": 3.5, "y": 3.8, "z": 3.2},
        primary_fault="mechanical_looseness", twin="T11",
    ),
    "bpfo": _FaultRecipe(
        machine=_comp_with_bearing(), rpm=1800.0,
        peaks_by_axis={
            "x": [(41.2, 0.013), (53.7, 0.01), (84.1, 0.005)],
            "y": [(107.16, 0.36), (214.32, 0.14), (264.4, 0.01)],
            "z": [(107.16, 0.33), (214.32, 0.13), (234.2, 0.01)],
        },
        velocities_mms={"x": 0.5, "y": 5.2, "z": 4.8},
        primary_fault="bearing_outer_race", twin="T12",
    ),
    "bpfi": _FaultRecipe(
        # BPFI = (N/2)(1+ratio)*shaft = 4.5*1.2072*30 ~= 163.0 Hz for a 6206
        # at 1800 rpm. Sidebands at BPFI +/- shaft (133, 193 Hz) are seeded
        # at low amplitude — supporting evidence, not part of the packet's
        # top-3 triplet, but present in the raw spectrum for
        # enrich_with_sidebands() to find.
        machine=_comp_with_bearing(), rpm=1800.0,
        peaks_by_axis={
            "x": [(41.2, 0.012), (53.7, 0.008), (84.1, 0.005)],
            "y": [(163.0, 0.30), (326.0, 0.10), (133.0, 0.02), (193.0, 0.02)],
            "z": [(163.0, 0.28), (326.0, 0.09), (133.0, 0.02), (193.0, 0.02)],
        },
        velocities_mms={"x": 0.5, "y": 5.0, "z": 4.6},
        primary_fault="bearing_inner_race", twin=None,
    ),
    "belt_fault": _FaultRecipe(
        # belt_freq=52.0 Hz: >=5% clear of every shaft harmonic (15/30/45/
        # 60/75/90/120/150) and its 2x (104.0 Hz) likewise clear.
        machine=_motor_with_belt(52.0), rpm=1800.0,
        peaks_by_axis={
            "x": [(41.2, 0.02), (53.7, 0.015), (84.1, 0.01)],
            "y": [(52.0, 0.09), (104.0, 0.03), (264.4, 0.01)],
            "z": [(52.0, 0.08), (104.0, 0.03), (234.2, 0.01)],
        },
        velocities_mms={"x": 0.6, "y": 3.3, "z": 3.4},
        primary_fault="belt_fault", twin=None,
    ),
    "blade_pass": _FaultRecipe(
        # 6 blades x 30 Hz shaft = 180 Hz blade-pass frequency, >=5% clear
        # of the nearest shaft harmonic (150 Hz, 5x). z's ambient filler
        # uses 220 Hz instead of the usual 184.6 Hz, which would sit within
        # tolerance of 180 Hz and collide.
        machine=_pump_with_blades(6), rpm=1800.0,
        peaks_by_axis={
            "x": [(41.2, 0.02), (53.7, 0.015), (84.1, 0.01)],
            "y": [(180.0, 0.07), (81.3, 0.02), (264.4, 0.01)],
            "z": [(180.0, 0.065), (220.0, 0.02), (234.2, 0.01)],
        },
        velocities_mms={"x": 0.7, "y": 3.5, "z": 3.6},
        primary_fault="elevated_blade_pass", twin=None,
    ),
    "resonance": _FaultRecipe(
        # 99 Hz (3.3x shaft) on x, rank 1, >=5% clear of every shaft
        # harmonic and (no bearing/belt/blades on this machine) nothing
        # else known-expected — an unexplained loud peak.
        machine=_motor(), rpm=1800.0,
        peaks_by_axis={
            "x": [(99.0, 0.09), (41.2, 0.02), (53.7, 0.015)],
            "y": [(49.5, 0.02), (81.3, 0.015), (264.4, 0.01)],
            "z": [(52.8, 0.02), (184.6, 0.015), (234.2, 0.01)],
        },
        velocities_mms={"x": 0.7, "y": 3.4, "z": 3.3},
        primary_fault="possible_resonance", twin=None,
    ),
}

FAULT_MENU: tuple[str, ...] = tuple(_FAULT_RECIPES.keys())

# Amendment B5: synthetic case name -> reference test name, for every fault
# with a fixture twin.
FIXTURE_TWINS: dict[str, str] = {
    name: recipe.twin for name, recipe in _FAULT_RECIPES.items() if recipe.twin is not None
}


def _build_fault_case(
    name: str,
    recipe: _FaultRecipe,
    iso_table: dict[str, dict[str, float]],
    thresholds: dict[str, Any],
    *,
    seed: int,
    fmax: float = 500.0,
    lines: int = 2000,
) -> Case:
    # kind=envelope (bearing). `fmax`/`lines` default to make_spectrum's own
    # defaults, so every existing seeded case is byte-identical (Session REPORT-2).
    spectra = make_spectrum(recipe.peaks_by_axis, fmax=fmax, lines=lines, seed=seed)
    raw_spectra = _as_velocity(spectra)  # kind=velocity (1x-family), same tones
    sensor_data = packet_from_spectra(spectra, recipe.velocities_mms, recipe.rpm)
    expected = _derive_expected(
        sensor_data, recipe.machine, iso_table, thresholds, spectra=spectra, raw_spectra=raw_spectra
    )
    return Case(
        name=name, machine=recipe.machine, sensor_data=sensor_data,
        spectra=spectra, raw_spectra=raw_spectra, expected=expected,
    )


# ─────────────────────────────────────────────────────────────────────────
# Gate-violation menu (Amendment B3)
# ─────────────────────────────────────────────────────────────────────────

GATE_VIOLATION_MENU: tuple[str, ...] = (
    "machine_off", "startup_transient", "rpm_off_nominal",
    "low_battery", "flat_spectrum", "clipped_spectrum",
)


def _build_machine_off(iso_table: dict, thresholds: dict, *, seed: int) -> Case:
    machine = _pump()
    peaks_by_axis = {
        "x": [(12.5, 0.0002), (47.3, 0.0001), (88.1, 0.0001)],
        "y": [(23.7, 0.0002), (91.4, 0.0001), (156.2, 0.0001)],
        "z": [(8.9, 0.0002), (64.2, 0.0001), (201.5, 0.0001)],
    }
    velocities = {"x": 0.003, "y": 0.005, "z": 0.002}
    spectra = make_spectrum(peaks_by_axis, seed=seed, noise_level=0.0001)
    sensor_data = packet_from_spectra(spectra, velocities, rpm=15.0)
    expected = _derive_expected(sensor_data, machine, iso_table, thresholds, spectra=spectra)
    return Case(name="machine_off", machine=machine, sensor_data=sensor_data, spectra=spectra, expected=expected)


def _build_startup_transient(iso_table: dict, thresholds: dict, *, seed: int) -> Case:
    healthy = _FAULT_RECIPES["healthy"]
    spectra = make_spectrum(healthy.peaks_by_axis, seed=seed)
    sensor_data = packet_from_spectra(spectra, healthy.velocities_mms, healthy.rpm)
    lifecycle = LifecycleState(startup_counter=1)
    expected = _derive_expected(
        sensor_data, healthy.machine, iso_table, thresholds,
        lifecycle=lifecycle, spectra=spectra,
    )
    return Case(
        name="startup_transient", machine=healthy.machine, sensor_data=sensor_data,
        spectra=spectra, lifecycle=lifecycle, expected=expected,
    )


def _build_rpm_off_nominal(iso_table: dict, thresholds: dict, *, seed: int) -> Case:
    healthy = _FAULT_RECIPES["healthy"]
    machine = healthy.machine.model_copy(update={"rpm_nominal": 3600.0, "rpm_tolerance_pct": 5.0})
    spectra = make_spectrum(healthy.peaks_by_axis, seed=seed)
    sensor_data = packet_from_spectra(spectra, healthy.velocities_mms, healthy.rpm)
    expected = _derive_expected(sensor_data, machine, iso_table, thresholds, spectra=spectra)
    return Case(name="rpm_off_nominal", machine=machine, sensor_data=sensor_data, spectra=spectra, expected=expected)


def _build_low_battery(iso_table: dict, thresholds: dict, *, seed: int) -> Case:
    healthy = _FAULT_RECIPES["healthy"]
    machine = healthy.machine.model_copy(update={"min_battery_pct": 20.0})
    spectra = make_spectrum(healthy.peaks_by_axis, seed=seed)
    sensor_data = packet_from_spectra(spectra, healthy.velocities_mms, healthy.rpm)
    expected = _derive_expected(
        sensor_data, machine, iso_table, thresholds,
        battery_percent=5.0, spectra=spectra,
    )
    return Case(
        name="low_battery", machine=machine, sensor_data=sensor_data, spectra=spectra,
        battery_percent=5.0, expected=expected,
    )


def _build_flat_spectrum(iso_table: dict, thresholds: dict, *, seed: int) -> Case:
    healthy = _FAULT_RECIPES["healthy"]
    spectra = _flat_spectrum()
    sensor_data = packet_from_spectra(spectra, healthy.velocities_mms, healthy.rpm)
    expected = _derive_expected(sensor_data, healthy.machine, iso_table, thresholds, spectra=spectra)
    return Case(
        name="flat_spectrum", machine=healthy.machine, sensor_data=sensor_data,
        spectra=spectra, expected=expected,
    )


def _build_clipped_spectrum(iso_table: dict, thresholds: dict, *, seed: int) -> Case:
    healthy = _FAULT_RECIPES["healthy"]
    spectra = _clipped_spectrum(healthy.peaks_by_axis, clip_freq_hz=49.5, seed=seed)
    sensor_data = packet_from_spectra(spectra, healthy.velocities_mms, healthy.rpm)
    expected = _derive_expected(sensor_data, healthy.machine, iso_table, thresholds, spectra=spectra)
    return Case(
        name="clipped_spectrum", machine=healthy.machine, sensor_data=sensor_data,
        spectra=spectra, expected=expected,
    )


_GATE_VIOLATION_BUILDERS: dict[str, Any] = {
    "machine_off": _build_machine_off,
    "startup_transient": _build_startup_transient,
    "rpm_off_nominal": _build_rpm_off_nominal,
    "low_battery": _build_low_battery,
    "flat_spectrum": _build_flat_spectrum,
    "clipped_spectrum": _build_clipped_spectrum,
}


# ─────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────


def make_case(
    name: str,
    *,
    iso_table: dict[str, dict[str, float]],
    thresholds: dict[str, Any],
    seed: int,
    fmax: float = 500.0,
    lines: int = 2000,
) -> Case:
    """Build one named case (fault or gate-violation) from FAULT_MENU /
    GATE_VIOLATION_MENU. Deterministic: identical seed -> identical Case.

    Session REPORT-2: `fmax` / `lines` set the spectrum span and line count of a
    FAULT recipe (defaults are make_spectrum's, so the seeded cases every test,
    golden and eval case reads are unchanged). A CAT analyst reviewing the
    500 Hz outreach sample asked for an Fmax that reaches the bearing harmonics;
    `scripts/make_sample_csv.py` builds the same recipe at 2 kHz through this
    hook. The gate-violation builders keep their own spans — passing a
    non-default here for one of those is refused rather than silently ignored.
    """
    if name in _FAULT_RECIPES:
        return _build_fault_case(
            name, _FAULT_RECIPES[name], iso_table, thresholds, seed=seed, fmax=fmax, lines=lines
        )
    if name in _GATE_VIOLATION_BUILDERS:
        if fmax != 500.0 or lines != 2000:
            raise ValueError(
                f"fmax/lines apply to fault recipes only; {name!r} is a gate-violation case"
            )
        return _GATE_VIOLATION_BUILDERS[name](iso_table, thresholds, seed=seed)
    raise ValueError(f"unknown case name: {name!r} (see FAULT_MENU / GATE_VIOLATION_MENU)")


# ─────────────────────────────────────────────────────────────────────────
# History (Amendment B4 — two cadences)
# ─────────────────────────────────────────────────────────────────────────


def make_history(
    n_points: int,
    start_value: float,
    end_value: float,
    *,
    noise_pct: float = 0.0,
    cadence: Literal["daily", "monthly"] = "daily",
    seed: int,
    end_ts: datetime | None = None,
) -> list[HistoryPoint]:
    """Real-timestamp history. `cadence="daily"` reproduces
    reference/flows.json's 30-day backfill formula (Flow T — Generate 30d
    flat/ramp history) exactly when noise_pct=0. `cadence="monthly"` spaces
    points ~30.44 days apart, for sparse route-collected data.
    """
    rng = np.random.default_rng(seed)
    end_ts = end_ts or datetime.now(timezone.utc)
    step = timedelta(days=1) if cadence == "daily" else timedelta(days=30.44)

    points: list[HistoryPoint] = []
    for i in range(n_points):
        v = (
            start_value + (end_value - start_value) * (i / (n_points - 1))
            if n_points > 1
            else start_value
        )
        if noise_pct > 0:
            v += v * noise_pct * (rng.random() - 0.5)
        ts = end_ts - step * (n_points - i)
        points.append(HistoryPoint(ts=ts, value=v))
    return points
