"""Unit tests for the drill-down tool dispatch (agent/tools.py). These tools
are read-only elaboration over an already-computed AnalysisResult/PeakSet --
none of them re-run RCA, the quality gate, or any other pdm_core layer.
"""

from __future__ import annotations

import json

import pytest

from vib_agent.agent.loop import _build_peak_set
from vib_agent.agent.tools import ToolContext, execute_tool
from vib_agent.pipeline import run_analysis
from vib_agent.synth.generator import make_case, make_history


@pytest.fixture
def bpfo_ctx(iso_table, thresholds, rules) -> ToolContext:
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    peak_set = _build_peak_set(case, thresholds)
    return ToolContext(result=result, case=case, peak_set=peak_set)


class TestRecomputeBearingFrequencies:
    def test_returns_alternate_bearing_freqs(self, bpfo_ctx):
        output = execute_tool(
            "recompute_bearing_frequencies",
            {"n_balls": 8, "ball_dia_mm": 7.0, "pitch_dia_mm": 35.0, "contact_angle_deg": 0.0},
            ctx=bpfo_ctx,
        )
        data = json.loads(output)
        assert data["shaft_freq_hz"] == bpfo_ctx.result.rca.shaft_freq_hz
        assert {"BPFO", "BPFI", "BSF", "FTF"} <= data.keys()

    def test_invalid_geometry_reports_error_not_crash(self, bpfo_ctx):
        output = execute_tool(
            "recompute_bearing_frequencies",
            {"n_balls": -1, "ball_dia_mm": 7.0, "pitch_dia_mm": 35.0},
            ctx=bpfo_ctx,
        )
        assert "error" in json.loads(output)

    def test_no_rca_reports_error(self, iso_table, thresholds, rules):
        case = make_case("machine_off", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        ctx = ToolContext(result=result, case=case, peak_set=None)
        output = execute_tool(
            "recompute_bearing_frequencies",
            {"n_balls": 9, "ball_dia_mm": 7.94, "pitch_dia_mm": 39.04},
            ctx=ctx,
        )
        assert "error" in json.loads(output)


class TestGetRawPeakDetail:
    def test_returns_peaks(self, bpfo_ctx):
        data = json.loads(execute_tool("get_raw_peak_detail", {}, ctx=bpfo_ctx))
        assert data["peaks"]
        assert all({"axis", "freq", "rank"} <= p.keys() for p in data["peaks"])

    def test_axis_filter(self, bpfo_ctx):
        data = json.loads(execute_tool("get_raw_peak_detail", {"axis": "y"}, ctx=bpfo_ctx))
        assert data["peaks"]
        assert all(p["axis"] == "y" for p in data["peaks"])

    def test_no_peak_set_reports_error(self, iso_table, thresholds, rules):
        case = make_case("machine_off", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        ctx = ToolContext(result=result, case=case, peak_set=None)
        assert "error" in json.loads(execute_tool("get_raw_peak_detail", {}, ctx=ctx))


class TestGetHistoryStats:
    def test_no_history_reports_error(self, bpfo_ctx):
        assert "error" in json.loads(execute_tool("get_history_stats", {}, ctx=bpfo_ctx))

    def test_returns_stats_when_history_present(self, iso_table, thresholds, rules):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        history = make_history(10, 2.0, 3.0, seed=1)
        case = case.model_copy(update={"history": history})
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        ctx = ToolContext(result=result, case=case, peak_set=None)
        data = json.loads(execute_tool("get_history_stats", {}, ctx=ctx))
        assert data["n_points"] == 10
        assert data["min_value"] <= data["mean_value"] <= data["max_value"]


def test_unknown_tool_name_reports_error(bpfo_ctx):
    assert "error" in json.loads(execute_tool("nonexistent_tool", {}, ctx=bpfo_ctx))
