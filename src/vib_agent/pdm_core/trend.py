"""Layer 4: trend regression over a history of daily-mean broadband velocity.

Faithful port of reference/flows.json 'Compute trend + classify': linear
regression, baseline (first 7 points) vs current (last 7 points) windows, an
ISO §6.5.2 alarm limit (baseline_avg + 0.25 × zone B/C threshold), and
severity gated on slope > 0 (danger > warn(3σ) > info(2σ) > ok). Requires
≥14 points, matching the reference's insufficient-data guard.

Amendment A4: the regression runs against real timestamps (days elapsed
since the first point), not an integer day-index — day-index is what you
get when timestamps happen to be evenly spaced one day apart, which is
exactly the reference's own backfill fixtures. This is future-proofing for
irregular route-collector intervals, not a behavior change; see
tests/test_trend.py for the golden test proving the two are numerically
identical on daily-cadence data.

Additive (not in reference): r_squared and days_to_next_boundary, projecting
forward to the reference's own alarm_limit. Only emitted when slope > 0 and
r² clears min_r_squared — otherwise trend_note is "no reliable trend" and
nothing is extrapolated.
"""

from __future__ import annotations

import statistics
from typing import Any

from vib_agent.models import HistoryPoint, IsoSeverity, TrendResult


def _ols(day_offsets: list[float], values: list[float]) -> tuple[float, float, float]:
    """Ordinary least squares: returns (slope, intercept, r_squared)."""
    n = len(values)
    mx = statistics.fmean(day_offsets)
    my = statistics.fmean(values)
    num = sum((day_offsets[i] - mx) * (values[i] - my) for i in range(n))
    denom = sum((day_offsets[i] - mx) ** 2 for i in range(n))
    slope = 0.0 if denom == 0 else num / denom
    intercept = my - slope * mx

    ss_tot = sum((v - my) ** 2 for v in values)
    if ss_tot == 0:
        r_squared = 0.0
    else:
        ss_res = sum((values[i] - (slope * day_offsets[i] + intercept)) ** 2 for i in range(n))
        r_squared = 1.0 - ss_res / ss_tot

    return slope, intercept, r_squared


def compute_trend(
    history: list[HistoryPoint],
    zone_bc: float,
    thresholds: dict[str, Any],
) -> TrendResult:
    """Compute the Layer 4 trend verdict.

    `zone_bc` is the machine's resolved ISO B/C boundary (mm/s) — the
    reference reads this from `machine.thresholds.bc`. `thresholds` is the
    resolved 'trend' section of config/thresholds.json.
    """
    history = sorted(history, key=lambda p: p.ts)
    values = [p.value for p in history]
    n = len(values)
    min_days = thresholds.get("min_days", 14)

    if n < min_days:
        return TrendResult(
            status="insufficient_data",
            severity="ok",
            n_days=n,
            slope=0.0,
            pct_change=0.0,
        )

    ts0 = history[0].ts
    day_offsets = [(p.ts - ts0).total_seconds() / 86400.0 for p in history]

    slope, intercept, r_squared = _ols(day_offsets, values)
    my = statistics.fmean(values)
    pct_change = 0.0 if my == 0 else (slope * n / my) * 100.0

    baseline_window = thresholds.get("baseline_window_days", 7)
    current_window = thresholds.get("current_window_days", 7)
    baseline = values[:baseline_window]
    current = values[-current_window:]
    baseline_avg = statistics.fmean(baseline)
    baseline_std = statistics.stdev(baseline) if len(baseline) >= 2 else 0.0
    current_avg = statistics.fmean(current)

    iso_alarm_factor = thresholds.get("iso_alarm_factor", 0.25)
    alarm_limit = baseline_avg + iso_alarm_factor * zone_bc

    sigma_warn = thresholds.get("sigma_warn", 3.0)
    sigma_info = thresholds.get("sigma_info", 2.0)

    severity: IsoSeverity = "ok"
    if slope > 0:
        if current_avg > alarm_limit:
            severity = "danger"
        elif current_avg > baseline_avg + sigma_warn * baseline_std:
            severity = "warn"
        elif current_avg > baseline_avg + sigma_info * baseline_std:
            severity = "info"

    min_r_squared = thresholds.get("min_r_squared", 0.7)
    days_to_boundary: float | None = None
    trend_note: str | None = None
    if slope > 0 and r_squared >= min_r_squared:
        if slope != 0:
            t_target = (alarm_limit - intercept) / slope
            projected = t_target - day_offsets[-1]
            if projected > 0:
                days_to_boundary = projected
    else:
        trend_note = "no reliable trend"

    return TrendResult(
        status="ok",
        severity=severity,
        n_days=n,
        slope=round(slope, 6),
        pct_change=round(pct_change, 2),
        baseline_avg=round(baseline_avg, 4),
        baseline_std=round(baseline_std, 4),
        current_avg=round(current_avg, 4),
        alarm_limit=round(alarm_limit, 4),
        r_squared=round(r_squared, 4),
        days_to_next_boundary=round(days_to_boundary, 1) if days_to_boundary is not None else None,
        trend_note=trend_note,
    )
