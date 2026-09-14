"""Layer 5: bearing fault frequencies + root-cause analysis.

Faithful port of reference/flows.json 'Compute RCA — fault diagnosis':
seven independently-testable detectors (bearing faults, mechanical
looseness, the misalignment family, imbalance, belt fault, elevated blade
pass, possible resonance), run in the reference's exact priority order with
its exact detector-interaction rules (imbalance suppressed once a bearing,
looseness, or misalignment match exists; misalignment downgraded to low
confidence — with an appended note — when looseness also fires).

Amendment A3 — architectural boundary: every detector reads a PeakSet and
nothing else. peaks_from_ncd() and peaks_from_spectrum() are the two
adapters that normalize a raw source (NCD packet triplets; a synth-generated
FFT spectrum) into that shape. A future third-party source (e.g. a
route-collector export) adds a third adapter; detectors never change.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from scipy.signal import find_peaks

from vib_agent.models import (
    Axis,
    BearingFreqs,
    BearingSpec,
    Confidence,
    ConfidenceFactor,
    DifferentialCandidate,
    FaultMatch,
    IsoSeverityOrUnrated,
    MachineMeta,
    MachineThresholds,
    Peak,
    PeakSet,
    RcaResult,
    SensorData,
    Spectrum,
)

_AXES: tuple[Axis, ...] = ("x", "y", "z")
_HARMONIC_MULTIPLES: dict[str, float] = {
    "0.5x": 0.5,
    "1x": 1.0,
    "1.5x": 1.5,
    "2x": 2.0,
    "2.5x": 2.5,
    "3x": 3.0,
    "4x": 4.0,
    "5x": 5.0,
}


# ─────────────────────────────────────────────────────────────────────────
# Bearing fault frequencies
# ─────────────────────────────────────────────────────────────────────────


def bearing_frequencies(bearing: BearingSpec, shaft_hz: float) -> BearingFreqs:
    """BPFO/BPFI/BSF/FTF from bearing geometry and shaft speed."""
    ratio = (bearing.ball_dia_mm / bearing.pitch_dia_mm) * math.cos(
        math.radians(bearing.contact_angle_deg)
    )
    n = bearing.n_balls
    return BearingFreqs(
        BPFO=(n / 2) * (1 - ratio) * shaft_hz,
        BPFI=(n / 2) * (1 + ratio) * shaft_hz,
        BSF=(bearing.pitch_dia_mm / (2 * bearing.ball_dia_mm)) * (1 - ratio * ratio) * shaft_hz,
        FTF=0.5 * (1 - ratio) * shaft_hz,
    )


# ─────────────────────────────────────────────────────────────────────────
# Amendment A3 — PeakSet adapters (the only functions allowed to see a raw
# source format; everything below this section speaks PeakSet only)
# ─────────────────────────────────────────────────────────────────────────


def peaks_from_ncd(sensor_data: SensorData) -> PeakSet:
    """Adapter: NCD Gen4 packet's 3-peaks-per-axis triplets -> PeakSet.

    This is the reference pipeline's native source format. The 3-per-axis
    cap here is NOT a tunable — it's a hardware constraint of the NCD
    sensor's onboard firmware, which only ever reports 3 peaks per axis.
    There is nothing to configure.
    """
    rpm = sensor_data.rpm or 0.0
    peaks: list[Peak] = []
    for axis in _AXES:
        for rank, suffix in ((1, "peak_one_Hz"), (2, "peak_two_Hz"), (3, "peak_three_Hz")):
            freq = getattr(sensor_data, f"{axis}_{suffix}")
            if freq is not None and math.isfinite(freq) and freq > 0:
                peaks.append(Peak(axis=axis, freq=freq, rank=rank))

    velocities = {
        "x": sensor_data.x_velocity_mm_sec or 0.0,
        "y": sensor_data.y_velocity_mm_sec or 0.0,
        "z": sensor_data.z_velocity_mm_sec or 0.0,
    }
    return PeakSet(
        source="ncd_triplet",
        kind="ncd_peaks",
        shaft_freq_hz=rpm / 60.0,
        rpm=rpm,
        peaks=peaks,
        velocities_mms=velocities,
        # Session PDMFIX: `velocities` above coerces an unreported axis to 0.0,
        # which reads downstream as "measured and silent". Record what was
        # actually reported so a detector conjunct cannot be satisfied by an
        # absent channel. An NCD packet normally carries all three.
        measured_axes=measured_axes_from_sensor_data(sensor_data),
    )


def measured_axes_from_sensor_data(sensor_data: SensorData) -> list[Axis]:
    """The axes that ACTUALLY reported a velocity. `None` means the channel was
    never measured; `0.0` means it was measured and read as silent. The two are
    different evidence and must not collapse into each other."""
    return [ax for ax in _AXES if getattr(sensor_data, f"{ax}_velocity_mm_sec") is not None]


def peaks_from_spectrum(
    spectra: dict[Axis, Spectrum],
    rpm: float,
    velocities_mms: dict[str, float],
    *,
    max_peaks_per_axis: int = 3,
    prominence: float | None = None,
    measured_axes: list[Axis] | None = None,
) -> PeakSet:
    """Adapter: per-axis FFT amplitude spectra -> PeakSet, via scipy.find_peaks.

    Additive (not in reference) — the extra evidence source available when a
    full spectrum was captured (e.g. synthetic data, or a real envelope
    spectrum) instead of only the NCD sensor's onboard peak triplets. Only
    axes present in `spectra` are scanned; ranks are assigned by descending
    amplitude within each axis, matching the "rank 1 = loudest" convention
    the reference relies on.

    Unlike peaks_from_ncd's 3-per-axis cap (a hardware constraint of that
    source), `max_peaks_per_axis` and `prominence` here are genuine tunables
    — a self-computed spectrum has no such hardware limit, and the right
    candidate-window width is a property of the analysis, calibrated per
    profile (see config/thresholds.json profiles.*.rca). Defaults (3, None)
    preserve the original behavior exactly when neither is specified.
    `prominence`, when given, is applied to scipy.find_peaks BEFORE ranking,
    so genuinely prominent-but-not-loudest peaks aren't crowded out of a
    small top-N by louder, low-prominence spectral content.

    Session PDMFIX — `measured_axes`: which axes the reading actually covered.
    Defaults to the axes present in `spectra`, since a captured spectrum implies
    a measured channel; the pipeline overrides it with the SensorData velocity
    fields, which are what `axial_radial_ratio` is computed from.
    """
    peaks: list[Peak] = []
    axis_mean_amp: dict[str, float] = {}
    for axis, spectrum in spectra.items():
        if not spectrum.amplitude:
            continue
        amp = spectrum.amplitude
        freq = spectrum.freq_hz
        # Session B (B4): the amplitude-floor reference for this axis — the mean
        # of the full spectrum (the broadband floor a genuine tone must stand out
        # from). Recorded per axis before ranking; consumed by the floor check.
        axis_mean_amp[axis] = sum(amp) / len(amp)
        if prominence is not None:
            indices, _ = find_peaks(amp, prominence=prominence)
        else:
            indices, _ = find_peaks(amp)
        if len(indices) == 0:
            continue
        ranked = sorted(indices, key=lambda i: amp[i], reverse=True)[:max_peaks_per_axis]
        for rank, idx in enumerate(ranked, start=1):
            peaks.append(Peak(axis=axis, freq=freq[idx], rank=rank, amplitude=amp[idx]))

    # Informational provenance only: the kind of the spectra these peaks came from
    # (a case's spectra dict is uniform kind — all envelope, or all raw). No
    # detector branches on this; routing is done by which PeakSet builds a context.
    spectrum_kind = next((s.kind for s in spectra.values()), "envelope")
    return PeakSet(
        source="spectrum",
        kind=spectrum_kind,
        shaft_freq_hz=rpm / 60.0,
        rpm=rpm,
        peaks=peaks,
        velocities_mms=dict(velocities_mms),
        axis_mean_amp=axis_mean_amp or None,
        measured_axes=(
            measured_axes if measured_axes is not None else [ax for ax in _AXES if ax in spectra]
        ),
    )


# ─────────────────────────────────────────────────────────────────────────
# Shared matching helpers (operate on PeakSet.peaks only)
# ─────────────────────────────────────────────────────────────────────────


def _within(actual: float, expected: float, tolerance: float) -> bool:
    if expected <= 0:
        return False
    return abs(actual - expected) / expected <= tolerance


def _find_peaks_matching(
    peaks: list[Peak],
    expected_freq: float,
    tolerance: float,
    axis_filter: tuple[Axis, ...] | None = None,
) -> list[Peak]:
    return [
        p
        for p in peaks
        if (axis_filter is None or p.axis in axis_filter) and _within(p.freq, expected_freq, tolerance)
    ]


def _top_peak_is(peaks: list[Peak], axis: Axis, expected_freq: float, tolerance: float) -> bool:
    rank1 = next((p for p in peaks if p.axis == axis and p.rank == 1), None)
    return rank1 is not None and _within(rank1.freq, expected_freq, tolerance)


# ─────────────────────────────────────────────────────────────────────────
# Confidence rubric (Phase 3) — deterministic, config-driven.
# Detectors emit signed ConfidenceFactors; their deltas sum to a score,
# banded into high/medium/low. Weights and bands live in
# config/thresholds.json `confidence`; no ordinal is hard-coded.
# ─────────────────────────────────────────────────────────────────────────


def _factor(conf_cfg: dict[str, Any], key: str, detail: str, *, count: float = 1.0) -> ConfidenceFactor:
    return ConfidenceFactor(name=key, detail=detail, delta=conf_cfg["weights"][key] * count)


def _score_confidence(factors: list[ConfidenceFactor], conf_cfg: dict[str, Any]) -> Confidence:
    total = sum(f.delta for f in factors)
    bands = conf_cfg["bands"]
    if total >= bands["high"]:
        return "high"
    if total >= bands["medium"]:
        return "medium"
    return "low"


def _peak_delta_pct(observed: float, expected: float) -> float:
    if expected <= 0:
        return 100.0
    return abs(observed - expected) / expected * 100.0


def _clears_evidence_floor(
    peak: Peak, axis_mean_amp: dict[str, float] | None, floor_min: float | None
) -> bool:
    """Session R3-DIFF (item 1a) — is this peak loud enough to be cited as evidence?

    The bar is the SAME one report/charts.py already draws on every spectrum
    figure (`_amplitude_floor_value`): amplitude / axis-spectrum MEAN >=
    rca.floor_min. So a peak the reader can see sitting under the dashed floor
    line is one the prose is no longer allowed to name — the figure and the text
    now answer to a single constant.

    Inert (returns True) whenever the question cannot be asked: no `floor_min`
    in the profile (streaming — frozen), no per-axis spectrum mean (the NCD
    triplet path carries none), or an amplitude-less peak. Those paths are
    byte-identical to pre-R3.
    """
    if floor_min is None or axis_mean_amp is None or peak.amplitude is None:
        return True
    mean_amp = axis_mean_amp.get(peak.axis)
    if mean_amp is None or mean_amp <= 0:
        return True
    return peak.amplitude / mean_amp >= floor_min


def _amplitude_is_marginal(peak: Peak, ctx: _RcaContext) -> bool:
    """True only when we have amplitude info (spectrum source) and this peak
    sits near the axis noise floor. NCD triplets carry no amplitude, so this
    factor simply never fires for them."""
    if peak.amplitude is None:
        return False
    axis_peaks = [p.amplitude for p in ctx.peaks if p.axis == peak.axis and p.amplitude is not None]
    if not axis_peaks:
        return False
    ceiling = max(axis_peaks)
    if ceiling <= 0:
        return False
    return peak.amplitude / ceiling < ctx.confidence_cfg.get("amplitude_marginal_ratio", 0.05)


def _context_factors(conf_cfg: dict[str, Any], ctx: _RcaContext) -> list[ConfidenceFactor]:
    """Cross-cutting factors that apply to any detector: a corroborating
    rising trend raises confidence; concurrent gate warnings lower it."""
    factors: list[ConfidenceFactor] = []
    if ctx.trend_rising:
        factors.append(_factor(conf_cfg, "trend_corroborates", "Rising trend corroborates the finding"))
    if ctx.gate_warnings:
        factors.append(
            _factor(conf_cfg, "gate_warnings_present", "Data-quality warnings present on this reading")
        )
    return factors


# ─────────────────────────────────────────────────────────────────────────
# Context builder — runs once per PeakSet
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class _RcaContext:
    machine: MachineMeta
    peaks: list[Peak]
    shaft_freq: float
    rpm: float
    axial: Axis
    radial: tuple[Axis, Axis]
    v: dict[str, float]
    v_axial: float
    v_radial_max: float
    axial_radial_ratio: float | None
    tolerance: float
    confidence_cfg: dict[str, Any] = field(default_factory=dict)
    gate_warnings: bool = False
    trend_rising: bool = False
    harmonics: dict[str, list[Peak]] = field(default_factory=dict)
    has_1x_radial: bool = False
    has_1x_axial: bool = False
    has_2x_radial: bool = False
    has_2x_axial: bool = False
    has_subharmonic: bool = False
    has_higher_harmonics: bool = False
    shaft_harmonic_count: int = 0
    bearing_freqs: BearingFreqs | None = None
    axis_mean_amp: dict[str, float] | None = None  # Session B (B4): per-axis floor reference
    # Session PDMFIX. `measured_axes` None = unknown -> `axial_measured` stays
    # True, the pre-PDMFIX assumption, so any caller that does not supply it is
    # byte-identical. `iso_thresholds` is the 3-tier resolver's output
    # (resolve_thresholds: machine override > factory default > ISO table) — the
    # 1x-family severity gate answers to the MACHINE's own zone boundaries, never
    # to an ISO number written into a detector.
    measured_axes: tuple[Axis, ...] | None = None
    axial_measured: bool = True
    iso_thresholds: MachineThresholds | None = None


def _build_context(
    peak_set: PeakSet,
    machine: MachineMeta,
    tolerance: float,
    confidence_cfg: dict[str, Any],
    *,
    gate_warnings: bool = False,
    trend_rising: bool = False,
    floor_min: float | None = None,
    iso_thresholds: MachineThresholds | None = None,
) -> _RcaContext:
    axial = machine.axial_axis
    radial = tuple(a for a in _AXES if a != axial)  # type: ignore[assignment]
    assert len(radial) == 2

    v = peak_set.velocities_mms
    v_axial = v.get(axial, 0.0)
    v_radial_max = max(v.get(radial[0], 0.0), v.get(radial[1], 0.0))
    axial_radial_ratio = v_axial / v_radial_max if v_radial_max > 0 else None

    ctx = _RcaContext(
        machine=machine,
        peaks=peak_set.peaks,
        shaft_freq=peak_set.shaft_freq_hz,
        rpm=peak_set.rpm,
        axial=axial,
        radial=radial,  # type: ignore[arg-type]
        v=v,
        v_axial=v_axial,
        v_radial_max=v_radial_max,
        axial_radial_ratio=axial_radial_ratio,
        tolerance=tolerance,
        confidence_cfg=confidence_cfg,
        gate_warnings=gate_warnings,
        trend_rising=trend_rising,
        axis_mean_amp=peak_set.axis_mean_amp,
        measured_axes=tuple(peak_set.measured_axes) if peak_set.measured_axes is not None else None,
        # Unknown (None) keeps the pre-PDMFIX assumption that every axis was
        # measured, so a hand-built PeakSet behaves exactly as it did before.
        axial_measured=(peak_set.measured_axes is None or axial in peak_set.measured_axes),
        iso_thresholds=iso_thresholds,
    )

    # Session R3-DIFF (1a): the shaft-order harmonic sets are filtered to peaks
    # that clear the amplitude floor, so no sub-floor peak can reach an evidence
    # string, a confidence factor, or a harmonic COUNT. Everything derived below
    # (has_1x_*, has_2x_*, has_subharmonic, has_higher_harmonics,
    # shaft_harmonic_count) therefore inherits the filter for free.
    #
    # `ctx.peaks` is deliberately NOT filtered. The bearing detector reads it
    # directly and has its own, different treatment of the same floor
    # (`_amplitude_floor` demotes a sub-floor bearing match to the differential
    # at LOW, with the resolving capture named — it does not hide it), and peak
    # RANK must stay computed over the axis's full peak list.
    ctx.harmonics = {
        label: [
            p
            for p in _find_peaks_matching(ctx.peaks, ctx.shaft_freq * mult, tolerance)
            if _clears_evidence_floor(p, ctx.axis_mean_amp, floor_min)
        ]
        for label, mult in _HARMONIC_MULTIPLES.items()
    }

    ctx.has_1x_radial = any(p.axis in ctx.radial for p in ctx.harmonics["1x"])
    ctx.has_1x_axial = any(p.axis == ctx.axial for p in ctx.harmonics["1x"])
    ctx.has_2x_radial = any(p.axis in ctx.radial for p in ctx.harmonics["2x"])
    ctx.has_2x_axial = any(p.axis == ctx.axial for p in ctx.harmonics["2x"])
    ctx.has_subharmonic = bool(
        ctx.harmonics["0.5x"] or ctx.harmonics["1.5x"] or ctx.harmonics["2.5x"]
    )
    ctx.has_higher_harmonics = bool(ctx.harmonics["3x"] or ctx.harmonics["4x"] or ctx.harmonics["5x"])
    ctx.shaft_harmonic_count = sum(
        1 for label in ("0.5x", "1x", "1.5x", "2x", "2.5x", "3x") if ctx.harmonics[label]
    )

    if machine.bearing is not None:
        ctx.bearing_freqs = bearing_frequencies(machine.bearing, ctx.shaft_freq)

    return ctx


# ─────────────────────────────────────────────────────────────────────────
# Detector (A) — bearing faults
# ─────────────────────────────────────────────────────────────────────────


def detect_bearing_faults(ctx: _RcaContext) -> list[FaultMatch]:
    if ctx.bearing_freqs is None:
        return []

    fault_names = {
        "BPFO": ("bearing_outer_race", "outer race fault (BPFO)"),
        "BPFI": ("bearing_inner_race", "inner race fault (BPFI)"),
        "BSF": ("bearing_ball_spin", "ball/roller fault (BSF)"),
        "FTF": ("bearing_cage", "cage fault (FTF)"),
    }

    conf = ctx.confidence_cfg
    tight_pct = conf.get("peak_match_tight_pct", 1.5)

    matches: list[FaultMatch] = []
    for key, expected_freq in ctx.bearing_freqs.model_dump().items():
        hits = _find_peaks_matching(ctx.peaks, expected_freq, ctx.tolerance, ctx.radial)
        if not hits:
            continue
        best = sorted(hits, key=lambda p: p.rank)[0]
        harmonic_2x = _find_peaks_matching(ctx.peaks, expected_freq * 2, ctx.tolerance, ctx.radial)
        has_harmonic = bool(harmonic_2x)
        delta_pct = _peak_delta_pct(best.freq, expected_freq)

        factors = [_factor(conf, "base", f"{key} peak matched on a radial axis")]
        factors.append(
            _factor(conf, "axis_pattern_consistent", "Bearing tone on the expected radial axis")
        )
        if best.rank == 1:
            factors.append(_factor(conf, "rank_one", f"{key} is the dominant (rank-1) peak on {best.axis}"))
        if delta_pct <= tight_pct:
            factors.append(
                _factor(conf, "peak_match_tight", f"Frequency match within {delta_pct:.2f}% of computed {key}")
            )
        if has_harmonic:
            factors.append(_factor(conf, "harmonic_each", f"2× {key} harmonic present", count=1))
        if key == "FTF" and ctx.has_subharmonic:
            factors.append(
                _factor(conf, "amplitude_marginal", "Cage (FTF) frequency overlaps looseness subharmonic — ambiguous")
            )
        if _amplitude_is_marginal(best, ctx):
            factors.append(_factor(conf, "amplitude_marginal", f"{key} peak sits near the noise floor"))
        factors.extend(_context_factors(conf, ctx))
        confidence = _score_confidence(factors, conf)

        name, desc = fault_names[key]
        bearing_model = ctx.machine.bearing.model if ctx.machine.bearing else None
        evidence_parts = [
            f"Peak at {best.freq:.1f} Hz on {best.axis} (rank {best.rank})",
            f"matches calculated {key} ({expected_freq:.1f} Hz ±{ctx.tolerance:.0%})",
            f"for {bearing_model or 'configured bearing'}",
        ]
        if has_harmonic:
            evidence_parts.append(f"2× {key} harmonic also detected (supporting evidence)")

        matches.append(
            FaultMatch(
                fault=name,
                description=f"Bearing {desc}",
                freq_hz=round(best.freq, 2),
                expected_hz=round(expected_freq, 2),
                axis=best.axis,
                confidence=confidence,
                confidence_evidence=factors,
                evidence=". ".join(evidence_parts),
                harmonic_present=has_harmonic,
                bearing_model=bearing_model,
            )
        )
    return matches


# ─────────────────────────────────────────────────────────────────────────
# Session B (B3) — synchronous-collision guard
#
# A matched bearing peak that lands within `epsilon_sync` of an integer shaft
# order (k×, k ≤ 6) is *synchronous-ambiguous*: at that frequency a genuine
# bearing defect is indistinguishable from ordinary shaft-order content
# (misalignment / looseness / electrical / a plain shaft harmonic). Committing a
# bearing fault there is the integer-order-collision false-positive family
# (e.g. the MAFAULDA normals whose BPFO≈3× and BPFI≈5×). Such a match is moved
# to the differential at LOW confidence and ships the measurement that resolves
# it (synchronous-averaged time-waveform / envelope demodulation), via the
# recommendations channel — the product pillar: every unresolved ambiguity
# ships with the measurement that resolves it.
#
# Corroboration deviation (documented): the design allowed a collision to still
# COMMIT given "non-synchronous corroboration". For an integer-order collision
# none of the enumerated signals can actually arise from the evidence present:
#   * a bearing harmonic cannot corroborate — the 2× of an order ≈ k sits at
#     ≈ 2k, and its fractional distance to 2k EQUALS the fundamental's to k
#     (|2·o − 2k| / 2k = |o − k| / k), so if the fundamental is inside the guard
#     the harmonic is too; the whole series aliases onto shaft orders;
#   * shaft-rate sidebands around an integer center are themselves integer;
#   * the one signal that *could* corroborate — an independent envelope-spectrum
#     peak when the collision arose in a raw spectrum — has no source here
#     (MAFAULDA, the only raw-collision case in scope, carries no envelope).
# So the guard demotes unconditionally rather than wiring an envelope-corroboration
# hook no dataset yet exercises (that hook is deferred, not speculatively built).
# ─────────────────────────────────────────────────────────────────────────


def _synchronous_order_k(order: float, eps_pct: float, kmax: int = 6) -> int | None:
    """The integer shaft order k (1..kmax) this order collides with, within
    `eps_pct` percent of k, else None. Distance is |order − k| / k (percent of
    the shaft order), matching how the collision margins are documented."""
    for k in range(1, kmax + 1):
        if abs(order - k) / k * 100.0 <= eps_pct:
            return k
    return None


def _synchronous_collision(
    match: FaultMatch, shaft_freq: float, eps_pct: float | None
) -> DifferentialCandidate | None:
    """If `match` (a committed bearing FaultMatch) collides with an integer
    shaft order, return the LOW-confidence DifferentialCandidate it demotes to;
    else None (commit unchanged). `eps_pct is None` (streaming profile has no
    `epsilon_sync`) disables the guard entirely — byte-identical to pre-B3."""
    if eps_pct is None or match.freq_hz is None or shaft_freq <= 0:
        return None
    order = match.freq_hz / shaft_freq
    k = _synchronous_order_k(order, eps_pct)
    if k is None:
        return None
    pct = abs(order - k) / k * 100.0
    adjudication = (
        f"Synchronous-ambiguous: the matched peak at {match.freq_hz:.1f} Hz is "
        f"{order:.3f}× shaft — within {pct:.2f}% of {k}× (a shaft-order line). At this "
        f"frequency a bearing defect cannot be separated from ordinary shaft-order "
        f"content (misalignment, looseness, electrical, or a plain shaft harmonic) "
        f"without synchronous-averaged time-waveform or envelope analysis."
    )
    return DifferentialCandidate(
        fault=match.fault,
        description=match.description,
        confidence="low",
        adjudication=adjudication,
    )


# ─────────────────────────────────────────────────────────────────────────
# Session B (B4) — amplitude floor
#
# A committed bearing match must stand out from the axis's broadband noise
# floor: peak amplitude / axis-spectrum MEAN ≥ floor_min. This is DISTINCT from
# _amplitude_is_marginal (which is a soft confidence FACTOR, keyed on the max of
# the selected top-N peaks) — the floor is a hard commit/differential gate keyed
# on the spectrum MEAN, the true broadband reference. Below floor → differential,
# LOW, with the resolving high-resolution-envelope capture via recommendations.
# Inert for the NCD path (no spectrum → no axis_mean_amp) and under any profile
# without `floor_min` (streaming) — byte-identical there.
# ─────────────────────────────────────────────────────────────────────────


def _amplitude_floor(
    match: FaultMatch, ctx: _RcaContext, floor_min: float | None
) -> DifferentialCandidate | None:
    """If `match`'s peak sits below the axis amplitude floor, return the LOW
    DifferentialCandidate it demotes to; else None (commit unchanged)."""
    if floor_min is None or ctx.axis_mean_amp is None or match.freq_hz is None:
        return None
    mean_amp = ctx.axis_mean_amp.get(match.axis)
    if mean_amp is None or mean_amp <= 0:
        return None
    # The matched peak (best-ranked in its window) lives in ctx.peaks; recover its
    # amplitude by nearest freq on the same axis.
    candidates = [p for p in ctx.peaks if p.axis == match.axis and p.amplitude is not None]
    if not candidates:
        return None
    peak = min(candidates, key=lambda p: abs(p.freq - match.freq_hz))
    if peak.amplitude is None:
        return None
    ratio = peak.amplitude / mean_amp
    if ratio >= floor_min:
        return None
    adjudication = (
        f"Below amplitude floor: the matched peak is only {ratio:.1f}× the axis "
        f"spectrum mean (floor {floor_min:.1f}×) — near the broadband noise floor, "
        f"where an apparent bearing tone cannot be distinguished from spectral noise. "
        f"A higher-resolution enveloping/PeakVue capture is needed to confirm or clear it."
    )
    return DifferentialCandidate(
        fault=match.fault,
        description=match.description,
        confidence="low",
        adjudication=adjudication,
    )


# ─────────────────────────────────────────────────────────────────────────
# Detector (B) — mechanical looseness
# ─────────────────────────────────────────────────────────────────────────


def detect_looseness(ctx: _RcaContext) -> list[FaultMatch]:
    if not (ctx.shaft_harmonic_count >= 2 and ctx.has_subharmonic):
        return []

    sub_peak = next(
        (
            ctx.harmonics[label][0]
            for label in ("0.5x", "1.5x", "2.5x")
            if ctx.harmonics[label]
        ),
        None,
    )
    if sub_peak is None:
        return []

    harmonics_by_axis: dict[str, list[str]] = {axis: [] for axis in _AXES}
    for axis in _AXES:
        for label, hits in ctx.harmonics.items():
            if any(p.axis == axis for p in hits):
                harmonics_by_axis[axis].append(label)

    loudest_axis = None
    if ctx.peaks:
        loudest_axis = sorted(
            ctx.peaks, key=lambda p: (-ctx.v.get(p.axis, 0.0), p.rank)
        )[0].axis

    conf = ctx.confidence_cfg
    harmonic_cap = conf.get("harmonic_cap", 3)
    factors = [
        _factor(conf, "base", "Subharmonic present — the characteristic looseness signature"),
        _factor(
            conf,
            "harmonic_each",
            f"{ctx.shaft_harmonic_count} shaft harmonics detected across axes",
            count=min(ctx.shaft_harmonic_count, harmonic_cap),
        ),
        _factor(conf, "axis_pattern_consistent", "Harmonic family spans multiple axes"),
    ]
    factors.extend(_context_factors(conf, ctx))
    confidence = _score_confidence(factors, conf)

    return [
        FaultMatch(
            fault="mechanical_looseness",
            description="Mechanical looseness — loose mounting, bearing fit, or foundation",
            freq_hz=round(sub_peak.freq, 2),
            expected_hz=round(ctx.shaft_freq * 0.5, 2),
            axis=sub_peak.axis,
            confidence=confidence,
            confidence_evidence=factors,
            evidence=(
                f"Multiple shaft harmonics detected ({ctx.shaft_harmonic_count} across axes) "
                f"including subharmonic at {sub_peak.freq:.1f} Hz. Subharmonic presence is the "
                "characteristic signature of looseness — not produced by imbalance, "
                "misalignment, or bearing faults."
            ),
            harmonics_by_axis=harmonics_by_axis,
            loudest_axis=loudest_axis,
        )
    ]


# ─────────────────────────────────────────────────────────────────────────
# Detector (C) — misalignment family
# ─────────────────────────────────────────────────────────────────────────


def detect_misalignment_family(
    ctx: _RcaContext, ratio_trigger: float, ratio_strong: float
) -> list[FaultMatch]:
    triggered = (
        (ctx.has_1x_axial and ctx.has_2x_axial)
        or (ctx.has_2x_radial and ctx.has_1x_axial)
        or (
            ctx.has_1x_axial
            and ctx.axial_radial_ratio is not None
            and ctx.axial_radial_ratio > ratio_trigger
        )
    )
    if not triggered:
        return []

    axial_1x_dominant = _top_peak_is(ctx.peaks, ctx.axial, ctx.shaft_freq, ctx.tolerance)
    radial_2x_dominant = any(
        _top_peak_is(ctx.peaks, ax, ctx.shaft_freq * 2, ctx.tolerance) for ax in ctx.radial
    )
    coupled = ctx.machine.coupled
    conf = ctx.confidence_cfg
    factors = [_factor(conf, "base", "1×/2× shaft-harmonic pattern present")]

    if ctx.has_higher_harmonics and ctx.has_1x_axial and ctx.has_2x_axial and ctx.has_2x_radial:
        fault = "severe_misalignment"
        description = "Severe misalignment — coupling under significant distress"
        higher = ", ".join(h for h in ("3x", "4x", "5x") if ctx.harmonics[h])
        n_higher = sum(1 for h in ("3x", "4x", "5x") if ctx.harmonics[h])
        factors.append(_factor(conf, "axis_pattern_consistent", "1× and 2× on both axial and radial axes"))
        factors.append(_factor(conf, "harmonic_each", f"Higher harmonics present ({higher})", count=n_higher))
        evidence = (
            f"1× and 2× shaft peaks present with higher harmonics ({higher}) — pattern "
            "indicates severe misalignment or significant coupling distress"
        )
    elif (
        axial_1x_dominant
        and ctx.axial_radial_ratio is not None
        and ctx.axial_radial_ratio > ratio_trigger
    ):
        ratio_strong_flag = ctx.axial_radial_ratio > ratio_strong
        factors.append(_factor(conf, "rank_one", f"1× dominant (rank-1) on axial axis {ctx.axial}"))
        factors.append(
            _factor(
                conf,
                "axis_pattern_consistent",
                f"Axial-to-radial velocity ratio {ctx.axial_radial_ratio:.2f}"
                + (" (strong)" if ratio_strong_flag else ""),
                count=2 if ratio_strong_flag else 1,
            )
        )
        if not coupled:
            fault = "bent_shaft"
            description = "Bent shaft — permanent deformation in the rotating shaft"
            # Session GEOM-B, fix 2. This sentence used to end "Pattern suggests
            # center bend likely", chosen by a ternary whose predicate was
            # `_top_peak_is(ctx.peaks, ctx.axial, ctx.shaft_freq, ctx.tolerance)`
            # — the SAME call, with the same arguments, that computes
            # `axial_1x_dominant` and guards entry to this branch. The else arm
            # was unreachable, so every bent-shaft report asserted a centre bend,
            # and the assertion was a restatement of the branch condition wearing
            # a finding's clothes. FAULT_COVERAGE §3 had it as dead text; GEOM-A
            # made the branch reachable, at which point dead text became a claim
            # an analyst reads and could act on.
            #
            # No sub-type is asserted, because no predicate available here
            # distinguishes the two: a mid-span bend and an end-of-shaft bend
            # both present as a dominant axial 1×, and what separates them is 1×
            # PHASE read at both bearings, which this analysis does not measure
            # (session PHASE, ROADMAP §S17). Inventing a discriminator without
            # phase would trade one unsupported claim for another — GEOM-A's
            # close-out said so, and the operator ruled the same way. The finding
            # states the limit and names what resolves it, which is the register
            # the sibling angular branch below already uses.
            evidence = (
                f"1× shaft peak dominant on axial axis ({ctx.axial}) with axial-to-radial "
                f"velocity ratio {ctx.axial_radial_ratio:.2f}. Machine has no coupling, ruling "
                f"out misalignment. Bend LOCATION is not determined by this measurement: a "
                f"mid-span bend and a bend at the shaft end both present as a dominant axial "
                f"1×, and 1× phase read at both bearings is what separates them."
            )
        else:
            fault = "angular_misalignment"
            description = "Angular misalignment — coupled shafts meeting at an angle"
            strength = (
                "strong angular signature (>1.0 ratio)"
                if ratio_strong_flag
                else "moderate angular signature (0.5-1.0 ratio, worth investigating)"
            )
            evidence = (
                f"1× shaft peak dominant on axial axis ({ctx.axial}) with axial-to-radial "
                f"velocity ratio {ctx.axial_radial_ratio:.2f} — {strength}. Sub-type "
                "confirmation requires phase analysis across coupling."
            )
    elif radial_2x_dominant and ctx.has_2x_radial:
        fault = "parallel_misalignment"
        description = "Parallel misalignment — coupled shafts parallel but offset"
        factors.append(_factor(conf, "rank_one", "2× dominant (rank-1) on a radial axis"))
        factors.append(_factor(conf, "axis_pattern_consistent", "1× and 2× both present on radial axes"))
        evidence = (
            "2× shaft peak dominant on radial axes with both 1× and 2× present. Pattern "
            "consistent with parallel (offset) misalignment. Sub-type confirmation requires "
            "phase analysis across coupling."
        )
    else:
        fault = "misalignment_general"
        description = "Misalignment detected — sub-type not determinable from single sensor"
        factors.append(_factor(conf, "axis_pattern_consistent", "1× and 2× present across multiple axes"))
        evidence = (
            "1× and 2× shaft peaks present on multiple axes without clear sub-type signature. "
            "Recommend laser alignment which corrects both angular and parallel sub-types."
        )

    factors.extend(_context_factors(conf, ctx))
    confidence = _score_confidence(factors, conf)

    # Session BENT-FIX, from SESSION_GEOMB.md §7 finding 2. All five sub-types
    # used to publish `expected_hz = shaft × 2` and an observed frequency that
    # PREFERS the first 2× peak found on ANY axis. For four of them that is the
    # order they argue from and it is left exactly as it was — which is also
    # what holds the corpus at zero.
    #
    # `bent_shaft` is the exception. It commits on `axial_1x_dominant`: the
    # whole argument is a dominant 1× on the AXIAL axis, and its evidence
    # sentence says so. The row therefore printed a computed 40.0 against an
    # observed 20.0 on the 1200 rpm fixture — which reads as a 100 % frequency
    # error on a finding that is correct — and wherever any stray 2× existed on
    # any axis, the observed value became that 2× and the 1× the branch argued
    # from vanished from the report altogether. The evidence row now carries the
    # frequency the branch actually reasoned about, on the axis it reasoned on.
    if fault == "bent_shaft":
        axial_1x = next((p for p in ctx.harmonics["1x"] if p.axis == ctx.axial), None)
        expected_freq = ctx.shaft_freq
        # `axial_1x_dominant` guards this branch, so the peak is always there;
        # the fallback keeps the row honest rather than absent if that changes.
        observed_freq = axial_1x.freq if axial_1x is not None else ctx.shaft_freq
    else:
        top_peak = (ctx.harmonics["2x"][0] if ctx.harmonics["2x"] else None) or (
            ctx.harmonics["1x"][0] if ctx.harmonics["1x"] else None
        )
        expected_freq = ctx.shaft_freq * 2
        observed_freq = top_peak.freq if top_peak is not None else ctx.shaft_freq * 2

    return [
        FaultMatch(
            fault=fault,
            description=description,
            freq_hz=round(observed_freq, 2),
            expected_hz=round(expected_freq, 2),
            axis=ctx.axial,
            confidence=confidence,
            confidence_evidence=factors,
            evidence=evidence,
            axial_velocity_mms=round(ctx.v_axial, 3),
            radial_velocity_mms=round(ctx.v_radial_max, 3),
            axial_radial_ratio=round(ctx.axial_radial_ratio, 2)
            if ctx.axial_radial_ratio is not None
            else None,
        )
    ]


# ─────────────────────────────────────────────────────────────────────────
# Session R3-DIFF (item 1b) — the shaft-order differential gate
#
# detect_misalignment_family is TRIGGERED by the PRESENCE of 1×/2× across
# axes. Presence is not a discriminator: every rotating machine has a 1× and a
# 2×, and imbalance puts a large 1× on the radial axes with a small 2× — the
# same peaks, in different proportions. The trigger never asked about
# PROPORTION, so a textbook imbalance trio (prod test B2_blower: H 4.2 / V 3.1
# / axial 0.9 mm/s, 2× ≈ 0.4) satisfied it and committed severe misalignment.
#
# This gate asks the amplitude question. Misalignment commits only when one of
# the two positive signatures is actually present:
#
#   (a) 2×/1× amplitude ratio on the DOMINANT RADIAL axis ≥ misalignment_2x_1x_min
#       — a real misalignment 2× is a substantial fraction of the 1×; and
#   (b) axial/radial overall velocity ratio ≥ misalignment_axial_radial_min WITH
#       1× and 2× both present axially — the angular signature, which is about
#       energy reaching the axial axis, not about a ratio between harmonics.
#
# Either alone commits. Neither → the family is demoted to the differential
# (never dropped: the operator still sees the candidate and why it lost) and,
# when the 1× radial line dominates, imbalance commits in its place.
#
# Route-only by construction: both constants are absent from `streaming`, and a
# missing constant turns the gate off, so the frozen NCD pipeline is unchanged.
# Amplitude-less peaks (the NCD triplets) likewise turn branch (a) off — the
# question cannot be asked of a source that reports rank but not level.
# ─────────────────────────────────────────────────────────────────────────


def _dominant_radial_axis(ctx: _RcaContext) -> Axis | None:
    """The radial axis carrying the most overall velocity — the axis a route
    analyst would read the 2×/1× ratio on."""
    best_v, best_axis = -1.0, None
    for axis in ctx.radial:
        v = ctx.v.get(axis, 0.0)
        if v > best_v:
            best_v, best_axis = v, axis
    return best_axis if best_v > 0 else None


def _harmonic_amplitude(ctx: _RcaContext, label: str, axis: Axis) -> float | None:
    """Loudest floor-clearing `label` peak on `axis`, or None if there is none.

    Reads ctx.harmonics, so item 1a's floor filter applies here too: a sub-floor
    2× is not a 2× for gating purposes any more than it is for evidence.
    """
    amps = [p.amplitude for p in ctx.harmonics[label] if p.axis == axis and p.amplitude is not None]
    return max(amps) if amps else None


def _misalignment_amplitude_gate(
    ctx: _RcaContext, two_x_min: float | None, axial_radial_min: float | None
) -> str | None:
    """None → misalignment may commit. A string → the adjudication for demoting it.

    Deliberately fail-OPEN on every question it cannot answer (gate off, no
    amplitudes, no radial 1× to take a ratio against): this gate exists to stop
    a 1×-dominant radial trio being called misalignment, and where that shape
    cannot be established the pre-R3 behaviour stands.
    """
    if two_x_min is None or axial_radial_min is None:
        return None

    axis = _dominant_radial_axis(ctx)
    if axis is None:
        return None
    amp_1x = _harmonic_amplitude(ctx, "1x", axis)
    amp_2x = _harmonic_amplitude(ctx, "2x", axis)
    if amp_1x is None or amp_1x <= 0:
        return None  # no radial 1× to judge against — not the shape this gate polices

    ratio_2x = (amp_2x or 0.0) / amp_1x
    if ratio_2x >= two_x_min:
        return None  # branch (a): a genuine radial 2×

    axial_ratio = ctx.axial_radial_ratio
    # "1×/2× present axially" reads as 1× OR 2× — axial shaft-order content — not as
    # a conjunction. The classic angular-misalignment / bent-shaft signature is a
    # dominant axial 1× with no 2× anywhere (detect_misalignment_family's own third
    # trigger clause needs no 2× either), and tests/test_multiaxis.py's Session E
    # conjunction proof is exactly that trio: axial 0.60 vs radial 0.15/0.14 mm/s,
    # one peak per channel. Requiring both would have made that trio commit nothing
    # and reopened the axis-mislabel bug Session E closed. The distinction cannot
    # rescue a 1×-dominant RADIAL trio in any case — B2_blower fails branch (b) on
    # the ratio itself (0.21 < 0.50), whichever way the harmonic clause is read.
    axial_shaft_order = ctx.has_1x_axial or ctx.has_2x_axial
    if axial_ratio is not None and axial_ratio >= axial_radial_min and axial_shaft_order:
        return None  # branch (b): a genuine axial (angular) signature

    if axial_ratio is None:
        axial_clause = "axial-to-radial velocity ratio unavailable"
    elif axial_ratio >= axial_radial_min:
        axial_clause = (
            f"axial-to-radial velocity ratio {axial_ratio:.2f} reaches the {axial_radial_min:.2f} "
            f"bar but no 1× or 2× shaft-order peak clears the amplitude floor on the axial axis "
            f"({ctx.axial})"
        )
    else:
        axial_clause = (
            f"axial-to-radial velocity ratio {axial_ratio:.2f} is below {axial_radial_min:.2f}, "
            f"so the radial axes carry the energy"
        )
    return (
        f"Downgraded on amplitude: the 2×/1× ratio on the dominant radial axis ({axis}) is "
        f"{ratio_2x:.2f}, below the {two_x_min:.2f} a misalignment 2× is expected to reach, and "
        f"{axial_clause}. 1× and 2× are both present, but presence alone does not separate "
        f"misalignment from a 1×-dominant condition. Confirm or clear with phase measurement "
        f"across the coupling."
    )


# ─────────────────────────────────────────────────────────────────────────
# Session PDMFIX — the 1x-family severity gate (ruling 1)
#
# THE DEFECT. detect_misalignment_family and detect_imbalance are triggered by
# the SHAPE of the shaft-order content and never asked how loud it is. On a
# healthy machine the rank-1 peak is always 1x, so some 1x pattern always
# matches — and the FP sweep (scratchpad/fp_sweep/baseline.md) measured the
# consequence: 24 of 28 ISO Zone-A cells committed a 1x-family fault, and every
# severity column of the grid was IDENTICAL. Two live production reproductions:
# a healthy single-channel upload committing rotor imbalance at HIGH, and a
# healthy trio (all axes ~0.6 mm/s, Zone A) committing angular misalignment.
#
# THE RULE. A 1x-family candidate commits only when the axis it argued from is
# at least `one_x_severity_min_zone` on THAT MACHINE'S OWN resolved boundaries
# (iso_classify.resolve_thresholds: machine override > factory default > ISO
# table), OR clears `one_x_absolute_floor_mms`. The config holds a zone LETTER
# and one mm/s number; no ISO boundary is written into this module.
#
# The absolute floor only ever LOOSENS the gate — it exists for machines whose
# own A/B boundary is high (ISO group 1 / flexible mounts, ab up to 3.5 mm/s),
# where a 2.8 mm/s 1x line is Zone A by class but objectively worth raising.
# For every group-2 rigid machine (ab=1.4) it is inert.
#
# BEARING TONES ARE NOT GATED, by ruling. A discrete BPFO at Zone A is early
# detection working as designed — that is the damage-stage note's own argument —
# so this gate is applied to the 1x family only, never to detect_bearing_faults.
#
# Route-only: streaming carries neither key, so the gate is unarmed there and
# the frozen NCD profile is byte-identical. Same pattern as epsilon_sync,
# floor_min and the misalignment amplitude gate.
# ─────────────────────────────────────────────────────────────────────────

_ZONE_FLOOR_ATTR: dict[str, str] = {"B": "ab", "C": "bc", "D": "cd"}

#: The gate's closing argument, per family. It is the REASON a quiet candidate is
#: refused, and the reason differs: a 1x line is present on every rotating
#: machine, so its mere presence proves nothing — a belt line is not, so the
#: honest refusal is about amplitude alone. Session GEOM-B added the parameter;
#: the default is the pre-GEOM-B sentence, byte for byte.
_ONE_X_CLOSING = (
    "A 1x shaft-order pattern is present on every rotating machine; at this amplitude it is "
    "not evidence of a fault."
)
_BELT_CLOSING = (
    "At this amplitude the matched belt line is not evidence of a belt-drive fault."
)


def _one_x_gate_axis(match: FaultMatch, ctx: _RcaContext) -> Axis:
    """The axis the detector's OWN argument rests on — which is not always
    `match.axis`.

    `detect_imbalance` sets `match.axis` to the radial 1x peak it argued from, so
    for imbalance the two coincide. `detect_misalignment_family` does not: it
    sets `axis` from `harmonics["2x"][0]`, i.e. whichever axis happens to come
    first in peak-list (dict-iteration) order. Judging the angular branch on that
    axis would let a loud RADIAL line license an AXIAL-argued misalignment call —
    precisely the confusion this gate exists to remove — so each sub-type is
    mapped to the axis its evidence string actually cites.
    """
    if match.fault in ("angular_misalignment", "bent_shaft"):
        # "1x shaft peak dominant on axial axis (…) with axial-to-radial ratio …"
        return ctx.axial
    if match.fault == "parallel_misalignment":
        # "2x shaft peak dominant on radial axes …"
        return max(ctx.radial, key=lambda a: ctx.v.get(a, 0.0))
    if match.fault in ("severe_misalignment", "misalignment_general"):
        # These argue from content spanning several axes ("1x and 2x on both
        # axial and radial"), so the loudest axis is the honest reading.
        return max(_AXES, key=lambda a: ctx.v.get(a, 0.0))
    if match.fault == "belt_fault":
        # Session GEOM-B. The belt detector argues from ONE matched line, and
        # `match.axis` IS that line's axis — so the fallthrough below was already
        # the right answer. Stated rather than inherited, because the whole point
        # of this helper is that the axis is chosen per detector's own argument,
        # and a family reaching the gate by accident of the fallthrough is how
        # the confusion this helper removes gets back in.
        return match.axis
    return match.axis


def _one_x_severity_gate(
    match: FaultMatch,
    ctx: _RcaContext,
    min_zone: str | None,
    absolute_floor_mms: float | None,
    closing: str = _ONE_X_CLOSING,
) -> str | None:
    """None → this candidate may commit. A string → why it may not.

    Judged on the velocity of the axis the DETECTOR ITSELF argued from
    (`_one_x_gate_axis`), not on the reading's overall severity: the overall is
    the max across axes, so using it would let a loud radial axis license an
    axial-argued misalignment call.

    Unarmed — and therefore inert, returning None — whenever the question cannot
    be asked: no gate keys in the profile (streaming), no resolved
    MachineThresholds supplied by the caller, or no MEASURED velocity on the
    arguing axis (an acceleration-only reading).

    Session GEOM-B widened the gate's reach from the 1x family to the belt
    detector, which competes with it for the same reading. The BAR is identical
    (same two constants, same arguing-axis rule); only the closing sentence
    differs, because the reason a quiet candidate is refused is family-specific —
    hence `closing`, whose default is the pre-GEOM-B 1x sentence verbatim.
    """
    if min_zone is not None:
        attr = _ZONE_FLOOR_ATTR.get(min_zone)
        if ctx.iso_thresholds is None or attr is None:
            return None
        zone_bar: float | None = getattr(ctx.iso_thresholds, attr)
    else:
        zone_bar = None

    if zone_bar is None and absolute_floor_mms is None:
        return None

    axis = _one_x_gate_axis(match, ctx)
    # Session PDMFIX, found by the corpus diff and NOT by the FP sweep (every
    # sweep cell had velocity). ISO severity is a VELOCITY judgement, and
    # `ctx.v` carries the pipeline's 0.0 coercion for an axis that reported
    # nothing — so on an acceleration-only reading (MAFAULDA, CWRU, MFPT, an
    # unscaled WAV: `iso_zone == "not_assessable"`, `severity_rms is None`) this
    # gate would read a coerced zero as a genuinely quiet machine and refuse
    # every 1x-family commit on the entire dataset. That is the same
    # absent-vs-measured-silent confusion ruling (2) exists to remove, and
    # `iso_classify._validate_velocity` already forbids it in as many words:
    # missing velocity is "NEVER coerced to 0.0, which would fake a
    # quiet-machine Zone A reading out of missing data".
    #
    # So: no measured velocity on the arguing axis -> the severity question
    # cannot be asked -> the gate stays unarmed, exactly as it does when the
    # profile carries no keys. Acceleration-only datasets keep their pre-PDMFIX
    # verdicts; measuring velocity is what arms the gate.
    if ctx.measured_axes is not None and axis not in ctx.measured_axes:
        return None
    velocity = ctx.v.get(axis)
    if velocity is None:
        return None

    bars = [b for b in (zone_bar, absolute_floor_mms) if b is not None]
    if any(velocity >= b for b in bars):
        return None

    clauses = []
    if zone_bar is not None:
        clauses.append(f"the {zone_bar:.2f} mm/s Zone-{min_zone} boundary for this machine")
    if absolute_floor_mms is not None:
        clauses.append(f"the {absolute_floor_mms:.2f} mm/s absolute floor")
    return (
        f"Not committed on severity: {match.fault} argues from axis {axis}, which measures "
        f"{velocity:.2f} mm/s — below {' and below '.join(clauses)}. {closing}"
    )


# ─────────────────────────────────────────────────────────────────────────
# Detector (D) — imbalance
# ─────────────────────────────────────────────────────────────────────────


_MISALIGNMENT_FAULTS = frozenset(
    {
        "angular_misalignment",
        "parallel_misalignment",
        "severe_misalignment",
        "misalignment_general",
        "bent_shaft",
    }
)


def _intra_radial_1x_dominates(ctx: _RcaContext, d_1x: float) -> bool:
    """Session B (B5/J3) intra-radial 1× dominance: the strongest 1× radial peak
    must lead the strongest higher-harmonic radial peak (2×/3×/4×/5×) by ≥ d_1x.
    This separates imbalance (a 1×-dominant radial line) from misalignment/looseness
    (which put strong 2× — or higher — on the radial axes). Uses peak amplitude when
    present (spectrum sources); falls back to peak RANK for the amplitude-less NCD
    triplets (rank encodes the sensor's own loudness order, so d_1x=1.0 ⇔ the 1×
    peak is not out-ranked by any higher harmonic)."""
    radial_1x = [p for p in ctx.harmonics["1x"] if p.axis in ctx.radial]
    if not radial_1x:
        return False
    competitors = [
        p
        for label in ("2x", "3x", "4x", "5x")
        for p in ctx.harmonics[label]
        if p.axis in ctx.radial
    ]
    if not competitors:
        return True  # nothing higher on the radial axes → the 1× line dominates
    best_1x = min(radial_1x, key=lambda p: p.rank)
    if best_1x.amplitude is not None and all(c.amplitude is not None for c in competitors):
        strongest = max(c.amplitude for c in competitors)
        return strongest <= 0 or best_1x.amplitude >= d_1x * strongest
    # NCD (no amplitude): rank proxy. d_1x=1.0 ⇔ 1× rank ≤ strongest competitor's rank.
    return best_1x.rank <= min(c.rank for c in competitors)


def _strongest_radial_1x(ctx: _RcaContext) -> Peak | None:
    """The radial 1× peak the imbalance call argues from: the LOUDEST one.

    Session DOMINANT-RANK (PROD_READINESS.md:416). This was `next(...)` — the
    first radial 1× peak in `spectra` dict-iteration order, which on the
    multi-axis upload path is **upload SLOT order**. So an identical machine,
    measured identically, was reported on a different axis, at a different
    frequency and with a different radial velocity depending on which file the
    analyst happened to drop into slot 1 — and, because `_one_x_gate_axis`
    returns `match.axis` for imbalance, judged for severity on that axis's
    velocity too. Selection is a diagnostic decision; it may not be an accident
    of a dict.

    Amplitude when every candidate carries it (spectrum sources); peak RANK
    otherwise — the amplitude-less NCD triplets, where rank IS the sensor's own
    loudness order. That is the same fallback `_intra_radial_1x_dominates` uses
    one function above, deliberately: the gate and the reported peak now answer
    to one notion of "strongest".

    `max`/`min` return the FIRST extremal element, so a genuine tie keeps the
    pre-existing answer byte-for-byte.

    NOT fixed here: `_intra_radial_1x_dominates`'s own `best_1x` still picks by
    cross-axis rank while comparing against competitors by amplitude
    (PROD_READINESS.md:929). That is the *gate*'s selection, it changes WHICH
    readings fire rather than which peak is reported, and it is left open on
    purpose — this commit is scoped to the peak the sentence describes.
    """
    candidates = [p for p in ctx.harmonics["1x"] if p.axis in ctx.radial]
    if not candidates:
        return None
    if all(p.amplitude is not None for p in candidates):
        return max(candidates, key=lambda p: p.amplitude)
    return min(candidates, key=lambda p: p.rank)


def detect_imbalance(
    ctx: _RcaContext, r_dom: float | None = None, d_1x: float = 1.0
) -> FaultMatch | None:
    """Detect the imbalance *signature* only (1× radial-dominant). Suppression by
    a competing diagnosis is adjudicated in run_rca so the suppressed candidate can
    be surfaced in the differential — this detector no longer silently returns
    nothing when another fault is present.

    Session B (B5/J3): when `r_dom` is None (streaming / no key) the strict pre-B5
    gate holds — a 1× radial peak with NO axial 1× peak — so behavior is
    byte-identical. When set, a TWO-condition gate replaces it: (1) radial velocity
    dominance v_radial_max/v_axial (= 1/axial_radial_ratio) ≥ r_dom, AND (2)
    intra-radial 1× dominance (`_intra_radial_1x_dominates`, ≥ d_1x). Both constants
    are fixture-derived: T07 fires; T05/T06/T08/T10/T15 stay silent (T10 via the
    intra-radial condition — its radial 2× leads the 1×; the rest via r_dom).
    """
    if not ctx.has_1x_radial:
        return None
    if r_dom is None:
        if ctx.has_1x_axial:
            return None
    else:
        if ctx.axial_radial_ratio is None:
            return None
        dominance = float("inf") if ctx.axial_radial_ratio == 0 else 1.0 / ctx.axial_radial_ratio
        if dominance < r_dom or not _intra_radial_1x_dominates(ctx, d_1x):
            return None
    # Session DOMINANT-RANK — the strongest radial 1×, not the first one the
    # dict happened to yield. Unreachable None (has_1x_radial above guarantees a
    # candidate), kept as the type contract it always was.
    radial_peak = _strongest_radial_1x(ctx)
    if radial_peak is None:
        return None

    conf = ctx.confidence_cfg
    # Session PDMFIX — ruling (2). "Axial quiet" is a CONJUNCT, and a conjunct is
    # satisfied by measurement, never by absence. Before this, a single radial
    # channel had no axial peaks for the trivial reason that it had no axial
    # channel, `not ctx.has_1x_axial` read True, and the resulting count=2
    # `axis_pattern_consistent` bonus was the exact 0.5 that carried the score
    # from 2.5 (medium) to 3.0 (the `high` band floor). The absent channel was
    # the difference between medium and high on a machine reading 1.25 mm/s.
    axial_quiet = ctx.axial_measured and not ctx.has_1x_axial
    if axial_quiet:
        pattern_detail = f"1× radial-dominant with axial axis ({ctx.axial}) measured and quiet at shaft frequency"
    elif not ctx.axial_measured:
        pattern_detail = (
            f"1× radial-dominant on the measured radial channel; axial axis ({ctx.axial}) "
            "not measured, so the axial term is unevidenced"
        )
    else:
        pattern_detail = f"1× radial-dominant — radial velocity leads axis ({ctx.axial}) at shaft frequency"
    factors = [
        _factor(conf, "base", "1× present on a radial axis"),
        _factor(
            conf,
            "axis_pattern_consistent",
            pattern_detail,
            # The doubled weight is what a MEASURED axial channel buys — quiet
            # or loud, it is evidence. An unmeasured one buys nothing. Keyed on
            # `axial_measured`, NOT on `axial_quiet`, so the trio paths (both
            # the quiet-axial and the radial-leads branches) are unchanged.
            count=2 if ctx.axial_measured else 1,
        ),
    ]
    if radial_peak.rank == 1:
        factors.append(_factor(conf, "rank_one", f"1× is the dominant peak on {radial_peak.axis}"))
    if _amplitude_is_marginal(radial_peak, ctx):
        factors.append(_factor(conf, "amplitude_marginal", "1× peak sits near the noise floor"))
    factors.extend(_context_factors(conf, ctx))
    confidence = _score_confidence(factors, conf)
    # Session PDMFIX — ruling (2), the cap. With no axial channel the imbalance
    # call rests on one radial line and cannot be separated from an axial-dominant
    # misalignment or bent shaft. A HARD ceiling, not a weight nudge: the ruling
    # says single-channel 1x-dominant caps at medium, and a ceiling is the only
    # form of that statement a future weight change cannot quietly undo.
    if not ctx.axial_measured and confidence == "high":
        confidence = "medium"

    # Session DOMINANT-RANK — "dominant" is a claim about this peak's standing on
    # its own axis, and the detector already knows the answer: the `rank_one`
    # factor above is awarded only `if radial_peak.rank == 1`. The SCORING was
    # rank-honest and the SENTENCE was not — it called a rank-2 line "dominant"
    # on every reading where a louder, unrelated line sat on the same axis. On
    # the HIST-2 sample (before.csv, no bearing model) that shipped a report
    # committing to 30.0 Hz at amplitude 0.9 and calling it dominant, while
    # 107.0 Hz sat 1.56× louder on the same axis, claimed by nothing — the
    # defect Session UNEXPLAINED-PEAK measured (§3) and could not reach from the
    # report layer, because the word is born here.
    #
    # ONE clause, built once, interpolated into all three branches below. Those
    # three strings repeated the same eleven words verbatim; writing a
    # rank-aware opening three times is three chances to drift, which is exactly
    # how UNEXPLAINED-PEAK's own defect survived (four templates, one wrong
    # gate, all copies of each other).
    if radial_peak.rank == 1:
        lead = (
            f"1× shaft frequency ({radial_peak.freq:.1f} Hz) dominant on radial axis "
            f"{radial_peak.axis}"
        )
        rank_note = ""
    else:
        lead = (
            f"1× shaft frequency ({radial_peak.freq:.1f} Hz) present on radial axis "
            f"{radial_peak.axis} at rank {radial_peak.rank} of that axis's peaks"
        )
        # The louder line, read off the SAME peak list rank was computed over.
        # `ctx.peaks` is deliberately unfiltered here (the harmonic sets carry
        # the R3-DIFF evidence floor, rank does not), so this names the line the
        # analyst sees at the top of that axis's spectrum — the one this finding
        # does not claim. No amplitude is quoted: the report layer's own
        # "Unexplained dominant peak" section owns the ratio, and two places
        # computing it is two places to drift.
        loudest = next(
            (p for p in ctx.peaks if p.axis == radial_peak.axis and p.rank == 1), None
        )
        rank_note = (
            f" The loudest line on {radial_peak.axis} is {loudest.freq:.1f} Hz, "
            "unexplained by this finding."
            if loudest is not None
            else ""
        )

    if axial_quiet:
        evidence = (
            f"{lead}, axial axis ({ctx.axial}) measured and quiet at shaft frequency. "
            f"Characteristic signature of rotor imbalance.{rank_note}"
        )
    elif not ctx.axial_measured:
        evidence = (
            f"{lead}, and the 1× line leads the higher radial harmonics. Consistent "
            f"with rotor imbalance. The axial axis ({ctx.axial}) was NOT measured, so the quiet-"
            "axial half of the imbalance signature is unevidenced and an axial-dominant "
            f"misalignment or bent shaft cannot be ruled out from this channel alone.{rank_note}"
        )
    else:
        evidence = (
            f"{lead}; radial velocity dominates the axial axis ({ctx.axial}) and "
            "the 1× line leads the higher radial harmonics. Characteristic signature of rotor "
            f"imbalance.{rank_note}"
        )

    return FaultMatch(
        fault="imbalance",
        description="Rotor imbalance — uneven mass distribution",
        freq_hz=round(radial_peak.freq, 2),
        expected_hz=round(ctx.shaft_freq, 2),
        axis=radial_peak.axis,
        confidence=confidence,
        confidence_evidence=factors,
        evidence=evidence,
        radial_velocity_mms=round(ctx.v.get(radial_peak.axis, 0.0), 3),
        axial_velocity_mms=round(ctx.v_axial, 3),
    )


# ─────────────────────────────────────────────────────────────────────────
# Detector (E) — belt fault
#
# Session GEOM-B — the amplitude discipline this detector never had.
#
# GEOM-A made belt reachable from the product for the first time (pulley
# dimensions → belt.freq_hz, derived at intake), and the first thing a reachable
# belt detector did was commit on a line twelve times quieter than the one that
# explained the reading. `run_rca` called it BARE: belt answered to none of the
# discipline the families it competes with answer to — not `_clears_evidence_floor`
# (the harmonic family's bar), not `_one_x_severity_gate` (PDMFIX ruling 1, which
# imbalance and misalignment both pass through). Measured on the GEOM-A fixture: a
# 0.25 mm/s stray inside the ±3% belt window, on a Zone-C machine whose condition
# is a 3.0 mm/s 1× imbalance, committed `belt_fault` next to the correct call.
#
# THREE conditions now, each INERT when its question cannot be asked, so the
# frozen streaming/NCD path is byte-identical (the floor_min / epsilon_sync
# pattern):
#
#   (1) AMPLITUDE FLOOR — the matched peak clears `_clears_evidence_floor`: the
#       same bar report/charts.py draws on every spectrum figure. A line the
#       reader can see under the dashed floor is not one the prose may name.
#   (2) DOMINANCE — the matched peak is at least `belt_dominance_min` of the
#       loudest peak on its OWN axis. This is the condition that carries the
#       GEOM-A conjunction proof, and it is needed: measured, (1) and (3) BOTH
#       pass the stray (peak/mean 23.6 vs a 12.7 floor; axis velocity 3.07 mm/s
#       vs a 1.4 mm/s Zone-B bar). A belt line an order of magnitude under the
#       line that explains the reading is not the story of the reading.
#   (3) SEVERITY — `_one_x_severity_gate` at the `run_rca` call site, where every
#       other severity gate lives, on the axis this detector argued from.
#
# The dominance bar is a RATIO, never an absolute mm/s compared against a peak.
# Peak amplitudes are not velocities on every path — the synthetic recipes carry
# 0.09-scale tones alongside 3.3 mm/s axis velocities, and `_as_velocity` labels
# those same tones kind="velocity" — so an mm/s bar on a peak would be unit-wrong
# wherever the spectrum is not a calibrated velocity spectrum. Every other
# amplitude discipline in this file is a ratio for exactly that reason.
# ─────────────────────────────────────────────────────────────────────────


def _clears_belt_dominance(
    peak: Peak, ctx: _RcaContext, dominance_min: float | None
) -> bool:
    """Is this matched belt line a substantial fraction of the loudest line on
    its own axis?

    Inert (returns True) whenever the question cannot be asked: no bar
    configured, or an amplitude missing anywhere in the comparison — the NCD
    triplets report RANK, not level, and rank is not a proportion. Same
    inertness contract as `_clears_evidence_floor`, so the frozen streaming
    profile is untouched.
    """
    if dominance_min is None or peak.amplitude is None:
        return True
    amps = [p.amplitude for p in ctx.peaks if p.axis == peak.axis and p.amplitude is not None]
    if not amps:
        return True
    ceiling = max(amps)
    if ceiling <= 0:
        return True
    return peak.amplitude / ceiling >= dominance_min


def detect_belt_fault(
    ctx: _RcaContext,
    *,
    floor_min: float | None = None,
    dominance_min: float | None = None,
) -> list[FaultMatch]:
    if ctx.machine.belt is None:
        return []
    belt_freq = ctx.machine.belt.freq_hz
    hits = _find_peaks_matching(ctx.peaks, belt_freq, ctx.tolerance) + _find_peaks_matching(
        ctx.peaks, belt_freq * 2, ctx.tolerance
    )
    # Session GEOM-B: order-PRESERVING filter, so the peak this detector reports
    # is unchanged wherever today's pick already clears the discipline; a hit is
    # dropped only when it fails a question that could be asked of it.
    hits = [
        p
        for p in hits
        if _clears_evidence_floor(p, ctx.axis_mean_amp, floor_min)
        and _clears_belt_dominance(p, ctx, dominance_min)
    ]
    if not hits:
        return []
    best = hits[0]
    conf = ctx.confidence_cfg
    factors = [
        _factor(conf, "base", "Peak matches configured belt frequency"),
        _factor(conf, "axis_pattern_consistent", "Belt tone present in the spectrum"),
    ]
    factors.extend(_context_factors(conf, ctx))
    return [
        FaultMatch(
            fault="belt_fault",
            description="Belt drive fault — worn, loose, or mismatched belt",
            freq_hz=round(best.freq, 2),
            expected_hz=round(belt_freq, 2),
            axis=best.axis,
            confidence=_score_confidence(factors, conf),
            confidence_evidence=factors,
            evidence=(
                f"Peak at {best.freq:.1f} Hz matches belt frequency ({belt_freq:.1f} Hz "
                f"±{ctx.tolerance:.0%}). Inspect belt tension and condition."
            ),
        )
    ]


# ─────────────────────────────────────────────────────────────────────────
# Detector (F) — elevated blade pass
# ─────────────────────────────────────────────────────────────────────────


def detect_blade_pass(ctx: _RcaContext, iso_severity: IsoSeverityOrUnrated) -> list[FaultMatch]:
    if ctx.machine.blades is None:
        return []
    bpf = ctx.machine.blades * ctx.shaft_freq
    hits = _find_peaks_matching(ctx.peaks, bpf, ctx.tolerance)
    if not hits:
        return []
    if iso_severity not in ("warn", "danger"):
        return []
    best = hits[0]
    conf = ctx.confidence_cfg
    factors = [
        _factor(conf, "base", "Peak matches blade-pass frequency"),
        _factor(conf, "axis_pattern_consistent", "Coincides with elevated ISO severity"),
    ]
    factors.extend(_context_factors(conf, ctx))
    return [
        FaultMatch(
            fault="elevated_blade_pass",
            description=(
                "Elevated blade pass — possible flow issue (cavitation, blockage, "
                "hydraulic instability)"
            ),
            freq_hz=round(best.freq, 2),
            expected_hz=round(bpf, 2),
            axis=best.axis,
            confidence=_score_confidence(factors, conf),
            confidence_evidence=factors,
            evidence=(
                f"Elevated peak at {best.freq:.1f} Hz matches blade pass frequency "
                f"({ctx.machine.blades} blades × {ctx.shaft_freq:.1f} Hz shaft = {bpf:.1f} Hz). "
                "Combined with elevated ISO severity. Investigate flow conditions."
            ),
        )
    ]


# ─────────────────────────────────────────────────────────────────────────
# Detector (G) — possible resonance (last resort)
# ─────────────────────────────────────────────────────────────────────────


def detect_resonance(ctx: _RcaContext, iso_severity: IsoSeverityOrUnrated) -> list[FaultMatch]:
    # The "no committed diagnosis exists" guard lives in run_rca (resonance is
    # a last resort); this detector only decides whether an unexplained loud
    # peak is present.
    if iso_severity not in ("warn", "danger"):
        return []

    known_expected: list[float] = [ctx.shaft_freq * m for m in _HARMONIC_MULTIPLES.values()]
    if ctx.bearing_freqs is not None:
        for f in ctx.bearing_freqs.model_dump().values():
            known_expected.append(f)
            known_expected.append(f * 2)
    if ctx.machine.belt is not None:
        known_expected.append(ctx.machine.belt.freq_hz)
        known_expected.append(ctx.machine.belt.freq_hz * 2)
    if ctx.machine.blades is not None:
        known_expected.append(ctx.machine.blades * ctx.shaft_freq)

    unexplained_rank1 = [
        p
        for p in ctx.peaks
        if p.rank == 1 and not any(_within(p.freq, exp, ctx.tolerance) for exp in known_expected)
    ]
    if not unexplained_rank1:
        return []

    peak = unexplained_rank1[0]
    conf = ctx.confidence_cfg
    factors = [_factor(conf, "base", "Unexplained loud peak with elevated ISO severity")]
    factors.extend(_context_factors(conf, ctx))
    return [
        FaultMatch(
            fault="possible_resonance",
            description="Possible resonance — peak doesn't match known fault frequencies",
            freq_hz=round(peak.freq, 2),
            expected_hz=None,
            axis=peak.axis,
            confidence=_score_confidence(factors, conf),
            confidence_evidence=factors,
            evidence=(
                f"Loud peak at {peak.freq:.1f} Hz on {peak.axis} doesn't match any shaft "
                "harmonic, bearing fault, belt, or blade pass frequency. Combined with elevated "
                "ISO severity. Possible resonance — requires bump test or modal analysis to "
                "confirm."
            ),
        )
    ]


# ─────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────


def run_rca(
    peak_set: PeakSet,
    machine: MachineMeta,
    iso_severity: IsoSeverityOrUnrated,
    thresholds: dict[str, Any],
    *,
    gate_warnings: bool = False,
    trend_rising: bool = False,
    flow_peak_set: PeakSet | None = None,
    iso_thresholds: MachineThresholds | None = None,
) -> RcaResult:
    """Run all seven detectors in the reference's exact priority order and
    adjudicate them into a committed differential:
      - `primary_findings`: the committed diagnosis (confidence-graded)
      - `differential`: candidates the evidence raised but the interaction
        rules set aside (suppressed/downgraded), each with its adjudication.

    `thresholds` is the full resolved profile dict (needs both the `rca` and
    `confidence` sections). `gate_warnings` / `trend_rising` are cross-cutting
    confidence inputs supplied by the pipeline.

    Session B (B1) — spectrum kind routing: `peak_set` is the ENVELOPE (bearing)
    peaks; `flow_peak_set` is the RAW/velocity peaks the 1x-family detectors read
    instead (imbalance / misalignment / looseness / belt / blade-pass / resonance),
    because a 1x line in an envelope spectrum is a demodulation artifact. When
    `flow_peak_set is None` the flow context REUSES the bearing context object
    (no rebuild, no filter), so the NCD path and any single-spectrum caller stay
    byte-identical to before B1.

    Session PDMFIX — `iso_thresholds`: the 3-tier resolver's output for this
    machine (`iso_classify.resolve_thresholds`). Feeds the 1x-family severity
    gate, which answers to the machine's own zone boundaries rather than to any
    ISO number written into a detector. Omit it and the gate is unarmed, so every
    pre-PDMFIX caller stays byte-identical.
    """
    rca_cfg = thresholds["rca"]
    conf_cfg = thresholds["confidence"]
    tolerance = rca_cfg.get("tolerance_pct", 5.0) / 100.0
    min_rpm = rca_cfg.get("min_rpm", 30)
    ratio_trigger = rca_cfg.get("axial_radial_ratio_trigger", 0.5)
    ratio_strong = rca_cfg.get("axial_radial_ratio_strong", 1.0)
    one_x_min_zone = rca_cfg.get("one_x_severity_min_zone")
    one_x_floor = rca_cfg.get("one_x_absolute_floor_mms")

    if peak_set.rpm < min_rpm:
        return RcaResult(status="machine_off", shaft_freq_hz=0.0, rpm=peak_set.rpm)

    floor_min = rca_cfg.get("floor_min")

    try:
        ctx_bearing = _build_context(
            peak_set, machine, tolerance, conf_cfg,
            gate_warnings=gate_warnings, trend_rising=trend_rising,
            floor_min=floor_min, iso_thresholds=iso_thresholds,
        )
        # The 1x-family detectors read the raw/velocity peaks. With no raw peaks,
        # reuse the bearing context object verbatim (the byte-identity guard).
        if flow_peak_set is None:
            ctx_flow = ctx_bearing
        else:
            ctx_flow = _build_context(
                flow_peak_set, machine, tolerance, conf_cfg,
                gate_warnings=gate_warnings, trend_rising=trend_rising,
                floor_min=floor_min, iso_thresholds=iso_thresholds,
            )

        primary: list[FaultMatch] = []
        differential: list[DifferentialCandidate] = []

        # Bearing faults read the ENVELOPE context; looseness (1x-family) reads flow.
        # Session B (B3): a matched bearing peak that collides with an integer shaft
        # order (within epsilon_sync) is synchronous-ambiguous — committed to the
        # differential at LOW rather than primary (the resolving envelope/time-waveform
        # capture is emitted by the recommendations engine). Guard disabled when the
        # profile carries no `epsilon_sync` (streaming) → byte-identical to pre-B3.
        # Session B (B4): after the collision guard, a committed match that sits
        # below the axis amplitude floor is likewise demoted to the differential.
        eps_sync = rca_cfg.get("epsilon_sync")
        for m in detect_bearing_faults(ctx_bearing):
            collision = _synchronous_collision(m, ctx_bearing.shaft_freq, eps_sync)
            if collision is not None:
                differential.append(collision)
                continue
            floored = _amplitude_floor(m, ctx_bearing, floor_min)
            if floored is not None:
                differential.append(floored)
                continue
            primary.append(m)
        looseness = detect_looseness(ctx_flow)
        primary.extend(looseness)
        loose_detected = bool(looseness)

        # Misalignment: committed on its own, but downgraded to the differential
        # when looseness co-occurs (looseness can manufacture similar 1×/2×).
        misalignment = detect_misalignment_family(ctx_flow, ratio_trigger, ratio_strong)
        # Session R3-DIFF (1b): before any interaction rule, the family must clear
        # the amplitude gate. A gate-rejected misalignment is NOT a competing
        # diagnosis that explains the 1× — it is a candidate positively argued
        # against — so it is tracked separately from the looseness downgrade and
        # is excluded from the imbalance suppressor set below.
        mis_gate = (
            _misalignment_amplitude_gate(
                ctx_flow,
                rca_cfg.get("misalignment_2x_1x_min"),
                rca_cfg.get("misalignment_axial_radial_min"),
            )
            if misalignment
            else None
        )
        misalignment_gated = mis_gate is not None
        if misalignment_gated:
            for m in misalignment:
                m.confidence_evidence.append(
                    _factor(
                        conf_cfg,
                        "interaction_downgrade",
                        "2×/1× amplitude ratio and axial energy both below the misalignment gate",
                    )
                )
                m.confidence = _score_confidence(m.confidence_evidence, conf_cfg)
                differential.append(
                    DifferentialCandidate(
                        fault=m.fault,
                        description=m.description,
                        confidence=m.confidence,
                        adjudication=mis_gate,
                    )
                )
        elif loose_detected and misalignment:
            for m in misalignment:
                m.confidence_evidence.append(
                    _factor(
                        conf_cfg,
                        "interaction_downgrade",
                        "Mechanical looseness present — can produce similar 1×/2× peaks",
                    )
                )
                m.confidence = _score_confidence(m.confidence_evidence, conf_cfg)
                differential.append(
                    DifferentialCandidate(
                        fault=m.fault,
                        description=m.description,
                        confidence=m.confidence,
                        adjudication=(
                            "Downgraded: mechanical looseness also detected, which can produce "
                            "similar 1×/2× peaks. Address looseness first, then re-measure."
                        ),
                    )
                )
        else:
            # Session PDMFIX — ruling (1). The severity gate is the LAST word on a
            # misalignment commit: a candidate the interaction rules were happy with
            # still has to be loud enough to be a finding. Gated candidates are
            # DROPPED, not demoted — a quiet machine is a resolved answer, not an
            # unresolved differential, and an "Also considered: misalignment" line on
            # every Zone-A report would re-create the false alarm in a new costume.
            for m in misalignment:
                if _one_x_severity_gate(m, ctx_flow, one_x_min_zone, one_x_floor) is None:
                    primary.append(m)

        # Imbalance: committed unless a stronger diagnosis explains the 1× energy.
        d_1x = rca_cfg.get("imbalance_1x_dominance", 1.0)
        imbalance = detect_imbalance(ctx_flow, rca_cfg.get("imbalance_radial_dominance"), d_1x)
        # Session R3-DIFF (1b), the "otherwise" half of the gate: once misalignment
        # has been positively argued against on shaft-order amplitude, the only
        # question left is whether the 1× radial line dominates — so the bar
        # imbalance answers to becomes the gate's own radial-dominance bar
        # (1 / misalignment_axial_radial_min = "radial ≥ 2× axial") rather than
        # imbalance_radial_dominance, which is calibrated for the much harder case
        # of committing imbalance with NO competitor examined. The intra-radial 1×
        # dominance condition is unchanged and still applies.
        axial_radial_min = rca_cfg.get("misalignment_axial_radial_min")
        if imbalance is None and misalignment_gated and axial_radial_min:
            imbalance = detect_imbalance(ctx_flow, 1.0 / axial_radial_min, d_1x)
        if imbalance is not None:
            # Session B (B6): a bearing suppresses imbalance ONLY when COMMITTED (in
            # primary — it passed the B3 collision guard and the B4 floor). A
            # bearing that was DEMOTED to the differential is itself uncertain, so it
            # must not silently override a clear 1× radial imbalance signature.
            # Looseness / misalignment keep the original primary-or-differential
            # reach (a looseness-downgraded misalignment still explains the 1×).
            seen_primary = {m.fault for m in primary}
            gated_faults = {m.fault for m in misalignment} if misalignment_gated else set()
            seen = seen_primary | ({d.fault for d in differential} - gated_faults)
            if any(f.startswith("bearing_") for f in seen_primary):
                suppressor = "a bearing fault"
            elif "mechanical_looseness" in seen:
                suppressor = "mechanical looseness"
            elif seen & _MISALIGNMENT_FAULTS:
                suppressor = "misalignment"
            else:
                suppressor = None
            if suppressor is not None:
                differential.append(
                    DifferentialCandidate(
                        fault="imbalance",
                        description=imbalance.description,
                        confidence=imbalance.confidence,
                        adjudication=f"Suppressed: {suppressor} explains the 1× energy on the radial axis.",
                    )
                )
            elif _one_x_severity_gate(imbalance, ctx_flow, one_x_min_zone, one_x_floor) is not None:
                # Session PDMFIX — ruling (1). Dropped, not demoted; see the
                # misalignment site above for why.
                pass
            else:
                primary.append(imbalance)
                # Session PDMFIX — ruling (2), the differential framing. A committed
                # imbalance with NO axial channel is one radial line standing in for a
                # two-term signature: the misalignment family it cannot separate itself
                # from is raised here with the missing measurement named. This mirrors
                # what the trio path already does, where the loser is adjudicated away
                # on a STATED ratio rather than silently dropped. The adjudication
                # prefix is the trigger `recommendations.py` keys on — the same idiom
                # as the B3 collision and B4 amplitude-floor guards.
                if not ctx_flow.axial_measured:
                    differential.append(
                        DifferentialCandidate(
                            fault="misalignment_general",
                            description=(
                                "Misalignment detected — sub-type not determinable from single sensor"
                            ),
                            confidence="low",
                            adjudication=(
                                f"Axial channel not measured: the axial axis ({ctx_flow.axial}) "
                                "carries no measurement, so an axial-dominant misalignment or bent "
                                "shaft cannot be separated from rotor imbalance on this radial "
                                "channel alone. The imbalance call is capped at medium confidence "
                                "for that reason."
                            ),
                        )
                    )

        # Session GEOM-B. Belt now answers to the same discipline as the families
        # it competes with. The two amplitude questions live INSIDE the detector
        # (they are questions about the matched peak, which only the detector
        # holds); the severity gate lives here, where every other severity gate
        # lives. Gated candidates are DROPPED, not demoted — PDMFIX ruling 1: a
        # quiet machine is a resolved answer, not an unresolved differential, and
        # an "Also considered: belt fault" line on every quiet belt-driven
        # machine would re-create the false alarm in a new costume.
        belt_dominance = rca_cfg.get("belt_dominance_min", 0.3)
        for m in detect_belt_fault(
            ctx_flow, floor_min=floor_min, dominance_min=belt_dominance
        ):
            if _one_x_severity_gate(
                m, ctx_flow, one_x_min_zone, one_x_floor, _BELT_CLOSING
            ) is None:
                primary.append(m)
        primary.extend(detect_blade_pass(ctx_flow, iso_severity))

        # Resonance is the last resort — only when nothing else was raised.
        if not primary and not differential:
            primary.extend(detect_resonance(ctx_flow, iso_severity))

        # Scalars (shaft/rpm/axial/geometry/velocity-ratio) are invariant between
        # the two contexts (same reading/velocities/machine) — report from the
        # always-built bearing context.
        return RcaResult(
            status="ok",
            shaft_freq_hz=round(ctx_bearing.shaft_freq, 2),
            rpm=ctx_bearing.rpm,
            axial_axis=ctx_bearing.axial,
            bearing_specs_present=ctx_bearing.bearing_freqs is not None,
            bearing_freqs=ctx_bearing.bearing_freqs,
            axial_radial_ratio=round(ctx_bearing.axial_radial_ratio, 2)
            if ctx_bearing.axial_radial_ratio is not None
            else None,
            primary_findings=primary,
            differential=differential,
        )
    except Exception as exc:  # defensive parity with the reference's catch-all
        return RcaResult(status="error", reason=str(exc))


def enrich_with_sidebands(
    result: RcaResult,
    spectra: dict[Axis, Spectrum],
    shaft_hz: float,
    tolerance: float,
    confidence_cfg: dict[str, Any] | None = None,
) -> RcaResult:
    """Additive post-pass (not in reference): annotate bearing-family
    primary findings with ±1× shaft sidebands found in the raw spectrum, when
    one was captured. Detectors themselves only ever see a PeakSet (Amendment
    A3) — this decorates an already-computed RcaResult with extra supporting
    evidence. When `confidence_cfg` is supplied, a found sideband adds a
    `sidebands_present` confidence factor and the match is re-scored (sidebands
    are corroborating evidence for a bearing fault); fault identity never
    changes. Pure: returns a new RcaResult rather than mutating the input.
    """
    if not spectra:
        return result

    new_primary: list[FaultMatch] = []
    for match in result.primary_findings:
        if not match.fault.startswith("bearing_") or match.freq_hz is None:
            new_primary.append(match)
            continue

        spectrum = spectra.get(match.axis)
        if spectrum is None or not spectrum.amplitude:
            new_primary.append(match)
            continue

        # Height floor relative to this axis's loudest peak, so broadband
        # noise-floor wiggles (which find_peaks would otherwise happily
        # report as "peaks") never register as sidebands — only genuine,
        # distinguishable spectral content does.
        height_floor = max(spectrum.amplitude) * 0.02
        peak_indices, peak_props = find_peaks(spectrum.amplitude, height=height_floor)
        amps = peak_props["peak_heights"]
        freqs_arr = spectrum.freq_hz

        # A sideband target is a single specific frequency (fundamental ±
        # shaft rate) — tolerance defines the MATCH window, not a set to
        # collect wholesale. Densely-spaced local maxima inside one window
        # are spectral texture around one physical feature, not distinct
        # sidebands; take the single loudest candidate per window (same
        # "best-match wins" convention every other detector in this module
        # uses), giving at most one sideband below and one above.
        sidebands: list[float] = []
        for target in (match.freq_hz - shaft_hz, match.freq_hz + shaft_hz):
            candidates = [
                i for i in range(len(peak_indices)) if _within(freqs_arr[peak_indices[i]], target, tolerance)
            ]
            if candidates:
                best_i = max(candidates, key=lambda i: amps[i])
                sidebands.append(freqs_arr[peak_indices[best_i]])
        sidebands.sort()
        if not sidebands:
            new_primary.append(match)
            continue

        update: dict[str, Any] = {"sidebands": sidebands}
        if confidence_cfg is not None:
            factors = list(match.confidence_evidence)
            factors.append(
                _factor(confidence_cfg, "sidebands_present", f"±1× shaft sidebands present ({len(sidebands)} found)")
            )
            update["confidence_evidence"] = factors
            update["confidence"] = _score_confidence(factors, confidence_cfg)
        new_primary.append(match.model_copy(update=update))

    return result.model_copy(update={"primary_findings": new_primary})
