"""Recommendations engine: each seeded rule fires on its trigger; the
gate-fail path emits re-capture advice through the same channel; dedup +
priority sort.
"""

from __future__ import annotations

from vib_agent.models import (
    Check,
    DifferentialCandidate,
    FaultMatch,
    MachineMeta,
    QualityGateResult,
)
from vib_agent.pdm_core.recommendations import recommend_measurements


def _gate(overall="pass", checks=None) -> QualityGateResult:
    return QualityGateResult(
        overall=overall, checks=checks or [], train_baseline=(overall == "pass"), train_reasons=[]
    )


def _machine(**kw) -> MachineMeta:
    base = dict(mac="M", name="M", active=True, type="motor", iso_group="2", iso_support="rigid", machine_type="motor")
    base.update(kw)
    return MachineMeta(**base)


def _bearing_match(confidence="high") -> FaultMatch:
    return FaultMatch(
        fault="bearing_outer_race", description="d", axis="y", confidence=confidence, evidence="e"
    )


def test_unresolved_1x2x_differential_triggers_phase_analysis(rules):
    diff = [DifferentialCandidate(fault="imbalance", description="d", confidence="low", adjudication="a")]
    primary = [
        FaultMatch(fault="angular_misalignment", description="d", axis="x", confidence="medium", evidence="e")
    ]
    recs = recommend_measurements(primary, diff, _gate(), _machine(), None, rules)
    techniques = [r.technique for r in recs]
    assert any("phase analysis" in t.lower() for t in techniques)


def test_low_confidence_bearing_triggers_enveloping(rules):
    primary = [_bearing_match(confidence="low")]
    recs = recommend_measurements(primary, [], _gate("warn"), _machine(), None, rules)
    assert any("envelop" in r.technique.lower() or "peakvue" in r.technique.lower() for r in recs)


def test_high_confidence_bearing_needs_no_measurement(rules):
    primary = [_bearing_match(confidence="high")]
    recs = recommend_measurements(primary, [], _gate("warn"), _machine(), None, rules)
    # A clean, committed, resolved diagnosis emits nothing to collect.
    assert recs == []


def test_suspected_resonance_triggers_bump_test(rules):
    primary = [
        FaultMatch(fault="possible_resonance", description="d", axis="x", confidence="low", evidence="e")
    ]
    recs = recommend_measurements(primary, [], _gate("warn"), _machine(type="fan"), None, rules)
    assert any("bump test" in r.technique.lower() or "coast-down" in r.technique.lower() for r in recs)


def test_gate_fail_emits_recapture_advice(rules):
    checks = [
        Check(name="machine_running", status="fail", reason="all axes below min_running_g"),
    ]
    recs = recommend_measurements([], [], _gate("fail", checks), _machine(), None, rules)
    assert recs, "gate-fail must still name what to collect"
    assert any("re-measure" in r.technique.lower() for r in recs)


def test_flat_and_plateau_checks_emit_data_quality_recs(rules):
    # Session DQ-FLAGS: `clipping` became `spectral_plateau`, and its advice
    # deliberately no longer says "gain"/"range" -- a flat-topped SPECTRUM is
    # not what sensor saturation produces, so pointing the analyst at the sensor
    # range was advice this evidence could not support. The pinned property is
    # unchanged: each warning/failing data-quality check emits its own actionable
    # follow-up, and the two here do not collapse into one.
    checks = [
        Check(name="spectrum_non_flat", status="fail", reason="flat"),
        Check(name="spectral_plateau", status="warn", reason="plateau"),
    ]
    recs = recommend_measurements([], [], _gate("fail", checks), _machine(), None, rules)
    techniques = " ".join(r.technique.lower() for r in recs)
    assert "mounting" in techniques  # spectrum_non_flat advice
    assert "amplitude-capped" in techniques  # spectral_plateau advice
    assert len({r.technique for r in recs}) == 2


def test_dedup_and_priority_sort(rules):
    # Two triggers that both map to phase analysis-ish? Use one high + one
    # medium and confirm high sorts first and no duplicate techniques remain.
    primary = [
        FaultMatch(fault="angular_misalignment", description="d", axis="x", confidence="medium", evidence="e")
    ]
    diff = [DifferentialCandidate(fault="imbalance", description="d", confidence="low", adjudication="a")]
    recs = recommend_measurements(primary, diff, _gate("warn"), _machine(), None, rules)
    techniques = [r.technique for r in recs]
    assert len(techniques) == len(set(techniques))  # deduped
    priorities = [r.priority for r in recs]
    order = {"high": 0, "medium": 1, "low": 2}
    assert priorities == sorted(priorities, key=lambda p: order[p])  # sorted


# ── Session BENT-FIX: the committed 1x family splits on the coupling ──────
#
# `bent_shaft` stays in `_UNRESOLVED_1X2X` — as a DIFFERENTIAL candidate it is
# still one of the things phase work separates. What changed is the COMMITTED
# case: `bearing_rca` commits `bent_shaft` only where `machine.coupled` is
# False, so the alignment-and-thermography rule was sending analysts across a
# coupling their own report had ruled out (SESSION_GEOMB.md §7, finding 1).


def _misalignment_match(fault, confidence="medium") -> FaultMatch:
    return FaultMatch(fault=fault, description="d", axis="x", confidence=confidence, evidence="e")


def test_committed_bent_shaft_asks_for_phase_and_runout_not_an_alignment(rules):
    recs = recommend_measurements(
        [_misalignment_match("bent_shaft")], [], _gate(), _machine(coupled=False), None, rules
    )
    blob = " ".join(f"{r.technique} {r.purpose} {r.trigger}" for r in recs).lower()
    assert "runout" in blob and "phase" in blob, blob
    assert "coupl" not in blob, blob


def test_committed_angular_misalignment_still_asks_for_the_alignment(rules):
    """The rule is narrowed by one fault, not disabled."""
    recs = recommend_measurements(
        [_misalignment_match("angular_misalignment")], [], _gate(), _machine(), None, rules
    )
    techniques = [r.technique.lower() for r in recs]
    assert any("alignment" in t for t in techniques), techniques
    assert not any("runout" in t for t in techniques), techniques


def test_a_bent_shaft_in_the_differential_still_reads_as_unresolved(rules):
    """`bent_shaft` was NOT removed from the unresolved-differential set: an
    uncommitted bent shaft is exactly the ambiguity cross-coupling phase work
    exists to settle. Only the committed case moved."""
    diff = [DifferentialCandidate(fault="bent_shaft", description="d", confidence="low",
                                  adjudication="a")]
    recs = recommend_measurements([], diff, _gate(), _machine(), None, rules)
    assert any("phase analysis" in r.technique.lower() for r in recs), recs


def test_low_confidence_bent_shaft_is_not_committed_enough_to_answer_for(rules):
    """Same confidence bar as the rule it split from — a `low` call does not
    earn a corrective-outage measurement on either side of the split."""
    recs = recommend_measurements(
        [_misalignment_match("bent_shaft", confidence="low")], [], _gate(),
        _machine(coupled=False), None, rules
    )
    assert not any("runout" in r.technique.lower() for r in recs), recs
