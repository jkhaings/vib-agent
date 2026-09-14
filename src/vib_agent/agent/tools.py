"""Drill-down tool schemas + dispatch for the Phase 4 drafting agent.

These tools are read-only elaboration over data pipeline.run_analysis()
already computed -- they exist so the model can double-check or expand on
evidence already in the AnalysisResult. None of them re-run RCA, the
quality gate, ISO classification, trend, or synthesis; the fixed analysis
procedure runs exactly once, in pipeline.py, before the agent is ever
called. See agent/loop.py for how ToolContext is built (the same
peaks_from_ncd/peaks_from_spectrum adapter call pipeline.py itself makes --
pure and deterministic, so re-deriving it for display can't diverge from
the numbers already committed to the AnalysisResult).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from vib_agent.knowledge import lookup_causes
from vib_agent.knowledge.loader import BEARING_FAULT_FAMILIES
from vib_agent.models import AnalysisResult, BearingSpec, Case, PeakSet
from vib_agent.pdm_core.bearing_rca import bearing_frequencies


@dataclass
class ToolContext:
    result: AnalysisResult
    case: Case
    peak_set: PeakSet | None


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "recompute_bearing_frequencies",
        "description": (
            "Compute BPFO/BPFI/BSF/FTF fault frequencies for an ALTERNATE bearing geometry, "
            "at the shaft speed already established by the AnalysisResult. Use this only to "
            "double-check whether a differential candidate's frequency match would look "
            "different under a different bearing model -- never to change the committed "
            "diagnosis, which is already final."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "n_balls": {"type": "integer", "description": "Number of rolling elements."},
                "ball_dia_mm": {"type": "number", "description": "Ball/roller diameter, mm."},
                "pitch_dia_mm": {"type": "number", "description": "Pitch diameter, mm."},
                "contact_angle_deg": {
                    "type": "number",
                    "description": "Contact angle in degrees (0 for deep-groove ball bearings).",
                },
            },
            "required": ["n_balls", "ball_dia_mm", "pitch_dia_mm"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_raw_peak_detail",
        "description": (
            "Fetch the raw peak list (axis, frequency, rank, amplitude if available) that the "
            "already-completed bearing RCA was computed from. Use this to cite specific peak "
            "detail in your narrative beyond what's summarized in the AnalysisResult's finding "
            "evidence strings. Read-only -- does not re-run RCA."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "axis": {
                    "type": "string",
                    "enum": ["x", "y", "z"],
                    "description": "Restrict to one axis. Omit for all axes.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_history_stats",
        "description": (
            "Fetch summary statistics (point count, date range, min/max/mean value) over the "
            "case's raw history points, if any were supplied. Use this to add color to the "
            "Trend Summary section beyond the already-computed TrendResult. Read-only -- does "
            "not recompute the trend regression."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "lookup_causes",
        "description": (
            "Look up the operator-approved UNDERLYING CAUSES that are known to produce a "
            "committed bearing fault, from the reference library (knowledge/causes.yaml). "
            "These are HYPOTHESES FOR THE ANALYST, not findings: a vibration reading "
            "identifies a fault (BPFO/BPFI/BSF/FTF), never the cause that produced it, and "
            "most of the evidence that separates these causes is oil, thermal or dismounted "
            "visual inspection that this analysis did not perform. Use this ONLY to describe "
            "candidate causes as hypotheses to be confirmed by the analyst, with the evidence "
            "that would confirm or exclude each. You may not name a cause this tool did not "
            "return, may not present any of them as established, confirmed or diagnosed, and "
            "may not call it when no bearing fault was committed. Read-only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fault_family": {
                    "type": "string",
                    "enum": sorted(BEARING_FAULT_FAMILIES),
                    "description": "The committed bearing fault id, exactly as it appears in the "
                    "AnalysisResult's findings.",
                },
                "stage": {
                    "type": "string",
                    "description": "Damage stage to filter on, from the Damage Stage Estimate "
                    "('stage_3_early', 'stage_3_advanced', 'stage_4_suspected', "
                    "'not_determinable'). Omit, or pass 'any', for every stage.",
                },
            },
            "required": ["fault_family"],
            "additionalProperties": False,
        },
    },
]


def execute_tool(name: str, tool_input: dict[str, Any], *, ctx: ToolContext) -> str:
    if name == "recompute_bearing_frequencies":
        return _recompute_bearing_frequencies(tool_input, ctx)
    if name == "get_raw_peak_detail":
        return _get_raw_peak_detail(tool_input, ctx)
    if name == "get_history_stats":
        return _get_history_stats(ctx)
    if name == "lookup_causes":
        return _lookup_causes(tool_input, ctx)
    return json.dumps({"error": f"unknown tool {name!r}"})


def _recompute_bearing_frequencies(tool_input: dict[str, Any], ctx: ToolContext) -> str:
    if ctx.result.rca is None or not ctx.result.rca.shaft_freq_hz:
        return json.dumps({"error": "no shaft frequency available -- RCA did not run for this case"})
    try:
        bearing = BearingSpec(
            n_balls=tool_input["n_balls"],
            ball_dia_mm=tool_input["ball_dia_mm"],
            pitch_dia_mm=tool_input["pitch_dia_mm"],
            contact_angle_deg=tool_input.get("contact_angle_deg", 0.0),
        )
    except Exception as exc:  # invalid geometry from the model -- report, don't crash the loop
        return json.dumps({"error": f"invalid bearing geometry: {exc}"})
    freqs = bearing_frequencies(bearing, ctx.result.rca.shaft_freq_hz)
    return json.dumps({"shaft_freq_hz": ctx.result.rca.shaft_freq_hz, **freqs.model_dump()})


def _get_raw_peak_detail(tool_input: dict[str, Any], ctx: ToolContext) -> str:
    if ctx.peak_set is None:
        return json.dumps({"error": "no peak data available -- RCA did not run for this case"})
    axis = tool_input.get("axis")
    peaks = [p for p in ctx.peak_set.peaks if axis is None or p.axis == axis]
    return json.dumps({"peaks": [p.model_dump() for p in peaks]})


def _committed_bearing_faults(ctx: ToolContext) -> set[str]:
    return {
        f.fault for f in ctx.result.findings if f.fault in BEARING_FAULT_FAMILIES
    }


def _lookup_causes(tool_input: dict[str, Any], ctx: ToolContext) -> str:
    """Read-only cause lookup, GATED ON THE COMMITTED DIAGNOSIS.

    The gate is the point. Left ungated, this tool would let the model ask for
    the causes of a fault the analysis never committed and then write about
    them -- the RUN v5 fabrication shape, one layer further out. So a family the
    AnalysisResult did not commit returns an error naming what WAS committed,
    and a clean reading returns an empty list rather than a menu.

    The payload deliberately excludes each citation's `note` (a reviewer's
    description of the source, not analyst-facing text) and each entry's
    `review_question` / `disagreement` (open questions for the operator, not
    report material).
    """
    committed = _committed_bearing_faults(ctx)
    family = tool_input.get("fault_family")
    if not committed:
        return json.dumps(
            {
                "causes": [],
                "error": "no bearing fault was committed for this reading, so there is no "
                "fault to explain. Do not write a causes section, and do not name a cause.",
            }
        )
    if family not in committed:
        return json.dumps(
            {
                "causes": [],
                "error": f"{family!r} was not committed by this analysis. Committed bearing "
                f"finding(s): {sorted(committed)}. Look up causes only for a committed fault.",
            }
        )
    stage = tool_input.get("stage") or "any"
    causes = lookup_causes(family, stage)
    return json.dumps(
        {
            "fault_family": family,
            "stage": stage,
            "status": "hypotheses_for_analyst_confirmation",
            "caveat": (
                "None of these is confirmed by this measurement. A vibration reading "
                "identifies the fault, not its cause; most discriminating evidence below is "
                "oil, thermal or dismounted visual inspection that was not performed."
            ),
            "causes": [
                {
                    "cause_id": c.cause_id,
                    "cause": c.cause,
                    "mechanism": c.mechanism,
                    "basis": c.basis,
                    "consider": c.consider,
                    "citations": [cit.rendered() for cit in c.citations],
                    "discriminating_evidence": [
                        {
                            "observation": o.observation,
                            "stream": o.stream,
                            "how_to_collect": o.how_to_collect,
                            "citations": [cit.rendered() for cit in o.citations],
                            "inferred": o.inferred,
                        }
                        for o in c.discriminating_evidence
                    ],
                }
                for c in causes
            ],
        }
    )


def _get_history_stats(ctx: ToolContext) -> str:
    history = ctx.case.history
    if not history:
        return json.dumps({"error": "no history points on this case"})
    values = [p.value for p in history]
    return json.dumps(
        {
            "n_points": len(history),
            "first_ts": history[0].ts.isoformat(),
            "last_ts": history[-1].ts.isoformat(),
            "min_value": min(values),
            "max_value": max(values),
            "mean_value": sum(values) / len(values),
        }
    )
