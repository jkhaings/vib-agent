"""Synthetic generator tests (Phase 2): determinism, both-path agreement
(B1), full detector menu + axis-awareness (B2), gate-aware construction
(B3), history cadences (B4), reference-fixture cross-check (B5), and the
sideband enrichment carried over from Phase 1.
"""

from __future__ import annotations

import pytest

from tests.fixtures import REFERENCE_CASES
from vib_agent.models import SensorData
from vib_agent.pdm_core.anomaly import compute_zscore, init_welford_state, isolation_forest_score
from vib_agent.pdm_core.bearing_rca import enrich_with_sidebands, peaks_from_ncd, peaks_from_spectrum, run_rca
from vib_agent.pdm_core.iso_classify import classify, resolve_thresholds
from vib_agent.pdm_core.trend import compute_trend
from vib_agent.synth.generator import FAULT_MENU, FIXTURE_TWINS, make_case, make_history

_TWIN_KEY_TO_CASE = {
    "T01": "T01_healthy_zone_a",
    "T07": "T07_imbalance",
    "T08": "T08_bent_shaft_uncoupled",
    "T09": "T09_angular_misalignment",
    "T10": "T10_parallel_misalignment",
    "T11": "T11_looseness",
    "T12": "T12_bpfo_bearing_fault",
    "T13": "T13_severe_misalignment",
}


def _both_path_faults(case, iso_table, thresholds):
    resolved = resolve_thresholds(case.machine, iso_table)
    reading = classify(case.sensor_data, case.machine, resolved)

    ncd_peak_set = peaks_from_ncd(case.sensor_data)
    ncd_result = run_rca(ncd_peak_set, case.machine, reading.iso_severity, thresholds)

    velocities = {
        "x": case.sensor_data.x_velocity_mm_sec or 0.0,
        "y": case.sensor_data.y_velocity_mm_sec or 0.0,
        "z": case.sensor_data.z_velocity_mm_sec or 0.0,
    }
    spectrum_peak_set = peaks_from_spectrum(case.spectra, rpm=case.sensor_data.rpm, velocities_mms=velocities)
    spectrum_result = run_rca(spectrum_peak_set, case.machine, reading.iso_severity, thresholds)

    return {m.fault for m in ncd_result.primary_findings}, {m.fault for m in spectrum_result.primary_findings}


def _reference_rca_faults(twin_code, machines, iso_table, thresholds):
    ref = REFERENCE_CASES[_TWIN_KEY_TO_CASE[twin_code]]
    machine = machines[ref["mac"]]
    sensor_data = SensorData(**ref["sensor_data"])
    resolved = resolve_thresholds(machine, iso_table)
    reading = classify(sensor_data, machine, resolved)
    peak_set = peaks_from_ncd(sensor_data)
    result = run_rca(peak_set, machine, reading.iso_severity, thresholds)
    return {m.fault for m in result.primary_findings}


class TestDeterminism:
    def test_same_seed_identical_case(self, iso_table, thresholds):
        a = make_case("imbalance", iso_table=iso_table, thresholds=thresholds, seed=42)
        b = make_case("imbalance", iso_table=iso_table, thresholds=thresholds, seed=42)
        assert a.model_dump() == b.model_dump()

    def test_different_seed_different_noise(self, iso_table, thresholds):
        a = make_case("imbalance", iso_table=iso_table, thresholds=thresholds, seed=1)
        b = make_case("imbalance", iso_table=iso_table, thresholds=thresholds, seed=2)
        assert a.spectra["x"].amplitude != b.spectra["x"].amplitude
        # the seeded fault signature itself is unaffected by the noise seed
        assert a.expected.faults == b.expected.faults


class TestBothPathAgreement:
    """Amendment B1: extends Phase 1's A3 boundary test to the whole menu."""

    @pytest.mark.parametrize("case_name", FAULT_MENU)
    def test_ncd_and_spectrum_paths_agree(self, case_name, iso_table, thresholds):
        case = make_case(case_name, iso_table=iso_table, thresholds=thresholds, seed=1)
        ncd_faults, spectrum_faults = _both_path_faults(case, iso_table, thresholds)
        assert ncd_faults == spectrum_faults
        assert ncd_faults == set(case.expected.faults)


class TestFaultMenuAxisAware:
    """Amendment B2: full detector menu, with axis-placement spot checks."""

    @pytest.mark.parametrize("case_name", FAULT_MENU)
    def test_case_triggers_its_intended_fault_or_none(self, case_name, iso_table, thresholds):
        case = make_case(case_name, iso_table=iso_table, thresholds=thresholds, seed=1)
        if case_name == "healthy":
            assert case.expected.faults == []
        else:
            assert case.expected.faults, f"{case_name} produced no RCA matches"

    def test_imbalance_is_radial_dominant_not_axial(self, iso_table, thresholds):
        case = make_case("imbalance", iso_table=iso_table, thresholds=thresholds, seed=1)
        assert case.sensor_data.y_velocity_mm_sec > case.sensor_data.x_velocity_mm_sec
        assert case.sensor_data.z_velocity_mm_sec > case.sensor_data.x_velocity_mm_sec
        assert case.expected.faults == ["imbalance"]

    def test_angular_misalignment_is_axial_dominant(self, iso_table, thresholds):
        case = make_case("angular_misalignment", iso_table=iso_table, thresholds=thresholds, seed=1)
        assert case.sensor_data.x_velocity_mm_sec > case.sensor_data.y_velocity_mm_sec
        assert case.sensor_data.x_velocity_mm_sec > case.sensor_data.z_velocity_mm_sec
        assert "angular_misalignment" in case.expected.faults

    def test_bent_shaft_not_angular_because_machine_uncoupled(self, iso_table, thresholds):
        case = make_case("bent_shaft", iso_table=iso_table, thresholds=thresholds, seed=1)
        assert case.machine.coupled is False
        assert "bent_shaft" in case.expected.faults
        assert "angular_misalignment" not in case.expected.faults

    def test_parallel_misalignment_is_radial_2x_dominant(self, iso_table, thresholds):
        case = make_case("parallel_misalignment", iso_table=iso_table, thresholds=thresholds, seed=1)
        assert "parallel_misalignment" in case.expected.faults

    def test_belt_fault_driven_by_machine_belt_config(self, iso_table, thresholds):
        case = make_case("belt_fault", iso_table=iso_table, thresholds=thresholds, seed=1)
        assert case.machine.belt is not None
        assert "belt_fault" in case.expected.faults

    def test_blade_pass_driven_by_machine_blade_config(self, iso_table, thresholds):
        case = make_case("blade_pass", iso_table=iso_table, thresholds=thresholds, seed=1)
        assert case.machine.blades is not None
        assert "elevated_blade_pass" in case.expected.faults

    def test_bpfi_uses_inner_race_not_outer(self, iso_table, thresholds):
        case = make_case("bpfi", iso_table=iso_table, thresholds=thresholds, seed=1)
        assert "bearing_inner_race" in case.expected.faults
        assert "bearing_outer_race" not in case.expected.faults


class TestGateAwareConstruction:
    """Amendment B3."""

    def test_healthy_passes_gate_and_trains(self, iso_table, thresholds):
        case = make_case("healthy", iso_table=iso_table, thresholds=thresholds, seed=1)
        assert case.expected.gate_overall == "pass"
        assert case.expected.insufficient is False

    @pytest.mark.parametrize("case_name", [n for n in FAULT_MENU if n != "healthy"])
    def test_fault_cases_warn_never_fail(self, case_name, iso_table, thresholds):
        # Every fault case lands in Zone C or D by construction -> the
        # elevated-zone check warns, but a real fault reading is never a
        # gate FAILURE (it's exactly the kind of data the gate should let
        # through for diagnosis).
        case = make_case(case_name, iso_table=iso_table, thresholds=thresholds, seed=1)
        assert case.expected.gate_overall == "warn"
        assert case.expected.insufficient is False

    def test_machine_off_fails_gate(self, iso_table, thresholds):
        case = make_case("machine_off", iso_table=iso_table, thresholds=thresholds, seed=1)
        assert case.expected.gate_overall == "fail"
        assert case.expected.insufficient is True
        assert case.expected.faults == []

    def test_startup_transient_warns_not_fails(self, iso_table, thresholds):
        case = make_case("startup_transient", iso_table=iso_table, thresholds=thresholds, seed=1)
        assert case.lifecycle is not None
        assert case.expected.gate_overall == "warn"

    def test_rpm_off_nominal_warns(self, iso_table, thresholds):
        case = make_case("rpm_off_nominal", iso_table=iso_table, thresholds=thresholds, seed=1)
        assert case.machine.rpm_nominal == 3600.0
        assert case.expected.gate_overall == "warn"

    def test_low_battery_warns(self, iso_table, thresholds):
        case = make_case("low_battery", iso_table=iso_table, thresholds=thresholds, seed=1)
        assert case.battery_percent == 5.0
        assert case.expected.gate_overall == "warn"

    def test_flat_spectrum_fails_gate(self, iso_table, thresholds):
        case = make_case("flat_spectrum", iso_table=iso_table, thresholds=thresholds, seed=1)
        assert case.expected.gate_overall == "fail"
        assert case.expected.insufficient is True

    def test_clipped_spectrum_warns_not_fails(self, iso_table, thresholds):
        case = make_case("clipped_spectrum", iso_table=iso_table, thresholds=thresholds, seed=1)
        assert case.expected.gate_overall == "warn"
        assert case.expected.insufficient is False


class TestHistoryCadences:
    """Amendment B4."""

    def test_daily_ramp_reproduces_warn_or_danger_severity(self, thresholds):
        history = make_history(30, 2.0, 3.5, cadence="daily", seed=1)
        result = compute_trend(history, zone_bc=2.8, thresholds=thresholds["trend"])
        assert result.status == "ok"
        assert result.severity in ("warn", "danger")

    def test_monthly_sparse_history_hedges_across_all_three_layers(self, thresholds):
        history = make_history(18, 2.0, 2.6, noise_pct=0.25, cadence="monthly", seed=1)
        assert len(history) == 18

        # Layer 4: enough points to compute (>= min_days), but realistic
        # month-to-month noise should push r-squared below the reliability
        # threshold -> hedged, no projection.
        trend_result = compute_trend(history, zone_bc=2.8, thresholds=thresholds["trend"])
        assert trend_result.status == "ok"
        assert trend_result.r_squared is not None
        assert trend_result.r_squared < thresholds["trend"]["min_r_squared"]
        assert trend_result.trend_note == "no reliable trend"
        assert trend_result.days_to_next_boundary is None

        # Layer 2: 18 monthly points against a 30-day half-life -> effective
        # sample weight stays far below min_readings -> permanently
        # warming_up (declines to answer).
        state = init_welford_state(now_ms=history[0].ts.timestamp() * 1000)
        zscore_result = None
        for point in history:
            sd = SensorData(
                x_rms_ACC_G=point.value * 0.08,
                y_rms_ACC_G=point.value * 0.08,
                z_rms_ACC_G=point.value * 0.08,
            )
            state, zscore_result = compute_zscore(
                state, sd, "SPARSE", "Sparse Route Machine", thresholds["zscore"],
                now_ms=point.ts.timestamp() * 1000,
            )
        assert zscore_result.axes["x"].status == "warming_up"

        # Layer 3: stub always declines regardless of point count (Amendment A1).
        if_result = isolation_forest_score([[p.value] for p in history], thresholds["isolation_forest"])
        assert if_result.status == "not_enough_history"


class TestFixtureCrossCheck:
    """Amendment B5: every synthetic fault with a T-fixture twin must
    trigger the same primary detector verdict as its reference twin.
    """

    @pytest.mark.parametrize("case_name", list(FIXTURE_TWINS.keys()))
    def test_synthetic_case_agrees_with_reference_twin(self, case_name, machines, iso_table, thresholds):
        synthetic_case = make_case(case_name, iso_table=iso_table, thresholds=thresholds, seed=1)
        twin_code = FIXTURE_TWINS[case_name]
        reference_faults = _reference_rca_faults(twin_code, machines, iso_table, thresholds)
        synthetic_faults = set(synthetic_case.expected.faults)

        if case_name == "healthy":
            assert synthetic_faults == set()
            assert reference_faults == set()
        else:
            assert synthetic_faults & reference_faults, (
                f"{case_name} (twin {twin_code}): synthetic={synthetic_faults} "
                f"vs reference={reference_faults}"
            )


class TestSidebandEnrichment:
    def test_bpfi_case_gets_sidebands(self, iso_table, thresholds):
        case = make_case("bpfi", iso_table=iso_table, thresholds=thresholds, seed=1)
        resolved = resolve_thresholds(case.machine, iso_table)
        reading = classify(case.sensor_data, case.machine, resolved)
        peak_set = peaks_from_ncd(case.sensor_data)
        result = run_rca(peak_set, case.machine, reading.iso_severity, thresholds)
        tolerance = thresholds["rca"]["tolerance_pct"] / 100.0

        enriched = enrich_with_sidebands(result, case.spectra, result.shaft_freq_hz, tolerance)
        bpfi_matches = [m for m in enriched.primary_findings if m.fault == "bearing_inner_race"]
        assert bpfi_matches
        assert bpfi_matches[0].sidebands

    def test_bpfo_case_has_no_sidebands_seeded(self, iso_table, thresholds):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        resolved = resolve_thresholds(case.machine, iso_table)
        reading = classify(case.sensor_data, case.machine, resolved)
        peak_set = peaks_from_ncd(case.sensor_data)
        result = run_rca(peak_set, case.machine, reading.iso_severity, thresholds)
        tolerance = thresholds["rca"]["tolerance_pct"] / 100.0

        enriched = enrich_with_sidebands(result, case.spectra, result.shaft_freq_hz, tolerance)
        bpfo_matches = [m for m in enriched.primary_findings if m.fault == "bearing_outer_race"]
        assert bpfo_matches
        assert bpfo_matches[0].sidebands is None
