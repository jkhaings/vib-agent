"""Layer 2: Welford-family time-weighted anomaly detection. Layer 3: Isolation Forest.

Layer 2 is a faithful port of reference/flows.json 'Layer 2 — Z-Score (3-axis,
time-weighted Welford)': West's weighted incremental variance with exponential
time decay (30-day half-life default), a window cap (720 default), z-score
computed against the PRIOR baseline before absorbing the new value, and
absorption gated on a `train` flag (fed by quality_gate.train_baseline).
Runs on acceleration (x/y/z_rms_ACC_G), not velocity.

Layer 3 does not exist in reference/flows.json — it lives in the user's
external FastAPI sidecar. Per Amendment A1: if that sidecar implementation
has not been supplied into reference/, this stays a stub returning
not_enough_history. No net-new Isolation Forest is invented here.
"""

from __future__ import annotations

import math
import time
from typing import Any

from vib_agent.models import (
    AxisZScore,
    IsolationForestResult,
    SensorData,
    WelfordAxisState,
    WelfordState,
    ZScoreResult,
)


def init_welford_state(now_ms: float | None = None) -> WelfordState:
    """A fresh, untrained per-machine Welford state (mirrors the reference's
    lazy zState[mac] initialization on first-seen MAC)."""
    now_ms = now_ms if now_ms is not None else time.time() * 1000
    axis = WelfordAxisState(sum_w=0.0, mean=0.0, m2=0.0, last_ts_ms=now_ms)
    return WelfordState(x=axis.model_copy(), y=axis.model_copy(), z=axis.model_copy())


def _compute_axis(
    state: WelfordAxisState,
    value: float | None,
    *,
    now_ms: float,
    min_readings: float,
    watch: float,
    flag: float,
    window_cap: float,
    half_life_days: float,
    train: bool,
) -> tuple[WelfordAxisState, AxisZScore]:
    if value is None or not math.isfinite(value):
        return state, AxisZScore(value=0.0, mean=0.0, std=0.0, z=0.0, n=0.0, status="invalid", flagged=False)

    # 1. age prior observations (time-weighted decay)
    sum_w, m2 = state.sum_w, state.m2
    if sum_w > 0:
        elapsed = max(0.0, now_ms - state.last_ts_ms)
        half_life_ms = half_life_days * 24 * 60 * 60 * 1000
        decay = 0.5 ** (elapsed / half_life_ms)
        sum_w *= decay
        m2 *= decay
    mean = state.mean

    # 2. z-score against the PRIOR baseline (before absorbing this value)
    z_score = 0.0
    status = "warming_up"
    flagged = False
    prior_std = math.sqrt(max(m2 / sum_w, 0.0)) if sum_w > 0 else 0.0
    if sum_w >= min_readings:
        if prior_std > 0:
            z_score = abs(value - mean) / prior_std
        if z_score >= flag:
            status, flagged = "anomaly", True
        elif z_score >= watch:
            status = "watch"
        else:
            status = "normal"

    # 3. absorb the new observation, only if training is allowed
    if train:
        w = 1.0
        new_sum_w = sum_w + w
        capped_sum_w = min(new_sum_w, window_cap)
        scale = capped_sum_w / new_sum_w
        delta = value - mean
        mean = mean + (w / new_sum_w) * delta
        delta2 = value - mean
        m2 = (m2 + w * delta * delta2) * scale
        sum_w = capped_sum_w

    post_std = math.sqrt(max(m2 / sum_w, 0.0)) if sum_w > 0 else 0.0

    new_state = WelfordAxisState(sum_w=sum_w, mean=mean, m2=m2, last_ts_ms=now_ms)
    result = AxisZScore(
        value=round(value, 6),
        mean=round(mean, 6),
        std=round(post_std, 6),
        z=round(z_score, 4),
        n=round(sum_w, 2),
        status=status,
        flagged=flagged,
    )
    return new_state, result


def compute_zscore(
    state: WelfordState,
    sensor_data: SensorData,
    mac: str,
    machine_id: str,
    thresholds: dict[str, Any],
    *,
    train_baseline: bool = True,
    now_ms: float | None = None,
) -> tuple[WelfordState, ZScoreResult]:
    """One Layer 2 evaluation across all 3 axes.

    `thresholds` is the resolved 'zscore' section of config/thresholds.json
    (vib_agent.config.load_thresholds()['zscore']). `train_baseline` should
    come from quality_gate.run_quality_gate(...).train_baseline.

    Returns (new_state, result) — the caller persists new_state for the next
    call, mirroring how the reference threads zscore_state through
    global.set(..., "persistent").
    """
    now_ms = now_ms if now_ms is not None else time.time() * 1000
    min_readings = thresholds.get("min_readings", 30)
    watch = thresholds.get("watch", 2.5)
    flag = thresholds.get("flag", 3.5)
    window_cap = thresholds.get("window_cap", 720)
    half_life_days = thresholds.get("half_life_days", 30)

    raw_values = {"x": sensor_data.x_rms_ACC_G, "y": sensor_data.y_rms_ACC_G, "z": sensor_data.z_rms_ACC_G}

    new_states: dict[str, WelfordAxisState] = {}
    axis_results: dict[str, AxisZScore] = {}
    any_flag = False
    max_z = 0.0
    flagged_axes: list[str] = []

    for axis in ("x", "y", "z"):
        new_state, result = _compute_axis(
            getattr(state, axis),
            raw_values[axis],
            now_ms=now_ms,
            min_readings=min_readings,
            watch=watch,
            flag=flag,
            window_cap=window_cap,
            half_life_days=half_life_days,
            train=train_baseline,
        )
        new_states[axis] = new_state
        axis_results[axis] = result
        if result.flagged:
            any_flag = True
            flagged_axes.append(axis)
        if result.z > max_z:
            max_z = result.z

    new_welford_state = WelfordState(x=new_states["x"], y=new_states["y"], z=new_states["z"])
    result = ZScoreResult(
        mac=mac,
        machine_id=machine_id,
        axes=axis_results,
        any_flag=any_flag,
        max_z=round(max_z, 4),
        flagged_axes=flagged_axes,
        window_cap=window_cap,
        min_readings=min_readings,
        trained=train_baseline,
    )
    return new_welford_state, result


def isolation_forest_score(
    history: list[list[float]],
    thresholds: dict[str, Any],
) -> IsolationForestResult:
    """Layer 3 — Isolation Forest over a trailing window of feature vectors.

    STUB (Amendment A1): reference/flows.json has no Layer 3 — it lives in
    the user's external FastAPI sidecar, not yet ported into reference/.
    Always returns not_enough_history until that source lands; no net-new
    Isolation Forest is implemented here.
    """
    min_history = thresholds.get("min_history_samples", 30)
    if len(history) < min_history:
        return IsolationForestResult(status="not_enough_history", n_samples=len(history))
    return IsolationForestResult(status="not_enough_history", n_samples=len(history))
