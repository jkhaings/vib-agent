"""Pipeline orchestration tests: BPFO -> committed bearing finding with
sidebands only when spectra are present; gate-fail -> insufficient result
with recommendations still emitted; trend history -> rising_trend finding;
healthy -> no_significant_findings; and a regression proving
analysis_to_expected reproduces the generator's own expected outcome across
every seeded case (the generator now delegates to run_analysis for exactly
this reason).
"""

from __future__ import annotations

import pytest

from vib_agent.models import HistoryPoint
from vib_agent.pipeline import analysis_to_expected, run_analysis
from vib_agent.synth.generator import (
    FAULT_MENU,
    GATE_VIOLATION_MENU,
    make_case,
)


class TestRunAnalysis:
    def test_bpfo_case_yields_committed_bearing_finding(self, iso_table, thresholds, rules):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        assert result.rca is not None
        faults = {m.fault for m in result.rca.primary_findings}
        assert "bearing_outer_race" in faults
        assert any(f.fault == "bearing_outer_race" for f in result.findings)

    def test_sidebands_only_enriched_when_spectra_present(self, iso_table, thresholds, rules):
        case = make_case("bpfi", iso_table=iso_table, thresholds=thresholds, seed=1)
        with_spectra = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        bpfi = next(m for m in with_spectra.rca.primary_findings if m.fault == "bearing_inner_race")
        assert bpfi.sidebands

        no_spectra_case = case.model_copy(update={"spectra": None})
        without_spectra = run_analysis(no_spectra_case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        bpfi2 = next(m for m in without_spectra.rca.primary_findings if m.fault == "bearing_inner_race")
        assert bpfi2.sidebands is None

    def test_gate_fail_case_is_insufficient_but_still_recommends(self, iso_table, thresholds, rules):
        case = make_case("machine_off", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        assert result.quality_gate.overall == "fail"
        assert result.iso is not None
        assert result.findings == []
        assert result.rca is None
        assert result.recommended_measurements  # still says what to collect

    def test_healthy_case_yields_no_significant_findings(self, iso_table, thresholds, rules):
        case = make_case("healthy", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        assert [f.fault for f in result.findings] == ["no_significant_findings"]

    def test_trend_history_yields_rising_trend_finding(self, iso_table, thresholds, rules):
        case = make_case("healthy", iso_table=iso_table, thresholds=thresholds, seed=1)
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        history = [
            HistoryPoint(ts=now - timedelta(days=(30 - i)), value=2.0 + (3.5 - 2.0) * i / 29)
            for i in range(30)
        ]
        case = case.model_copy(update={"history": history})
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        assert result.trend is not None
        assert result.trend.severity in ("warn", "danger")
        assert any(f.fault == "rising_trend" for f in result.findings)
        assert result.isolation_forest is not None
        assert result.isolation_forest.status == "not_enough_history"
        assert result.zscore is None  # declines: no streaming baseline in a single-file analysis

    def test_implausible_velocity_downgrades_to_not_assessable(self, iso_table, thresholds, rules):
        # 7B fix (b): a PRESENT but implausible velocity (500 mm/s > sane max)
        # must not anchor an ISO zone. The gate WARNs (not fails), the pipeline
        # downgrades the reading to not_assessable("implausible velocity units")
        # preserving the measured value, every finding renders "unrated", and the
        # velocity-coverage follow-up fires.
        case = make_case("healthy", iso_table=iso_table, thresholds=thresholds, seed=1)
        sd = case.sensor_data.model_copy(
            update={"x_velocity_mm_sec": 500.0, "y_velocity_mm_sec": 500.0, "z_velocity_mm_sec": 500.0}
        )
        case = case.model_copy(update={"sensor_data": sd})
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        assert result.quality_gate.overall == "warn"  # NOT fail
        assert result.iso.iso_zone == "not_assessable"
        assert result.iso.not_assessable_reason == "implausible velocity units"
        assert result.iso.severity_rms == 500.0  # measured value preserved
        assert result.findings and all(f.severity == "unrated" for f in result.findings)
        assert any(
            "Velocity measurement per ISO 20816" in m.technique for m in result.recommended_measurements
        )

    def test_gate_fail_present_low_velocity_not_downgraded(self, machines, iso_table, thresholds, rules):
        # REGRESSION (Session A): T02 carries present-but-low velocity and gate-FAILS
        # (machine off). The implausible-units downgrade is exempt on gate-fail, so
        # the reading keeps its real zone (never flips to not_assessable) and no
        # velocity-coverage follow-up is emitted. Guards the 23-fixture byte floor.
        from tests.fixtures import REFERENCE_CASES
        from vib_agent.models import Case, SensorData

        spec = REFERENCE_CASES["T02_machine_off"]
        case = Case(
            name="t02", machine=machines[spec["mac"]],
            sensor_data=SensorData(**spec["sensor_data"]), battery_percent=spec.get("battery_percent"),
        )
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        assert result.quality_gate.overall == "fail"
        assert result.iso.iso_zone == "A"
        assert result.iso.not_assessable_reason is None
        assert not any(
            "Velocity measurement per ISO 20816" in m.technique for m in result.recommended_measurements
        )

    def test_no_numbers_invented_every_finding_traces_to_a_layer(self, iso_table, thresholds, rules):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        bearing_finding = next(f for f in result.findings if f.fault == "bearing_outer_race")
        rca_match = next(m for m in result.rca.primary_findings if m.fault == "bearing_outer_race")
        assert bearing_finding.evidence["freq_hz"] == rca_match.freq_hz
        assert bearing_finding.evidence["expected_hz"] == rca_match.expected_hz


class TestAnalysisToExpectedRegression:
    """The generator's expected block is now derived FROM run_analysis
    (via analysis_to_expected) — this proves projecting a fresh
    AnalysisResult reproduces exactly the Case's own stored `expected`.
    """

    @pytest.mark.parametrize("case_name", list(FAULT_MENU) + list(GATE_VIOLATION_MENU))
    def test_projection_matches_stored_expected(self, case_name, iso_table, thresholds):
        case = make_case(case_name, iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds)
        projected = analysis_to_expected(result)
        assert projected == case.expected
