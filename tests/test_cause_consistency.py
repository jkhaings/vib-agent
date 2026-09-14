"""Session R2-BUILD — the cause-language consistency contract.

The cause section is rendered deterministically and spliced in, so in the normal
case the model contributes nothing to it. This file is about the abnormal cases,
and about why they are worth a hard gate at all.

A drafting model has an effectively unlimited supply of plausible bearing
causes, none of them sourced. That is a categorically different risk from the
ones the earlier checks cover: a wrong ISO zone or an upgraded confidence is a
value the AnalysisResult can be diffed against, but "excessive belt tension" is
a *new claim* with nothing behind it, sitting in a list where every neighbour
carries `[#01 §5 …]`. It inherits their authority. That is the R2-CITECHECK
finding — authority by adjacency — recurring one layer out, in front of a
customer rather than a reviewer.

So the contract is: names, mechanisms and citations are a SUBSET of the lookup;
no cause language at all when nothing was committed; and never any wording that
upgrades a candidate to a finding.
"""

from __future__ import annotations

import json

import pytest

from tests.fake_anthropic import FakeAnthropicClient, build_consistent_echo, draft_message
from vib_agent.agent.consistency import check_cause_language, strip_echo_block
from vib_agent.agent.loop import AgentAnalysisError, run_agent_analysis
from vib_agent.knowledge import CAUSE_HEADING, lookup_causes_for
from vib_agent.pipeline import run_analysis
from vib_agent.report.generate import render_markdown
from vib_agent.synth.generator import make_case


def _analysed(name, iso_table, thresholds, rules, seed=1):
    case = make_case(name, iso_table=iso_table, thresholds=thresholds, seed=seed)
    return case, run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)


def _deterministic(name, iso_table, thresholds, rules, seed=1):
    """The deterministic report IS by definition a perfectly consistent draft —
    it is what the model is shown as its reference structure. Using it as the
    happy-path fixture means a false positive in the checker shows up here
    immediately, rather than as a mysterious degrade in production."""
    case, result = _analysed(name, iso_table, thresholds, rules, seed)
    return result, render_markdown(result, case.machine, case=case,
                                   thresholds=thresholds, profile="route")


# ── the happy path ────────────────────────────────────────────────────────


class TestACorrectDraftPasses:
    def test_the_deterministic_report_is_clean(self, iso_table, thresholds, rules):
        result, markdown = _deterministic("bpfo", iso_table, thresholds, rules)
        assert check_cause_language(markdown, result) == []

    def test_a_draft_that_writes_no_cause_section_is_clean(self, iso_table, thresholds, rules):
        """The normal outcome: the model drafts prose, the section is spliced in
        afterwards. Silence is not a violation."""
        _, result = _analysed("bpfo", iso_table, thresholds, rules)
        text = "# Vibration Survey Report — x\n\nThe committed diagnosis is a bearing outer-race fault.\n"
        assert check_cause_language(text, result) == []

    def test_dropping_a_cause_is_allowed_but_adding_one_is_not(self, iso_table, thresholds, rules):
        """Subset, not equality. A shorter section is a presentation choice; a
        longer one is a fabrication."""
        result, markdown = _deterministic("bpfo", iso_table, thresholds, rules)
        section = markdown.split(f"## {CAUSE_HEADING}", 1)[1].split("\n## ", 1)[0]
        first_cause = section.split("### ", 2)[1]
        trimmed = f"# Vibration Survey Report — x\n\n## {CAUSE_HEADING}\n\n### {first_cause}"
        assert check_cause_language(trimmed, result) == []

    def test_every_bearing_family_report_passes_its_own_check(self, iso_table, thresholds, rules):
        for name in ("bpfo", "bpfi"):
            result, markdown = _deterministic(name, iso_table, thresholds, rules)
            assert check_cause_language(markdown, result) == [], name


# ── 1. SUBSET ─────────────────────────────────────────────────────────────


class TestFabricatedCauses:
    def test_an_off_list_cause_fails_the_contract(self, iso_table, thresholds, rules):
        """THE regression. 'Excessive belt tension' is a real bearing failure
        cause and a perfectly sensible thing for a model to write — it is simply
        not in the approved library, so it has no source behind it and may not
        appear beside entries that do."""
        result, markdown = _deterministic("bpfo", iso_table, thresholds, rules)
        forged = markdown.replace(
            "### Solid contaminant ingress past a failed or inadequate seal",
            "### Excessive belt tension overloading the bearing",
        )
        mismatches = check_cause_language(forged, result)
        assert any(m.startswith("fabricated cause:") for m in mismatches)
        assert "Excessive belt tension overloading the bearing" in mismatches[0]

    def test_the_message_lists_the_approved_causes(self, iso_table, thresholds, rules):
        """A retry is only useful if the model is told what it may say."""
        result, markdown = _deterministic("bpfo", iso_table, thresholds, rules)
        forged = markdown.replace(
            "### Water or condensate reaching the raceway", "### Cavitation erosion"
        )
        message = next(m for m in check_cause_language(forged, result)
                       if m.startswith("fabricated cause:"))
        for cause in lookup_causes_for(["bearing_outer_race"]):
            assert cause.cause in message

    def test_a_cause_from_another_bearing_family_is_still_off_list(
        self, iso_table, thresholds, rules
    ):
        """The cage entry is real knowledge — but not for an outer-race call."""
        result, markdown = _deterministic("bpfo", iso_table, thresholds, rules)
        forged = markdown.replace(
            "### Water or condensate reaching the raceway",
            "### Debris embedding in the cage and cutting the rolling elements",
        )
        assert any(m.startswith("fabricated cause:") for m in check_cause_language(forged, result))

    def test_a_fabricated_citation_fails_even_on_an_approved_cause(
        self, iso_table, thresholds, rules
    ):
        """The higher-value half. A right cause with an invented page reference
        is a source claim nobody can check — the exact defect the whole
        reference register exists to prevent."""
        result, markdown = _deterministic("bpfo", iso_table, thresholds, rules)
        forged = markdown.replace(
            "[#02 Wear - Abrasive Contamination, p.7]", "[#02 Wear - Abrasive Contamination, p.9]"
        )
        mismatches = check_cause_language(forged, result)
        assert any(m.startswith("cause citation:") for m in mismatches)

    def test_a_citation_to_an_unindexed_document_fails(self, iso_table, thresholds, rules):
        result, markdown = _deterministic("bpfo", iso_table, thresholds, rules)
        forged = markdown.replace(
            "[#04 Abrasive Wear, pp.5-6]", "[#77 Bearing Failure Handbook, p.12]"
        )
        assert any(m.startswith("cause citation:") for m in check_cause_language(forged, result))

    def test_a_rewritten_mechanism_fails(self, iso_table, thresholds, rules):
        """A mechanism is a sourced claim, not prose to paraphrase. The model is
        told to reproduce this section verbatim if it reproduces it at all, so
        exact comparison costs nothing and closes a real drift path."""
        result, markdown = _deterministic("bpfo", iso_table, thresholds, rules)
        forged = markdown.replace(
            "Hard particles of roughly the oil-film thickness enter the rolling contact",
            "Large particles of any size enter the rolling contact",
        )
        assert any(m.startswith("cause mechanism:") for m in check_cause_language(forged, result))


# ── 2. ZERO ───────────────────────────────────────────────────────────────


class TestZeroCauseLanguageWithoutACommittedBearingFault:
    @pytest.mark.parametrize("name", ["healthy", "imbalance", "looseness", "angular_misalignment"])
    def test_a_cause_section_is_refused_outright(self, name, iso_table, thresholds, rules):
        _, result = _analysed(name, iso_table, thresholds, rules)
        forged = (
            f"# Vibration Survey Report — x\n\n## {CAUSE_HEADING}\n\n"
            "### Water or condensate reaching the raceway\n\nWater in the bearing.\n"
        )
        mismatches = check_cause_language(forged, result)
        assert mismatches
        assert any("Remove the section entirely" in m for m in mismatches)

    def test_loose_cause_prose_is_refused_too(self, iso_table, thresholds, rules):
        """Not only the heading. A model that drops the section but keeps "the
        underlying cause is likely contamination" in the summary has done the
        same thing in a place a reader trusts more."""
        _, result = _analysed("healthy", iso_table, thresholds, rules)
        forged = (
            "# Vibration Survey Report — x\n\n## Executive Summary\n\n"
            "No fault was committed, though the underlying cause of the elevated "
            "noise may be seal wear.\n"
        )
        assert check_cause_language(forged, result)

    def test_a_reference_citation_anywhere_is_refused(self, iso_table, thresholds, rules):
        """`[#NN …]` is a shape nothing else in a report emits, which makes it a
        zero-false-positive probe for library content leaking somewhere it has
        no committed fault to attach to."""
        _, result = _analysed("imbalance", iso_table, thresholds, rules)
        forged = (
            "# Vibration Survey Report — x\n\nImbalance was committed "
            "[#01 §5 Damage and actions, Abrasive wear, p.66].\n"
        )
        assert any("Reference-library citations" in m for m in check_cause_language(forged, result))

    def test_a_clean_report_with_no_cause_language_passes(self, iso_table, thresholds, rules):
        for name in ("healthy", "imbalance", "belt_fault"):
            result, markdown = _deterministic(name, iso_table, thresholds, rules)
            assert check_cause_language(markdown, result) == [], name


# ── 3. HYPOTHESIS ONLY ────────────────────────────────────────────────────


class TestNoCauseIsEverPresentedAsEstablished:
    @pytest.mark.parametrize(
        "phrase",
        [
            "The root cause is contamination.",
            "The underlying cause is a failed seal.",
            "This cause has been confirmed by the analysis.",
            "We determined that the seal failed.",
            "The cause was definitively established.",
        ],
    )
    def test_assertive_wording_in_the_section_fails(
        self, phrase, iso_table, thresholds, rules
    ):
        result, markdown = _deterministic("bpfo", iso_table, thresholds, rules)
        forged = markdown.replace(
            "**These are hypotheses, not findings.**",
            f"**These are hypotheses, not findings.** {phrase}",
        )
        mismatches = check_cause_language(forged, result)
        assert any("states a cause as established" in m for m in mismatches), phrase

    def test_the_retry_message_never_repeats_the_offending_phrase(
        self, iso_table, thresholds, rules
    ):
        """Session F2's rule — the drafting model is never HANDED the vocabulary
        the report must not use — applies to a retry prompt exactly as it
        applies to the first one. Detecting a phrase is not teaching it;
        echoing it back is."""
        result, markdown = _deterministic("bpfo", iso_table, thresholds, rules)
        forged = markdown.replace(
            "**These are hypotheses, not findings.**",
            "**These are hypotheses, not findings.** The root cause is contamination.",
        )
        joined = " ".join(check_cause_language(forged, result)).lower()
        assert joined
        assert "root cause" not in joined and "root-cause" not in joined
        assert "rca" not in joined

    def test_the_sections_own_framing_is_not_mistaken_for_an_assertion(
        self, iso_table, thresholds, rules
    ):
        """"hypotheses for analyst confirmation", "confirm or exclude" and "the
        analysis committed <fault>" all contain words a naive word-level ban
        would fire on. Precision matters: a false positive costs a retry and
        then a degrade on a CORRECT report."""
        result, markdown = _deterministic("bpfo", iso_table, thresholds, rules)
        assert "confirmation" in CAUSE_HEADING
        section = markdown.split(f"## {CAUSE_HEADING}", 1)[1]
        assert "confirm or exclude" in section
        assert "committed" in section
        assert "is confirmed by this measurement" in section  # inside "none of them is..."
        assert check_cause_language(markdown, result) == []


# ── through the loop ──────────────────────────────────────────────────────


class TestThroughTheDraftingLoop:
    """The check is only worth anything if it is actually wired into the retry
    path — the same path the title, echo, zone, fault, confidence, numeric and
    stage checks ride."""

    @staticmethod
    def _draft(result, machine_name, body):
        title = f"# Vibration Survey Report — {machine_name}"
        narrative = (
            f"{title}\n\n## Executive Summary\n\nThe committed diagnosis is a bearing "
            f"outer-race fault (BPFO), assessed with high confidence.\n\n{body}\n\n"
            "DRAFT -- prepared by automated analysis, pending analyst review."
        )
        return draft_message(narrative, build_consistent_echo(result))

    def test_a_fabricated_cause_is_retried_then_hard_fails(
        self, iso_table, thresholds, rules, tmp_path
    ):
        case, result = _analysed("bpfo", iso_table, thresholds, rules)
        forged_body = (
            f"## {CAUSE_HEADING}\n\n### Excessive belt tension overloading the bearing\n\n"
            "Belt tension crushes the outer race.\n"
        )
        client = FakeAnthropicClient(
            responses=[
                self._draft(result, case.machine.name, forged_body),
                self._draft(result, case.machine.name, forged_body),
            ]
        )
        with pytest.raises(AgentAnalysisError, match=r"fabricated cause"):
            run_agent_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules,
                               out_dir=tmp_path / "job", client=client)
        assert len(client.messages.calls) == 2  # one draft, one retry, then stop

    def test_the_retry_prompt_carries_the_approved_list(
        self, iso_table, thresholds, rules, tmp_path
    ):
        case, result = _analysed("bpfo", iso_table, thresholds, rules)
        forged_body = f"## {CAUSE_HEADING}\n\n### Cavitation erosion\n\nBubbles.\n"
        client = FakeAnthropicClient(
            responses=[self._draft(result, case.machine.name, forged_body)] * 2
        )
        with pytest.raises(AgentAnalysisError):
            run_agent_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules,
                               out_dir=tmp_path / "job", client=client)
        retry_prompt = client.messages.calls[1]["messages"][0]["content"]
        assert "fabricated cause" in retry_prompt
        assert "Solid contaminant ingress past a failed or inadequate seal" in retry_prompt

    def test_a_correct_draft_publishes_with_the_deterministic_section_spliced_in(
        self, iso_table, thresholds, rules, tmp_path
    ):
        """The whole point of the deterministic splice: the model writes prose
        and gets the sourced cause section attached, byte for byte."""
        case, result = _analysed("bpfo", iso_table, thresholds, rules)
        client = FakeAnthropicClient(
            responses=[self._draft(result, case.machine.name, "## Diagnosis\n\nBPFO.\n")]
        )
        run_agent_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules,
                           out_dir=tmp_path / "job", client=client)
        published = (tmp_path / "job" / "report.md").read_text()
        assert f"## {CAUSE_HEADING}" in published
        deterministic = render_markdown(result, case.machine, case=case,
                                        thresholds=thresholds, profile="route")
        expected = deterministic.split(f"## {CAUSE_HEADING}", 1)[1].split("\n## ", 1)[0]
        actual = published.split(f"## {CAUSE_HEADING}", 1)[1].split("\n## ", 1)[0]
        assert actual == expected

    def test_a_healthy_machine_never_gets_a_cause_section_published(
        self, iso_table, thresholds, rules, tmp_path
    ):
        case, result = _analysed("healthy", iso_table, thresholds, rules)
        title = f"# Vibration Survey Report — {case.machine.name}"
        narrative = (
            f"{title}\n\n## Diagnosis\n\n**Committed diagnosis: none — parameters within "
            "normal range.**\n\nDRAFT -- prepared by automated analysis, pending analyst review."
        )
        client = FakeAnthropicClient(
            responses=[draft_message(narrative, build_consistent_echo(result))]
        )
        run_agent_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules,
                           out_dir=tmp_path / "job", client=client)
        published = (tmp_path / "job" / "report.md").read_text()
        assert CAUSE_HEADING not in published
        assert "underlying cause" not in published.lower()


# ── the tool ──────────────────────────────────────────────────────────────


class TestTheAgentTool:
    @staticmethod
    def _ctx(case, result):
        from vib_agent.agent.tools import ToolContext

        return ToolContext(result=result, case=case, peak_set=None)

    def test_it_is_registered_and_read_only(self):
        from vib_agent.agent.tools import TOOL_SCHEMAS

        schema = next(t for t in TOOL_SCHEMAS if t["name"] == "lookup_causes")
        assert "HYPOTHESES" in schema["description"]
        assert "Read-only" in schema["description"]
        assert schema["input_schema"]["properties"]["fault_family"]["enum"] == [
            "bearing_ball_spin", "bearing_cage", "bearing_inner_race", "bearing_outer_race"
        ]

    def test_it_returns_the_lookup_for_a_committed_fault(self, iso_table, thresholds, rules):
        from vib_agent.agent.tools import execute_tool

        case, result = _analysed("bpfo", iso_table, thresholds, rules)
        payload = json.loads(
            execute_tool("lookup_causes", {"fault_family": "bearing_outer_race"},
                         ctx=self._ctx(case, result))
        )
        assert [c["cause_id"] for c in payload["causes"]] == [
            c.cause_id for c in lookup_causes_for(["bearing_outer_race"])
        ]
        assert payload["status"] == "hypotheses_for_analyst_confirmation"
        assert "None of these is confirmed by this measurement" in payload["caveat"]

    def test_it_refuses_a_fault_the_analysis_did_not_commit(self, iso_table, thresholds, rules):
        """Left ungated, the tool would let the model ask for the causes of a
        fault that was never committed and then write about them — the RUN v5
        fabrication shape, one layer further out."""
        from vib_agent.agent.tools import execute_tool

        case, result = _analysed("bpfo", iso_table, thresholds, rules)
        payload = json.loads(
            execute_tool("lookup_causes", {"fault_family": "bearing_cage"},
                         ctx=self._ctx(case, result))
        )
        assert payload["causes"] == []
        assert "was not committed by this analysis" in payload["error"]

    def test_a_clean_reading_gets_an_empty_list_and_an_instruction(
        self, iso_table, thresholds, rules
    ):
        from vib_agent.agent.tools import execute_tool

        case, result = _analysed("healthy", iso_table, thresholds, rules)
        payload = json.loads(
            execute_tool("lookup_causes", {"fault_family": "bearing_outer_race"},
                         ctx=self._ctx(case, result))
        )
        assert payload["causes"] == []
        assert "Do not write a causes section" in payload["error"]

    def test_the_payload_carries_no_reviewer_only_field(self, iso_table, thresholds, rules):
        from vib_agent.agent.tools import execute_tool

        case, result = _analysed("bpfo", iso_table, thresholds, rules)
        raw = execute_tool("lookup_causes", {"fault_family": "bearing_outer_race"},
                           ctx=self._ctx(case, result))
        assert "review_question" not in raw
        assert "disagreement" not in raw
        assert '"note"' not in raw

    def test_the_system_prompt_states_the_hypothesis_rule(self):
        from vib_agent.agent.system_prompt import SYSTEM_PROMPT

        assert "UNDERLYING CAUSES ARE HYPOTHESES, NEVER FINDINGS" in SYSTEM_PROMPT
        assert "lookup_causes" in SYSTEM_PROMPT
        assert "four read-only drill-down tools" in SYSTEM_PROMPT


def test_strip_echo_block_is_applied_before_any_cause_check(iso_table, thresholds, rules):
    """The echo block legitimately carries fault ids; it must never be scanned
    as narrative, exactly as every other prose-level check treats it."""
    _, result = _analysed("healthy", iso_table, thresholds, rules)
    text = (
        "# Vibration Survey Report — x\n\nNothing committed.\n\n"
        '<<<ECHO_START>>>\n{"zone": "A", "faults": [], "recommended_measurements": []}\n'
        "<<<ECHO_END>>>"
    )
    assert "ECHO_START" not in strip_echo_block(text)
    assert check_cause_language(text, result) == []
