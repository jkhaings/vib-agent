"""Session A — severity truthfulness pins.

The product must never emit ISO severity language it cannot support. On
acceleration-only data (no velocity) the reading is `not_assessable`: findings
render "unrated", the report drops all Zone A-D language and carries a Severity
& Coverage block, and a velocity-collection follow-up rides the recommendations
channel.

Two layers:
  * INLINE tests (always run) reproduce the acceleration-only condition from a
    synthetic case with its velocity nulled — so CI covers the logic with no
    external data.
  * DATA-GATED pins reproduce the exact PDFs that Phase 7/7B booked as passes
    while they asserted "ISO Zone A / no action" on known-faulted machines
    (`outer_270_1`, `real_world_planet_bearing`, wind-turbine `final_day50`).
    Skipped cleanly when data/ is absent — same design as the adapter tests.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.report.generate import render_markdown
from vib_agent.synth.generator import make_case

_MFPT_DATA = Path(__file__).resolve().parents[1] / "data" / "mfpt"
_WT_DATA = Path(__file__).resolve().parents[1] / "data" / "wind_turbine"

_ZONE_RE = re.compile(r"ISO\s+Zone|\bZone\s+[A-D]\b", re.IGNORECASE)
_VELOCITY_FOLLOWUP = "Velocity measurement per ISO 20816"


def _assert_zone_free(md: str) -> None:
    hit = _ZONE_RE.search(md)
    assert hit is None, f"not_assessable report must name no ISO zone, found {hit.group(0)!r}"
    assert "0.00 mm/s" not in md  # no coerced clean-bill number


def _has_velocity_followup(result) -> bool:
    return any(_VELOCITY_FOLLOWUP in m.technique for m in result.recommended_measurements)


# ─────────────────────────────────────────────────────────────────────────
# INLINE — always run (no external data)
# ─────────────────────────────────────────────────────────────────────────


def _acceleration_only_case(fault: str, iso_table, thresholds):
    """A synthetic fault case (spectra intact -> RCA still commits) with velocity
    nulled on every axis, i.e. an acceleration-only reading."""
    case = make_case(fault, iso_table=iso_table, thresholds=thresholds, seed=1)
    sd = case.sensor_data.model_copy(
        update={"x_velocity_mm_sec": None, "y_velocity_mm_sec": None, "z_velocity_mm_sec": None}
    )
    return case.model_copy(update={"sensor_data": sd})


def test_inline_committed_fault_is_unrated_not_ok(iso_table, thresholds, rules):
    # REGRESSION (Session A): a correctly-diagnosed fault on acceleration-only
    # data reads unrated severity, high confidence — never "ok" (the outer_270_1
    # failure mode: real fault, correctly found, historically rated "ok").
    case = _acceleration_only_case("bpfo", iso_table, thresholds)
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    assert result.iso.iso_zone == "not_assessable"
    bearing = next(f for f in result.findings if f.fault == "bearing_outer_race")
    assert bearing.severity == "unrated"
    assert bearing.confidence == "high"
    assert _has_velocity_followup(result)
    _assert_zone_free(render_markdown(result, case.machine))


def test_inline_clean_bill_is_unrated_with_coverage(iso_table, thresholds, rules):
    # REGRESSION (Session A): no fault matched on acceleration-only data must NOT
    # produce a Zone A "no corrective action" clean bill (the final_day50 /
    # planet_bearing failure mode).
    case = _acceleration_only_case("healthy", iso_table, thresholds)
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    assert result.iso.iso_zone == "not_assessable"
    assert [f.fault for f in result.findings] == ["no_significant_findings"]
    assert result.findings[0].severity == "unrated"
    assert _has_velocity_followup(result)
    md = render_markdown(result, case.machine)
    assert "## Severity & Coverage" in md
    _assert_zone_free(md)


# ─────────────────────────────────────────────────────────────────────────
# DATA-GATED — the exact benchmark PDFs Phase 7/7B mis-graded
# ─────────────────────────────────────────────────────────────────────────


def _mfpt_result(name: str):
    from vib_agent.adapters.mfpt import to_case

    case = to_case(
        _MFPT_DATA / f"{name}.mat",
        mfpt_cfg=load_config("mfpt"),
        bearings_cfg=load_config("bearings"),
    )
    return case, run_analysis(
        case, iso_table=load_config("iso_zones")["zones"], thresholds=load_thresholds("route"),
        rules=load_config("next_measurements"),
    )


@pytest.mark.skipif(not (_MFPT_DATA / "outer_270_1.mat").exists(), reason="data/mfpt/outer_270_1.mat absent")
def test_pin_outer_270_1_high_confidence_unrated():
    # REGRESSION (Session A): outer_270_1 must read bearing_outer_race, HIGH
    # confidence, severity UNRATED — the canonical "real fault rated ok" case.
    case, result = _mfpt_result("outer_270_1")
    assert result.iso.iso_zone == "not_assessable"
    bearing = next(f for f in result.findings if f.fault == "bearing_outer_race")
    assert bearing.confidence == "high"
    assert bearing.severity == "unrated"
    assert _has_velocity_followup(result)
    _assert_zone_free(render_markdown(result, case.machine))


@pytest.mark.skipif(
    not (_MFPT_DATA / "real_world_planet_bearing.mat").exists(),
    reason="data/mfpt/real_world_planet_bearing.mat absent",
)
def test_pin_planet_bearing_unrated_with_followup():
    # REGRESSION (Session A): known-faulted planet bearing no longer gets a
    # Zone A clean bill; severity unrated, velocity follow-up emitted.
    case, result = _mfpt_result("real_world_planet_bearing")
    assert result.iso.iso_zone == "not_assessable"
    assert all(f.severity == "unrated" for f in result.findings)
    assert _has_velocity_followup(result)
    _assert_zone_free(render_markdown(result, case.machine))


@pytest.mark.skipif(not list(_WT_DATA.glob("data-*.mat")), reason="data/wind_turbine absent")
def test_pin_wt_final_day_unrated_with_followup():
    # REGRESSION (Session A): the last wind-turbine recording before a confirmed
    # inner-race failure (final_dayNN) — historically "ISO Zone A, no action" —
    # now reads unrated severity with a velocity follow-up and zero zone language.
    from vib_agent.adapters.wind_turbine import parse_timestamp, to_case

    last = sorted(_WT_DATA.glob("data-*.mat"), key=parse_timestamp)[-1]
    case = to_case(last, wt_cfg=load_config("wind_turbine"), bearings_cfg=load_config("bearings"))
    result = run_analysis(
        case, iso_table=load_config("iso_zones")["zones"], thresholds=load_thresholds("route"),
        rules=load_config("next_measurements"),
    )
    assert result.iso.iso_zone == "not_assessable"
    assert all(f.severity == "unrated" for f in result.findings)
    assert _has_velocity_followup(result)
    _assert_zone_free(render_markdown(result, case.machine))
