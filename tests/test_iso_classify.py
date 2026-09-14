"""Layer 1 tests: threshold resolution (3-tier priority) and zone classification.

Zone-boundary assertions use the reference's own T01–T06 fixtures; severity_rms
is recomputed here so each expected zone is traceable to the ISO table values.
"""

from __future__ import annotations

import pytest

from tests.fixtures import REFERENCE_CASES
from vib_agent.models import MachineThresholds, SensorData
from vib_agent.pdm_core.iso_classify import classify, resolve_thresholds


def _reading_for(case_name: str, machines: dict, iso_table: dict):
    case = REFERENCE_CASES[case_name]
    machine = machines[case["mac"]]
    sensor_data = SensorData(**case["sensor_data"])
    resolved = resolve_thresholds(machine, iso_table)
    return classify(sensor_data, machine, resolved)


class TestResolveThresholds:
    def test_machine_override_wins(self, pump_machine, iso_table):
        pump_machine = pump_machine.model_copy(
            update={"thresholds": MachineThresholds(ab=9.0, bc=10.0, cd=11.0)}
        )
        resolved = resolve_thresholds(pump_machine, iso_table)
        assert resolved.source == "custom"
        assert resolved.thresholds.ab == 9.0

    def test_factory_default_wins_over_iso_table(self, pump_machine, iso_table):
        factory_default = MachineThresholds(ab=5.0, bc=6.0, cd=7.0)
        resolved = resolve_thresholds(pump_machine, iso_table, factory_default=factory_default)
        assert resolved.source == "factory_default"
        assert resolved.thresholds.ab == 5.0

    def test_iso_table_is_the_fallback(self, pump_machine, iso_table):
        resolved = resolve_thresholds(pump_machine, iso_table)
        assert resolved.source == "iso_20816_3"
        assert resolved.thresholds.ab == 1.4  # group2_rigid
        assert resolved.thresholds.bc == 2.8
        assert resolved.thresholds.cd == 4.5

    def test_raises_when_unresolvable(self, iso_table):
        from vib_agent.models import MachineMeta

        machine = MachineMeta(mac="X", name="No Config", active=True)
        with pytest.raises(ValueError, match="cannot resolve"):
            resolve_thresholds(machine, iso_table)

    def test_raises_on_unknown_iso_key(self, iso_table):
        from vib_agent.models import MachineMeta

        # iso_group/support individually valid, but combination not present
        # in a deliberately incomplete table.
        machine = MachineMeta(mac="X", name="Y", active=True, iso_group="1", iso_support="rigid")
        with pytest.raises(ValueError, match="unknown ISO key"):
            resolve_thresholds(machine, {"2_rigid": {"ab": 1, "bc": 2, "cd": 3}})


class TestClassify:
    def test_t01_healthy_zone_a(self, machines, iso_table):
        r = _reading_for("T01_healthy_zone_a", machines, iso_table)
        assert r.iso_zone == "A"
        assert r.iso_severity == "ok"
        assert r.severity_rms == pytest.approx(0.3)
        assert r.dominant_axis == "y"

    def test_t02_machine_off_still_zone_a(self, machines, iso_table):
        r = _reading_for("T02_machine_off", machines, iso_table)
        assert r.iso_zone == "A"
        assert r.severity_rms == pytest.approx(0.005)

    def test_t03_zone_b_clean(self, machines, iso_table):
        r = _reading_for("T03_zone_b_clean", machines, iso_table)
        assert r.iso_zone == "B"
        assert r.iso_severity == "info"
        assert r.severity_rms == pytest.approx(2.0)

    def test_t04_ab_boundary_lands_in_zone_b(self, machines, iso_table):
        # severity_rms 1.41 >= ab(1.4) -> classify()'s >= semantics put this
        # in Zone B, not A — the reference's own boundary-precision test.
        r = _reading_for("T04_ab_boundary", machines, iso_table)
        assert r.iso_zone == "B"
        assert r.severity_rms == pytest.approx(1.41)

    def test_t05_bc_boundary_zone_c(self, machines, iso_table):
        r = _reading_for("T05_bc_boundary_zone_c", machines, iso_table)
        assert r.iso_zone == "C"
        assert r.iso_severity == "warn"
        assert r.severity_rms == pytest.approx(2.81)

    def test_t06_cd_boundary_zone_d(self, machines, iso_table):
        r = _reading_for("T06_cd_boundary_zone_d", machines, iso_table)
        assert r.iso_zone == "D"
        assert r.iso_severity == "danger"
        assert r.severity_rms == pytest.approx(4.51)

    def test_margin_to_next_boundary_present_below_zone_d(self, machines, iso_table):
        r = _reading_for("T01_healthy_zone_a", machines, iso_table)
        assert r.margin_to_next_boundary == pytest.approx(1.4 - 0.3)

    def test_margin_to_next_boundary_none_at_zone_d(self, machines, iso_table):
        r = _reading_for("T06_cd_boundary_zone_d", machines, iso_table)
        assert r.margin_to_next_boundary is None

    def test_invalid_velocity_raises(self, pump_machine, iso_table):
        sensor_data = SensorData(x_velocity_mm_sec=float("nan"))
        resolved = resolve_thresholds(pump_machine, iso_table)
        with pytest.raises(ValueError, match="invalid velocity"):
            classify(sensor_data, pump_machine, resolved)

    def test_missing_velocity_is_not_assessable(self, pump_machine, iso_table):
        # Session A: no velocity on ANY axis -> severity is not assessable.
        # Missing velocity must NEVER be coerced to 0.0 mm/s (which used to fake
        # a quiet-machine Zone A / "ok" out of an acceleration-only reading).
        sensor_data = SensorData(mode=0)
        resolved = resolve_thresholds(pump_machine, iso_table)
        r = classify(sensor_data, pump_machine, resolved)
        assert r.severity_rms is None
        assert r.iso_zone == "not_assessable"
        assert r.iso_severity == "unrated"
        assert r.not_assessable_reason == "velocity not measured"
        assert r.dominant_axis is None
        assert r.x_vel_mms is None and r.y_vel_mms is None and r.z_vel_mms is None
        assert r.margin_to_next_boundary is None

    def test_partial_velocity_still_classifies(self, pump_machine, iso_table):
        # One axis present (webapp single-axis-velocity uploads) -> classify on
        # that one real value; absent axes stay None (never 0.0), and the result
        # is byte-identical to the pre-Session-A max-over-{value,0,0}.
        resolved = resolve_thresholds(pump_machine, iso_table)
        r = classify(SensorData(y_velocity_mm_sec=3.0), pump_machine, resolved)
        assert r.severity_rms == 3.0
        assert r.dominant_axis == "y"
        assert r.iso_zone == "C"  # 3.0 mm/s, group2/rigid: bc=2.8 <= 3.0 < cd=4.5
        assert r.iso_severity == "warn"
        assert r.x_vel_mms is None and r.z_vel_mms is None
        assert r.not_assessable_reason is None
