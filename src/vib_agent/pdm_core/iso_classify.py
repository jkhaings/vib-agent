"""Layer 1: ISO 20816-3:2022 broadband velocity zone classification.

Faithful port of reference/flows.json 'Build global.pdm' (threshold resolution)
and 'Layer 1 — ISO 20816-3:2022' (per-reading classification). Threshold
resolution happens once (config-load time, mirroring Flow 0); classification
happens per reading (mirroring Flow 1) against the already-resolved thresholds.

Pure functions — the iso_table and factory_default are passed in by the
caller (loaded from config/iso_zones.json), never read from disk here.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from vib_agent.models import (
    Axis,
    IsoSeverity,
    IsoSeverityOrUnrated,
    IsoZone,
    IsoZoneOrNA,
    MachineMeta,
    MachineThresholds,
    Reading,
    ResolvedThresholds,
    SensorData,
    ZoneBasis,
)


def _iso_fallback(
    machine: MachineMeta, iso_table: dict[str, dict[str, float]]
) -> MachineThresholds | None:
    """Tier 3's answer for this machine, or None — never a raise.

    Session LIMITS-1a. Same lookup as tier 3 below, but resolved unconditionally
    so it survives tier 1 short-circuiting, and REFUSING TO RAISE where tier 3
    would. The difference is deliberate and is the whole reason this is a
    separate function rather than a flag on the one below:

    * tier 3 raising is correct — it is the last tier, and a machine that
      reaches it with no usable ISO key has no thresholds at all;
    * this raising would be a regression. It is consulted only when a limit
      HAS been set, and a plant sets its own limit precisely for a machine ISO
      20816-3 does not cover. Making `iso_zone_would_be` fail such a job would
      punish the case the feature exists for.

    So an unknown key or a missing group/support is None here: "ISO has no
    opinion about this machine", which is exactly what the report should say.
    """
    if machine.iso_group is None or machine.iso_support is None:
        return None
    entry = iso_table.get(f"{machine.iso_group}_{machine.iso_support}")
    if entry is None:
        return None
    return MachineThresholds(ab=entry["ab"], bc=entry["bc"], cd=entry["cd"])


def resolve_thresholds(
    machine: MachineMeta,
    iso_table: dict[str, dict[str, float]],
    factory_default: MachineThresholds | None = None,
) -> ResolvedThresholds:
    """3-tier threshold priority: machine override > factory default > ISO table.

    Raises ValueError if none of the three tiers can be resolved — the
    reference silently drops the machine from the registry at config-load
    time (`node.warn(...); continue`); we fail loudly instead, at the same
    point in the pipeline (config load), so a misconfigured machine is
    caught immediately rather than producing readings with no thresholds.
    """
    # Session LIMITS-1a: resolved BEFORE the tiers branch, because tiers 1 and 2
    # return without ever consulting the ISO table and this is the last moment
    # `iso_table` is in scope. Carried on every tier (on tier 3 it is the same
    # numbers as `thresholds`) so no caller has to know which tier won.
    iso_fallback = _iso_fallback(machine, iso_table)

    if machine.thresholds is not None:
        return ResolvedThresholds(
            thresholds=machine.thresholds,
            source="custom",
            note=machine.thresholds.source_note or "Machine-specific override",
            iso_fallback=iso_fallback,
        )

    if factory_default is not None:
        return ResolvedThresholds(
            thresholds=factory_default,
            source="factory_default",
            note=factory_default.source_note or "Factory-wide default",
            iso_fallback=iso_fallback,
        )

    if machine.iso_group is not None and machine.iso_support is not None:
        key = f"{machine.iso_group}_{machine.iso_support}"
        entry = iso_table.get(key)
        if entry is None:
            raise ValueError(
                f'Machine {machine.name} ({machine.mac}): unknown ISO key "{key}" and no override set'
            )
        return ResolvedThresholds(
            thresholds=MachineThresholds(ab=entry["ab"], bc=entry["bc"], cd=entry["cd"]),
            source="iso_20816_3",
            note=f"ISO 20816-3:2022 — Group {machine.iso_group}, {machine.iso_support} support",
            iso_fallback=iso_fallback,
        )

    raise ValueError(
        f"Machine {machine.name} ({machine.mac}): no thresholds override, no factory default, "
        f"and no iso_group/iso_support set — cannot resolve ISO zone thresholds"
    )


def _validate_velocity(v: float | None) -> float | None:
    """None (axis not reported) stays None -- NEVER coerced to 0.0, which would
    fake a quiet-machine Zone A reading out of missing data. NaN/Infinity
    (corrupted packet) -> raises."""
    if v is None:
        return None
    if not math.isfinite(v):
        raise ValueError(f"invalid velocity reading: {v!r} (NaN/Infinity)")
    return abs(v)


def _zone_for(
    severity_rms: float, thresholds: MachineThresholds
) -> tuple[IsoZone, IsoSeverity]:
    """The zone ladder, extracted so it can be run twice without drifting.

    Session LIMITS-1a. It is run against the EFFECTIVE thresholds to produce the
    zone, and again against the ISO fallback to produce `iso_zone_would_be`. The
    two answers are only comparable if they come from identical arithmetic, and
    the surest way to guarantee that is one ladder with one `>=` semantics — the
    reference's own, where a value sitting exactly on a boundary lands in the
    WORSE zone (pinned by T04/T05/T06 in tests/test_iso_classify.py).

    Body is verbatim from the inline ladder this replaced; no behaviour moved.
    """
    if severity_rms >= thresholds.cd:
        return "D", "danger"
    if severity_rms >= thresholds.bc:
        return "C", "warn"
    if severity_rms >= thresholds.ab:
        return "B", "info"
    return "A", "ok"


def _margin_to_next_boundary(
    zone: IsoZone, severity_rms: float, thresholds: MachineThresholds
) -> float | None:
    """Additive extra (not in reference): distance to the next-worse zone boundary."""
    if zone == "A":
        return thresholds.ab - severity_rms
    if zone == "B":
        return thresholds.bc - severity_rms
    if zone == "C":
        return thresholds.cd - severity_rms
    return None  # zone D — no further boundary


def classify(
    sensor_data: SensorData,
    machine: MachineMeta,
    resolved: ResolvedThresholds,
    *,
    factory_id: str | None = None,
    factory_timezone: str = "UTC",
    ts: str | None = None,
) -> Reading:
    """Classify a single reading into an ISO 20816-3 zone.

    severity_rms = max(|x|, |y|, |z|) velocity across axes; dominant_axis is
    whichever axis produced that max (x wins ties over y, y wins ties over z
    — matches the reference's `x >= y && x >= z ? "x" : y >= z ? "y" : "z"`).
    """
    x = _validate_velocity(sensor_data.x_velocity_mm_sec)
    y = _validate_velocity(sensor_data.y_velocity_mm_sec)
    z = _validate_velocity(sensor_data.z_velocity_mm_sec)

    th = resolved.thresholds
    present = {ax: v for ax, v in (("x", x), ("y", y), ("z", z)) if v is not None}

    # Session LIMITS-1a. `threshold_source` names WHICH tier won; `zone_basis`
    # answers the only question a report reader asks — was this letter the ISO
    # table's, or a limit somebody set? A factory-wide default is the latter.
    zone_basis: ZoneBasis = "iso" if resolved.source == "iso_20816_3" else "custom"

    zone: IsoZoneOrNA
    severity: IsoSeverityOrUnrated
    severity_rms: float | None
    dominant_axis: Axis | None
    not_assessable_reason: str | None = None
    iso_zone_would_be: IsoZone | None = None
    if not present:
        # No velocity on ANY axis: ISO 20816 severity is a velocity judgement,
        # so it cannot be assessed. Return a not_assessable reading rather than
        # coercing missing velocity to 0.0 mm/s (which would fake ISO Zone A).
        severity_rms = None
        dominant_axis = None
        zone, severity = "not_assessable", "unrated"
        not_assessable_reason = "velocity not measured"
        margin = None
    else:
        # severity_rms / dominant_axis are computed over the axes that ACTUALLY
        # reported a velocity. Absent axes are excluded (not treated as 0.0), so
        # a single-axis velocity reading classifies on its one real value; when
        # all three are present this is identical to max(|x|,|y|,|z|) with the
        # reference's x>=y>=z tie-break.
        severity_rms = max(present.values())
        dominant_axis = next(ax for ax in ("x", "y", "z") if present.get(ax) == severity_rms)
        zone, severity = _zone_for(severity_rms, th)
        margin = _margin_to_next_boundary(zone, severity_rms, th)
        # Only on the custom path: on the ISO path there is no "would be", the
        # zone above IS ISO's answer, and repeating it in a second field would
        # invite a report to print the same letter twice. None also when the ISO
        # table has no opinion about this machine (_iso_fallback returned None).
        if zone_basis == "custom" and resolved.iso_fallback is not None:
            iso_zone_would_be = _zone_for(severity_rms, resolved.iso_fallback)[0]

    return Reading(
        ts=ts or datetime.now(timezone.utc).isoformat(),
        factory_id=factory_id,
        factory_timezone=factory_timezone,
        machine_id=machine.name,
        machine_type=machine.type or "unknown",
        mac=machine.mac,
        x_vel_mms=x,
        y_vel_mms=y,
        z_vel_mms=z,
        severity_rms=severity_rms,
        dominant_axis=dominant_axis,
        iso_zone=zone,
        iso_severity=severity,
        iso_group=machine.iso_group,
        iso_support=machine.iso_support,
        threshold_source=resolved.source,
        threshold_note=resolved.note,
        th_ab=th.ab,
        th_bc=th.bc,
        th_cd=th.cd,
        margin_to_next_boundary=margin,
        not_assessable_reason=not_assessable_reason,
        zone_basis=zone_basis,
        iso_zone_would_be=iso_zone_would_be,
    )


def mark_not_assessable(reading: Reading, reason: str) -> Reading:
    """Downgrade an already-classified reading to not_assessable, preserving the
    measured velocity/severity_rms. Used when velocity is PRESENT but implausible
    (the gate's units_plausibility WARN): the measured value is still carried
    (report labels it implausible) while the ISO zone/severity are withheld,
    because a value outside the sane mm/s range cannot anchor an ISO 20816 zone.

    Session LIMITS-1a: `iso_zone_would_be` is withheld here too, for the same
    reason `margin_to_next_boundary` always was. Withholding the zone while
    still printing "ISO would have said D" would hand back the very judgement
    this function exists to withhold, through a second door. `zone_basis`
    SURVIVES — it is provenance ("we were going to judge this against a plant
    limit"), not a verdict, and it stays true of a withheld reading."""
    return reading.model_copy(
        update={
            "iso_zone": "not_assessable",
            "iso_severity": "unrated",
            "not_assessable_reason": reason,
            "margin_to_next_boundary": None,
            "iso_zone_would_be": None,
        }
    )
