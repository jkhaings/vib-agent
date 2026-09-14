"""Layer 2 tests: West's time-weighted Welford z-score. Layer 3: stub contract.

Faithful-port checks (decay, prior-baseline z-score, window cap, train gating)
use small synthetic per-axis acceleration series for full numeric control.
T15/T16 fixtures are used specifically for the "no crash on zero-variance
baseline" case, since the reference's own repeated-identical-reading test
harness (Generate 10 noisy baseline readings) produces exactly that shape.
"""

from __future__ import annotations

import math

import pytest

from tests.fixtures import REFERENCE_CASES
from vib_agent.models import SensorData
from vib_agent.pdm_core.anomaly import compute_zscore, init_welford_state, isolation_forest_score


def _sensor_data(case_name: str) -> SensorData:
    return SensorData(**REFERENCE_CASES[case_name]["sensor_data"])


def test_fresh_state_is_all_zero():
    state = init_welford_state(now_ms=1000.0)
    assert state.x.sum_w == 0.0
    assert state.x.mean == 0.0
    assert state.x.m2 == 0.0
    assert state.x.last_ts_ms == 1000.0


def test_warming_up_before_min_readings(thresholds):
    state = init_welford_state(now_ms=0.0)
    sd = _sensor_data("T01_healthy_zone_a")
    _, result = compute_zscore(state, sd, "TEST-PUMP-01", "Test Pump 01", thresholds["zscore"], now_ms=1000.0)
    assert result.axes["x"].status == "warming_up"
    assert result.trained is True


def test_zscore_computed_against_prior_baseline_not_posterior(thresholds):
    tconf = {**thresholds["zscore"], "min_readings": 1}
    state = init_welford_state(now_ms=0.0)
    sd1 = SensorData(x_rms_ACC_G=0.010, y_rms_ACC_G=0.010, z_rms_ACC_G=0.010)
    state, _ = compute_zscore(state, sd1, "M", "M", tconf, now_ms=1000.0)

    sd2 = SensorData(x_rms_ACC_G=0.010, y_rms_ACC_G=0.010, z_rms_ACC_G=0.010)
    state, r2 = compute_zscore(state, sd2, "M", "M", tconf, now_ms=2000.0, train_baseline=False)
    # train_baseline=False -> state (and its reported mean) is untouched by
    # this call; the mean reported must be the PRIOR mean, not recomputed.
    assert r2.axes["x"].mean == pytest.approx(0.010)


def test_window_cap_never_exceeded(thresholds):
    tconf = {**thresholds["zscore"], "window_cap": 5, "min_readings": 1}
    state = init_welford_state(now_ms=0.0)
    sd = SensorData(x_rms_ACC_G=0.02, y_rms_ACC_G=0.02, z_rms_ACC_G=0.02)
    now = 0.0
    for _ in range(20):
        now += 1000.0
        state, _ = compute_zscore(state, sd, "M", "M", tconf, now_ms=now)
    assert state.x.sum_w <= 5.0 + 1e-9


def test_train_baseline_false_does_not_absorb(thresholds):
    tconf = {**thresholds["zscore"], "min_readings": 1}
    state = init_welford_state(now_ms=0.0)
    sd = SensorData(x_rms_ACC_G=0.02, y_rms_ACC_G=0.02, z_rms_ACC_G=0.02)
    state, _ = compute_zscore(state, sd, "M", "M", tconf, now_ms=1000.0)
    before = state.x.sum_w
    state, _ = compute_zscore(state, sd, "M", "M", tconf, now_ms=2000.0, train_baseline=False)
    assert state.x.sum_w == pytest.approx(before)


def test_invalid_axis_reports_invalid_status_not_zero(thresholds):
    state = init_welford_state(now_ms=0.0)
    sd = SensorData(x_rms_ACC_G=float("nan"))
    _, result = compute_zscore(state, sd, "M", "M", thresholds["zscore"], now_ms=1000.0)
    assert result.axes["x"].status == "invalid"
    assert result.axes["y"].status == "invalid"  # None (axis not reported) -> also invalid


def test_decay_ages_prior_weight_over_one_half_life(thresholds):
    tconf = {**thresholds["zscore"], "min_readings": 1, "half_life_days": 1}
    state = init_welford_state(now_ms=0.0)
    sd = SensorData(x_rms_ACC_G=0.02, y_rms_ACC_G=0.02, z_rms_ACC_G=0.02)
    state, _ = compute_zscore(state, sd, "M", "M", tconf, now_ms=0.0)
    assert state.x.sum_w == pytest.approx(1.0)

    one_day_ms = 24 * 60 * 60 * 1000
    state2, _ = compute_zscore(state, sd, "M", "M", tconf, now_ms=one_day_ms, train_baseline=False)
    assert state2.x.sum_w == pytest.approx(0.5, rel=0.05)


def test_json_round_trip_preserves_state(thresholds):
    state = init_welford_state(now_ms=0.0)
    sd = SensorData(x_rms_ACC_G=0.02, y_rms_ACC_G=0.02, z_rms_ACC_G=0.02)
    state, _ = compute_zscore(state, sd, "M", "M", {**thresholds["zscore"], "min_readings": 1}, now_ms=1000.0)

    restored = type(state).model_validate_json(state.model_dump_json())
    assert restored == state


def test_t16_zero_std_baseline_no_crash(thresholds):
    # Reference's own baseline-seeding harness trains on near-identical
    # readings; std can legitimately be 0. z-score must resolve to a finite
    # 0, never NaN/inf, when priorStd is 0.
    tconf = {**thresholds["zscore"], "min_readings": 3}
    sd = _sensor_data("T01_healthy_zone_a")
    state = init_welford_state(now_ms=0.0)
    now = 0.0
    for _ in range(5):
        now += 1000.0
        state, _ = compute_zscore(state, sd, "TEST-PUMP-01", "Test Pump 01", tconf, now_ms=now)

    sd16 = _sensor_data("T16_zscore_zero_std")
    _, result = compute_zscore(
        state, sd16, "TEST-PUMP-01", "Test Pump 01", tconf, now_ms=now + 1000.0, train_baseline=False
    )
    for axis in ("x", "y", "z"):
        assert math.isfinite(result.axes[axis].z)
        assert result.axes[axis].status != "anomaly"  # identical-ish reading, not a spike


def test_spike_flags_watch_or_anomaly_against_jittered_baseline(thresholds):
    # A baseline with small (deterministic) variance, then a reading several
    # multiples of that variance away must register at least "watch".
    tconf = {**thresholds["zscore"], "min_readings": 3}
    state = init_welford_state(now_ms=0.0)
    now = 0.0
    for i in range(6):
        now += 1000.0
        jitter = 1.0 + (0.03 if i % 2 == 0 else -0.03)
        sd = SensorData(x_rms_ACC_G=0.020 * jitter, y_rms_ACC_G=0.020 * jitter, z_rms_ACC_G=0.020 * jitter)
        state, _ = compute_zscore(state, sd, "M", "M", tconf, now_ms=now)

    spike = SensorData(x_rms_ACC_G=0.10, y_rms_ACC_G=0.10, z_rms_ACC_G=0.10)
    _, result = compute_zscore(state, spike, "M", "M", tconf, now_ms=now + 1000.0, train_baseline=False)
    assert result.axes["x"].status in ("watch", "anomaly")
    assert result.any_flag or result.max_z >= thresholds["zscore"]["watch"]


class TestIsolationForestStub:
    """Amendment A1: Layer 3 is stubbed pending the FastAPI sidecar port."""

    def test_returns_not_enough_history_below_threshold(self, thresholds):
        result = isolation_forest_score([], thresholds["isolation_forest"])
        assert result.status == "not_enough_history"
        assert result.n_samples == 0

    def test_still_stubbed_above_threshold(self, thresholds):
        # No net-new Isolation Forest implemented — always not_enough_history
        # until the sidecar source lands in reference/.
        history = [[0.1, 0.2, 0.3]] * 100
        result = isolation_forest_score(history, thresholds["isolation_forest"])
        assert result.status == "not_enough_history"
        assert result.n_samples == 100
