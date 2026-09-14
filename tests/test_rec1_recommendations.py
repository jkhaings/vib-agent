"""Session REC-1 — the corrective recommendation, in the analyst's shape.

Action -> timing -> reassessment, per fault id, from outputs/SESSION_REPORT2.md
F-3 (§3.6). The analyst's own wording, which the shape is taken from:

    "It is recommended to replace the compressor bearings during the next
    available maintenance schedule. The equipment health status should be
    reassessed after the above-mentioned maintenance action has been completed."

Two things are pinned here and nowhere else:

  * the TEXT, byte for byte against F-3's table — the whole point of the session
    is that the analyst reviewed these sentences, so a later edit that "tidies"
    one is a change to reviewed copy and must land as a red test;
  * the three named PARTS, which are spans of that text in order. That check is
    what stops `action`/`timing`/`reassess` drifting away from the sentence they
    claim to decompose, because nothing else reads them yet (REC-1 brief item 3:
    they exist for a later report session to lay out).
"""

from __future__ import annotations

import pytest

from vib_agent.models import Finding
from vib_agent.pdm_core.recommendations import (
    _CORRECTIVE,
    _ZONE_TIMING,
    corrective_recommendation,
    corrective_recommendations_for_findings,
)

ZONES = ("A", "B", "C", "D", "not_assessable", None)

# ── outputs/SESSION_REPORT2.md §3.6, verbatim ────────────────────────────────
# The ids whose F-3 row names its own timing. Copied from the table, not
# generated from the source: a golden that is derived from the thing it guards
# guards nothing.
F3_FIXED: dict[str, str] = {
    "bearing_outer_race": (
        "Replace the bearing at the next available maintenance window; monitor closely "
        "until then. Reassess machine health with a repeat measurement at the same point "
        "after the work is completed."
    ),
    "bearing_inner_race": (
        "Replace the bearing at the next available maintenance window; monitor closely "
        "until then. Reassess machine health with a repeat measurement at the same point "
        "after the work is completed."
    ),
    "bearing_ball_spin": (
        "Replace the bearing at the next available maintenance window; monitor closely "
        "until then. Reassess machine health with a repeat measurement at the same point "
        "after the work is completed."
    ),
    "bearing_cage": (
        "Trend the bearing closely and replace it at the next available maintenance "
        "window. Reassess with a repeat measurement at the same point after the work is "
        "completed."
    ),
    "mechanical_looseness": (
        "Inspect and re-torque mounting, foundation and bearing-fit hardware at the next "
        "available window. Reassess with a repeat measurement after the work is completed."
    ),
    "angular_misalignment": (
        "Carry out a precision (laser) shaft-alignment check at the next available window "
        "and correct as found. Reassess with a repeat measurement after the alignment is "
        "completed."
    ),
    "parallel_misalignment": (
        "Carry out a precision (laser) shaft-alignment check at the next available window "
        "and correct as found. Reassess with a repeat measurement after the alignment is "
        "completed."
    ),
    "misalignment_general": (
        "Carry out a precision (laser) shaft-alignment check at the next available window "
        "and correct as found. Reassess with a repeat measurement after the alignment is "
        "completed."
    ),
    "severe_misalignment": (
        "Carry out a precision alignment check promptly and inspect the coupling; correct "
        "as found. Reassess with a repeat measurement after the work is completed."
    ),
    "bent_shaft": (
        "Inspect the shaft for runout at the next opportunity, verified with a dial "
        "indicator; correct or replace as found. Reassess with a repeat measurement after "
        "the work is completed."
    ),
    "imbalance": (
        "Field-balance the rotor at the next available window. Reassess with a repeat "
        "measurement after balancing."
    ),
    "belt_fault": (
        "Inspect belt tension, wear and sheave condition at the next opportunity; "
        "re-tension or replace as found. Reassess with a repeat measurement after the "
        "work is completed."
    ),
    "elevated_blade_pass": (
        "Investigate process/flow conditions (cavitation, blockage, hydraulic "
        "instability) at the next opportunity and correct the operating condition. "
        "Reassess with a repeat measurement under the corrected condition."
    ),
    "rising_trend": (
        "Increase the monitoring frequency now and investigate the cause before the "
        "projected boundary is reached. Reassess at each reading against this report's "
        "trend."
    ),
    "no_significant_findings": (
        "Continue routine monitoring at the normal interval. Reassess at the next "
        "scheduled reading."
    ),
}

# The two ids whose F-3 row named NO timing — REC-1 brief item 1 sends those to
# the zone map. `{t}` is where the zone's phrase lands.
F3_ZONED: dict[str, str] = {
    "possible_resonance": (
        "Confirm with a bump test {t} before any corrective action; until then avoid "
        "operating at the resonant speed. Reassess once the test result is in hand."
    ),
    "elevated_vibration_undetermined": (
        "Collect additional data {t} to identify the source before planning corrective "
        "work. Reassess once the source is identified."
    ),
}

F3_DEFAULT = (
    "Confirm the finding with a follow-up measurement at the next scheduled interval. "
    "Reassess against this report when that measurement is in hand."
)

ALL_IDS = tuple(F3_FIXED) + tuple(F3_ZONED)


class TestTheTextIsTheReviewedText:
    """F-3's table, byte for byte."""

    @pytest.mark.parametrize("fault", sorted(F3_FIXED))
    @pytest.mark.parametrize("zone", ZONES)
    def test_fixed_timing_rows_match_f3(self, fault, zone):
        assert corrective_recommendation(fault, zone).text == F3_FIXED[fault]

    @pytest.mark.parametrize("fault", sorted(F3_ZONED))
    @pytest.mark.parametrize(
        "zone,phrase",
        [
            ("A", "at the next scheduled interval"),
            ("B", "at the next scheduled interval"),
            ("C", "within the next maintenance window"),
            ("D", "as soon as practicable"),
        ],
    )
    def test_zoned_rows_take_the_map_phrase(self, fault, zone, phrase):
        assert corrective_recommendation(fault, zone).text == F3_ZONED[fault].format(t=phrase)

    @pytest.mark.parametrize("zone", ZONES)
    def test_an_unknown_fault_id_gets_the_default_row(self, zone):
        assert corrective_recommendation("no_such_fault", zone).text == F3_DEFAULT

    def test_every_id_in_f3_is_in_the_table(self):
        """A row deleted from the table would otherwise only surface as a
        default-text render, which reads plausibly and says nothing."""
        assert set(_CORRECTIVE) == set(ALL_IDS)


class TestTheThreeParts:
    """Brief item 4: the three parts, in order, with a negative control each."""

    @pytest.mark.parametrize("fault", ALL_IDS + ("no_such_fault",))
    @pytest.mark.parametrize("zone", ZONES)
    def test_the_parts_occur_in_the_text_in_order(self, fault, zone):
        rec = corrective_recommendation(fault, zone)
        for name, part in (("action", rec.action), ("timing", rec.timing),
                           ("reassess", rec.reassess)):
            assert part, f"{fault}: {name} is empty"
            assert part in rec.text, f"{fault}: {name}={part!r} is not in the text"
        assert (
            rec.text.index(rec.action)
            < rec.text.index(rec.timing)
            < rec.text.index(rec.reassess)
        ), f"{fault}: parts are out of order in {rec.text!r}"

    @pytest.mark.parametrize("fault", ALL_IDS)
    @pytest.mark.parametrize("dropped", ["action", "timing", "reassess"])
    def test_negative_control_a_text_missing_one_part_fails_the_check(self, fault, dropped):
        """The ordered-occurrence assertion above must be capable of failing.
        Remove any ONE part from the sentence and it does."""
        rec = corrective_recommendation(fault, "D")
        part = getattr(rec, dropped)
        mangled = rec.text.replace(part, "", 1)
        assert part not in mangled or mangled.count(part) < rec.text.count(part)
        with pytest.raises(ValueError):
            mangled.index(part)

    @pytest.mark.parametrize("fault", ALL_IDS + ("no_such_fault",))
    def test_every_recommendation_ends_by_naming_a_reassessment(self, fault):
        """Brief item 2. The half the pre-REC-1 strings were missing entirely."""
        rec = corrective_recommendation(fault, "D")
        assert rec.text.rstrip().endswith(rec.reassess)
        assert rec.reassess.startswith("Reassess")
        assert rec.reassess.rstrip().endswith(".")


class TestTheAnalystsOwnCase:
    """Brief item 4: `bearing_outer_race` reads as the analyst's two sentences,
    modulo the machine name. The analyst wrote "the compressor bearings"; these
    strings are machine-neutral, which is F-3's own stated rule."""

    def test_it_is_two_sentences(self):
        text = corrective_recommendation("bearing_outer_race", "D").text
        sentences = [s.strip() for s in text.split(". ") if s.strip()]
        assert len(sentences) == 2, sentences

    def test_first_sentence_is_replace_at_the_next_available_window(self):
        rec = corrective_recommendation("bearing_outer_race", "D")
        first = rec.text.split(". ")[0]
        assert first.startswith("Replace the bearing")
        assert "at the next available maintenance window" in first
        assert rec.action == "Replace the bearing"
        assert rec.timing == "at the next available maintenance window"

    def test_second_sentence_reassesses_health_after_the_work(self):
        rec = corrective_recommendation("bearing_outer_race", "D")
        assert rec.reassess == (
            "Reassess machine health with a repeat measurement at the same point "
            "after the work is completed."
        )

    def test_it_no_longer_merely_plans(self):
        """The pre-REC-1 string said "Plan bearing replacement"; the analyst
        said "replace"."""
        text = corrective_recommendation("bearing_outer_race", "D").text
        assert "Plan bearing replacement" not in text


class TestTheZoneMapIsATextMapNotASeverityRule:
    """The map exists to word a timing F-3 left open. If it ever starts moving
    a timing that F-3 fixed, it has become a severity rule, and this fails."""

    @pytest.mark.parametrize("fault", sorted(F3_FIXED))
    def test_a_fixed_rows_timing_is_identical_in_every_zone(self, fault):
        timings = {corrective_recommendation(fault, z).timing for z in ZONES}
        assert len(timings) == 1, f"{fault} moved with the zone: {timings}"

    @pytest.mark.parametrize("fault", sorted(F3_ZONED))
    def test_a_zoned_rows_timing_does_move(self, fault):
        """The negative control for the map itself."""
        timings = {corrective_recommendation(fault, z).timing for z in ("A", "C", "D")}
        assert timings == {
            "at the next scheduled interval",
            "within the next maintenance window",
            "as soon as practicable",
        }

    @pytest.mark.parametrize("fault", sorted(F3_ZONED))
    @pytest.mark.parametrize("zone", ["not_assessable", None, "", "Z"])
    def test_an_unratable_zone_falls_back_to_the_ab_wording(self, fault, zone):
        """With no zone there is no basis for saying anything more urgent."""
        assert corrective_recommendation(fault, zone).timing == _ZONE_TIMING["A"]

    def test_the_map_covers_exactly_the_four_iso_zones(self):
        assert set(_ZONE_TIMING) == {"A", "B", "C", "D"}


class TestTheFindingsContract:
    """Same ordered, de-duplicated contract `recommendations_for_findings` has."""

    @staticmethod
    def _finding(fault: str) -> Finding:
        return Finding(fault=fault, severity="danger", confidence="high", reason="r")

    def test_order_follows_the_findings(self):
        findings = [self._finding("imbalance"), self._finding("bearing_outer_race")]
        recs = corrective_recommendations_for_findings(findings, "D")
        assert [r.fault for r in recs] == ["imbalance", "bearing_outer_race"]

    def test_the_three_bearing_faults_collapse_to_one_line(self):
        """They are the same bearing and the same work — as they were before
        REC-1, when the three shared one string."""
        findings = [
            self._finding("bearing_outer_race"),
            self._finding("bearing_inner_race"),
            self._finding("bearing_ball_spin"),
        ]
        recs = corrective_recommendations_for_findings(findings, "D")
        assert len(recs) == 1

    def test_no_findings_means_no_recommendations(self):
        assert corrective_recommendations_for_findings([], "D") == []


class TestConservativeThroughout:
    """The standing rule: never "run to failure"."""

    @pytest.mark.parametrize("fault", ALL_IDS + ("no_such_fault",))
    @pytest.mark.parametrize("zone", ZONES)
    def test_no_run_to_failure_language(self, fault, zone):
        text = corrective_recommendation(fault, zone).text.lower()
        for banned in ("run to failure", "run-to-failure", "no action", "ignore"):
            assert banned not in text
