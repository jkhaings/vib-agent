"""Follow-up measurement recommendations — the "says what to collect next" half
of the product pillar.

Pure and config-driven: config/next_measurements.json owns the rule table
(techniques, purposes, priorities, phrasing); this module owns the trigger
*predicates* (which analysis conditions fire which rule). Every "need more
information" path — an unresolved diagnostic differential OR a data-quality
gate problem — ships its advice through this one channel, so an
insufficient-data report still names exactly what to re-collect.

No I/O, no LLM. The caller loads the rule table (vib_agent.config) and passes
it in.

Session REC-1 — this module now holds BOTH recommendation channels, and they
are not the same thing:

  * the **measurement** channel (`recommend_measurements`, above) — what to
    COLLECT next when the evidence cannot resolve the differential or the data
    failed its quality gate. Config-driven; this is the channel the corpus
    scores as `recommendations`, and it is untouched by REC-1.
  * the **corrective** channel (`corrective_recommendation`, at the bottom) —
    what to DO about a fault that has been committed, in the analyst's own
    three-part shape: action, timing, reassessment. Text only; it computes
    nothing, gates nothing, and no number anywhere depends on it.

The corrective strings the report shipped before REC-1 live in
`synthesize.py::_RECOMMENDATIONS` and are left exactly where they are — that
file was latched for this session. `report/generate.py` chooses which of the
two tables fills its `recommendations` context key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vib_agent.models import (
    DifferentialCandidate,
    FaultMatch,
    Finding,
    MachineMeta,
    QualityGateResult,
    Reading,
    RecommendedMeasurement,
    TrendResult,
)

_MISALIGNMENT_FAULTS = frozenset(
    {
        "angular_misalignment",
        "parallel_misalignment",
        "severe_misalignment",
        "misalignment_general",
        "bent_shaft",
    }
)
_UNRESOLVED_1X2X = _MISALIGNMENT_FAULTS | {"imbalance"}

#: Session BENT-FIX. The 1×/2× family as a DIFFERENTIAL is still one question
#: — `_UNRESOLVED_1X2X` above is unchanged, because before anything is
#: committed a bent shaft really is one of the candidates phase work separates.
#: Once `bent_shaft` is COMMITTED it is a different question with a different
#: answer: `bearing_rca.py` commits it only where `machine.coupled` is False,
#: so the alignment-and-thermography rule would send an analyst to a coupling
#: the same report just said does not exist (SESSION_GEOMB.md §7, finding 1).
#: The two sets differ by exactly that one fault, and `bent_shaft_committed`
#: below answers for it.
_COUPLING_MISALIGNMENT_FAULTS = _MISALIGNMENT_FAULTS - {"bent_shaft"}

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _diagnosis_trigger_reason(
    rule_id: str,
    primary: list[FaultMatch],
    differential: list[DifferentialCandidate],
    machine: MachineMeta,
) -> str | None:
    """Return a human-readable trigger reason if the named rule fires, else None.
    Each branch is one classical discriminator condition.
    """
    primary_faults = {m.fault for m in primary}
    differential_faults = {d.fault for d in differential}

    if rule_id == "missing_axial_channel":
        # Session PDMFIX: a committed imbalance whose axial channel was never
        # measured. run_rca raises the misalignment family it cannot separate
        # itself from into the differential; this is the measurement that
        # resolves it. Identified by the adjudication phrasing run_rca writes —
        # the same idiom as synchronous_shaft_collision and
        # bearing_below_amplitude_floor.
        unmeasured = [
            d for d in differential if d.adjudication.startswith("Axial channel not measured")
        ]
        if unmeasured:
            return (
                "The axial channel was not measured, so rotor imbalance cannot be separated "
                "from an axial-dominant misalignment or bent shaft."
            )
        return None

    if rule_id == "unresolved_1x2x_differential":
        unresolved = (differential_faults & _UNRESOLVED_1X2X) or (
            {"misalignment_general"} & primary_faults
        )
        if unresolved:
            return (
                "The 1x/2x pattern is consistent with more than one fault "
                f"({', '.join(sorted(unresolved))}) — the differential is unresolved."
            )
        return None

    if rule_id == "low_confidence_bearing":
        low_bearing = [
            m for m in primary if m.fault.startswith("bearing_") and m.confidence == "low"
        ]
        if low_bearing:
            return f"A bearing fault ({low_bearing[0].fault}) was matched only at low confidence."
        return None

    if rule_id == "synchronous_shaft_collision":
        # Session B (B3): a bearing candidate the synchronous-collision guard moved
        # to the differential (matched at an integer shaft order). Identified by the
        # adjudication phrasing run_rca's guard writes — the resolving measurement is
        # the order-synchronous / envelope capture that separates the two.
        collided = [
            d
            for d in differential
            if d.fault.startswith("bearing_")
            and d.adjudication.startswith("Synchronous-ambiguous")
        ]
        if collided:
            return (
                f"A bearing candidate ({collided[0].fault}) matched at an integer shaft "
                "order — a bearing defect cannot be separated from shaft-order content there."
            )
        return None

    if rule_id == "bearing_below_amplitude_floor":
        # Session B (B4): a bearing candidate the amplitude floor moved to the
        # differential (peak near the broadband noise floor). Identified by the
        # adjudication phrasing run_rca's floor check writes.
        floored = [
            d
            for d in differential
            if d.fault.startswith("bearing_")
            and d.adjudication.startswith("Below amplitude floor")
        ]
        if floored:
            return (
                f"A bearing candidate ({floored[0].fault}) matched only near the broadband "
                "noise floor — its amplitude cannot be distinguished from spectral noise."
            )
        return None

    if rule_id == "misalignment_committed":
        committed = [
            m
            for m in primary
            if m.fault in _COUPLING_MISALIGNMENT_FAULTS and m.confidence in ("medium", "high")
        ]
        if committed:
            return f"{committed[0].fault} was committed at {committed[0].confidence} confidence."
        return None

    if rule_id == "bent_shaft_committed":
        # The bent-shaft resolver. A bend is a property of the shaft, so the
        # measurement that settles it is on the shaft: 1× phase read at both
        # bearings (which also separates a bend from imbalance, and locates it
        # along the span) and dial-indicator runout. This is the same
        # measurement the finding's own evidence sentence names as what it
        # cannot supply — the report now asks for it instead of merely
        # regretting its absence.
        committed = [
            m for m in primary if m.fault == "bent_shaft" and m.confidence in ("medium", "high")
        ]
        if committed:
            return f"bent_shaft was committed at {committed[0].confidence} confidence."
        return None

    if rule_id == "looseness_bearing_ambiguity":
        has_looseness = "mechanical_looseness" in primary_faults
        has_bearing = any(
            f.startswith("bearing_") for f in (primary_faults | differential_faults)
        )
        if has_looseness and has_bearing:
            return "Looseness and a bearing signature co-occur — their harmonic content overlaps."
        return None

    if rule_id == "suspected_resonance":
        if "possible_resonance" in primary_faults:
            return "An unexplained peak suggests possible resonance."
        return None

    if rule_id == "electrical_suspicion":
        # An unexplained peak on a motor-driven machine could be electrically
        # forced rather than mechanical — worth separating with a coast-down.
        motor_driven = (machine.type or "").lower() in ("motor", "compressor", "pump")
        if "possible_resonance" in primary_faults and motor_driven:
            return "An unexplained peak on a motor-driven machine could be electrically forced."
        return None

    return None


def recommend_measurements(
    primary_findings: list[FaultMatch],
    differential: list[DifferentialCandidate],
    gate_result: QualityGateResult,
    machine: MachineMeta,
    trend: TrendResult | None,
    rules: dict[str, Any],
    *,
    reading: Reading | None = None,
) -> list[RecommendedMeasurement]:
    """Emit follow-up measurements for unresolved diagnoses and data-quality
    problems. `rules` is the loaded config/next_measurements.json.
    Deduplicated by technique (highest priority kept) and sorted by priority.

    `reading` (keyword-only, optional) drives the severity-coverage follow-up:
    when its ISO zone is not_assessable, a velocity measurement is recommended to
    resolve severity. This is a REDUCED-SCOPE coverage statement, not a blanket
    "collect more" on every clean report -- it fires only when severity is
    genuinely unassessable, and is excluded from the diagnosis-resolving set that
    the MFPT Part B scorer counts.
    """
    out: list[RecommendedMeasurement] = []

    # 1. Data-quality re-capture advice — one per failing/warning DQ check.
    #    This is what makes an insufficient-data report actionable.
    dq_rules: dict[str, Any] = rules.get("data_quality_rules", {})
    for check in gate_result.checks:
        if check.status not in ("fail", "warn"):
            continue
        rule = dq_rules.get(check.name)
        if rule is None:
            continue
        reason = check.reason or f"data-quality check '{check.name}' did not pass"
        out.append(
            RecommendedMeasurement(
                technique=rule["technique"],
                purpose=rule["purpose"],
                trigger=reason,
                priority=rule["priority"],
            )
        )

    # 2. Diagnosis-driven measurements — resolve unresolved differentials.
    for rule in rules.get("diagnosis_rules", []):
        reason = _diagnosis_trigger_reason(rule["id"], primary_findings, differential, machine)
        if reason is None:
            continue
        out.append(
            RecommendedMeasurement(
                technique=rule["technique"],
                purpose=rule["purpose"],
                trigger=reason,
                priority=rule["priority"],
            )
        )

    # 3. Severity-coverage follow-up (Session A) — fired once when the reading's
    #    ISO zone is not_assessable (velocity absent, or present-but-implausible).
    #    Resolves the severity zone, NOT the fault diagnosis.
    if reading is not None and reading.iso_zone == "not_assessable":
        cov = rules.get("severity_coverage", {}).get("not_assessable")
        if cov is not None:
            out.append(
                RecommendedMeasurement(
                    technique=cov["technique"],
                    purpose=cov["purpose"],
                    trigger=f"ISO severity not assessable — {reading.not_assessable_reason}",
                    priority=cov["priority"],
                )
            )

    # Dedup by technique, keeping the highest-priority occurrence.
    by_technique: dict[str, RecommendedMeasurement] = {}
    for rec in out:
        existing = by_technique.get(rec.technique)
        if existing is None or _PRIORITY_ORDER[rec.priority] < _PRIORITY_ORDER[existing.priority]:
            by_technique[rec.technique] = rec

    return sorted(by_technique.values(), key=lambda r: _PRIORITY_ORDER[r.priority])


# ─────────────────────────────────────────────────────────────────────────
# The CORRECTIVE channel (Session REC-1) — what to DO, in the analyst's shape
#
# Everything below is text. It reads a committed fault id and the ISO zone the
# report already prints, and returns words. It computes nothing, it is read by
# no detector, and no number in any report depends on it.
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CorrectiveRecommendation:
    """One corrective recommendation in the analyst's three-part shape.

    The analyst's own wording, which this shape is taken from:

        "It is recommended to replace the compressor bearings during the next
        available maintenance schedule. The equipment health status should be
        reassessed after the above-mentioned maintenance action has been
        completed."

    Action -> timing -> reassessment.

    `text` is what the report renders and is authoritative. `action`, `timing`
    and `reassess` are NAMED SPANS OF THAT TEXT, in order — they are offered so
    a later report session can lay the three parts out in a table without
    re-parsing a sentence, and they are deliberately NOT a partition: where the
    source draft attached a clause after the timing ("... at the next available
    window AND CORRECT AS FOUND"), that clause belongs to neither part and is
    carried by `text` alone. `tests/test_rec1_recommendations.py` pins that all
    three occur in `text`, in order, for every id — which is what stops the
    parts drifting away from the sentence.
    """

    fault: str
    action: str
    timing: str
    reassess: str
    text: str


#: ISO zone -> the words used for a timing the source draft left open.
#:
#: **This is a TEXT MAP, NOT A SEVERITY RULE.** It does not rate anything, does
#: not escalate anything, and must never be read as one: severity is the ISO
#: zone itself (`iso_classify.py`), and urgency-by-priority is the measurement
#: channel's `priority` field above. All this does is choose an English phrase
#: for the two fault ids whose recommendation text names no timing of its own
#: (`possible_resonance`, `elevated_vibration_undetermined`); every other id
#: carries the timing its own text names, identically in all four zones.
#:
#: The brief's shorthand ("A/B: next scheduled; C: plan within the next window;
#: D: as soon as practicable") in renderable phrases. `not_assessable` and a
#: missing zone fall back to the A/B wording: with no zone there is no basis for
#: saying anything more urgent, and the report says separately that severity
#: could not be rated.
_ZONE_TIMING: dict[str, str] = {
    "A": "at the next scheduled interval",
    "B": "at the next scheduled interval",
    "C": "within the next maintenance window",
    "D": "as soon as practicable",
}
_ZONE_TIMING_FALLBACK = _ZONE_TIMING["A"]


@dataclass(frozen=True)
class _Corrective:
    """One row of the table below.

    `template` is the source text with the timing phrase replaced by a single
    `{timing}` slot, so the timing is named exactly once per row whether it is
    fixed or zone-derived. `timing` is the phrase that fills it, or None to take
    it from `_ZONE_TIMING`.
    """

    action: str
    timing: str | None
    reassess: str
    template: str


#: Corrective action per fault id, from outputs/SESSION_REPORT2.md F-3 (§3.6),
#: which drafted this text against the analyst's review and STOPped because
#: pdm_core was latched that session. Conservative throughout — confirm, plan,
#: schedule, replace; never "run to failure" — and every one ends by naming what
#: to re-measure and when.
_CORRECTIVE: dict[str, _Corrective] = {
    # The three bearing faults the analyst reviewed, in the analyst's own two
    # sentences modulo the machine name (these strings are machine-neutral:
    # "the bearing", not "the compressor bearings").
    "bearing_outer_race": _Corrective(
        action="Replace the bearing",
        timing="at the next available maintenance window",
        reassess=(
            "Reassess machine health with a repeat measurement at the same point "
            "after the work is completed."
        ),
        template=(
            "Replace the bearing {timing}; monitor closely until then. "
            "Reassess machine health with a repeat measurement at the same point "
            "after the work is completed."
        ),
    ),
    "bearing_cage": _Corrective(
        action="Trend the bearing closely and replace it",
        timing="at the next available maintenance window",
        reassess=(
            "Reassess with a repeat measurement at the same point after the work "
            "is completed."
        ),
        template=(
            "Trend the bearing closely and replace it {timing}. "
            "Reassess with a repeat measurement at the same point after the work "
            "is completed."
        ),
    ),
    "mechanical_looseness": _Corrective(
        action="Inspect and re-torque mounting, foundation and bearing-fit hardware",
        timing="at the next available window",
        reassess="Reassess with a repeat measurement after the work is completed.",
        template=(
            "Inspect and re-torque mounting, foundation and bearing-fit hardware "
            "{timing}. Reassess with a repeat measurement after the work is completed."
        ),
    ),
    "angular_misalignment": _Corrective(
        action="Carry out a precision (laser) shaft-alignment check",
        timing="at the next available window",
        reassess="Reassess with a repeat measurement after the alignment is completed.",
        template=(
            "Carry out a precision (laser) shaft-alignment check {timing} and correct "
            "as found. Reassess with a repeat measurement after the alignment is completed."
        ),
    ),
    "severe_misalignment": _Corrective(
        action="Carry out a precision alignment check",
        timing="promptly",
        reassess="Reassess with a repeat measurement after the work is completed.",
        template=(
            "Carry out a precision alignment check {timing} and inspect the coupling; "
            "correct as found. Reassess with a repeat measurement after the work is "
            "completed."
        ),
    ),
    "bent_shaft": _Corrective(
        action="Inspect the shaft for runout",
        timing="at the next opportunity",
        reassess="Reassess with a repeat measurement after the work is completed.",
        template=(
            "Inspect the shaft for runout {timing}, verified with a dial indicator; "
            "correct or replace as found. Reassess with a repeat measurement after the "
            "work is completed."
        ),
    ),
    "imbalance": _Corrective(
        action="Field-balance the rotor",
        timing="at the next available window",
        reassess="Reassess with a repeat measurement after balancing.",
        template=(
            "Field-balance the rotor {timing}. Reassess with a repeat measurement "
            "after balancing."
        ),
    ),
    "belt_fault": _Corrective(
        action="Inspect belt tension, wear and sheave condition",
        timing="at the next opportunity",
        reassess="Reassess with a repeat measurement after the work is completed.",
        template=(
            "Inspect belt tension, wear and sheave condition {timing}; re-tension or "
            "replace as found. Reassess with a repeat measurement after the work is "
            "completed."
        ),
    ),
    "elevated_blade_pass": _Corrective(
        action=(
            "Investigate process/flow conditions (cavitation, blockage, hydraulic "
            "instability)"
        ),
        timing="at the next opportunity",
        reassess="Reassess with a repeat measurement under the corrected condition.",
        template=(
            "Investigate process/flow conditions (cavitation, blockage, hydraulic "
            "instability) {timing} and correct the operating condition. Reassess with "
            "a repeat measurement under the corrected condition."
        ),
    ),
    # The two ids whose F-3 text named no timing — the zone map supplies one.
    "possible_resonance": _Corrective(
        action="Confirm with a bump test",
        timing=None,
        reassess="Reassess once the test result is in hand.",
        template=(
            "Confirm with a bump test {timing} before any corrective action; until then "
            "avoid operating at the resonant speed. Reassess once the test result is in "
            "hand."
        ),
    ),
    "elevated_vibration_undetermined": _Corrective(
        action="Collect additional data",
        timing=None,
        reassess="Reassess once the source is identified.",
        template=(
            "Collect additional data {timing} to identify the source before planning "
            "corrective work. Reassess once the source is identified."
        ),
    ),
    "rising_trend": _Corrective(
        action="Increase the monitoring frequency",
        timing="now",
        reassess="Reassess at each reading against this report's trend.",
        template=(
            "Increase the monitoring frequency {timing} and investigate the cause before "
            "the projected boundary is reached. Reassess at each reading against this "
            "report's trend."
        ),
    ),
    "no_significant_findings": _Corrective(
        action="Continue routine monitoring",
        timing="at the normal interval",
        reassess="Reassess at the next scheduled reading.",
        template=(
            "Continue routine monitoring {timing}. Reassess at the next scheduled reading."
        ),
    ),
}

#: Ids that share a row with another. The three bearing faults are the same
#: bearing and the same work; the three coupling-misalignment ids are the same
#: alignment check. Written as aliases rather than copies so each of the
#: analyst's sentences exists once in the tree.
_ALIASES: dict[str, str] = {
    "bearing_inner_race": "bearing_outer_race",
    "bearing_ball_spin": "bearing_outer_race",
    "parallel_misalignment": "angular_misalignment",
    "misalignment_general": "angular_misalignment",
}
_CORRECTIVE.update({alias: _CORRECTIVE[source] for alias, source in _ALIASES.items()})

_DEFAULT_CORRECTIVE = _Corrective(
    action="Confirm the finding with a follow-up measurement",
    timing="at the next scheduled interval",
    reassess="Reassess against this report when that measurement is in hand.",
    template=(
        "Confirm the finding with a follow-up measurement {timing}. Reassess against "
        "this report when that measurement is in hand."
    ),
)


def corrective_recommendation(fault: str, iso_zone: str | None) -> CorrectiveRecommendation:
    """The corrective recommendation for one committed fault, in three parts.

    `iso_zone` is the zone the report already prints ("A".."D",
    "not_assessable", or None when severity was never rated). It is read ONLY
    to word a timing for the two ids whose text names none — see `_ZONE_TIMING`,
    which is a text map and not a severity rule. Every other id returns the same
    timing in every zone.
    """
    row = _CORRECTIVE.get(fault, _DEFAULT_CORRECTIVE)
    timing = row.timing
    if timing is None:
        timing = _ZONE_TIMING.get(iso_zone or "", _ZONE_TIMING_FALLBACK)
    return CorrectiveRecommendation(
        fault=fault,
        action=row.action,
        timing=timing,
        reassess=row.reassess,
        text=row.template.format(timing=timing),
    )


def corrective_recommendations_for_findings(
    findings: list[Finding], iso_zone: str | None
) -> list[CorrectiveRecommendation]:
    """Ordered, de-duplicated corrective recommendations for the report's
    numbered Recommendations section — the same contract
    `synthesize.recommendations_for_findings` has, returning the three-part
    objects instead of bare strings. De-duplicated on the rendered text, so the
    three bearing faults collapse to one line exactly as they did before.
    """
    seen: set[str] = set()
    out: list[CorrectiveRecommendation] = []
    for finding in findings:
        rec = corrective_recommendation(finding.fault, iso_zone)
        if rec.text not in seen:
            seen.add(rec.text)
            out.append(rec)
    return out
