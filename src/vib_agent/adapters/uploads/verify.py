"""Session G: the verification gate for recipe-parsed data (MANDATORY).

An inferred layout is a hypothesis. This module tests the hypothesis against
the data it produced, before any diagnosis is attempted:

  * the frequency axis really is an axis (monotonic, non-degenerate);
  * its bins really are bins (consistent spacing);
  * the amplitudes are finite, non-negative, and not absurd;
  * there is running-speed content where the stated RPM says there should be.

A failure here is not an error card — it is an honest stop: the caller turns
these Checks into the same forced quality-gate failure Session E uses for
cross-file integrity, so the analyst gets an insufficient-data report naming
what went wrong, and no diagnosis is made on a misread file.

Pure functions, no I/O, no LLM. Tolerances are injected (config/webapp.json
`inference`), never hardcoded — and they are intake tolerances, deliberately
NOT part of config/thresholds.json, which is frozen detector calibration.
"""

from __future__ import annotations

import math
from typing import Any

from vib_agent.models import Case, Check

# The running-speed check accepts content at 1x, 2x or 3x shaft: plenty of
# route points are dominated by a harmonic rather than the fundamental.
_SPEED_HARMONICS = (1, 2, 3)


def _finite(values: list[float]) -> list[float]:
    return [v for v in values if math.isfinite(v)]


def _check(name: str, status: str, reason: str) -> Check:
    return Check(name=name, status=status, reason=reason)  # type: ignore[arg-type]


def _monotonic_axis(freqs: list[float]) -> Check:
    name = "inferred_axis_monotonic"
    if len(freqs) < 8:
        return _check(name, "fail", "The interpreted frequency axis has too few points to analyse.")
    backwards = sum(1 for a, b in zip(freqs, freqs[1:]) if b <= a)
    if backwards:
        return _check(
            name, "fail",
            "The column read as frequency does not increase down the file, so it is not a "
            "frequency axis — the file was probably interpreted with the wrong columns.",
        )
    if freqs[-1] <= freqs[0]:
        return _check(name, "fail", "The interpreted frequency axis does not span a range.")
    return _check(name, "pass", "")


def _bin_consistency(freqs: list[float], *, max_ratio: float) -> Check:
    """FFT bins are uniform. A ragged axis means the column is something else
    (a log-scaled plot export, an order list, a mis-picked column)."""
    name = "inferred_bin_consistency"
    steps = [b - a for a, b in zip(freqs, freqs[1:])]
    steps = [s for s in steps if s > 0]
    if len(steps) < 4:
        return _check(name, "fail", "The interpreted frequency axis has too few steps to check.")
    steps_sorted = sorted(steps)
    median = steps_sorted[len(steps_sorted) // 2]
    if median <= 0:
        return _check(name, "fail", "The interpreted frequency axis has no usable bin spacing.")
    if steps_sorted[-1] / median > max_ratio:
        return _check(
            name, "warn",
            "The interpreted frequency axis has uneven spacing, so bin-to-bin comparisons are "
            "less reliable than for a standard FFT export.",
        )
    return _check(name, "pass", "")


def _amplitude_plausibility(amps: list[float], *, max_abs: float) -> Check:
    name = "inferred_amplitude_range"
    finite = _finite(amps)
    if len(finite) < len(amps):
        return _check(name, "fail", "The column read as amplitude contains values that are not numbers.")
    if not finite or all(v == 0 for v in finite):
        return _check(name, "fail", "Every interpreted amplitude is zero — the wrong column was read, "
                                    "or the file carries no signal.")
    if any(v < 0 for v in finite):
        return _check(name, "fail", "The column read as amplitude contains negative values, so it is "
                                    "not a spectrum amplitude column.")
    if max(finite) > max_abs:
        return _check(name, "fail", f"Interpreted amplitudes reach {max(finite):.3g}, far outside any "
                                    "plausible vibration reading — the units or the column are wrong.")
    return _check(name, "pass", "")


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else 0.0


def _axis_resolution(freqs: list[float], rpm: float, *, min_bins_per_order: float) -> Check:
    """Can the interpreted axis resolve running speed at all?

    This is the INFLATION half of the unit-error class, and it was the missing
    half. `_speed_presence` catches an axis that has been compressed (Hz read as
    CPM leaves nothing at 30 Hz, so the axis never reaches the shaft line and it
    fails). The opposite error — a CPM axis read as Hz, the same 60x mistake
    with the sign flipped — sails past it, because the inflated axis still spans
    the shaft frequency; it simply has one enormous bin sitting on top of it.
    Worse, `_speed_presence` widens its own tolerance to 1.5x the bin step, so
    the coarser the misread the wider the window it accepts, and the check
    quietly downgrades itself to a warning on exactly the files it should refuse.

    So this asks the question the other check cannot: are the bins fine enough
    for 1x to be a peak rather than a smear? Every diagnosis this product makes
    is an order-domain claim (1x, 2x, BPFO at 3.57x), and none of them mean
    anything on an axis whose bins are wider than a shaft order.

    Threshold justified by measurement, not taste: across every corpus that
    reaches this gate, correctly-read files land between 6 and 60 bins per order
    (6 is the coarsest, from a waveform's own FFT). The misreads measured at 1.0
    and 0.6. The default of 2.0 sits between them with a 3x margin below the
    tightest good file. (INTAKE-HARDEN, fixture decimal_comma_cpm.txt read as Hz.)
    """
    name = "inferred_axis_resolution"
    shaft = rpm / 60.0
    steps = [b - a for a, b in zip(freqs, freqs[1:]) if b > a]
    step = _median(steps) if steps else 0.0
    if shaft <= 0 or step <= 0:
        return _check(name, "pass", "")     # nothing to judge; other checks cover it
    bins_per_order = shaft / step
    if bins_per_order < min_bins_per_order:
        return _check(
            name, "fail",
            f"The interpreted frequency axis has bins {step:.4g} Hz wide, which is coarser than "
            f"the running speed itself ({shaft:.2f} Hz) — 1x cannot be told from 2x on an axis "
            "like this, so no order-based diagnosis is possible. The usual cause is a CPM axis "
            "read as Hz (a 60x error) or the wrong decimal convention.",
        )
    return _check(name, "pass", "")


def _velocity_plausibility(overall: float | None, *, max_mm_s: float) -> Check:
    """Is the overall this reading claims a vibration level a machine can have?

    Only asked when the interpretation carries a VELOCITY unit, i.e. exactly
    when an ISO 20816 severity claim is on the table — an acceleration or
    displacement reading has no overall to judge and no severity to get wrong.

    This is the backstop for the whole family of scale errors: a dB-scaled
    spectrum declared as mm/s (a 41.6 dB noise floor reads as 41.6 mm/s, and the
    overall comes out at 940), a decimal-convention error (258,535), a
    displacement column labelled velocity. None of these look wrong bin by bin —
    the spectrum keeps its shape, the 1x line stays put, and `max_amplitude` at
    1e6 is three orders of magnitude too loose to notice.

    The number is anchored to the standard rather than chosen: the highest zone
    boundary in config/iso_zones.json is 11.0 mm/s (group 1, flexible, C/D), and
    Zone D means "damage is occurring". The default ceiling is 150 mm/s — about
    13x that, so a genuinely disintegrating machine still gets a diagnosis —
    while the cheapest misread observed was 940. The honest cost is stated
    plainly: a real machine above the ceiling gets an insufficient-data report
    naming this check instead of a diagnosis. That is the right side to err on,
    because this gate only ever runs on an INFERRED interpretation, where the
    prior that an impossible number is a misread vastly exceeds the prior that
    it is a measurement.
    """
    name = "inferred_velocity_plausibility"
    if overall is None:
        return _check(name, "not_applicable",
                      "No velocity reading was interpreted, so no severity claim is made.")
    if overall > max_mm_s:
        return _check(
            name, "fail",
            f"Read this way the file gives an overall vibration of {overall:.4g} mm/s RMS. "
            "ISO 20816-3's highest zone boundary is 11 mm/s, so a reading of this size is a "
            "unit, scale or decimal-convention error in how the file was read — not a machine "
            "condition. Check the amplitude units and the decimal convention.",
        )
    return _check(name, "pass", "")


def _speed_presence(freqs: list[float], amps: list[float], rpm: float, *, tol_pct: float,
                    presence_ratio: float, band_harmonics: tuple[int, ...] = _SPEED_HARMONICS) -> Check:
    """Is there running-speed content where the stated speed says it should be?

    Two deliberately different verdicts, because they are two different facts:

    FAIL — the interpreted axis does not even REACH the shaft frequency (or its
      2nd/3rd harmonic). That is a unit error, not a quiet machine: an axis read
      as CPM when it was Hz is a 60x error that leaves nothing at 30 Hz at all.
      This is the failure mode that turns a good file into a confident wrong
      answer, and it is worth stopping for.

    WARN — the axis covers the shaft frequency but nothing stands above the
      noise floor there. A high-passed acceleration export can legitimately look
      like this, so we say we could not confirm the axis rather than refusing to
      analyse a file that may be perfectly good.

    Note what is NOT required: that 1x be the DOMINANT peak. On a bearing-fault
    spectrum the defect tone routinely outranks it (BPFO at 3.5x shaft is bigger
    than 1x in every corpus file here), so a dominance test would fail-closed on
    exactly the machines that need reading.
    """
    name = "inferred_speed_presence"
    shaft = rpm / 60.0
    if shaft <= 0:
        return _check(name, "fail", "No running speed was available to check the frequency axis against.")
    if len(freqs) < 8:
        return _check(name, "fail", "The interpreted frequency axis has too few points to check.")

    steps = [b - a for a, b in zip(freqs, freqs[1:]) if b > a]
    bin_step = _median(steps) if steps else 0.0
    floor = _median(amps) or 0.0
    covered: list[int] = []
    for k in band_harmonics:
        target = k * shaft
        # tolerance never narrower than the spectrum's own resolution: a coarse
        # Welch spectrum must not fail for landing 1x in the neighbouring bin
        tolerance = max((tol_pct / 100.0) * target, 1.5 * bin_step)
        window = [a for f, a in zip(freqs, amps) if abs(f - target) <= tolerance]
        if not window:
            continue
        covered.append(k)
        if floor <= 0 or max(window) >= presence_ratio * floor:
            return _check(name, "pass", "")

    if not covered:
        return _check(
            name, "fail",
            f"The interpreted frequency axis never reaches the stated running speed "
            f"({shaft:.2f} Hz) or its 2nd/3rd harmonic — it spans {freqs[0]:.2f}–{freqs[-1]:.2f} Hz. "
            "Either the running speed is wrong or the file was read with the wrong frequency unit "
            "(Hz read as CPM, or shaft orders read as Hz, are the usual causes).",
        )
    return _check(
        name, "warn",
        f"No peak stands above the noise floor at the stated running speed ({shaft:.2f} Hz) or its "
        "2nd/3rd harmonic, so the frequency axis could not be confirmed against the stated speed.",
    )


def verify_case(case: Case, *, rpm: float, tolerances: dict[str, Any]) -> list[Check]:
    """Run every applicable check. Returns the Checks; the caller treats any
    `fail` as a hard stop (an insufficient-data report), and `warn` as a note.

    Trend cases have no frequency axis, so they get the amplitude check only.
    """
    speed_tol = float(tolerances.get("speed_tolerance_pct", 5.0))
    max_ratio = float(tolerances.get("max_bin_ratio", 8.0))
    max_abs = float(tolerances.get("max_amplitude", 1.0e6))
    presence_ratio = float(tolerances.get("speed_presence_ratio", 3.0))
    min_bins_per_order = float(tolerances.get("min_bins_per_order", 2.0))
    max_velocity = float(tolerances.get("max_overall_velocity_mm_s", 150.0))

    # Only a velocity interpretation carries a severity claim, so only a velocity
    # interpretation has an overall worth judging for plausibility.
    overall = case.sensor_data.y_velocity_mm_sec

    # Check the RAW spectrum when one exists: a 1x line in an ENVELOPE spectrum is
    # a demodulation artifact (Session B, B1), so an envelope is the wrong place
    # to look for running-speed content — and its absence there means nothing.
    spectrum = (case.raw_spectra or {}).get("y") or (case.spectra or {}).get("y")
    if spectrum is None:
        values = [p.value for p in (case.history or [])]
        if not values:
            return [_check("inferred_content", "fail",
                           "The interpreted file produced neither a spectrum nor a trend history.")]
        return [_amplitude_plausibility(values, max_abs=max_abs),
                _velocity_plausibility(overall, max_mm_s=max_velocity)]

    freqs, amps = list(spectrum.freq_hz), list(spectrum.amplitude)
    checks = [
        _monotonic_axis(freqs),
        _bin_consistency(freqs, max_ratio=max_ratio),
        _amplitude_plausibility(amps, max_abs=max_abs),
        _velocity_plausibility(overall, max_mm_s=max_velocity),
    ]
    # A broken axis makes the speed check meaningless; do not pile a second,
    # confusing failure on top of the first.
    if any(c.status == "fail" for c in checks):
        return checks

    # Resolution before presence: if the bins are wider than a shaft order there
    # is nothing meaningful to look for at 1x, and _speed_presence would answer
    # with a window wide enough to swallow the question.
    checks.append(_axis_resolution(freqs, rpm, min_bins_per_order=min_bins_per_order))
    if any(c.status == "fail" for c in checks):
        return checks
    if spectrum.kind == "envelope":
        # Only an envelope is available (a waveform whose raw spectrum we chose
        # not to build). Say so rather than testing for a line that would be an
        # artifact if it were there.
        checks.append(_check("inferred_speed_presence", "not_applicable",
                             "Running-speed content is not checked against an envelope spectrum, "
                             "where a 1x line would be a demodulation artifact."))
        return checks
    checks.append(_speed_presence(freqs, amps, rpm, tol_pct=speed_tol, presence_ratio=presence_ratio))
    return checks


def failed(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.status == "fail"]
