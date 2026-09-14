"""Two-file Before/After comparison — Session HIST-2.

Pure, deterministic and strictly ADDITIVE. Nothing in this module changes any
existing analysis: both readings run the ordinary pipeline UNCHANGED and arrive
here as finished `AnalysisResult`s. This file only subtracts one from the other
and states exactly what the subtraction supports.

Four properties it exists to guarantee:

  * **The LLM never computes a delta.** Every number and every verdict word a
    comparison section prints is produced here and rendered from
    `ComparisonResult`. The drafting model is not shown the comparison at all —
    `report/generate.py` splices it in deterministically afterwards, the
    coverage-roster precedent (`render_markdown`'s docstring) — so there is no
    path by which a narrated delta can disagree with the arithmetic. That is a
    stronger guarantee than checking a narration after the fact.

  * **A refusal is a result, not an exception.** Two files that are not
    Before/After of one measurement point come back as
    `status="not_comparable"` carrying the reason. The caller turns that into
    the ratified `not_comparable` failure. A comparison is never produced with
    a caveat that it might be meaningless.

  * **Only ratios are interpreted.** Band levels are root-sum-square bin
    amplitudes; their absolute value depends on the export's scaling, their
    RATIO does not. That is what lets an acceleration-only pair (no velocity, no
    ISO zone) still get a real band-by-band answer instead of nothing.

  * **Constants are in-code defaults.** `config/thresholds.json` carries no
    `compare` block and was NOT edited by this session, so every value below is
    the `.get(key, DEFAULT)` fallback — the `quality_gate.py` pattern. A future
    session that wants to calibrate them adds the block; until then the
    published defaults are the whole story and the report states the one that
    matters (the significance ratio) in words.

Reads no files, holds no globals, calls no model. `peaks_from_spectrum` is
imported from `bearing_rca` on purpose: the peaks this module compares must be
picked by the SAME policy the diagnosis picked its peaks with, or a comparison
could pair two tones the analysis itself calls different.
"""

from __future__ import annotations

import math
from typing import Any

from vib_agent.models import (
    AnalysisResult,
    Axis,
    BandChange,
    Case,
    ComparisonResult,
    DeltaVerdict,
    OverallChange,
    PeakChange,
    RepairCheck,
    RepairVerdict,
    Spectrum,
    SpectrumComparison,
)
from vib_agent.pdm_core.bearing_rca import peaks_from_spectrum

# ── Constants, all in-code defaults (see the module docstring) ───────────────

#: A change counts as significant at a RATIO of 1.25 (or its reciprocal, 0.80).
#: Chosen for two independent reasons that agree. Physically, 1.25 is +1.94 dB:
#: two dB is the conventional smallest amplitude change that survives the
#: run-to-run variation of a route measurement at one point (mounting, load,
#: speed), and it is far below the narrowest ISO 20816-3 zone step (1.58x,
#: measured in SESSION_DQFLAGS.md §2). Internally, 0.25 is already the fraction
#: `trend.iso_alarm_factor` uses for "this has risen enough to say so", so the
#: product's two has-it-changed surfaces speak the same number. Applied as a
#: symmetric RATIO rather than a percent so a rise and a fall of the same
#: magnitude in dB are judged identically: +25% and -20% are the same 2 dB.
SIGNIFICANT_RATIO = 1.25

#: A committed fault's evidence peak must fall to HALF (-6 dB) before the
#: reading is called consistent with repair. Deliberately 3x the significance
#: bar: "something changed" and "the thing we diagnosed is gone" are different
#: claims and the second must cost more evidence than the first.
REPAIR_REDUCTION_RATIO = 0.5

#: Peak-pairing window, percent of frequency. Equal to route's
#: `rca.tolerance_pct` (3.0) on purpose: the comparison must not pair peaks more
#: loosely than the diagnosis matched them to fault frequencies, or it would
#: call two tones the analysis distinguishes "the same peak, moved".
PEAK_MATCH_TOLERANCE_PCT = 3.0

#: Maximum running-speed disagreement between the two readings, percent. Derived
#: from the line above rather than chosen: beyond this the shaft orders shift by
#: more than the peak-match window, so peaks stop pairing and every band
#: boundary moves. Two readings further apart than this are not Before/After of
#: one operating condition.
SPEED_TOLERANCE_PCT = 2.0

#: Maximum ratio between the two spectra's bin widths. Band levels are sums over
#: bins, so two spectra with materially different line resolutions cannot be
#: compared band-for-band without resampling — and resampling an amplitude
#: spectrum is a DSP choice this module refuses to make silently.
BIN_RATIO_MAX = 1.05

#: Minimum shaft orders the two spectra must have in COMMON for a spectral
#: comparison to mean anything. Below two orders the band scheme collapses to
#: "sub-synchronous plus the 1x line": there is no band left for everything
#: ABOVE running speed, which is where most of the energy that changes lives.
#: Deliberately NOT set to the scheme's own 10x top edge — a 200 Hz capture on
#: an 1800 rpm machine is 6.7 orders, narrow but perfectly real, and it is what
#: this product's own example file contains. Bands are clipped to the common
#: span and report the orders they ACTUALLY cover, so a narrow capture gets a
#: smaller, truthful table rather than a refusal.
MIN_COMMON_ORDERS = 2.0

#: Peaks considered per axis, per side. Wider than the diagnosis's own top-3
#: (route leaves `spectrum_max_peaks_per_axis` at its default) on purpose: a NEW
#: frequency that the diagnosis's top three never saw is exactly what a
#: comparison exists to find.
PEAKS_PER_AXIS = 12

#: A "new" or "gone" frequency must stand this many times above its own
#: spectrum's mean amplitude. Below it, appearing and disappearing is what a
#: broadband floor does between two captures, not an event.
PEAK_FLOOR_MULT = 3.0

#: Shaft-order bands, the analyst-standard split. Reported in this order.
#: The last band's upper edge is the common Fmax, whatever that turns out to be.
BANDS: tuple[tuple[str, str, float, float | None], ...] = (
    ("sub_synchronous", "Sub-synchronous", 0.0, 0.8),
    ("one_x", "Running speed", 0.8, 1.2),
    ("harmonics", "Shaft harmonics", 1.2, 10.0),
    ("high_frequency", "High frequency", 10.0, None),
)


def _c(cfg: dict[str, Any] | None, key: str, default: Any) -> Any:
    """The in-code-default read. `cfg` is `thresholds.get("compare", {})`, which
    is `{}` in every shipped profile — so today this always returns `default`,
    and a future calibration session changes behaviour by adding the block and
    nothing else."""
    return (cfg or {}).get(key, default)


# ── verdict arithmetic ───────────────────────────────────────────────────────


def _verdict(before: float | None, after: float | None, ratio: float) -> DeltaVerdict:
    """The one place a delta becomes a word. Symmetric in dB by construction:
    `ratio` up, `1/ratio` down."""
    if before is None or after is None:
        return "not_assessable"
    if before <= 0.0 and after <= 0.0:
        return "no_significant_change"
    if before <= 0.0:
        return "worsened"
    if after <= 0.0:
        return "improved"
    r = after / before
    if r >= ratio:
        return "worsened"
    if r <= 1.0 / ratio:
        return "improved"
    return "no_significant_change"


def _pct(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or before <= 0.0:
        return None
    return round((after - before) / before * 100.0, 1)


_ZONE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


def _zone_movement(before: str | None, after: str | None) -> str | None:
    a, b = _ZONE_ORDER.get(before or ""), _ZONE_ORDER.get(after or "")
    if a is None or b is None:
        return None
    return "same" if a == b else ("up" if b > a else "down")


# ── spectrum plumbing ────────────────────────────────────────────────────────


def _families(case: Case) -> dict[tuple[str, str], Spectrum]:
    """Every (kind, axis) spectrum this case carries, keyed so the two sides can
    be intersected. `raw_spectra` is read first so that when a case puts the
    same spectrum in both slots (the MAFAULDA adapter does) one entry results,
    not two identical ones."""
    out: dict[tuple[str, str], Spectrum] = {}
    for group in (case.raw_spectra, case.spectra):
        for axis, spectrum in (group or {}).items():
            if spectrum.freq_hz and spectrum.amplitude:
                out.setdefault((spectrum.kind, axis), spectrum)
    return out


def _bin_hz(spectrum: Spectrum) -> float | None:
    freqs = spectrum.freq_hz
    if len(freqs) < 2:
        return None
    span = freqs[-1] - freqs[0]
    return span / (len(freqs) - 1) if span > 0 else None


def _truncate(spectrum: Spectrum, fmax_hz: float) -> Spectrum:
    """A copy of `spectrum` covering only up to `fmax_hz`. Both sides are cut to
    their common span BEFORE anything is measured, so the bands and the peak
    universes cover exactly the same frequencies on both."""
    pairs = [(f, a) for f, a in zip(spectrum.freq_hz, spectrum.amplitude) if f <= fmax_hz]
    return Spectrum(
        freq_hz=[f for f, _ in pairs],
        amplitude=[a for _, a in pairs],
        fmax_hz=fmax_hz,
        kind=spectrum.kind,
    )


def _band_level(
    spectrum: Spectrum, lo_hz: float, hi_hz: float, *, inclusive_hi: bool = False
) -> tuple[float, int]:
    """Root-sum-square of the bin amplitudes in [lo_hz, hi_hz). Absolute value is
    never interpreted — only the Before/After ratio is, which is unit-free and so
    survives an export whose amplitude scaling nobody declared.

    `inclusive_hi` closes the interval for the TOP band, whose upper edge is the
    common Fmax itself: a half-open rule there would silently drop the last bin
    of every spectrum, on both sides, from the one band that has no band above
    it to catch it."""
    total, n = 0.0, 0
    for f, a in zip(spectrum.freq_hz, spectrum.amplitude):
        if lo_hz <= f and (f <= hi_hz if inclusive_hi else f < hi_hz):
            total += a * a
            n += 1
    return math.sqrt(total), n


def _peaks(spectrum: Spectrum, axis: str, rpm: float, n: int) -> tuple[list[Any], float | None]:
    """The product's OWN peak picker, over one truncated spectrum. Returns the
    peaks and this axis's mean amplitude (the floor reference)."""
    peak_set = peaks_from_spectrum(
        {axis: spectrum},  # type: ignore[dict-item]
        rpm,
        {},
        max_peaks_per_axis=n,
    )
    mean = (peak_set.axis_mean_amp or {}).get(axis)
    return list(peak_set.peaks), mean


def _match_peaks(
    before_peaks: list[Any], after_peaks: list[Any], tol_pct: float
) -> tuple[list[tuple[Any, Any]], list[Any], list[Any]]:
    """Pair Before peaks to After peaks, nearest-first within the tolerance
    window, each After peak used at most once. Deterministic: Before peaks are
    walked in ascending frequency and ties break on the lower After frequency."""
    remaining = sorted(after_peaks, key=lambda p: p.freq)
    matched: list[tuple[Any, Any]] = []
    gone: list[Any] = []
    for bp in sorted(before_peaks, key=lambda p: p.freq):
        window = bp.freq * tol_pct / 100.0
        best, best_d = None, None
        for ap in remaining:
            d = abs(ap.freq - bp.freq)
            if d <= window and (best_d is None or d < best_d):
                best, best_d = ap, d
        if best is None:
            gone.append(bp)
        else:
            matched.append((bp, best))
            remaining.remove(best)
    return matched, gone, remaining


def _compare_one_spectrum(
    key: tuple[str, str],
    before_spectrum: Spectrum,
    after_spectrum: Spectrum,
    *,
    shaft_hz: float,
    rpm: float,
    cfg: dict[str, Any] | None,
) -> tuple[SpectrumComparison | None, str | None, list[PeakChange]]:
    """One (kind, axis) pair, or None plus the reason it was skipped.

    The third element is the UNFILTERED Before-peak index — every Before peak
    this pass examined, matched or gone, before the report floor is applied.
    Repair verification looks a committed fault's evidence frequency up in that
    index rather than in the reported lists: a fault the diagnosis committed on
    is by definition not noise, so the floor that keeps noise out of "new
    frequencies" must not be able to turn a real evidence peak into "could not
    be re-measured".
    """
    kind, axis = key
    bin_b, bin_a = _bin_hz(before_spectrum), _bin_hz(after_spectrum)
    if bin_b is None or bin_a is None:
        return None, f"the {kind} spectrum on axis {axis} has too few bins to compare", []

    bin_ratio_max = float(_c(cfg, "bin_ratio_max", BIN_RATIO_MAX))
    ratio = max(bin_b, bin_a) / min(bin_b, bin_a)
    if ratio > bin_ratio_max:
        return None, (
            f"the two {kind} spectra on axis {axis} have different line resolutions "
            f"({bin_b:.3g} Hz before, {bin_a:.3g} Hz after) — band levels are sums over "
            "bins, so comparing them would compare the export settings, not the machine"
        ), []

    fmax = min(before_spectrum.freq_hz[-1], after_spectrum.freq_hz[-1])
    min_orders = float(_c(cfg, "min_common_orders", MIN_COMMON_ORDERS))
    if shaft_hz <= 0:
        return None, (
            f"the {kind} spectrum on axis {axis} has no running speed to express its bands in "
            "shaft orders"
        ), []
    if fmax / shaft_hz < min_orders:
        return None, (
            f"the two {kind} spectra on axis {axis} share only {fmax:.0f} Hz of span "
            f"({fmax / shaft_hz:.1f}x shaft), under the {min_orders:g}x orders this "
            "comparison needs before any band above running speed exists"
        ), []

    truncated = (
        abs(before_spectrum.freq_hz[-1] - fmax) > bin_b
        or abs(after_spectrum.freq_hz[-1] - fmax) > bin_a
    )
    note = None
    if truncated:
        note = (
            f"Compared over the common span 0-{fmax:.0f} Hz "
            f"(before reaches {before_spectrum.freq_hz[-1]:.0f} Hz, "
            f"after {after_spectrum.freq_hz[-1]:.0f} Hz); content above it is in one "
            "reading only and is not compared."
        )
    tb, ta = _truncate(before_spectrum, fmax), _truncate(after_spectrum, fmax)

    sig = float(_c(cfg, "significant_ratio", SIGNIFICANT_RATIO))
    bands: list[BandChange] = []
    for name, label, lo_o, hi_o in BANDS:
        lo_hz = lo_o * shaft_hz
        hi_hz = fmax if hi_o is None else min(hi_o * shaft_hz, fmax)
        if hi_hz <= lo_hz:
            continue
        top = hi_o is None
        b_level, b_n = _band_level(tb, lo_hz, hi_hz, inclusive_hi=top)
        a_level, a_n = _band_level(ta, lo_hz, hi_hz, inclusive_hi=top)
        if b_n == 0 and a_n == 0:
            continue  # no bins on either side: a band that was never measured
        bands.append(
            BandChange(
                name=name, label=label, lo_order=lo_o,
                # The order span this band ACTUALLY covers. A band defined as
                # 1.2-10x over a capture that stops at 6.7x is a 1.2-6.7x band,
                # and printing its definition instead of its coverage would put
                # a frequency range and an order range side by side that do not
                # agree with each other.
                hi_order=round(hi_hz / shaft_hz, 2),
                lo_hz=round(lo_hz, 2), hi_hz=round(hi_hz, 2),
                before=b_level, after=a_level, delta=a_level - b_level,
                pct_change=_pct(b_level, a_level),
                verdict=_verdict(b_level, a_level, sig),
            )
        )

    n_peaks = int(_c(cfg, "peaks_per_axis", PEAKS_PER_AXIS))
    tol = float(_c(cfg, "peak_match_tolerance_pct", PEAK_MATCH_TOLERANCE_PCT))
    floor = float(_c(cfg, "peak_floor_mult", PEAK_FLOOR_MULT))
    bp, b_mean = _peaks(tb, axis, rpm, n_peaks)
    ap, a_mean = _peaks(ta, axis, rpm, n_peaks)
    pairs, gone, new = _match_peaks(bp, ap, tol)

    def _order(freq: float) -> float | None:
        return round(freq / shaft_hz, 2) if shaft_hz > 0 else None

    def _clears(amp: float | None, mean: float | None) -> bool:
        # No floor reference (a spectrum with no mean) cannot exclude anything,
        # so the peak stands — the same "inert when unanswerable" rule
        # bearing_rca._clears_evidence_floor follows.
        if amp is None or mean is None or mean <= 0:
            return True
        return amp / mean >= floor

    matched_changes = [
        PeakChange(
            state="matched", before_hz=round(b.freq, 2), after_hz=round(a.freq, 2),
            before_amp=b.amplitude, after_amp=a.amplitude, order=_order(b.freq),
            pct_change=_pct(b.amplitude, a.amplitude),
            # The SAME ratio rule the bands use, applied to one tone's
            # amplitude. It is what lets the report print the peaks that moved
            # and count the ones that did not, instead of printing a table whose
            # every row says 0%.
            verdict=_verdict(b.amplitude, a.amplitude, sig),
        )
        for b, a in pairs
    ]
    gone_changes = [
        PeakChange(state="gone", before_hz=round(p.freq, 2), before_amp=p.amplitude,
                   order=_order(p.freq))
        for p in gone
    ]

    return (
        SpectrumComparison(
            kind=kind,  # type: ignore[arg-type]
            axis=axis,  # type: ignore[arg-type]
            shaft_hz=round(shaft_hz, 3),
            fmax_hz=round(fmax, 2),
            bin_hz=round((bin_b + bin_a) / 2.0, 4),
            truncated=truncated,
            truncation_note=note,
            bands=bands,
            # The SAME floor governs all three lists. Without it the reported
            # peak table is the top-N of the broadband floor: on a spectrum with
            # two real tones, ten rows of noise moving 0% each, which reads as
            # detail and is noise. The unfiltered index below keeps every peak
            # for repair verification, where a committed fault's evidence peak
            # is by definition not noise.
            matched=[
                c for c in matched_changes
                if _clears(c.before_amp, b_mean) or _clears(c.after_amp, a_mean)
            ],
            gone_frequencies=[c for c in gone_changes if _clears(c.before_amp, b_mean)],
            new_frequencies=[
                PeakChange(state="new", after_hz=round(p.freq, 2), after_amp=p.amplitude,
                           order=_order(p.freq))
                for p in sorted(new, key=lambda p: p.freq) if _clears(p.amplitude, a_mean)
            ],
        ),
        None,
        [*matched_changes, *gone_changes],
    )


# ── the public entry point ───────────────────────────────────────────────────


def compare_readings(
    before: AnalysisResult,
    after: AnalysisResult,
    *,
    before_case: Case,
    after_case: Case,
    cfg: dict[str, Any] | None = None,
) -> ComparisonResult:
    """Compare two finished analyses of ONE measurement point as Before/After.

    Both `AnalysisResult`s must come from `pipeline.run_analysis` on their own
    Case, unchanged. `cfg` is `thresholds.get("compare", {})`; no shipped profile
    carries that block, so every constant above applies as written.

    Raises `ValueError` when either analysis CRASHED (`rca.status == "error"`).
    That is the `analysis_to_expected` rule one layer out (S12FIX): a comparison
    computed from a screen that never ran would report "no new frequencies" about
    a spectrum nothing looked at — the clean-bill hazard wearing different
    clothes. Callers branch on `rca.status` before they get here; this is the
    backstop that makes forgetting impossible rather than merely unlikely.
    """
    for label, res in (("Before", before), ("After", after)):
        if res.rca is not None and res.rca.status == "error":
            raise ValueError(
                f"cannot compare: the {label} analysis failed ({res.rca.reason}) — "
                "there is no fault screen to compare against"
            )

    sig = float(_c(cfg, "significant_ratio", SIGNIFICANT_RATIO))
    result = ComparisonResult(
        status="ok",
        before_ts=before.ts,
        after_ts=after.ts,
        significance_note=(
            f"A change is called significant at a ratio of {sig:.2f} (about "
            f"+{20 * math.log10(sig):.1f} dB) or its reciprocal {1 / sig:.2f}; smaller "
            "movements are reported with their figures and called no significant change."
        ),
    )

    # ── 1 · The gate comes first, here as everywhere ─────────────────────────
    blocked = [
        (label, res)
        for label, res in (("Before", before), ("After", after))
        if res.quality_gate.overall == "fail"
    ]
    if blocked:
        reasons = []
        collect = []
        for label, res in blocked:
            detail = "; ".join(
                c.reason for c in res.quality_gate.checks if c.status == "fail" and c.reason
            ) or "data-quality checks failed"
            reasons.append(f"the {label} reading did not pass its data-quality gate ({detail})")
            collect.append(f"Re-capture the {label} reading: {detail}")
        result.status = "gate_blocked"
        result.reason_code = "gate_fail"
        result.reason = (
            "No comparison was made — " + "; and ".join(reasons)
            + ". A Before/After comparison against a reading the gate refused would "
            "carry that reading's defect into the delta."
        )
        result.what_to_collect = collect
        result.headline = "No comparison was made: a reading did not pass its data-quality gate."
        return result

    # ── 2 · A stopped machine is not the other end of a Before/After ─────────
    for label, res in (("Before", before), ("After", after)):
        if res.rca is not None and res.rca.status == "machine_off":
            return _not_comparable(
                result,
                "machine_not_running",
                f"the {label} reading was taken with the machine below its minimum running "
                "speed, so the two readings are not the same operating condition",
                [f"Re-capture the {label} reading with the machine running at its normal speed."],
            )

    # ── 3 · Same speed, or the orders do not line up ─────────────────────────
    rpm_b = (before_case.sensor_data.rpm if before_case.sensor_data else None) or 0.0
    rpm_a = (after_case.sensor_data.rpm if after_case.sensor_data else None) or 0.0
    speed_tol = float(_c(cfg, "speed_tolerance_pct", SPEED_TOLERANCE_PCT))
    if rpm_b > 0 and rpm_a > 0:
        drift = abs(rpm_a - rpm_b) / rpm_b * 100.0
        if drift > speed_tol:
            return _not_comparable(
                result,
                "speed_mismatch",
                f"the two readings were taken at different running speeds "
                f"({rpm_b:g} and {rpm_a:g} rpm, {drift:.1f}% apart, tolerance {speed_tol:g}%) — "
                "every shaft order, and therefore every band boundary, sits somewhere else "
                "in the two spectra",
                ["Re-capture both readings at the same running speed, or state the speed each "
                 "was taken at and compare them as separate surveys."],
            )
    shaft_hz = (rpm_a or rpm_b) / 60.0

    # ── 4 · Overall level, when both readings support an ISO judgement ───────
    result.overall = _overall(before, after, sig)

    # ── 5 · Spectra, per (kind, axis) present on both sides ──────────────────
    fam_b, fam_a = _families(before_case), _families(after_case)
    common = sorted(set(fam_b) & set(fam_a))
    skipped: list[str] = []
    evidence_index: dict[str, list[PeakChange]] = {}
    for key in common:
        comparison, why, index = _compare_one_spectrum(
            key, fam_b[key], fam_a[key], shaft_hz=shaft_hz, rpm=rpm_a or rpm_b, cfg=cfg
        )
        if comparison is not None:
            result.spectra.append(comparison)
            evidence_index.setdefault(comparison.axis, []).extend(index)
        elif why:
            skipped.append(why)

    if not common and (fam_b or fam_a):
        only_b = sorted(f"{k[0]} on axis {k[1]}" for k in set(fam_b) - set(fam_a))
        only_a = sorted(f"{k[0]} on axis {k[1]}" for k in set(fam_a) - set(fam_b))
        skipped.append(
            "the two readings carry different measurement types — before: "
            f"{', '.join(only_b) or 'none'}; after: {', '.join(only_a) or 'none'}"
        )
    if skipped and not result.spectra:
        result.spectra_note = (
            "No spectral comparison was made: " + "; ".join(skipped) + "."
        )
    elif skipped:
        result.spectra_note = "Some spectra were not compared: " + "; ".join(skipped) + "."

    # ── 6 · Nothing comparable at all is the one structural refusal left ─────
    if not result.spectra and result.overall.verdict == "not_assessable":
        return _not_comparable(
            result,
            "no_common_evidence",
            (result.spectra_note or "the two readings share no comparable spectrum")
            + " "
            + (result.overall.reason or "The overall level could not be compared either.")
            + " There is nothing these two files can be compared on.",
            ["Re-capture both readings on the same instrument with the same Fmax, line count "
             "and amplitude unit."],
        )

    # ── 7 · Repair verification, only where the Before analysis committed ────
    result.repair, result.repair_verdict = _repair(before, after, evidence_index, cfg)

    # ── 8 · The headline, from the arithmetic above and nothing else ─────────
    result.verdict = _headline_verdict(result)
    result.headline = _headline(result)
    result.what_to_collect.extend(_what_to_collect(result))
    return result


def _not_comparable(
    result: ComparisonResult, code: str, reason: str, collect: list[str]
) -> ComparisonResult:
    result.status = "not_comparable"
    result.reason_code = code
    result.reason = "These two files cannot be compared as Before/After of one measurement point: " + reason + "."
    result.what_to_collect = collect
    result.overall = None
    result.spectra = []
    result.headline = result.reason
    return result


def _overall(before: AnalysisResult, after: AnalysisResult, sig: float) -> OverallChange:
    """The mm/s movement — but only when BOTH readings produced an assessable ISO
    zone. `mark_not_assessable` preserves `severity_rms` while withholding the
    zone (implausible units), and an acceleration-only reading has no velocity at
    all; subtracting either would be arithmetic on a number the product has
    already refused to interpret."""
    iso_b, iso_a = before.iso, after.iso
    change = OverallChange(
        before_mms=iso_b.severity_rms if iso_b else None,
        after_mms=iso_a.severity_rms if iso_a else None,
        zone_before=iso_b.iso_zone if iso_b else None,
        zone_after=iso_a.iso_zone if iso_a else None,
    )
    unusable = [
        label
        for label, iso in (("Before", iso_b), ("After", iso_a))
        if iso is None or iso.iso_zone == "not_assessable" or iso.severity_rms is None
    ]
    if unusable:
        why = {
            label: (iso.not_assessable_reason if iso and iso.not_assessable_reason
                    else "no ISO-assessable overall velocity")
            for label, iso in (("Before", iso_b), ("After", iso_a))
            if label in unusable
        }
        change.reason = (
            "The overall mm/s change was not assessable: "
            + "; ".join(f"{label} — {reason}" for label, reason in why.items())
            + "."
        )
        return change
    change.delta_mms = round((change.after_mms or 0.0) - (change.before_mms or 0.0), 3)
    change.pct_change = _pct(change.before_mms, change.after_mms)
    change.verdict = _verdict(change.before_mms, change.after_mms, sig)
    change.zone_movement = _zone_movement(change.zone_before, change.zone_after)
    if change.zone_movement in ("up", "down") and change.verdict == "no_significant_change":
        change.zone_note = (
            f"The ISO zone moved {change.zone_before} to {change.zone_after}, but the change "
            f"({change.pct_change:+.1f}%) did not clear the significance ratio — the reading "
            "crossed a boundary it was already sitting on, which is a fact about the boundary "
            "and not a change in the machine."
        )
    return change


def _repair(
    before: AnalysisResult,
    after: AnalysisResult,
    evidence_index: dict[str, list[PeakChange]],
    cfg: dict[str, Any] | None,
) -> tuple[list[RepairCheck], RepairVerdict]:
    """Each fault the BEFORE analysis COMMITTED to, re-examined in the After
    reading. `after_committed` is decisive: if the After analysis committed the
    same fault on its own evidence, no amplitude arithmetic can make a repair
    verified — the screen that would have to be wrong is the one that just ran."""
    committed = list(before.rca.primary_findings) if before.rca is not None else []
    if not committed:
        return [], "not_applicable"

    after_faults = {m.fault for m in (after.rca.primary_findings if after.rca else [])}
    tol = float(_c(cfg, "peak_match_tolerance_pct", PEAK_MATCH_TOLERANCE_PCT))
    reduce_to = float(_c(cfg, "repair_reduction_ratio", REPAIR_REDUCTION_RATIO))
    checks: list[RepairCheck] = []

    for match in committed:
        check = RepairCheck(
            fault=match.fault,
            description=match.description,
            freq_hz=match.freq_hz,
            after_committed=match.fault in after_faults,
        )
        if check.after_committed:
            check.verdict = "not_verified"
            check.evidence = (
                "The After analysis committed this same fault on its own evidence."
            )
            checks.append(check)
            continue
        found = _evidence_peak(evidence_index, match.axis, match.freq_hz, tol)
        if found is None:
            check.verdict = "not_verified"
            check.evidence = (
                "The evidence peak could not be re-measured in the After reading — it was "
                "outside the compared span or below the peaks this comparison examined, so "
                "its absence is not evidence of anything."
            )
        elif found.state == "gone":
            check.before_amp = found.before_amp
            check.verdict = "consistent_with_repair"
            check.evidence = (
                f"The evidence peak at {found.before_hz:g} Hz is no longer among the peaks of "
                "the After reading, and the After analysis did not commit this fault."
            )
        else:
            check.before_amp, check.after_amp = found.before_amp, found.after_amp
            check.pct_change = found.pct_change
            ratio = (
                (found.after_amp / found.before_amp)
                if found.before_amp and found.after_amp is not None and found.before_amp > 0
                else None
            )
            if ratio is not None and ratio <= reduce_to:
                check.verdict = "consistent_with_repair"
                check.evidence = (
                    f"The evidence peak at {found.before_hz:g} Hz fell to {ratio * 100:.0f}% of "
                    f"its Before amplitude (at or below the {reduce_to * 100:.0f}% this check "
                    "requires), and the After analysis did not commit this fault."
                )
            else:
                check.verdict = "not_verified"
                check.evidence = (
                    f"The evidence peak at {found.before_hz:g} Hz is still present"
                    + (f" at {ratio * 100:.0f}% of its Before amplitude" if ratio is not None else "")
                    + f", above the {reduce_to * 100:.0f}% reduction this check requires."
                )
        checks.append(check)

    consistent = all(c.verdict == "consistent_with_repair" for c in checks)
    return checks, ("consistent_with_repair" if consistent else "not_verified")


def _evidence_peak(
    evidence_index: dict[str, list[PeakChange]], axis: Axis, freq_hz: float | None, tol_pct: float
) -> PeakChange | None:
    """The compared peak that carries a committed fault's evidence frequency, on
    that fault's own axis. Searches every compared spectrum family on that axis
    and takes the closest, so a bearing fault found in the envelope and a
    1x-family fault found in the raw spectrum are each looked up where they live."""
    if freq_hz is None or freq_hz <= 0:
        return None
    window = freq_hz * tol_pct / 100.0
    best: PeakChange | None = None
    best_d: float | None = None
    for peak in evidence_index.get(axis, []):
        if peak.before_hz is None:
            continue
        d = abs(peak.before_hz - freq_hz)
        if d <= window and (best_d is None or d < best_d):
            best, best_d = peak, d
    return best


def _headline_verdict(result: ComparisonResult) -> DeltaVerdict:
    """The overall mm/s movement when it is assessable; otherwise the aggregate
    of the band verdicts, erring toward `worsened`: a band that rose is worth an
    analyst's attention even when another band fell."""
    if result.overall is not None and result.overall.verdict != "not_assessable":
        return result.overall.verdict
    band_verdicts = [b.verdict for s in result.spectra for b in s.bands]
    if not band_verdicts:
        return "not_assessable"
    if "worsened" in band_verdicts:
        return "worsened"
    if "improved" in band_verdicts:
        return "improved"
    return "no_significant_change"


#: The closed delta vocabulary as PRINTABLE words. It lives here, next to the
#: arithmetic that assigns it, rather than in the report layer: the report has
#: two renderers (markdown and HTML) and a third would make three chances for
#: the same verdict to be worded differently. `report/generate.py` imports
#: `verdict_word`/`repair_word` and registers them on both Jinja environments,
#: so a verdict has exactly one spelling in the product.
_VERDICT_WORD = {
    "worsened": "worsened",
    "improved": "improved",
    "no_significant_change": "no significant change",
    "not_assessable": "not assessable",
}

_REPAIR_WORD = {
    "consistent_with_repair": "consistent with repair",
    "not_verified": "not verified",
    "not_applicable": "not applicable",
}


def verdict_word(verdict: str) -> str:
    """A delta verdict as the word a report prints. Unknown values pass through
    unchanged rather than raising: a template is not the place to discover that
    a vocabulary grew."""
    return _VERDICT_WORD.get(verdict, verdict)


def repair_word(verdict: str) -> str:
    """A repair verdict as the word a report prints. Note what is NOT here:
    there is no spelling of "repaired". The strongest thing a spectrum can
    witness is the absence of the signature that justified the repair."""
    return _REPAIR_WORD.get(verdict, verdict)



def _headline(result: ComparisonResult) -> str:
    overall = result.overall
    if overall is not None and overall.verdict != "not_assessable":
        moved = (
            f"Overall vibration moved from {overall.before_mms:.2f} to "
            f"{overall.after_mms:.2f} mm/s"
        )
        if overall.pct_change is not None:
            moved += f" ({overall.pct_change:+.1f}%)"
        parts = [moved]
        if overall.zone_movement == "same":
            parts.append(f"ISO zone {overall.zone_after} on both readings")
        else:
            parts.append(f"ISO zone {overall.zone_before} to {overall.zone_after}")
        head = "; ".join(parts) + f" — {_VERDICT_WORD[result.verdict]}."
    else:
        risen = [b.label for s in result.spectra for b in s.bands if b.verdict == "worsened"]
        fallen = [b.label for s in result.spectra for b in s.bands if b.verdict == "improved"]
        if result.verdict == "not_assessable":
            head = "Nothing in these two readings could be compared quantitatively."
        else:
            moved = risen if result.verdict == "worsened" else fallen
            head = (
                f"Band levels compared unit-relative (no ISO-assessable overall velocity): "
                f"{_VERDICT_WORD[result.verdict]}"
                + (f" — {', '.join(sorted(set(moved)))}." if moved else ".")
            )
    # A band that moved AGAINST the headline is not a detail. It is precisely
    # what a one-line summary buries, and precisely what an analyst needs: an
    # overall velocity can fall because imbalance was corrected while a bearing
    # tone grows underneath it, and "improved" on its own would be true and
    # misleading at the same time. The bands are in the table either way; this
    # makes the headline unable to hide one.
    against = sorted(
        {(b.label, b.verdict) for s in result.spectra for b in s.bands
         if b.verdict in ("worsened", "improved") and b.verdict != result.verdict}
    )
    if against:
        head += (
            " Not every band moved with it: "
            + "; ".join(f"{label} {_VERDICT_WORD[verdict]}" for label, verdict in against)
            + "."
        )
    if result.repair_verdict == "consistent_with_repair":
        head += " The Before reading's committed fault is not present in the After reading: consistent with repair."
    elif result.repair_verdict == "not_verified":
        head += " The Before reading's committed fault is not verified as resolved."
    return head


def _what_to_collect(result: ComparisonResult) -> list[str]:
    """The pillar, kept for the PAIR: whatever this comparison could not answer
    ships with the measurement that would answer it."""
    out: list[str] = []
    if result.overall is not None and result.overall.verdict == "not_assessable":
        out.append(
            "To compare overall severity in mm/s, capture both readings as velocity spectra "
            "with the amplitude unit declared — this pair supports only unit-relative "
            "band and peak comparison."
        )
    if result.spectra_note and not result.spectra:
        out.append(
            "To compare the spectra, re-capture both readings on the same instrument with the "
            "same Fmax, line count and measurement type."
        )
    if any(c.verdict == "not_verified" and c.after_amp is None and not c.after_committed
           for c in result.repair):
        out.append(
            "To verify the repair, re-capture the After reading with at least the Before "
            "reading's Fmax and line count so the fault's evidence frequency is measured."
        )
    return out
