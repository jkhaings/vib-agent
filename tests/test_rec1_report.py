"""Session REC-1 — the recommendation reaches both documents, under a heading.

Two separate defects are pinned here.

1. The DETERMINISTIC report renders the new three-part text under the
   "Recommendations" heading it already had. `default_survey.md.j2:207-216` and
   `survey.html.j2:250-259` are NOT restructured by this session — only the list
   they are handed changed — so what is pinned is that the sentences arrive
   there unchanged.

2. The DRAFTED report had lost the heading altogether. The model is free not to
   write one, and on the 2 kHz sample it did not: the corrective action came out
   as a stray sentence pair under the follow-up MEASUREMENTS section
   (outputs/PARTC_2026-09-11.md E2 item 5). It is now spliced deterministically,
   from the same macro and the same helper as the deterministic report, and
   suppressed when the draft did write its own — the want_stage/want_causes
   pattern.

The escalation trigger (REC-1 brief item 5) is the per-measurement `_Trigger:`
line the follow-up MEASUREMENTS section prints. It is untouched by this session
and pinned here so it stays that way. (The "expedited intervention" sentence in
the pre-REPORT-2 sample was model-written prose — it appears nowhere in `src/` —
so there is nothing deterministic to keep from it.)
"""

from __future__ import annotations

import re

import pytest

from vib_agent.pdm_core.recommendations import corrective_recommendation
from vib_agent.report.generate import (
    _drafted_evidence_block,
    _recommendation_texts,
    render_drafted_html,
    render_html,
    render_markdown,
)
from vib_agent.pipeline import run_analysis
from vib_agent.synth.generator import make_case

BEARING_TEXT = corrective_recommendation("bearing_outer_race", "D").text
DRAFT_WITHOUT_A_HEADING = "## Executive Summary\n\nThe model's narrative.\n"


def _section(doc: str, heading: str) -> str | None:
    m = re.search(rf"^{re.escape(heading)}$(.*?)(?=^## |\Z)", doc, re.S | re.M)
    return (heading + m.group(1)).rstrip() if m else None


@pytest.fixture
def bpfo(iso_table, thresholds, rules):
    """The seeded BPFO case — Zone D, one committed `bearing_outer_race`. The
    case the analyst reviewed, and the one F-3's headline row is about."""
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    return case, result


@pytest.fixture
def gate_fail(iso_table, thresholds, rules):
    """A gate-violation case — no diagnosis, but re-capture advice through the
    measurement channel, which is what carries the `_Trigger:` lines."""
    case = make_case("machine_off", iso_table=iso_table, thresholds=thresholds, seed=1)
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    return case, result


class TestTheDeterministicReport:
    def test_the_markdown_carries_the_text_under_its_own_heading(self, bpfo, thresholds):
        case, result = bpfo
        md = render_markdown(result, case.machine, case=case, thresholds=thresholds,
                             profile="route")
        section = _section(md, "## Recommendations")
        assert section is not None, "the Recommendations heading is gone"
        assert BEARING_TEXT in section

    def test_the_html_carries_the_same_text(self, bpfo, thresholds):
        case, result = bpfo
        html = render_html(result, case.machine, case=case, thresholds=thresholds,
                           profile="route")
        assert "Recommendations" in html
        assert BEARING_TEXT in html

    def test_the_pre_rec1_string_is_gone_from_the_document(self, bpfo, thresholds):
        """The reviewed sentence replaced the old one; it did not join it."""
        case, result = bpfo
        md = render_markdown(result, case.machine, case=case, thresholds=thresholds,
                             profile="route")
        assert "Plan bearing replacement at the next maintenance window" not in md

    def test_the_recommendation_names_a_reassessment(self, bpfo, thresholds):
        case, result = bpfo
        md = render_markdown(result, case.machine, case=case, thresholds=thresholds,
                             profile="route")
        section = _section(md, "## Recommendations")
        assert "Reassess machine health with a repeat measurement" in section


class TestTheDraftedReport:
    """E2 item 5 — the heading the drafted document lost."""

    def test_the_appendix_splices_the_section_when_the_draft_wrote_none(
        self, bpfo, thresholds
    ):
        case, result = bpfo
        block = _drafted_evidence_block(
            result, charts=None, case=case, thresholds=thresholds, profile="route",
            draft_text=DRAFT_WITHOUT_A_HEADING, machine=case.machine,
        )
        assert block.count("## Recommendations") == 1
        assert BEARING_TEXT in block

    def test_the_spliced_section_is_byte_identical_to_the_deterministic_one(
        self, bpfo, thresholds
    ):
        """Same macro, same helper — so the two documents cannot word the
        recommendation differently."""
        case, result = bpfo
        md = render_markdown(result, case.machine, case=case, thresholds=thresholds,
                             profile="route")
        block = _drafted_evidence_block(
            result, charts=None, case=case, thresholds=thresholds, profile="route",
            draft_text=DRAFT_WITHOUT_A_HEADING, machine=case.machine,
        )
        assert _section(block, "## Recommendations") == _section(md, "## Recommendations")

    def test_a_draft_that_wrote_its_own_heading_is_not_given_a_second(
        self, bpfo, thresholds
    ):
        case, result = bpfo
        draft = DRAFT_WITHOUT_A_HEADING + "\n## Recommendations\n\n1. The model's own.\n"
        block = _drafted_evidence_block(
            result, charts=None, case=case, thresholds=thresholds, profile="route",
            draft_text=draft, machine=case.machine,
        )
        assert block.count("## Recommendations") == 0
        # And the whole document therefore has exactly one.
        assert (draft + block).count("## Recommendations") == 1

    def test_the_follow_up_measurements_heading_does_not_suppress_it(
        self, bpfo, thresholds
    ):
        """The probe is the heading, not the word: a draft that wrote only
        "## Recommended Follow-up Measurements" still needs the corrective
        section. This is the exact shape of E2 item 5's defect."""
        case, result = bpfo
        draft = (
            DRAFT_WITHOUT_A_HEADING
            + "\n## Recommended Follow-up Measurements\n\nNone.\n"
        )
        block = _drafted_evidence_block(
            result, charts=None, case=case, thresholds=thresholds, profile="route",
            draft_text=draft, machine=case.machine,
        )
        assert block.count("## Recommendations") == 1

    def test_the_drafted_html_page_carries_the_section_too(self, bpfo, thresholds):
        case, result = bpfo
        html = render_drafted_html(
            DRAFT_WITHOUT_A_HEADING, result, case.machine, case=case,
            thresholds=thresholds, profile="route",
        )
        assert html is not None
        assert "Recommendations" in html
        assert BEARING_TEXT in html

    def test_the_drafted_html_suppresses_on_the_same_flag(self, bpfo, thresholds):
        case, result = bpfo
        draft = DRAFT_WITHOUT_A_HEADING + "\n## Recommendations\n\n1. The model's own.\n"
        html = render_drafted_html(
            draft, result, case.machine, case=case, thresholds=thresholds, profile="route",
        )
        assert html is not None
        assert BEARING_TEXT not in html, "the deterministic block was rendered as well"


class TestTheEscalationTriggerIsKept:
    """Brief item 5. The `_Trigger:` line on each recommended measurement —
    `default_survey.md.j2:199` and `survey.html.j2:242`, neither touched by this
    session."""

    def test_the_markdown_still_prints_a_trigger_per_measurement(
        self, gate_fail, thresholds
    ):
        case, result = gate_fail
        assert result.recommended_measurements, "fixture no longer exercises the channel"
        md = render_markdown(result, case.machine, case=case, thresholds=thresholds,
                             profile="route")
        assert md.count("_Trigger:") == len(result.recommended_measurements)

    def test_the_html_still_prints_a_trigger_per_measurement(self, gate_fail, thresholds):
        case, result = gate_fail
        html = render_html(result, case.machine, case=case, thresholds=thresholds,
                           profile="route")
        assert html.count("Trigger:") == len(result.recommended_measurements)

    def test_the_two_channels_stay_separate(self, gate_fail, thresholds):
        """A gate-fail case makes no diagnosis, so it has re-capture MEASUREMENTS
        and no corrective action. If corrective text ever appeared here, the two
        channels have been crossed."""
        case, result = gate_fail
        assert result.findings == []
        assert _recommendation_texts(result) == []
        md = render_markdown(result, case.machine, case=case, thresholds=thresholds,
                             profile="route")
        assert "No corrective action recommended at this time." in md
        assert "_Trigger:" in md


class TestTheHelperIsSharedByBothPaths:
    def test_both_paths_call_the_same_helper(self, bpfo):
        """`_recommendation_texts` is the single source: build_context uses it
        for the deterministic document and _drafted_evidence_block for the
        appendix. Pinned so a later session cannot quietly give one path its own
        list again."""
        _, result = bpfo
        assert _recommendation_texts(result) == [BEARING_TEXT]

    def test_the_zone_reaches_the_helper(self, iso_table, thresholds, rules):
        """The two zone-worded ids need the report's own zone. Proven on the
        helper rather than on a rendered string, because no seeded case commits
        `possible_resonance` at more than one zone."""
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        assert result.iso is not None and result.iso.iso_zone == "D"
        result.findings[0].fault = "possible_resonance"
        assert _recommendation_texts(result) == [
            corrective_recommendation("possible_resonance", "D").text
        ]
        assert "as soon as practicable" in _recommendation_texts(result)[0]
