"""Plain tool-use while-loop against the Messages API.

Architecture: the agent is a drafting pass over the canonical pipeline, not
a free orchestrator. `pipeline.run_analysis()` runs exactly once per report
-- that AnalysisResult is ground truth. The model drafts a narrative over
it, with up to `max_drilldown_tool_calls` read-only tool calls for
elaboration (never to re-run analysis), then appends a structured echo
block. The draft is checked two ways before publication (agent/consistency.py):
structurally (the first line must be the exact report title -- no preamble
or tool-use narration) and field-by-field (the echo block against the
AnalysisResult). Either kind of mismatch triggers exactly one retry with the
diff appended; a second mismatch is a hard failure -- drift is never
silently accepted. Gate-fail cases skip the LLM entirely: there is no
diagnosis to draft, so the deterministic insufficient-data report is reused
verbatim.

Every request/response in this module is logged to outputs/<case>/trace.jsonl
(model, stop_reason, usage, latency, and each tool call/result) for the
Task 3 side-by-side artifact and for debugging.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

from vib_agent.agent.consistency import (
    TITLE_TEMPLATE,
    check_baseline_claims,
    check_cause_language,
    check_confidence_binding,
    check_echo_against_result,
    check_fault_term_language,
    check_measured_channels,
    check_numeric_quotes,
    check_stage_language,
    check_title_line,
    check_zone_language,
    parse_echo_block,
    strip_echo_block,
)
from vib_agent.agent.system_prompt import SYSTEM_PROMPT
from vib_agent.agent.tools import TOOL_SCHEMAS, ToolContext, execute_tool
from vib_agent.config import load_config
from vib_agent.models import AnalysisResult, Case
from vib_agent.pdm_core.bearing_rca import (
    measured_axes_from_sensor_data,
    peaks_from_ncd,
    peaks_from_spectrum,
)
from vib_agent.pipeline import run_analysis
from vib_agent.report.generate import (
    render_markdown,
    render_report,
    staging_profile,
    write_drafted_report,
)


class AgentAnalysisError(RuntimeError):
    """Raised when the drafted narrative still fails the consistency check
    after one retry, or the tool-use loop cannot finish within its iteration
    budget."""


def run_agent_analysis(
    case: Case,
    *,
    iso_table: dict[str, dict[str, float]],
    thresholds: dict[str, Any],
    rules: dict[str, Any] | None,
    out_dir: Path,
    pdf: bool = False,
    client: anthropic.Anthropic | None = None,
    report_out: dict[str, Any] | None = None,
    profile: str | None = None,
) -> AnalysisResult:
    """Run the canonical pipeline once, then (gate permitting) draft a
    narrative report over it. Writes report.md / analysis.json / trace.jsonl
    into out_dir and returns the same AnalysisResult the --no-llm path would
    produce for this case (same tools, same numbers).

    `report_out`, when given, is filled with what the writer produced — the
    figure manifest and the UN-spliced narrative. A caller that has to render
    the PDF itself afterwards (the webapp does, because it inserts its own
    notes first) then needs neither to redraw every figure nor to recover the
    narrative by unpicking the report it just wrote.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    trace_path = out_dir / "trace.jsonl"

    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)

    if result.quality_gate.overall == "fail":
        # No diagnosis before the gate passes -- there is nothing to draft.
        # Reuse the deterministic insufficient-data report verbatim rather
        # than spend a call asking the model to reproduce it.
        _append_trace(trace_path, {"phase": "gate_fail_skip"})
        written = render_report(result, case.machine, out_dir, pdf=pdf, case=case,
                                thresholds=thresholds, profile=profile)
        if report_out is not None:
            report_out.update(written)
        return result

    agent_cfg = load_config("agent")
    client = client or anthropic.Anthropic()

    peak_set = _build_peak_set(case, thresholds) if result.rca is not None else None
    tool_ctx = ToolContext(result=result, case=case, peak_set=peak_set)

    # Session R3-DIFF (item 3): the reference report handed to the drafting model
    # deliberately OMITS the cause hypotheses. They are spliced into the drafted
    # report deterministically below (write_drafted_report), from the same lookup
    # — so including them here asks the model to reproduce 156 lines it cannot
    # improve, and whose only correct rendering is the one already in hand. See
    # render_markdown's own docstring for the failure this caused in production.
    # Session TFIX. `case` and `thresholds` are passed, and it matters more than
    # it looks: without them `charts.analysis_parameters()` cannot reach the
    # spectrum array or the resolved thresholds, so SEVEN rows of the reference
    # report's Analysis Parameters table come back "not recorded" — the sample
    # rate, the spectral line count, the line spacing, Fmax, the spectrum type,
    # the match tolerance and the amplitude floor. The model then transcribes
    # that table faithfully into its own narrative, which is how a shipped
    # sample came to say "Not recorded" for values the deterministic report on
    # the same reading prints correctly.
    #
    # `profile` is the one input still not threaded here: `run_agent_analysis`
    # is handed already-resolved thresholds and cannot recover the NAME from
    # them, so callers that know it pass it. Without it the "Threshold profile"
    # row reads "—", which is what the webapp's deterministic reports already
    # show, so the two stay consistent. See SESSION_TFIX.md.
    deterministic_md = render_markdown(
        result, case.machine, case=case, thresholds=thresholds, profile=profile,
        include_causes=False,
    )
    # Session REPORT-4 (item 2): which axes actually reported. It gates both the
    # mask on the ground-truth JSON and the check on the prose that comes back.
    measured_axes = list(peak_set.measured_axes) if (
        peak_set is not None and peak_set.measured_axes) else None
    user_prompt = _build_user_prompt(result, case.machine, deterministic_md, measured_axes)

    draft_text, mismatches = _draft_and_check(
        client, agent_cfg, tool_ctx, user_prompt, trace_path, "draft", case.machine.name,
        measured_axes=measured_axes,
    )

    if mismatches:
        retry_prompt = _build_retry_prompt(user_prompt, draft_text, mismatches)
        draft_text, mismatches = _draft_and_check(
            client, agent_cfg, tool_ctx, retry_prompt, trace_path, "retry", case.machine.name,
            measured_axes=measured_axes,
        )
        if mismatches:
            _append_trace(trace_path, {"phase": "hard_fail", "mismatches": mismatches})
            raise AgentAnalysisError(
                "Drafted report failed the consistency check after one retry: " + "; ".join(mismatches)
            )

    narrative = strip_echo_block(draft_text)
    # Session H: the model drafts prose only. The evidence layer (status badge,
    # spectrum/trend figures, analysis parameters, signature block) is rendered
    # deterministically from this same AnalysisResult and spliced in here, so a
    # drafted report carries exactly the evidence the --no-llm report does.
    written = write_drafted_report(
        narrative, result, out_dir, pdf=pdf,
        machine=case.machine, case=case, thresholds=thresholds, profile=profile,
    )
    if report_out is not None:
        report_out.update(written)
    return result


# ─────────────────────────────────────────────────────────────────────────
# Drafting call + tool-use loop
# ─────────────────────────────────────────────────────────────────────────


def _draft_and_check(
    client: anthropic.Anthropic,
    agent_cfg: dict[str, Any],
    tool_ctx: ToolContext,
    user_content: str,
    trace_path: Path,
    phase: str,
    machine_name: str,
    *,
    measured_axes: Sequence[str] | None = None,
) -> tuple[str, list[str]]:
    text = _run_tool_loop(client, agent_cfg, tool_ctx, user_content, trace_path, phase)
    mismatches: list[str] = []
    title_mismatch = check_title_line(text, machine_name)
    if title_mismatch is not None:
        mismatches.append(title_mismatch)
    echo = parse_echo_block(text)
    mismatches.extend(check_echo_against_result(echo, tool_ctx.result))
    # Prose-level checks (Session A zone language; Session D fault terms + confidence
    # binding). The echo block can be honest while the published narrative is not --
    # these read what the analyst actually receives.
    mismatches.extend(check_zone_language(text, tool_ctx.result))
    mismatches.extend(check_fault_term_language(text, tool_ctx.result))
    mismatches.extend(check_confidence_binding(text, tool_ctx.result))
    # Session H: the numbers themselves. A decimal-place slip in the executive
    # summary passes every check above -- the echo block stays honest and the
    # fault name and confidence are right, while the headline number is 10x off.
    mismatches.extend(check_numeric_quotes(text, tool_ctx.result))
    # Session R1: the damage stage. The block itself is rendered deterministically
    # and spliced in, but the model restates it in prose -- and a fabricated or
    # upgraded stage is an unearned severity claim. "route" is the agent path's
    # profile (the CLI default; the webapp names it in webapp.json).
    mismatches.extend(check_stage_language(text, tool_ctx.result, staging_profile("route")))
    # Session R2-BUILD: the cause layer. The section is rendered from
    # knowledge/causes.yaml and spliced in, but a drafting model has an
    # unlimited unsourced supply of plausible bearing causes -- this is what
    # stops one reaching an analyst's report beside cited ones.
    mismatches.extend(check_cause_language(text, tool_ctx.result))
    # Session TFIX: the baseline-training decision. The model is handed
    # `quality_gate.train_baseline` / `train_reasons` in its ground-truth JSON
    # and relayed them accurately into a shipped sample -- but no report
    # publishes them, and on a reading with no z-score layer there is no
    # baseline for them to be about. This is the check that refuses that draft
    # rather than hoping the next roll of the dice omits it.
    mismatches.extend(check_baseline_claims(text, tool_ctx.result))
    # Session REPORT-4 (item 2b): a channel that was never measured is not
    # evidence. The mask in `_build_user_prompt` removes the value that invited
    # this; this refuses the sentence however the model arrived at it, because a
    # drafting model needs no 0.0 in its input to write "the axial axis showed
    # no elevated content" — it needs only to know the machine has three axes.
    mismatches.extend(check_measured_channels(text, tool_ctx.result, measured_axes))
    return text, mismatches


def _run_tool_loop(
    client: anthropic.Anthropic,
    agent_cfg: dict[str, Any],
    tool_ctx: ToolContext,
    user_content: str,
    trace_path: Path,
    phase: str,
) -> str:
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
    tool_budget = agent_cfg["max_drilldown_tool_calls"]
    tool_calls_used = 0

    for iteration in range(agent_cfg["max_tool_iterations"]):
        t0 = time.monotonic()
        response = client.messages.create(
            model=agent_cfg["model"],
            max_tokens=agent_cfg["max_tokens"],
            temperature=agent_cfg["temperature"],
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        _append_trace(
            trace_path,
            {
                "phase": phase,
                "iteration": iteration,
                "model": agent_cfg["model"],
                "stop_reason": response.stop_reason,
                "usage": _usage_dict(response.usage),
                "latency_ms": latency_ms,
            },
        )

        if response.stop_reason != "tool_use":
            return "".join(block.text for block in response.content if block.type == "text")

        messages.append({"role": "assistant", "content": response.content})
        tool_results: list[dict[str, Any]] = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if tool_calls_used >= tool_budget:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": (
                            f"Drill-down tool budget exhausted (max {tool_budget} calls). "
                            "Finish the report now without further tool calls."
                        ),
                        "is_error": True,
                    }
                )
                continue
            tool_calls_used += 1
            output = execute_tool(block.name, block.input, ctx=tool_ctx)
            _append_trace(trace_path, {"phase": phase, "tool": block.name, "input": block.input, "output": output})
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": tool_results})

    raise AgentAnalysisError(
        f"Agent tool loop exceeded max_tool_iterations ({agent_cfg['max_tool_iterations']}) without finishing."
    )


# ─────────────────────────────────────────────────────────────────────────
# Prompt construction
# ─────────────────────────────────────────────────────────────────────────


#: Session REPORT-4, item 2(a). What an unmeasured axis's derived values are
#: shown to the drafting model as, in place of the number pdm_core computed.
NOT_MEASURED = "not measured"


def mask_unmeasured_axes(payload: dict[str, Any], measured_axes: Sequence[str] | None) -> dict[str, Any]:
    """Replace every value DERIVED FROM an axis that was never measured with
    "not measured", in the ground-truth JSON the drafting model is handed.

    The value this exists for is `rca.axial_radial_ratio`.
    `pdm_core/bearing_rca.py:366` computes it as `v.get(axial, 0.0) /
    v_radial_max`, and `v` carries no entry for an axis that was never
    collected — so on a single-radial-channel job the ratio is **0.0**, and 0.0
    is exactly what a real, measured, silent axial channel would read. The model
    is told to treat this JSON as ground truth and is given no way to tell the
    two apart, so it reported an axis nobody measured as corroborating evidence
    (SESSION_REPORT4.md §5 item 2, and the shipped report that prompted it).

    Masked HERE, at the agent boundary, and not at the mint: `pdm_core` is
    latched for this session, and the 0.0 is also load-bearing INSIDE the
    detectors — `detect_imbalance` reads `1/ratio` as radial dominance, so
    changing the computation changes diagnoses. What is wrong is not the number;
    it is publishing it to something that will narrate it. The deterministic
    report never printed it and is unaffected, and `analysis.json` keeps
    pdm_core's own value.

    None (unknown) masks nothing — the same fallback every other consumer of
    `PeakSet.measured_axes` uses, so a hand-built PeakSet stays byte-identical.
    """
    if not measured_axes:
        return payload
    rca = payload.get("rca")
    if not isinstance(rca, dict):
        return payload
    axial = rca.get("axial_axis")
    if axial is None or axial in measured_axes:
        return payload
    if rca.get("axial_radial_ratio") is not None:
        rca["axial_radial_ratio"] = NOT_MEASURED
    # The same two fields exist per FaultMatch. They are None on every path in
    # the tree today, so this loop is presently a no-op — it is here because the
    # field that IS populated and the fields that are not are filled from the
    # same context, and a session that starts populating them should not have to
    # rediscover this.
    for match in rca.get("primary_findings") or ():
        if not isinstance(match, dict):
            continue
        for field in ("axial_velocity_mms", "axial_radial_ratio"):
            if match.get(field) is not None:
                match[field] = NOT_MEASURED
    return payload


def _build_user_prompt(result: AnalysisResult, machine: Any, deterministic_md: str,
                       measured_axes: Sequence[str] | None = None) -> str:
    title_line = TITLE_TEMPLATE.format(machine_name=machine.name)
    ground_truth = mask_unmeasured_axes(
        json.loads(result.model_dump_json()), measured_axes
    )
    coverage = ""
    if measured_axes:
        missing = [a for a in ("x", "y", "z") if a not in measured_axes]
        if missing:
            coverage = (
                "\n## Channel coverage -- READ THIS BEFORE WRITING ABOUT ANY AXIS\n"
                f"Axes MEASURED on this reading: {', '.join(measured_axes)}.\n"
                f"Axes NOT measured: {', '.join(missing)}.\n"
                "An axis that was not measured is not a quiet axis. Nothing was collected "
                "there, so it did not 'show no elevated content', it has no amplitude, and "
                "any ratio involving it is an artefact rather than a measurement -- which is "
                "why such values read \"not measured\" in the JSON above. Never offer an "
                "unmeasured axis as evidence, for or against anything. You may state the "
                "coverage limit itself; the Limitations section is where that belongs.\n"
            )
    return (
        f"Draft a vibration analysis survey report for {machine.name}.\n\n"
        "## Output format -- read this before drafting anything\n"
        f"Your entire response must begin with exactly this line, character for character, with "
        f"absolutely nothing before it (no preamble, no tool-use narration, no meta-commentary):\n\n"
        f"{title_line}\n\n"
        "## AnalysisResult (ground truth JSON -- every number, fault name, confidence level, "
        "and recommendation in your report must come from here; never invent, upgrade, or omit)\n"
        "```json\n" + json.dumps(ground_truth, indent=2) + "\n```\n"
        + coverage +
        "\n## Machine context\n"
        "```json\n" + machine.model_dump_json(indent=2) + "\n```\n\n"
        "## Reference structure (deterministic baseline report -- rewrite this into polished "
        "analyst prose covering the same sections and facts; you may reorganize and rephrase, "
        "never add or drop a fact)\n\n" + deterministic_md
    )


def _build_retry_prompt(original_prompt: str, draft_text: str, mismatches: list[str]) -> str:
    diff = "\n".join(f"- {m}" for m in mismatches)
    return (
        original_prompt
        + "\n\n## Your previous draft (for reference only -- it had errors, do not repeat them)\n\n"
        + draft_text
        + "\n\n## Consistency check failed\n\nYour draft did not match the AnalysisResult. The "
        "checks read BOTH your structured echo block AND the published narrative prose, so a "
        "correct echo does not excuse a narrative that says something else. Failures:\n"
        + diff
        + "\n\nRedraft the full report from scratch, correcting every point above. Every fault "
        "named as a diagnosis, every confidence level, every severity statement, and every "
        "recommended measurement -- in your PROSE as well as in the echo block -- must trace "
        "exactly to the AnalysisResult JSON above. If a point above concerns forbidden severity "
        "wording, use one of the pre-cleared sentences from your instructions verbatim rather "
        "than rephrasing around it. End again with a corrected echo block."
    )


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _build_peak_set(case: Case, thresholds: dict[str, Any]):
    """Re-derive the PeakSet the pipeline already used for RCA, purely for
    tool-context exposure (get_raw_peak_detail). Same adapter call
    pipeline.py itself makes on the same inputs -- deterministic, so this
    cannot diverge from the numbers already committed to the AnalysisResult;
    it is not a re-run of RCA or any detector."""
    sensor_data = case.sensor_data
    if sensor_data is None:
        return None
    if case.spectra:
        velocities = {
            "x": sensor_data.x_velocity_mm_sec or 0.0,
            "y": sensor_data.y_velocity_mm_sec or 0.0,
            "z": sensor_data.z_velocity_mm_sec or 0.0,
        }
        rca_cfg = thresholds["rca"]
        return peaks_from_spectrum(
            case.spectra,
            sensor_data.rpm or 0.0,
            velocities,
            max_peaks_per_axis=rca_cfg.get("spectrum_max_peaks_per_axis", 3),
            prominence=rca_cfg.get("spectrum_peak_prominence"),
            # Session PDMFIX: mirror pipeline.py exactly. This PeakSet is only
            # tool-context exposure, but it must not disagree with the one RCA
            # actually read about which axes were measured.
            measured_axes=measured_axes_from_sensor_data(sensor_data),
        )
    return peaks_from_ncd(sensor_data)


def _usage_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    return dict(usage)


def _append_trace(trace_path: Path, entry: dict[str, Any]) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
    with trace_path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")
