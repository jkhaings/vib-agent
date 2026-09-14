"""Layer 4 tests: trend regression, reproducing the reference's T22/T23
30-day backfill fixtures, plus the Amendment A4 golden test proving the
real-timestamp regression is numerically identical to day-index regression
on daily-cadence data, and the additive r²/no-reliable-trend/projection
extras.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

import pytest

from vib_agent.models import HistoryPoint
from vib_agent.pdm_core.trend import compute_trend

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_DAY_MS = 86_400_000
ZONE_BC = 2.8  # TEST-PUMP-01, group2_rigid


def _backfill(start_val: float, end_val: float, days: int = 30) -> list[HistoryPoint]:
    """Reproduces reference/flows.json's 'Generate 30d flat/ramp history' formula,
    with jitter fixed to 0 for determinism (the reference's own jitter is
    uniform random noise, immaterial to the severity classification these
    tests check).
    """
    points = []
    for i in range(days):
        v = start_val + (end_val - start_val) * (i / (days - 1))
        ts = _NOW - timedelta(milliseconds=(days - i) * _DAY_MS)
        points.append(HistoryPoint(ts=ts, value=v))
    return points


def test_t22_flat_history_is_ok(thresholds):
    history = _backfill(2.0, 2.0)
    result = compute_trend(history, ZONE_BC, thresholds["trend"])
    assert result.status == "ok"
    assert result.severity == "ok"
    assert result.n_days == 30
    assert result.slope == pytest.approx(0.0, abs=1e-9)


def test_t23_ramp_history_is_warn_or_danger(thresholds):
    history = _backfill(2.0, 3.5)
    result = compute_trend(history, ZONE_BC, thresholds["trend"])
    assert result.status == "ok"
    assert result.severity in ("warn", "danger")
    assert result.slope > 0


def test_insufficient_data_below_min_days(thresholds):
    history = _backfill(2.0, 2.0, days=10)
    result = compute_trend(history, ZONE_BC, thresholds["trend"])
    assert result.status == "insufficient_data"
    assert result.severity == "ok"
    assert result.n_days == 10


def test_golden_timestamp_regression_matches_day_index_regression(thresholds):
    """Amendment A4: the regression runs on real elapsed days, not an
    integer day-index. On evenly daily-spaced data (exactly what the
    reference's own backfill fixtures are) the two must be numerically
    identical — this is the amendment's required proof.
    """
    history = _backfill(2.0, 3.5)
    result = compute_trend(history, ZONE_BC, thresholds["trend"])

    # Hand-computed day-index OLS slope, independent of pdm_core.trend.
    values = [p.value for p in sorted(history, key=lambda p: p.ts)]
    n = len(values)
    xs = list(range(n))
    mx, my = statistics.fmean(xs), statistics.fmean(values)
    num = sum((xs[i] - mx) * (values[i] - my) for i in range(n))
    denom = sum((xs[i] - mx) ** 2 for i in range(n))
    day_index_slope = num / denom

    # compute_trend() rounds slope to 6 decimals for the presented result;
    # tolerance reflects that rounding, not numerical drift between the two
    # regression methods.
    assert result.slope == pytest.approx(day_index_slope, abs=1e-6)


def test_no_reliable_trend_when_r_squared_too_low(thresholds):
    # Heavy alternating noise (±0.5) dwarfing a tiny 0.01/day rising trend
    # -> low r², even though the OLS slope itself is still positive.
    history = [
        HistoryPoint(
            ts=_NOW - timedelta(days=(20 - i)),
            value=2.0 + 0.01 * i + (0.5 if i % 2 == 0 else -0.5),
        )
        for i in range(20)
    ]
    result = compute_trend(history, ZONE_BC, thresholds["trend"])
    assert result.r_squared is not None
    assert result.r_squared < thresholds["trend"]["min_r_squared"]
    assert result.trend_note == "no reliable trend"
    assert result.days_to_next_boundary is None


def test_days_to_next_boundary_projected_when_reliable_and_not_yet_crossed(thresholds):
    # A slow, clean (noise-free) ramp that has NOT yet crossed alarm_limit
    # by the last observed day -> a positive forward projection is expected.
    history = _backfill(2.0, 2.2)
    result = compute_trend(history, ZONE_BC, thresholds["trend"])
    assert result.slope > 0
    assert result.r_squared >= thresholds["trend"]["min_r_squared"]
    assert result.current_avg < result.alarm_limit
    assert result.days_to_next_boundary is not None
    assert result.days_to_next_boundary > 0
    assert result.trend_note is None


def test_days_to_next_boundary_none_once_already_crossed(thresholds):
    # T23-style steep ramp already exceeds alarm_limit by the last point ->
    # no forward projection is offered (there's nothing left to project to).
    history = _backfill(2.0, 3.5)
    result = compute_trend(history, ZONE_BC, thresholds["trend"])
    assert result.current_avg > result.alarm_limit
    assert result.days_to_next_boundary is None
