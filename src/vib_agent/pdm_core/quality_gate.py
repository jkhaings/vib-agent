"""Data quality checks — always run before any other analysis layer.

Faithful port of reference/flows.json 'Baseline Filter' node: its DROP rules
(configuring, MOFF, invalid acceleration, machine-off) map to gate FAIL —
the reading is unusable. Its EVAL-ONLY rules (MOTION msg_type, startup
transient/window, RPM/temp/battery off-nominal, ISO Zone C/D) map to gate
WARN — the reading is usable for analysis, but Layer 2 must not absorb it
into the z-score baseline (train_baseline=False).

Additive (not in reference): metadata completeness, units plausibility, and
the spectrum-only checks (non-flat, spectral plateau, ski-slope, sensor
settling, AC-coupling rolloff, line-frequency content, speed sanity) — these
return "not_applicable" when no spectrum is supplied and are excluded from the
overall verdict.

Session DQ-FLAGS added the settling / AC-coupling / line-frequency checks,
graded the ski slope, and renamed `clipping` to `spectral_plateau` (it measures
a flat-topped spectrum, which is not what clipping does to one — see that
function). Every spectrum check states what it could NOT assess rather than
passing by default: `not_applicable` is a verdict here, not an omission.
"""

from __future__ import annotations

import math
import statistics
import time
from typing import Any

from vib_agent.models import (
    Check,
    LifecycleState,
    MachineMeta,
    QualityGateResult,
    Reading,
    SensorData,
    Spectrum,
)


def _metadata_completeness_check(machine: MachineMeta, sensor_data: SensorData) -> Check:
    has_speed = machine.rpm_nominal is not None or sensor_data.rpm is not None
    has_group_or_override = (
        machine.thresholds is not None
        or (machine.iso_group is not None and machine.iso_support is not None)
    )
    if has_speed and has_group_or_override:
        return Check(name="metadata_completeness", status="pass")
    missing = []
    if not has_speed:
        missing.append("speed (rpm_nominal or sensor rpm)")
    if not has_group_or_override:
        missing.append("iso_group/iso_support or thresholds override")
    return Check(
        name="metadata_completeness",
        status="fail",
        reason=f"missing: {', '.join(missing)}",
    )


def _units_plausibility_check(reading: Reading, thresholds: dict[str, Any]) -> Check:
    rms_min = thresholds.get("rms_sane_min_mm_s", 0.01)
    rms_max = thresholds.get("rms_sane_max_mm_s", 100.0)
    if reading.severity_rms is None:
        # Velocity was not measured (acceleration-only reading) -- classify()
        # already returned a not_assessable zone. Still WARN (so gate.overall
        # and the downstream RCA gate_warnings signal are unchanged from when
        # this branch fired on a 0.0-coerced severity_rms), but with an honest
        # reason instead of implying an implausibly-low measured velocity.
        return Check(
            name="units_plausibility",
            status="warn",
            reason="velocity not measured -- ISO severity not assessable (acceleration-only reading)",
        )
    if rms_min <= reading.severity_rms <= rms_max:
        return Check(name="units_plausibility", status="pass")
    return Check(
        name="units_plausibility",
        status="warn",
        reason=f"severity_rms {reading.severity_rms} outside sane range [{rms_min}, {rms_max}] mm/s",
    )


def _spectrum_non_flat_check(spectrum: Spectrum | None, thresholds: dict[str, Any]) -> Check:
    if spectrum is None or not spectrum.amplitude:
        return Check(name="spectrum_non_flat", status="not_applicable")
    min_var = thresholds.get("min_spectrum_variance", 1e-12)
    variance = statistics.pvariance(spectrum.amplitude) if len(spectrum.amplitude) > 1 else 0.0
    if variance >= min_var:
        return Check(name="spectrum_non_flat", status="pass")
    return Check(
        name="spectrum_non_flat",
        status="fail",
        reason=f"spectrum amplitude variance {variance:.3e} below minimum {min_var:.3e} — flat/dead signal",
    )


def _is_malformed(spectrum: Spectrum) -> bool:
    """freq_hz and amplitude disagree in length.

    Session DQ-FLAGS. Every shape question below pairs the two lists, and the
    pairing used `zip(..., strict=True)`, which RAISES on a mismatch — taking
    the whole gate down on untrusted upload input (`adapters/uploads/tabular.py`
    builds both lists from a user-supplied file). A malformed spectrum cannot
    support a shape question, so the checks return `not_applicable` and say so.
    """
    return len(spectrum.freq_hz) != len(spectrum.amplitude)


def _bin_spacing_hz(freq_hz: list[float]) -> float | None:
    """Median spacing between consecutive DISTINCT sorted bins, or None.

    Not `freq_hz[1] - freq_hz[0]`: bins are not guaranteed sorted or uniform —
    `adapters/uploads/tabular.py:88-95` passes a user-supplied frequency column
    through verbatim. The median is robust to a few irregular steps.
    """
    uniq = sorted({f for f in freq_hz if math.isfinite(f)})
    if len(uniq) < 2:
        return None
    # Deliberately not strict=True: the two sequences differ in length by one
    # by construction (pairwise stepping over a single list).
    return statistics.median(b - a for a, b in zip(uniq, uniq[1:]))


def _spectral_plateau_check(spectrum: Spectrum | None, thresholds: dict[str, Any]) -> Check:
    """A run of bins sharing one identical amplitude — a flat-topped spectrum.

    Session DQ-FLAGS renamed this from `clipping`, because it does not detect
    clipping and never did. Time-domain saturation produces harmonic distortion
    and a raised broadband floor, NOT a flat-topped spectral peak: hard-clipping
    a real MAFAULDA record to 94.8% rail occupancy (crest factor 3.41 -> 1.02)
    leaves this statistic at 1 — a clean `pass`. The discriminator that does
    track saturation is the time-domain crest factor, and the gate cannot see
    it (`SensorData.*_max_ACC_G` is None on every dataset file and a hardcoded
    multiple of RMS on every synthetic fixture), so the honest move is to name
    what is actually measured and stop advising a sensor-range change on it.

    What an identical-amplitude plateau does mean: the values in that region
    were written by something other than the measurement — a cap, a quantiser,
    or a synthetic fill — so amplitudes there cannot be read as measured.

    The config key stays `clipping_repeat_max_count`: `config/thresholds.json`
    is frozen this session, and the key's MEANING (how many identical bins are
    too many) is unchanged by the rename.
    """
    if spectrum is None or not spectrum.amplitude:
        return Check(name="spectral_plateau", status="not_applicable")
    if _is_malformed(spectrum):
        return Check(
            name="spectral_plateau",
            status="not_applicable",
            reason="spectrum malformed: the frequency and amplitude bin counts differ",
        )
    repeat_max = thresholds.get("clipping_repeat_max_count", 5)
    peak = max(spectrum.amplitude)
    repeat_count = sum(1 for v in spectrum.amplitude if v == peak)
    if repeat_count < repeat_max:
        return Check(name="spectral_plateau", status="pass")
    where = [f for f, a in zip(spectrum.freq_hz, spectrum.amplitude, strict=True) if a == peak]
    span = f"{min(where):.1f}-{max(where):.1f} Hz" if where else "across the spectrum"
    return Check(
        name="spectral_plateau",
        status="warn",
        reason=f"{repeat_count} bins share the identical amplitude {peak} ({span}) — a flat-topped "
        "spectrum; amplitudes in that region were not measured freely and cannot be read as "
        "true levels",
    )


def _ski_slope_check(
    spectrum: Spectrum | None, sensor_data: SensorData, thresholds: dict[str, Any]
) -> Check:
    """Low-frequency energy dominating the spectrum — the ski-slope artifact.

    Session DQ-FLAGS added a SEVERE class. By Parseval the artifact inflates the
    measured broadband level by 1/sqrt(1-ratio): at ratio 0.90 that is 3.16x,
    which is the full ISO 20816-3 A/B -> C/D span (7.1/2.3 = 3.09x group 1
    rigid, 4.5/1.4 = 3.21x group 2 rigid). In other words 0.90 is precisely the
    point where the artifact ALONE can account for an entire Zone-A-to-Zone-D
    escalation, so amplitudes stop being caveatable and become unusable. Below
    it, at the existing 0.5 trigger, the inflation is 1.41x — less than the
    narrowest zone step (1.58x) — which is why that class stays a caveat.

    The severe class is nested INSIDE the existing warn branch, so it cannot
    change any status: it only grades a reading that already warns.
    """
    if spectrum is None or not spectrum.amplitude:
        return Check(name="ski_slope", status="not_applicable")
    if _is_malformed(spectrum):
        return Check(
            name="ski_slope",
            status="not_applicable",
            reason="spectrum malformed: the frequency and amplitude bin counts differ",
        )
    band_hz = thresholds.get("ski_slope_low_freq_band_hz", 5.0)
    max_ratio = thresholds.get("ski_slope_low_freq_energy_ratio", 0.5)
    total_energy = sum(a * a for a in spectrum.amplitude)
    if total_energy <= 0:
        return Check(name="ski_slope", status="not_applicable")
    top_hz = max(spectrum.freq_hz) if spectrum.freq_hz else 0.0
    if top_hz <= band_hz:
        # The "low-frequency band" is the whole spectrum, so the ratio is 1.0 by
        # construction and says nothing about a slope. A zoom capture below the
        # band edge is a legitimate measurement, not an artifact.
        return Check(
            name="ski_slope",
            status="not_applicable",
            reason="spectrum ends at or below the low-frequency band — no out-of-band "
            "content to compare against",
        )
    # The lower bound is deliberate: a two-sided export can carry negative
    # frequencies, which would otherwise all count as "low-frequency" energy.
    in_band = [
        (f, a) for f, a in zip(spectrum.freq_hz, spectrum.amplitude, strict=True)
        if 0.0 <= f <= band_hz
    ]
    low_energy = sum(a * a for _, a in in_band)
    ratio = low_energy / total_energy
    if ratio <= max_ratio:
        return Check(name="ski_slope", status="pass")

    severe_ratio = thresholds.get("ski_slope_severe_energy_ratio", 0.90)
    min_band_bins = thresholds.get("ski_slope_severe_min_band_bins", 3)
    shaft_hz = (
        sensor_data.rpm / 60.0
        if sensor_data.rpm is not None and math.isfinite(sensor_data.rpm)
        else None
    )
    # A slow machine's own 1x lives inside the band, so its energy is content,
    # not artifact, and must never be graded severe. k=2 puts 1x at twice the
    # band edge (10 Hz for a 5 Hz band) before the severe class is available.
    shaft_clear = shaft_hz is not None and shaft_hz >= 2 * band_hz
    # A slope needs at least three points; below that the ratio is a statement
    # about one offset bin, which is meaningless on a mean-removed spectrum.
    enough_bins = len(in_band) >= min_band_bins
    if ratio >= severe_ratio and enough_bins and shaft_clear:
        return Check(
            name="ski_slope",
            status="warn",
            reason=f"{ratio:.0%} of spectrum energy below {band_hz} Hz — SEVERE ski-slope artifact "
            f"(loose mount/cabling); it inflates the broadband level by "
            f"{1.0 / math.sqrt(1.0 - min(ratio, 0.999)):.1f}x on its own, so amplitudes and any "
            "ISO zone derived from them are not usable on this reading",
        )
    return Check(
        name="ski_slope",
        status="warn",
        reason=f"{ratio:.0%} of spectrum energy below {band_hz} Hz — possible ski-slope artifact (loose mount/cabling)",
    )


def _sorted_pairs(spectrum: Spectrum) -> list[tuple[float, float]]:
    """(freq, amplitude) at non-negative finite frequencies, sorted by frequency.

    Sorting is not cosmetic: an upload's frequency column arrives verbatim, so
    "the first bins" is only "the lowest frequencies" after this.
    """
    return sorted(
        (f, a)
        for f, a in zip(spectrum.freq_hz, spectrum.amplitude, strict=True)
        if math.isfinite(f) and math.isfinite(a) and f >= 0.0
    )


def _sensor_settling_check(spectrum: Spectrum | None, thresholds: dict[str, Any]) -> Check:
    """A steep monotonic decay away from DC — a sensor still settling.

    Distinct from `ski_slope` by construction: that check is an aggregate ENERGY
    RATIO below a band edge and carries no notion of shape, so a broadband
    low-frequency rumble and a settling transient score identically there. This
    one asks only about SHAPE — does the very bottom of the spectrum fall away
    monotonically, steeply, and from a level well above the broadband floor —
    which is what a charge amplifier or a freshly-mounted sensor does while it
    settles, and what a loose-mount rattle (discrete humps) does not.

    Three conditions must hold together, and the depth condition is what keeps a
    FLAT spectrum out: equal bins are trivially "non-increasing", so a dead
    sensor would otherwise read as a perfect settling decay.
    """
    if spectrum is None or not spectrum.amplitude:
        return Check(name="sensor_settling", status="not_applicable")
    if _is_malformed(spectrum):
        return Check(
            name="sensor_settling",
            status="not_applicable",
            reason="spectrum malformed: the frequency and amplitude bin counts differ",
        )
    window_bins = thresholds.get("sensor_settling_window_bins", 12)
    min_decay_bins = thresholds.get("sensor_settling_min_decay_bins", 8)
    depth_frac = thresholds.get("sensor_settling_decay_depth_frac", 0.5)
    floor_mult = thresholds.get("sensor_settling_floor_mult", 10.0)

    pairs = _sorted_pairs(spectrum)
    if len(pairs) < window_bins:
        return Check(
            name="sensor_settling",
            status="not_applicable",
            reason="too few usable bins to judge the shape of the low-frequency region",
        )
    df = _bin_spacing_hz(spectrum.freq_hz)
    if df is None or df <= 0:
        return Check(
            name="sensor_settling",
            status="not_applicable",
            reason="bin spacing could not be determined",
        )
    # The settling signature lives AT the bottom of the spectrum. If the lowest
    # measured bin is not adjacent to DC, the region was never captured, and
    # "the maximum is at the first bin" would only mean "the maximum is at Fmin".
    f_first = pairs[0][0]
    if f_first > 2 * df:
        return Check(
            name="sensor_settling",
            status="not_applicable",
            reason="lowest measured bin is not adjacent to DC — the settling region "
            "was not captured",
        )

    window = pairs[:window_bins]
    amps = [a for _, a in window]
    if amps[0] <= 0:
        return Check(name="sensor_settling", status="pass")
    # Leading non-increasing run, broken at the first non-positive bin so a
    # blanked/zeroed tail below Fmin cannot masquerade as a decay.
    run = 1
    for i in range(1, len(amps)):
        if amps[i] <= 0 or amps[i] > amps[i - 1]:
            break
        run += 1
    decayed_enough = amps[run - 1] <= depth_frac * amps[0]
    broadband = statistics.median(a for _, a in pairs)
    stands_out = broadband > 0 and amps[0] >= floor_mult * broadband
    if run >= min_decay_bins and decayed_enough and stands_out:
        return Check(
            name="sensor_settling",
            status="warn",
            reason=f"amplitude falls monotonically over the first {run} bins from "
            f"{f_first:.2f} Hz, down to {amps[run - 1] / amps[0]:.0%} of the lowest bin "
            f"({amps[0] / broadband:.0f}x the broadband level) — the signature of a sensor "
            "still settling during capture; low-frequency amplitudes are inflated",
        )
    return Check(name="sensor_settling", status="pass")


def _ac_coupling_rolloff_check(
    spectrum: Spectrum | None, sensor_data: SensorData, thresholds: dict[str, Any]
) -> Check:
    """The running speed sits at or below the AC-coupling corner.

    This check exists in a narrow form on purpose. Industrial accelerometers are
    AC-coupled BY DESIGN, so a spectrum with no content below ~1 Hz is CORRECT,
    not defective — measured across all 61 MAFAULDA files, sub-5 Hz energy is
    0.04% of the total, and a naive "low-frequency content is missing" check
    would have warned on every one of them. Missing content only matters when it
    is content the analysis NEEDS, so this warns only when the machine's own 1x
    line falls where the coupling attenuates it.

    The corner default is 0.5 Hz because that is the -3 dB corner of the
    general-purpose IEPE class; at 2 Hz such a sensor is already flat (|H| =
    0.97, -0.26 dB), so a 2 Hz default would assert attenuation that is not
    happening.
    """
    if spectrum is None or not spectrum.amplitude:
        return Check(name="ac_coupling_rolloff", status="not_applicable")
    if _is_malformed(spectrum):
        return Check(
            name="ac_coupling_rolloff",
            status="not_applicable",
            reason="spectrum malformed: the frequency and amplitude bin counts differ",
        )
    corner_hz = thresholds.get("ac_coupling_corner_hz", 0.5)
    if sensor_data.rpm is None or not math.isfinite(sensor_data.rpm):
        return Check(
            name="ac_coupling_rolloff",
            status="not_applicable",
            reason="running speed not supplied — cannot tell whether the coupling attenuates 1x",
        )
    shaft_hz = sensor_data.rpm / 60.0
    if shaft_hz <= 0:
        return Check(name="ac_coupling_rolloff", status="not_applicable")
    df = _bin_spacing_hz(spectrum.freq_hz)
    if df is None or df >= shaft_hz:
        # At this resolution the 1x line is unresolvable whatever the coupling
        # does; blaming sensor electronics for a resolution limit would be wrong.
        return Check(
            name="ac_coupling_rolloff",
            status="not_applicable",
            reason="bin spacing cannot resolve a running speed this low",
        )
    pairs = _sorted_pairs(spectrum)
    if not pairs:
        return Check(name="ac_coupling_rolloff", status="not_applicable")
    f_low = pairs[0][0]
    if f_low > shaft_hz:
        # The 1x line is outside the acquired band: ABSENT, not attenuated. That
        # is a capture-range question (speed_sanity's), and claiming a rolloff
        # percentage here would state a measurement that was never made.
        return Check(
            name="ac_coupling_rolloff",
            status="not_applicable",
            reason="running speed is below the lowest measured bin — the 1x line is "
            "outside the acquired band, not attenuated",
        )
    if shaft_hz > corner_hz:
        return Check(name="ac_coupling_rolloff", status="pass")
    # Single-pole high-pass magnitude at the shaft rate.
    attenuation = shaft_hz / math.sqrt(shaft_hz * shaft_hz + corner_hz * corner_hz)
    return Check(
        name="ac_coupling_rolloff",
        status="warn",
        reason=f"running speed {shaft_hz:.2f} Hz is at or below the assumed {corner_hz} Hz "
        f"AC-coupling corner — a true 1x line is measured at about {attenuation:.0%} of its real "
        "amplitude, so 1x-family amplitudes read low on this reading",
    )


def _line_frequency_content_check(
    spectrum: Spectrum | None, sensor_data: SensorData, thresholds: dict[str, Any]
) -> Check:
    """A discrete line sitting on a nominal supply frequency that is not a shaft order.

    Deliberately DESCRIPTIVE: it reports what is in the spectrum and does not
    claim the line is electrical. It cannot: separating supply pickup from a
    neighbouring machine's 1x, a belt line, or a resonance needs the declared
    line frequency and pole count, or slip sidebands, and none of those exist on
    `MachineMeta`/`SensorData` today. The reason says so.

    Two consequences of that honesty, both deliberate:

    * Only the FUNDAMENTAL supply frequencies are examined. 2x line frequency
      (100/120 Hz) is rotor-bar / eccentricity / soft-foot physics — a DIAGNOSIS,
      not a data-quality artifact — and routing it through the gate would both
      mislabel an actionable electrical fault as cabling and downgrade every
      unrelated finding's confidence.
    * When every candidate line coincides with a shaft order, the check returns
      `not_applicable`, never `pass`. On a line-fed induction motor this is the
      normal outcome: shaft = LF/(p/2)*(1-s), so order p/2 sits exactly slip%
      away from 1x line frequency, and slip is 0-3% — inside any sane order
      tolerance. Passing there would claim the question was answered when the
      evidence cannot answer it (the file's own precedent at the
      `iso_zone_elevated` branch).
    """
    if spectrum is None or not spectrum.amplitude:
        return Check(name="line_frequency_content", status="not_applicable")
    if _is_malformed(spectrum):
        return Check(
            name="line_frequency_content",
            status="not_applicable",
            reason="spectrum malformed: the frequency and amplitude bin counts differ",
        )
    candidates = thresholds.get("line_frequency_candidates_hz", [50.0, 60.0])
    drift_hz = thresholds.get("line_frequency_drift_hz", 0.25)
    search_hz = thresholds.get("line_frequency_search_hz", 0.5)
    prominence = thresholds.get("line_frequency_prominence_ratio", 10.0)
    order_tol_pct = thresholds.get("line_frequency_order_tolerance_pct", 3.0)
    max_order = thresholds.get("line_frequency_max_shaft_order", 12)

    if sensor_data.rpm is None or not math.isfinite(sensor_data.rpm):
        return Check(
            name="line_frequency_content",
            status="not_applicable",
            reason="running speed not supplied — a line cannot be separated from a shaft order",
        )
    shaft_hz = sensor_data.rpm / 60.0
    if shaft_hz <= 0:
        return Check(name="line_frequency_content", status="not_applicable")
    df = _bin_spacing_hz(spectrum.freq_hz)
    if df is None or df > drift_hz:
        return Check(
            name="line_frequency_content",
            status="not_applicable",
            reason="bin spacing is coarser than the supply-frequency tolerance — a line "
            "cannot be located precisely enough",
        )
    pairs = _sorted_pairs(spectrum)
    broadband = statistics.median(a for _, a in pairs) if pairs else 0.0
    if broadband <= 0:
        return Check(name="line_frequency_content", status="not_applicable")

    def on_shaft_order(freq: float) -> bool:
        return any(
            abs(freq - k * shaft_hz) / freq * 100.0 <= order_tol_pct
            for k in range(1, int(max_order) + 1)
        )

    assessable = [lf for lf in candidates if not on_shaft_order(lf)]
    if not assessable:
        return Check(
            name="line_frequency_content",
            status="not_applicable",
            reason="every candidate supply frequency coincides with a shaft order on this "
            "machine — supply content and machine content are not separable on this reading",
        )

    hits: list[tuple[float, float, float]] = []
    for lf in assessable:
        window = [
            (i, f, a)
            for i, (f, a) in enumerate(pairs)
            if abs(f - lf) <= search_hz and 0 < i < len(pairs) - 1
        ]
        # A discrete line is a STRICT local maximum. A flat-topped plateau has
        # none by definition, which keeps a capped export out of this check
        # without depending on how ties happen to be broken.
        peaks = [(f, a) for i, f, a in window if a > pairs[i - 1][1] and a > pairs[i + 1][1]]
        if not peaks:
            continue
        f_peak, a_peak = max(peaks, key=lambda t: t[1])
        if abs(f_peak - lf) > drift_hz:
            continue
        ratio = a_peak / broadband
        if ratio >= prominence:
            hits.append((lf, f_peak, ratio))
    if not hits:
        skipped = [lf for lf in candidates if lf not in assessable]
        if skipped:
            # Partial coverage. A bare `pass` would read as "both supply
            # frequencies were checked and both were clean", which is not what
            # happened — the skipped one is indistinguishable from a shaft order
            # on this machine, so it was never assessed.
            return Check(
                name="line_frequency_content",
                status="pass",
                reason="no discrete line at the supply frequencies that could be assessed; "
                "the remainder coincide with a shaft order on this machine and were not assessed",
            )
        return Check(name="line_frequency_content", status="pass")
    lf, f_peak, ratio = max(hits, key=lambda t: t[2])
    return Check(
        name="line_frequency_content",
        status="warn",
        reason=f"discrete line at {f_peak:.2f} Hz, {ratio:.0f}x the broadband level, on the nominal "
        f"{lf:g} Hz supply frequency and not within {order_tol_pct}% of any 1-{int(max_order)}x shaft "
        f"order ({shaft_hz:.2f} Hz). Origin not established from this reading — the supply frequency "
        "was not declared, and separating supply pickup from machine content needs that plus the "
        "pole count or slip sidebands",
    )


def _speed_sanity_check(
    spectrum: Spectrum | None, sensor_data: SensorData, thresholds: dict[str, Any]
) -> Check:
    if spectrum is None or not spectrum.freq_hz or sensor_data.rpm is None:
        return Check(name="speed_sanity", status="not_applicable")
    shaft_hz = sensor_data.rpm / 60.0
    if shaft_hz <= 0:
        return Check(name="speed_sanity", status="not_applicable")
    tol_pct = thresholds.get("speed_peak_tolerance_pct", 5.0)
    tol_hz = shaft_hz * tol_pct / 100.0
    found = any(abs(f - shaft_hz) <= tol_hz for f in spectrum.freq_hz)
    if found:
        return Check(name="speed_sanity", status="pass")
    return Check(
        name="speed_sanity",
        status="warn",
        reason=f"no spectral content within ±{tol_pct}% of stated running speed ({shaft_hz:.1f} Hz)",
    )


def run_quality_gate(
    reading: Reading,
    sensor_data: SensorData,
    machine: MachineMeta,
    thresholds: dict[str, Any],
    *,
    lifecycle: LifecycleState | None = None,
    battery_percent: float | None = None,
    spectrum: Spectrum | None = None,
    now_ms: float | None = None,
) -> QualityGateResult:
    """Run all data-quality checks and decide whether Layer 2 may train its
    baseline on this reading (train_baseline).

    `thresholds` is the resolved 'quality_gate' section of config/thresholds.json
    (see vib_agent.config.load_thresholds()['quality_gate']) — pdm_core never
    reads config files itself.
    """
    lifecycle = lifecycle or LifecycleState()
    checks: list[Check] = []
    train_reasons: list[str] = []
    hard_fail = False

    # ---- DROP-equivalent checks: reading is unusable ----
    if lifecycle.configuring:
        checks.append(
            Check(name="configuring", status="fail", reason="sensor is being configured (PGM/ACK seen, no RUN yet)")
        )
        hard_fail = True
    else:
        checks.append(Check(name="configuring", status="pass"))

    if sensor_data.msg_type.upper() == "MOFF":
        checks.append(Check(name="msg_type", status="fail", reason="MOFF message type"))
        hard_fail = True
    else:
        checks.append(Check(name="msg_type", status="pass"))

    accel = {"x": sensor_data.x_rms_ACC_G, "y": sensor_data.y_rms_ACC_G, "z": sensor_data.z_rms_ACC_G}
    invalid_axes = [ax for ax, v in accel.items() if v is not None and not math.isfinite(v)]
    if invalid_axes:
        checks.append(
            Check(
                name="acceleration_data_validity",
                status="fail",
                reason=f"non-finite acceleration on axes: {invalid_axes}",
            )
        )
        hard_fail = True
    else:
        checks.append(Check(name="acceleration_data_validity", status="pass"))

    accel_vals = {ax: (v if v is not None and math.isfinite(v) else 0.0) for ax, v in accel.items()}
    min_running_g = thresholds.get("min_running_g", 0.010)
    if not hard_fail and max(accel_vals.values()) < min_running_g:
        checks.append(
            Check(
                name="machine_running",
                status="fail",
                reason=f"all axes below min_running_g ({min_running_g} g) — machine appears off",
            )
        )
        hard_fail = True
    else:
        checks.append(Check(name="machine_running", status="pass"))

    # ---- EVAL-ONLY checks: reading usable, but skip baseline training ----
    if sensor_data.msg_type.upper() == "MOTION":
        checks.append(Check(name="msg_type_motion", status="warn", reason="MOTION message type"))
        train_reasons.append("motion_msg_type")
    else:
        checks.append(Check(name="msg_type_motion", status="pass"))

    startup_transient_count = thresholds.get("startup_transient_count", 2)
    if lifecycle.startup_counter <= startup_transient_count:
        checks.append(
            Check(
                name="startup_transient",
                status="warn",
                reason=f"startup transient ({lifecycle.startup_counter}/{startup_transient_count})",
            )
        )
        train_reasons.append(f"startup_transient({lifecycle.startup_counter}/{startup_transient_count})")
    else:
        checks.append(Check(name="startup_transient", status="pass"))

    startup_window_min = thresholds.get("startup_window_min", 15)
    now_ms = now_ms if now_ms is not None else time.time() * 1000
    if lifecycle.last_start_ts_ms is not None and (now_ms - lifecycle.last_start_ts_ms) < startup_window_min * 60000:
        checks.append(Check(name="startup_window", status="warn", reason="within post-start startup window"))
        train_reasons.append("startup_window")
    else:
        checks.append(Check(name="startup_window", status="pass"))

    if machine.rpm_nominal is not None:
        rpm_actual = sensor_data.rpm if sensor_data.rpm is not None and math.isfinite(sensor_data.rpm) else 0.0
        tol = machine.rpm_tolerance_pct
        rpm_min = machine.rpm_nominal * (1 - tol / 100)
        rpm_max = machine.rpm_nominal * (1 + tol / 100)
        if not (rpm_min <= rpm_actual <= rpm_max):
            checks.append(
                Check(
                    name="rpm_nominal",
                    status="warn",
                    reason=f"rpm {rpm_actual} outside {rpm_min:.1f}-{rpm_max:.1f} (nominal {machine.rpm_nominal} ±{tol}%)",
                )
            )
            train_reasons.append(f"rpm_off_nominal({rpm_actual}vs{machine.rpm_nominal}±{tol}%)")
        else:
            checks.append(Check(name="rpm_nominal", status="pass"))
    else:
        checks.append(Check(name="rpm_nominal", status="not_applicable"))

    if machine.temp_min_c is not None and machine.temp_max_c is not None:
        temp = sensor_data.temperature
        if temp is not None and not (machine.temp_min_c <= temp <= machine.temp_max_c):
            checks.append(
                Check(
                    name="temperature_range",
                    status="warn",
                    reason=f"temperature {temp}°C outside [{machine.temp_min_c}, {machine.temp_max_c}]",
                )
            )
            train_reasons.append(f"temp_out_of_range({temp}°C)")
        else:
            checks.append(Check(name="temperature_range", status="pass"))
    else:
        checks.append(Check(name="temperature_range", status="not_applicable"))

    if reading.iso_zone == "not_assessable":
        # No velocity to place the reading in a zone -- this check has nothing to
        # assess (not "pass": passing would imply a non-elevated zone was found).
        checks.append(
            Check(
                name="iso_zone_elevated",
                status="not_applicable",
                reason="ISO zone not assessable -- velocity not measured",
            )
        )
    elif reading.iso_zone in ("C", "D"):
        checks.append(Check(name="iso_zone_elevated", status="warn", reason=f"ISO zone {reading.iso_zone}"))
        train_reasons.append(f"iso_zone_{reading.iso_zone}")
    else:
        checks.append(Check(name="iso_zone_elevated", status="pass"))

    if machine.min_battery_pct is not None:
        if battery_percent is not None and math.isfinite(battery_percent) and battery_percent < machine.min_battery_pct:
            checks.append(
                Check(
                    name="battery_level",
                    status="warn",
                    reason=f"battery {battery_percent}% below {machine.min_battery_pct}%",
                )
            )
            train_reasons.append(f"battery_low({battery_percent:.1f}%)")
        else:
            checks.append(Check(name="battery_level", status="pass"))
    else:
        checks.append(Check(name="battery_level", status="not_applicable"))

    # ---- Additive checks (not in reference) ----
    checks.append(_metadata_completeness_check(machine, sensor_data))
    checks.append(_units_plausibility_check(reading, thresholds))
    checks.append(_spectrum_non_flat_check(spectrum, thresholds))
    checks.append(_spectral_plateau_check(spectrum, thresholds))
    checks.append(_ski_slope_check(spectrum, sensor_data, thresholds))
    checks.append(_sensor_settling_check(spectrum, thresholds))
    checks.append(_ac_coupling_rolloff_check(spectrum, sensor_data, thresholds))
    checks.append(_line_frequency_content_check(spectrum, sensor_data, thresholds))
    checks.append(_speed_sanity_check(spectrum, sensor_data, thresholds))

    if hard_fail or any(c.status == "fail" for c in checks):
        overall = "fail"
    elif train_reasons or any(c.status == "warn" for c in checks):
        overall = "warn"
    else:
        overall = "pass"

    train_baseline = overall != "fail" and not train_reasons

    return QualityGateResult(
        overall=overall,
        checks=checks,
        train_baseline=train_baseline,
        train_reasons=train_reasons,
    )
