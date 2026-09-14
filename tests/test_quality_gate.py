"""Quality gate tests: DROP-equivalent (fail) rules, EVAL-ONLY (warn) rules,
and the additive metadata/units/spectrum checks.
"""

from __future__ import annotations

from tests.fixtures import REFERENCE_CASES
from vib_agent.models import LifecycleState, SensorData
from vib_agent.pdm_core.iso_classify import classify, resolve_thresholds
from vib_agent.pdm_core.quality_gate import run_quality_gate


def _gate_for(case_name: str, machines: dict, iso_table: dict, thresholds: dict, **kwargs):
    case = REFERENCE_CASES[case_name]
    machine = machines[case["mac"]]
    sensor_data = SensorData(**case["sensor_data"])
    resolved = resolve_thresholds(machine, iso_table)
    reading = classify(sensor_data, machine, resolved)
    result = run_quality_gate(
        reading,
        sensor_data,
        machine,
        thresholds["quality_gate"],
        battery_percent=case.get("battery_percent"),
        **kwargs,
    )
    return reading, result


def test_healthy_reading_passes_and_trains(machines, iso_table, thresholds):
    _, result = _gate_for("T01_healthy_zone_a", machines, iso_table, thresholds)
    assert result.overall == "pass"
    assert result.train_baseline is True
    assert result.train_reasons == []


def test_machine_off_fails_gate(machines, iso_table, thresholds):
    # T02: all acceleration axes below min_running_g (0.010g default).
    _, result = _gate_for("T02_machine_off", machines, iso_table, thresholds)
    assert result.overall == "fail"
    assert result.train_baseline is False
    machine_running = next(c for c in result.checks if c.name == "machine_running")
    assert machine_running.status == "fail"


def test_elevated_iso_zone_warns_and_skips_training(machines, iso_table, thresholds):
    # T05 lands in Zone C.
    reading, result = _gate_for("T05_bc_boundary_zone_c", machines, iso_table, thresholds)
    assert reading.iso_zone == "C"
    assert result.overall == "warn"
    assert result.train_baseline is False
    assert any(r.startswith("iso_zone_C") for r in result.train_reasons)


def test_moff_msg_type_fails_gate(machines, iso_table, thresholds):
    case = REFERENCE_CASES["T01_healthy_zone_a"]
    machine = machines[case["mac"]]
    sensor_data = SensorData(**{**case["sensor_data"], "msg_type": "MOFF"})
    resolved = resolve_thresholds(machine, iso_table)
    reading = classify(sensor_data, machine, resolved)
    result = run_quality_gate(reading, sensor_data, machine, thresholds["quality_gate"])
    assert result.overall == "fail"
    msg_type_check = next(c for c in result.checks if c.name == "msg_type")
    assert msg_type_check.status == "fail"


def test_configuring_lifecycle_fails_gate(machines, iso_table, thresholds):
    reading, result = _gate_for(
        "T01_healthy_zone_a",
        machines,
        iso_table,
        thresholds,
        lifecycle=LifecycleState(configuring=True),
    )
    assert result.overall == "fail"
    assert result.train_baseline is False


def test_motion_msg_type_warns_but_does_not_fail(machines, iso_table, thresholds):
    case = REFERENCE_CASES["T01_healthy_zone_a"]
    machine = machines[case["mac"]]
    sensor_data = SensorData(**{**case["sensor_data"], "msg_type": "MOTION"})
    resolved = resolve_thresholds(machine, iso_table)
    reading = classify(sensor_data, machine, resolved)
    result = run_quality_gate(reading, sensor_data, machine, thresholds["quality_gate"])
    assert result.overall == "warn"
    assert result.train_baseline is False
    assert "motion_msg_type" in result.train_reasons


def test_startup_transient_warns_and_skips_training(machines, iso_table, thresholds):
    reading, result = _gate_for(
        "T01_healthy_zone_a",
        machines,
        iso_table,
        thresholds,
        lifecycle=LifecycleState(startup_counter=1),
    )
    assert result.overall == "warn"
    assert result.train_baseline is False
    assert any(r.startswith("startup_transient") for r in result.train_reasons)


def test_rpm_off_nominal_warns(machines, iso_table, thresholds):
    case = REFERENCE_CASES["T01_healthy_zone_a"]  # rpm=1800
    machine = machines[case["mac"]].model_copy(update={"rpm_nominal": 3600, "rpm_tolerance_pct": 5})
    sensor_data = SensorData(**case["sensor_data"])
    resolved = resolve_thresholds(machine, iso_table)
    reading = classify(sensor_data, machine, resolved)
    result = run_quality_gate(reading, sensor_data, machine, thresholds["quality_gate"])
    assert result.overall == "warn"
    assert any(r.startswith("rpm_off_nominal") for r in result.train_reasons)


def test_temperature_out_of_range_warns(machines, iso_table, thresholds):
    case = REFERENCE_CASES["T01_healthy_zone_a"]  # temperature=23.67
    machine = machines[case["mac"]].model_copy(update={"temp_min_c": 30.0, "temp_max_c": 40.0})
    sensor_data = SensorData(**case["sensor_data"])
    resolved = resolve_thresholds(machine, iso_table)
    reading = classify(sensor_data, machine, resolved)
    result = run_quality_gate(reading, sensor_data, machine, thresholds["quality_gate"])
    assert result.overall == "warn"
    assert any(r.startswith("temp_out_of_range") for r in result.train_reasons)


def test_low_battery_warns(machines, iso_table, thresholds):
    case = REFERENCE_CASES["T01_healthy_zone_a"]
    machine = machines[case["mac"]].model_copy(update={"min_battery_pct": 20.0})
    sensor_data = SensorData(**case["sensor_data"])
    resolved = resolve_thresholds(machine, iso_table)
    reading = classify(sensor_data, machine, resolved)
    result = run_quality_gate(
        reading, sensor_data, machine, thresholds["quality_gate"], battery_percent=5.0
    )
    assert result.overall == "warn"
    assert any(r.startswith("battery_low") for r in result.train_reasons)


def test_spectrum_checks_not_applicable_without_spectrum(machines, iso_table, thresholds):
    _, result = _gate_for("T01_healthy_zone_a", machines, iso_table, thresholds)
    # Session DQ-FLAGS renamed `clipping` -> `spectral_plateau`. Every
    # spectrum-only check must be not_applicable — never `pass` — when no
    # spectrum was supplied, so an unasked question is never reported as an
    # answered one.
    spectrum_checks = {
        "spectrum_non_flat",
        "spectral_plateau",
        "ski_slope",
        "sensor_settling",
        "ac_coupling_rolloff",
        "line_frequency_content",
        "speed_sanity",
    }
    for check in result.checks:
        if check.name in spectrum_checks:
            assert check.status == "not_applicable"


def test_absent_velocity_units_check_warns_with_honest_reason(pump_machine, iso_table, thresholds):
    # Session A: acceleration-only reading (severity_rms is None) -> units_plausibility
    # still WARNs (so gate.overall stays "warn" and the downstream RCA gate_warnings
    # signal is unchanged), but with a reason that says velocity wasn't measured
    # rather than implying an implausibly-low measured value.
    sensor_data = SensorData(mode=0)  # no velocity on any axis
    resolved = resolve_thresholds(pump_machine, iso_table)
    reading = classify(sensor_data, pump_machine, resolved)
    result = run_quality_gate(reading, sensor_data, pump_machine, thresholds["quality_gate"])
    units = next(c for c in result.checks if c.name == "units_plausibility")
    assert units.status == "warn"
    assert "velocity not measured" in units.reason
    assert "outside sane range" not in units.reason


def test_iso_zone_elevated_not_applicable_when_not_assessable(pump_machine, iso_table, thresholds):
    # not_assessable reading -> the elevated-zone check has nothing to assess;
    # it is not_applicable (not "pass", which would imply a non-elevated zone).
    sensor_data = SensorData(mode=0)
    resolved = resolve_thresholds(pump_machine, iso_table)
    reading = classify(sensor_data, pump_machine, resolved)
    assert reading.iso_zone == "not_assessable"
    result = run_quality_gate(reading, sensor_data, pump_machine, thresholds["quality_gate"])
    elevated = next(c for c in result.checks if c.name == "iso_zone_elevated")
    assert elevated.status == "not_applicable"


def test_t02_present_but_low_velocity_reason_string_unchanged(machines, iso_table, thresholds):
    # REGRESSION (Session A): T02 carries PRESENT velocities (0.002-0.005 mm/s),
    # below rms_sane_min -> units_plausibility WARNs with the ORIGINAL measured-
    # value reason. This byte-stability pin guards the present-but-implausible
    # path against being merged into the new absent-velocity wording (the two
    # cases must stay distinct — see the reference-fixture byte-identity floor).
    reading, result = _gate_for("T02_machine_off", machines, iso_table, thresholds)
    assert reading.severity_rms == 0.005  # present, not None
    units = next(c for c in result.checks if c.name == "units_plausibility")
    assert units.status == "warn"
    assert "severity_rms 0.005 outside sane range" in units.reason
