"""Cross-layer synthesis: turn per-layer results into severity-anchored
Findings and conservative corrective recommendations.

Pure, deterministic, no LLM. Two rules govern it:
  - Severity is anchored to the ISO zone (the objective "how bad"); a fault's
    own confidence carries evidence strength ("how sure") separately.
  - Recommendations are conservative and actionable — never "run to failure".

These Findings are the committed diagnosis surfaced in the report's Diagnosis
section; the RcaResult's `differential` (surfaced as "Also considered") and the
recommended follow-up measurements ride alongside in the AnalysisResult.
"""

from __future__ import annotations

from vib_agent.models import (
    Finding,
    IsoSeverityOrUnrated,
    QualityGateResult,
    RcaResult,
    Reading,
    TrendResult,
)

# Conservative corrective action per fault family. Deliberately cautious —
# confirm/plan/schedule, never "run to failure". recommendations_for_findings()
# reuses these; a follow-up *measurement* (recommendations.py) is separate.
_RECOMMENDATIONS: dict[str, str] = {
    "bearing_outer_race": "Plan bearing replacement at the next maintenance window; monitor closely until then.",
    "bearing_inner_race": "Plan bearing replacement at the next maintenance window; monitor closely until then.",
    "bearing_ball_spin": "Plan bearing replacement at the next maintenance window; monitor closely until then.",
    "bearing_cage": "Trend the bearing closely and plan replacement at the next maintenance window.",
    "mechanical_looseness": "Inspect and re-torque mounting, foundation, and bearing-fit hardware.",
    "angular_misalignment": "Schedule a precision (laser) shaft-alignment check.",
    "parallel_misalignment": "Schedule a precision (laser) shaft-alignment check.",
    "severe_misalignment": "Schedule a precision alignment check promptly and inspect the coupling.",
    "misalignment_general": "Schedule a precision (laser) shaft-alignment check.",
    "bent_shaft": "Plan to inspect the shaft for runout at the next opportunity; verify with dial-indicator runout.",
    "imbalance": "Arrange field balancing of the rotor.",
    "belt_fault": "Inspect belt tension, wear, and sheave condition; re-tension or replace as needed.",
    "elevated_blade_pass": "Investigate process/flow conditions (cavitation, blockage, hydraulic instability).",
    "possible_resonance": "Confirm with a bump test before any corrective action; avoid operating at the resonant speed.",
    "rising_trend": "Increase monitoring frequency and investigate before the projected boundary is reached.",
    "elevated_vibration_undetermined": "Collect additional data to identify the source before planning corrective work.",
    "no_significant_findings": "Continue routine monitoring at the normal interval.",
}

_DEFAULT_RECOMMENDATION = "Confirm the finding with a follow-up measurement at the next scheduled interval."


def _confidence_details(evidence_factors) -> list[str]:
    return [f.detail for f in evidence_factors]


def synthesize_findings(
    reading: Reading,
    gate: QualityGateResult,
    rca: RcaResult | None,
    trend: TrendResult | None,
) -> list[Finding]:
    """Compose the committed diagnosis. On a gate FAIL there is no valid
    diagnosis, so this returns [] — the report renders an insufficient-data
    statement plus recommended re-capture measurements instead.
    """
    if gate.overall == "fail":
        return []

    findings: list[Finding] = []

    primary = rca.primary_findings if rca is not None else []
    for match in primary:
        findings.append(
            Finding(
                fault=match.fault,
                severity=reading.iso_severity,
                confidence=match.confidence,
                reason=match.evidence,
                evidence={
                    "freq_hz": match.freq_hz,
                    "expected_hz": match.expected_hz,
                    "axis": match.axis,
                    "harmonic_present": match.harmonic_present,
                    "sidebands": match.sidebands,
                    "bearing_model": match.bearing_model,
                    "axial_radial_ratio": match.axial_radial_ratio,
                    "confidence_factors": _confidence_details(match.confidence_evidence),
                },
            )
        )

    if trend is not None and trend.severity != "ok":
        trend_confidence = "low" if trend.trend_note == "no reliable trend" else "high"
        trend_severity: IsoSeverityOrUnrated
        if reading.iso_zone == "not_assessable":
            # No velocity, so the trend's mm/s alarm limit and ISO-boundary
            # projection are not supportable -- report the RISE (unit-relative,
            # still meaningful) but leave severity unrated, never the velocity-
            # gated trend.severity.
            trend_severity = "unrated"
            reason = (
                f"7-day average {trend.baseline_avg} → {trend.current_avg} "
                f"({trend.pct_change:+.1f}% over {trend.n_days} days, in the reading's "
                "native units). ISO severity unrated -- velocity measurement required."
            )
        else:
            trend_severity = trend.severity
            reason = (
                f"7-day average {trend.baseline_avg} → {trend.current_avg} mm/s "
                f"({trend.pct_change:+.1f}% over {trend.n_days} days)."
            )
            if trend.days_to_next_boundary is not None:
                reason += f" Projected ~{trend.days_to_next_boundary} days to the next ISO boundary."
        findings.append(
            Finding(
                fault="rising_trend",
                severity=trend_severity,
                confidence=trend_confidence,
                reason=reason,
                evidence={
                    "slope": trend.slope,
                    "pct_change": trend.pct_change,
                    "current_avg": trend.current_avg,
                    "alarm_limit": trend.alarm_limit,
                    "r_squared": trend.r_squared,
                    "days_to_next_boundary": trend.days_to_next_boundary,
                    "trend_note": trend.trend_note,
                },
            )
        )

    has_trend_finding = any(f.fault == "rising_trend" for f in findings)
    if not primary and not has_trend_finding:
        if reading.iso_zone == "not_assessable":
            # No fault matched AND no velocity to rate severity: say exactly that,
            # never a coerced "Zone A / no action". Severity is unrated and the
            # velocity-collection follow-up rides through the recommendations
            # channel (see recommendations.py severity_coverage rule).
            findings.append(
                Finding(
                    fault="no_significant_findings",
                    severity="unrated",
                    confidence="high",
                    reason=(
                        "No fault signature matched in the spectral/envelope evidence. "
                        "ISO severity is unrated -- a velocity measurement per ISO 20816 "
                        "is required to establish it."
                    ),
                    evidence={"iso_zone": "not_assessable", "severity_rms": None},
                )
            )
        elif reading.iso_zone in ("A", "B"):
            findings.append(
                Finding(
                    fault="no_significant_findings",
                    severity=reading.iso_severity,
                    confidence="high",
                    reason=(
                        f"Overall vibration is in ISO Zone {reading.iso_zone} "
                        f"({reading.severity_rms:.2f} mm/s) with no matched fault signature."
                    ),
                    evidence={"iso_zone": reading.iso_zone, "severity_rms": reading.severity_rms},
                )
            )
        else:
            findings.append(
                Finding(
                    fault="elevated_vibration_undetermined",
                    severity=reading.iso_severity,
                    confidence="low",
                    reason=(
                        f"Overall vibration is elevated (ISO Zone {reading.iso_zone}, "
                        f"{reading.severity_rms:.2f} mm/s) but no specific fault signature matched."
                    ),
                    evidence={"iso_zone": reading.iso_zone, "severity_rms": reading.severity_rms},
                )
            )

    return findings


def recommend_for(fault: str) -> str:
    """Conservative corrective action for a single fault. Never 'run to failure'."""
    return _RECOMMENDATIONS.get(fault, _DEFAULT_RECOMMENDATION)


def recommendations_for_findings(findings: list[Finding]) -> list[str]:
    """Ordered, de-duplicated conservative recommendations for the report's
    numbered Recommendations section.
    """
    seen: set[str] = set()
    out: list[str] = []
    for finding in findings:
        rec = recommend_for(finding.fault)
        if rec not in seen:
            seen.add(rec)
            out.append(rec)
    return out
