"""Findings synthesis: severity anchored to ISO zone, confidence carried from
the RCA match, conservative recommendations (never 'run to failure'), and
gate-fail → no findings.
"""

from __future__ import annotations

from vib_agent.models import Check, QualityGateResult
from vib_agent.pdm_core.iso_classify import classify, resolve_thresholds
from vib_agent.pdm_core.bearing_rca import peaks_from_ncd, run_rca
from vib_agent.pdm_core.synthesize import (
    recommend_for,
    recommendations_for_findings,
    synthesize_findings,
)
from vib_agent.models import SensorData
from tests.fixtures import REFERENCE_CASES


def _rca_and_reading(case_name, machines, iso_table, thresholds):
    case = REFERENCE_CASES[case_name]
    machine = machines[case["mac"]]
    sd = SensorData(**case["sensor_data"])
    resolved = resolve_thresholds(machine, iso_table)
    reading = classify(sd, machine, resolved)
    rca = run_rca(peaks_from_ncd(sd), machine, reading.iso_severity, thresholds)
    return reading, rca


def _pass_gate() -> QualityGateResult:
    return QualityGateResult(overall="warn", checks=[], train_baseline=False, train_reasons=[])


def test_severity_anchored_to_iso_zone(machines, iso_table, thresholds):
    reading, rca = _rca_and_reading("T12_bpfo_bearing_fault", machines, iso_table, thresholds)
    findings = synthesize_findings(reading, _pass_gate(), rca, None)
    bearing = next(f for f in findings if f.fault == "bearing_outer_race")
    assert bearing.severity == reading.iso_severity  # not the match's own confidence
    assert bearing.confidence == "high"  # confidence carries evidence strength separately


def test_gate_fail_yields_no_findings(machines, iso_table, thresholds):
    reading, rca = _rca_and_reading("T12_bpfo_bearing_fault", machines, iso_table, thresholds)
    fail_gate = QualityGateResult(
        overall="fail",
        checks=[Check(name="machine_running", status="fail", reason="off")],
        train_baseline=False,
        train_reasons=[],
    )
    assert synthesize_findings(reading, fail_gate, rca, None) == []


def test_healthy_zone_a_yields_no_significant_findings(machines, iso_table, thresholds):
    reading, rca = _rca_and_reading("T01_healthy_zone_a", machines, iso_table, thresholds)
    findings = synthesize_findings(reading, QualityGateResult(
        overall="pass", checks=[], train_baseline=True, train_reasons=[]
    ), rca, None)
    assert [f.fault for f in findings] == ["no_significant_findings"]
    assert findings[0].severity == "ok"


def _pass_or_warn_gate() -> QualityGateResult:
    return QualityGateResult(overall="warn", checks=[], train_baseline=False, train_reasons=[])


def test_not_assessable_no_fault_yields_unrated_not_ok(pump_machine, iso_table, thresholds):
    # Session A: acceleration-only reading, no fault matched -> the fallback
    # finding is "unrated", NEVER the old coerced Zone A / "ok" clean bill.
    resolved = resolve_thresholds(pump_machine, iso_table)
    reading = classify(SensorData(mode=0), pump_machine, resolved)
    assert reading.iso_zone == "not_assessable"
    findings = synthesize_findings(reading, _pass_or_warn_gate(), None, None)
    assert [f.fault for f in findings] == ["no_significant_findings"]
    assert findings[0].severity == "unrated"
    assert "unrated" in findings[0].reason.lower()
    assert "Zone A" not in findings[0].reason


def test_not_assessable_trend_finding_is_unrated(pump_machine, iso_table, thresholds):
    # A rising trend on acceleration-only data: report the rise, but severity is
    # unrated and the reason carries no mm/s alarm limit or ISO-boundary projection.
    from vib_agent.models import TrendResult

    resolved = resolve_thresholds(pump_machine, iso_table)
    reading = classify(SensorData(mode=0), pump_machine, resolved)
    trend = TrendResult(
        status="ok", severity="warn", n_days=30, slope=0.01, pct_change=50.0,
        baseline_avg=1.0, current_avg=1.5, alarm_limit=2.8, days_to_next_boundary=10.0,
    )
    findings = synthesize_findings(reading, _pass_or_warn_gate(), None, trend)
    trend_finding = next(f for f in findings if f.fault == "rising_trend")
    assert trend_finding.severity == "unrated"
    assert "mm/s" not in trend_finding.reason
    assert "ISO boundary" not in trend_finding.reason


def test_recommendations_are_conservative_never_run_to_failure():
    # Every canned recommendation must be conservative.
    from vib_agent.pdm_core.synthesize import _RECOMMENDATIONS

    banned = ("run to failure", "run until fail", "keep running until", "ignore")
    for fault, rec in _RECOMMENDATIONS.items():
        low = rec.lower()
        for phrase in banned:
            assert phrase not in low, f"{fault}: unsafe recommendation {rec!r}"


def test_recommend_for_bearing_plans_replacement():
    rec = recommend_for("bearing_outer_race")
    assert "replace" in rec.lower()
    assert "run to failure" not in rec.lower()


def test_recommendations_for_findings_dedup_and_order(machines, iso_table, thresholds):
    reading, rca = _rca_and_reading("T12_bpfo_bearing_fault", machines, iso_table, thresholds)
    findings = synthesize_findings(reading, _pass_gate(), rca, None)
    recs = recommendations_for_findings(findings)
    assert recs
    assert len(recs) == len(set(recs))
