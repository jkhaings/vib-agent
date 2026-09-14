"""Integration tests for the Phase 4 drafting loop (agent/loop.py), using a
fake Anthropic client -- no real API calls anywhere in this suite.
"""

from __future__ import annotations

import json

import pytest

from tests.fake_anthropic import (
    FakeAnthropicClient,
    FakeMessage,
    FakeTextBlock,
    FakeToolUseBlock,
    build_consistent_echo,
    draft_message,
)
from vib_agent.agent.consistency import TITLE_TEMPLATE
from vib_agent.agent.loop import AgentAnalysisError, run_agent_analysis
from vib_agent.config import load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.synth.generator import make_case

# The bpfo fault recipe's machine (see synth/generator.py::_comp_with_bearing).
_BPFO_MACHINE_NAME = "Synthetic Compressor 01"
_TITLE = TITLE_TEMPLATE.format(machine_name=_BPFO_MACHINE_NAME)
_NARRATIVE = f"{_TITLE}\n\nReport body.\n\nDRAFT -- prepared by automated analysis, pending analyst review."


def _run(case, iso_table, thresholds, rules, out_dir, responses):
    client = FakeAnthropicClient(responses)
    result = run_agent_analysis(
        case,
        iso_table=iso_table,
        thresholds=thresholds,
        rules=rules,
        out_dir=out_dir,
        client=client,
    )
    return result, client


class TestGateFailSkipsLLM:
    def test_gate_fail_never_calls_the_model(self, iso_table, thresholds, rules, tmp_path):
        case = make_case("machine_off", iso_table=iso_table, thresholds=thresholds, seed=1)
        out_dir = tmp_path / "case"
        client = FakeAnthropicClient(responses=[])  # any .create() call raises AssertionError

        result = run_agent_analysis(
            case, iso_table=iso_table, thresholds=thresholds, rules=rules, out_dir=out_dir, client=client
        )

        assert result.quality_gate.overall == "fail"
        assert client.messages.calls == []
        report_text = (out_dir / "report.md").read_text()
        assert "Insufficient data" in report_text
        trace = [json.loads(line) for line in (out_dir / "trace.jsonl").read_text().strip().splitlines()]
        assert any(entry["phase"] == "gate_fail_skip" for entry in trace)


class TestSuccessfulDraft:
    def test_consistent_draft_on_first_try(self, iso_table, thresholds, rules, tmp_path):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        expected = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        out_dir = tmp_path / "case"

        result, client = _run(
            case, iso_table, thresholds, rules, out_dir,
            responses=[draft_message(_NARRATIVE, build_consistent_echo(expected))],
        )

        assert result.rca is not None
        assert len(client.messages.calls) == 1
        report_text = (out_dir / "report.md").read_text()
        assert "<<<ECHO_START>>>" not in report_text
        assert "DRAFT" in report_text
        assert (out_dir / "analysis.json").exists()
        trace_lines = [json.loads(line) for line in (out_dir / "trace.jsonl").read_text().strip().splitlines()]
        assert any(entry.get("phase") == "draft" for entry in trace_lines)

    def test_tool_call_is_dispatched_and_reflected_in_trace(self, iso_table, thresholds, rules, tmp_path):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        expected = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        out_dir = tmp_path / "case"

        tool_call_response = FakeMessage(
            content=[FakeToolUseBlock(id="toolu_1", name="get_raw_peak_detail", input={"axis": "y"})],
            stop_reason="tool_use",
        )
        final_response = draft_message(_NARRATIVE, build_consistent_echo(expected))

        result, client = _run(
            case, iso_table, thresholds, rules, out_dir,
            responses=[tool_call_response, final_response],
        )

        assert result.rca is not None
        assert len(client.messages.calls) == 2
        second_call_messages = client.messages.calls[1]["messages"]
        tool_result_msg = second_call_messages[-1]
        assert tool_result_msg["role"] == "user"
        assert tool_result_msg["content"][0]["tool_use_id"] == "toolu_1"
        trace_lines = [json.loads(line) for line in (out_dir / "trace.jsonl").read_text().strip().splitlines()]
        assert any(entry.get("tool") == "get_raw_peak_detail" for entry in trace_lines)


class TestConsistencyRetry:
    def test_mismatch_then_consistent_retry_succeeds(self, iso_table, thresholds, rules, tmp_path):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        expected = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        bad_echo = (
            '<<<ECHO_START>>>\n{"zone": "Z", "faults": [], "recommended_measurements": []}\n<<<ECHO_END>>>'
        )
        out_dir = tmp_path / "case"

        result, client = _run(
            case, iso_table, thresholds, rules, out_dir,
            responses=[
                FakeMessage(content=[FakeTextBlock(text=f"{_NARRATIVE}\n\n{bad_echo}")], stop_reason="end_turn"),
                draft_message(_NARRATIVE, build_consistent_echo(expected)),
            ],
        )

        assert result.rca is not None
        assert len(client.messages.calls) == 2
        assert (out_dir / "report.md").exists()

    def test_mismatch_on_both_attempts_hard_fails(self, iso_table, thresholds, rules, tmp_path):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        bad_echo = (
            '<<<ECHO_START>>>\n{"zone": "Z", "faults": [], "recommended_measurements": []}\n<<<ECHO_END>>>'
        )
        out_dir = tmp_path / "case"

        with pytest.raises(AgentAnalysisError):
            _run(
                case, iso_table, thresholds, rules, out_dir,
                responses=[
                    draft_message(_NARRATIVE, bad_echo),
                    draft_message(_NARRATIVE, bad_echo),
                ],
            )

        assert not (out_dir / "report.md").exists()
        trace_lines = [json.loads(line) for line in (out_dir / "trace.jsonl").read_text().strip().splitlines()]
        assert any(entry.get("phase") == "hard_fail" for entry in trace_lines)


class TestTitleCheck:
    def test_preamble_before_title_triggers_retry_then_succeeds(self, iso_table, thresholds, rules, tmp_path):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        expected = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        echo = build_consistent_echo(expected)
        preamble_draft = (
            "Good -- I have the raw peak amplitudes. I now have everything I need to draft the "
            f"full report.\n\n---\n\n{_TITLE}\n\nReport body.\n\n"
            "DRAFT -- prepared by automated analysis, pending analyst review.\n\n" + echo
        )
        out_dir = tmp_path / "case"

        result, client = _run(
            case, iso_table, thresholds, rules, out_dir,
            responses=[
                FakeMessage(content=[FakeTextBlock(text=preamble_draft)], stop_reason="end_turn"),
                draft_message(_NARRATIVE, echo),
            ],
        )

        assert result.rca is not None
        assert len(client.messages.calls) == 2
        report_text = (out_dir / "report.md").read_text()
        assert report_text.startswith(_TITLE)
        assert "Good --" not in report_text

    def test_preamble_on_both_attempts_hard_fails(self, iso_table, thresholds, rules, tmp_path):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        expected = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        echo = build_consistent_echo(expected)
        preamble_draft = f"Let me draft this now.\n\n{_TITLE}\n\nReport body.\n\n" + echo
        out_dir = tmp_path / "case"

        with pytest.raises(AgentAnalysisError) as excinfo:
            _run(
                case, iso_table, thresholds, rules, out_dir,
                responses=[
                    FakeMessage(content=[FakeTextBlock(text=preamble_draft)], stop_reason="end_turn"),
                    FakeMessage(content=[FakeTextBlock(text=preamble_draft)], stop_reason="end_turn"),
                ],
            )

        assert "title:" in str(excinfo.value)
        assert not (out_dir / "report.md").exists()


class TestToolBudgetCap:
    def test_tool_calls_beyond_budget_are_refused_not_executed(
        self, iso_table, thresholds, rules, tmp_path, monkeypatch
    ):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        expected = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        out_dir = tmp_path / "case"

        import vib_agent.agent.loop as loop_module

        real_execute = loop_module.execute_tool
        executed_calls: list[str] = []

        def _tracking_execute(name, tool_input, *, ctx):
            executed_calls.append(name)
            return real_execute(name, tool_input, ctx=ctx)

        monkeypatch.setattr(loop_module, "execute_tool", _tracking_execute)

        six_tool_calls = FakeMessage(
            content=[
                FakeToolUseBlock(id=f"toolu_{i}", name="get_raw_peak_detail", input={}) for i in range(6)
            ],
            stop_reason="tool_use",
        )
        final_response = draft_message(_NARRATIVE, build_consistent_echo(expected))

        result, client = _run(
            case, iso_table, thresholds, rules, out_dir,
            responses=[six_tool_calls, final_response],
        )

        assert len(executed_calls) == 5  # capped at config max_drilldown_tool_calls
        tool_results = client.messages.calls[1]["messages"][-1]["content"]
        assert len(tool_results) == 6
        assert sum(1 for r in tool_results if r.get("is_error")) == 1
        assert result.rca is not None


class TestNarrativeFabricationIsCaught:
    """Session D regression for the RUN v5 defect: on the MFPT healthy rig
    baseline the model emitted an HONEST echo block (faults: []) while the
    published prose asserted "The committed diagnosis ... is Rotor imbalance,
    assessed at medium confidence". The echo check passed, the zone check passed,
    and a fabricated fault on a healthy machine shipped. It must now be caught,
    retried once, and -- if the retry repeats it -- hard-fail so the webapp
    degrades to the deterministic report instead of publishing the fabrication.
    """

    @staticmethod
    def _healthy_case(iso_table, thresholds):
        return make_case("healthy", iso_table=iso_table, thresholds=thresholds, seed=1)

    @staticmethod
    def _fabricated_narrative(machine_name: str) -> str:
        title = TITLE_TEMPLATE.format(machine_name=machine_name)
        return (
            f"{title}\n\n## Executive Summary\n\n"
            "The committed diagnosis for this machine is Rotor imbalance, assessed at medium "
            "confidence.\n\n## Diagnosis\n\n"
            "### Rotor Imbalance — severity: unrated, confidence: medium\n\n"
            "1x shaft frequency dominant on radial axis y; axial axis quiet at shaft frequency.\n\n"
            "DRAFT -- prepared by automated analysis, pending analyst review."
        )

    def test_fabrication_on_both_attempts_hard_fails(self, iso_table, thresholds, rules, tmp_path):
        case = self._healthy_case(iso_table, thresholds)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        assert [f.fault for f in result.findings] == ["no_significant_findings"]

        # Honest echo (empty faults, matching the AnalysisResult) + invented prose --
        # exactly the combination that slipped through in RUN v5.
        echo = build_consistent_echo(result)
        narrative = self._fabricated_narrative(case.machine.name)
        responses = [draft_message(narrative, echo), draft_message(narrative, echo)]

        with pytest.raises(AgentAnalysisError) as excinfo:
            _run(case, iso_table, thresholds, rules, tmp_path, responses)
        assert "fault language" in str(excinfo.value)
        assert "imbalance" in str(excinfo.value)

    def test_fabrication_then_honest_retry_succeeds(self, iso_table, thresholds, rules, tmp_path):
        case = self._healthy_case(iso_table, thresholds)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        echo = build_consistent_echo(result)
        title = TITLE_TEMPLATE.format(machine_name=case.machine.name)
        honest = (
            f"{title}\n\n## Diagnosis\n\n"
            "**Committed diagnosis: none — parameters within normal range.**\n\n"
            "No fault signature matched in the spectral evidence.\n\n"
            "DRAFT -- prepared by automated analysis, pending analyst review."
        )
        responses = [
            draft_message(self._fabricated_narrative(case.machine.name), echo),
            draft_message(honest, echo),
        ]
        analysis, client = _run(case, iso_table, thresholds, rules, tmp_path, responses)
        assert [f.fault for f in analysis.findings] == ["no_significant_findings"]
        assert len(client.messages.calls) == 2  # retried exactly once
        published = (tmp_path / "report.md").read_text()
        assert "none — parameters within normal range" in published
        assert "Rotor imbalance" not in published

    def test_retry_prompt_names_the_fabricated_fault(self, iso_table, thresholds, rules, tmp_path):
        """The retry must tell the model exactly what it invented, or it cannot fix it."""
        case = self._healthy_case(iso_table, thresholds)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        echo = build_consistent_echo(result)
        narrative = self._fabricated_narrative(case.machine.name)
        client = FakeAnthropicClient([draft_message(narrative, echo), draft_message(narrative, echo)])

        with pytest.raises(AgentAnalysisError):
            run_agent_analysis(
                case, iso_table=iso_table, thresholds=thresholds, rules=rules,
                out_dir=tmp_path, client=client,
            )

        assert len(client.messages.calls) == 2
        retry_prompt = client.messages.calls[1]["messages"][0]["content"]
        assert "fault language" in retry_prompt
        assert "imbalance" in retry_prompt
        # and it must point at the legitimate landing place, not just say "no"
        assert "parameters within normal range" in retry_prompt


# ── Session TFIX ─────────────────────────────────────────────────────────


class TestReferenceReportIsComplete:
    """The reference report handed to the drafting model must be the SAME
    report the deterministic path would issue.

    `run_agent_analysis` built it with `render_markdown(result, machine,
    include_causes=False)` — no `case`, no `thresholds`, no `profile`. Without
    them `analysis_parameters()` cannot reach the spectrum array or the resolved
    thresholds, so seven rows came back "not recorded", and the model
    transcribed that table faithfully into its narrative. A shipped sample said
    "Not recorded" for a spectrum's own line count.

    The model is a relay. Handing it a degraded reference and hoping it does
    better than what it was shown is not a contract.
    """

    def _user_prompt(self, iso_table, rules, out_dir):
        # `route` named explicitly. The shared `thresholds` fixture calls
        # load_thresholds() with no argument, which resolves `active_profile`
        # — and that is `streaming`, whose rca section carries no
        # `tolerance_pct`, so the Match-tolerance row would be absent for a
        # reason that has nothing to do with what this test is about.
        thresholds = load_thresholds("route")
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        echo = build_consistent_echo(
            run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules))
        client = FakeAnthropicClient([draft_message(_NARRATIVE, echo)])
        run_agent_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules,
                           out_dir=out_dir, client=client, profile="route")
        content = client.messages.calls[0]["messages"][0]["content"]
        return case, (content if isinstance(content, str) else str(content))

    def test_the_model_is_shown_the_derived_acquisition_parameters(
        self, iso_table, rules, tmp_path
    ):
        case, prompt = self._user_prompt(iso_table, rules, tmp_path / "c")

        for label, value in (
            ("Spectral lines (N)", str(len(case.spectra["y"].freq_hz))),
            ("Line spacing (Δf)", "0.250 Hz"),
            ("Fmax", "500.0 Hz"),
            ("Sample rate (fs)", "1000 Hz"),
            ("Spectrum type", "Envelope spectrum"),
            ("Match tolerance", "±3%"),
            ("Threshold profile", "route"),
        ):
            assert value in prompt, (
                f"the reference report does not give the model {label} = {value!r}; it will "
                f"transcribe whatever it was shown")

    def test_window_stays_unknown_because_it_genuinely_is(self, iso_table, rules, tmp_path):
        """The fix passes inputs; it does not invent values. `Window` is not
        recoverable from a supplied spectrum and still says so. (Session
        INTAKE-HONEST reworded the absent-value string from the old
        "not recorded on this path" hardcode to an honest not-provided
        statement; the property pinned here — the reference report never
        invents a window — is unchanged.)"""
        _, prompt = self._user_prompt(iso_table, rules, tmp_path / "c")
        assert "not provided — unknown window; amplitudes taken as supplied" in prompt


class TestBaselineClaimIsRefused:
    """The guard has to REFUSE the draft, not hope the next roll omits it.

    `check_baseline_claims` returning strings proves nothing on its own — it
    only matters if it is wired into the same retry-then-hard-fail path as the
    title, echo, zone, fault, confidence, numeric, stage and cause checks.
    """

    BASELINE_SENTENCE = (
        "This reading was **not** used to update the machine's baseline, as ISO Zone D readings "
        "are excluded from baseline training."
    )

    def _draft(self, echo: str) -> str:
        return (f"{_TITLE}\n\nReport body. {self.BASELINE_SENTENCE}\n\n"
                "DRAFT -- prepared by automated analysis, pending analyst review.")

    def test_a_draft_that_narrates_baseline_training_is_refused(
        self, iso_table, thresholds, rules, tmp_path
    ):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        expected = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        assert expected.zscore is None, "the fixture must have no baseline layer for this to apply"
        echo = build_consistent_echo(expected)
        out_dir = tmp_path / "case"

        with pytest.raises(AgentAnalysisError):
            _run(case, iso_table, thresholds, rules, out_dir,
                 responses=[draft_message(self._draft(echo), echo),
                            draft_message(self._draft(echo), echo)])

        assert not (out_dir / "report.md").exists(), "a refused draft must not be published"
        trace = [json.loads(line)
                 for line in (out_dir / "trace.jsonl").read_text().strip().splitlines()]
        assert any(e.get("phase") == "hard_fail" for e in trace)

    def test_the_same_draft_without_that_sentence_is_published(
        self, iso_table, thresholds, rules, tmp_path
    ):
        """The guard is narrow enough that the identical report passes once the
        one sentence is gone — it is not refusing the draft for another reason."""
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        expected = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        echo = build_consistent_echo(expected)
        out_dir = tmp_path / "clean"

        _run(case, iso_table, thresholds, rules, out_dir,
             responses=[draft_message(_NARRATIVE, echo)])
        assert (out_dir / "report.md").exists()
