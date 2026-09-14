"""CLI tests: `analyze --no-llm` / `demo --no-llm` on a generated case write
the deterministic report bundle and exit 0. Without `--no-llm`, both route
through the Phase 4 agent path (agent/loop.py) -- tested here with a fake
Anthropic client so no real API calls happen; a missing ANTHROPIC_API_KEY is
reported clearly and exits non-zero rather than crashing.
"""

from __future__ import annotations

import anthropic

from typer.testing import CliRunner

from tests.fake_anthropic import FakeAnthropicClient, build_consistent_echo, draft_message
from vib_agent.agent.consistency import TITLE_TEMPLATE
from vib_agent.cli import app
from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.synth.generator import make_case

runner = CliRunner()

# The bpfo fault recipe's machine (see synth/generator.py::_comp_with_bearing).
_TITLE = TITLE_TEMPLATE.format(machine_name="Synthetic Compressor 01")
_NARRATIVE = f"{_TITLE}\n\nReport body.\n\nDRAFT -- prepared by automated analysis, pending analyst review."


def test_analyze_no_llm_writes_report_bundle(tmp_path, monkeypatch):
    iso_table = load_config("iso_zones")["zones"]
    thresholds = load_thresholds()
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    case_file = tmp_path / "bpfo_case.json"
    case_file.write_text(case.model_dump_json())

    outputs_dir = tmp_path / "outputs"
    monkeypatch.setattr("vib_agent.cli._OUTPUTS_DIR", outputs_dir)

    result = runner.invoke(app, ["analyze", str(case_file), "--no-llm"])
    assert result.exit_code == 0, result.output
    report = outputs_dir / "bpfo_case" / "report.md"
    analysis = outputs_dir / "bpfo_case" / "analysis.json"
    assert report.exists()
    assert analysis.exists()
    assert "outer" in report.read_text().lower()


def test_analyze_without_api_key_errors_clearly(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    iso_table = load_config("iso_zones")["zones"]
    thresholds = load_thresholds()
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    case_file = tmp_path / "case.json"
    case_file.write_text(case.model_dump_json())
    result = runner.invoke(app, ["analyze", str(case_file)])
    assert result.exit_code != 0
    assert "ANTHROPIC_API_KEY" in result.output


def test_analyze_missing_file_errors(tmp_path):
    result = runner.invoke(app, ["analyze", str(tmp_path / "missing.json"), "--no-llm"])
    assert result.exit_code != 0


def test_analyze_agent_path_with_mocked_client_writes_report(tmp_path, monkeypatch):
    iso_table = load_config("iso_zones")["zones"]
    thresholds = load_thresholds()
    rules = load_config("next_measurements")
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    expected = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    case_file = tmp_path / "bpfo_case.json"
    case_file.write_text(case.model_dump_json())

    outputs_dir = tmp_path / "outputs"
    monkeypatch.setattr("vib_agent.cli._OUTPUTS_DIR", outputs_dir)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-placeholder-not-a-real-key")
    fake_client = FakeAnthropicClient(
        responses=[draft_message(_NARRATIVE, build_consistent_echo(expected))]
    )
    monkeypatch.setattr(anthropic, "Anthropic", lambda: fake_client)

    result = runner.invoke(app, ["analyze", str(case_file)])
    assert result.exit_code == 0, result.output
    report_text = (outputs_dir / "bpfo_case" / "report.md").read_text()
    assert "DRAFT" in report_text
    assert "<<<ECHO_START>>>" not in report_text
    assert (outputs_dir / "bpfo_case" / "trace.jsonl").exists()


def test_demo_no_llm_writes_report_naming_outer_race(tmp_path, monkeypatch):
    outputs_dir = tmp_path / "outputs"
    monkeypatch.setattr("vib_agent.cli._OUTPUTS_DIR", outputs_dir)

    result = runner.invoke(app, ["demo", "--no-llm"])
    assert result.exit_code == 0, result.output
    report = outputs_dir / "demo" / "report.md"
    assert report.exists()
    assert "outer" in report.read_text().lower()
    assert "DRAFT" in report.read_text()


def test_demo_agent_path_with_mocked_client_writes_report(tmp_path, monkeypatch):
    iso_table = load_config("iso_zones")["zones"]
    thresholds = load_thresholds()
    rules = load_config("next_measurements")
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    expected = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)

    outputs_dir = tmp_path / "outputs"
    monkeypatch.setattr("vib_agent.cli._OUTPUTS_DIR", outputs_dir)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-placeholder-not-a-real-key")
    fake_client = FakeAnthropicClient(
        responses=[draft_message(_NARRATIVE, build_consistent_echo(expected))]
    )
    monkeypatch.setattr(anthropic, "Anthropic", lambda: fake_client)

    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0, result.output
    report_text = (outputs_dir / "demo" / "report.md").read_text()
    assert "DRAFT" in report_text
    assert "<<<ECHO_START>>>" not in report_text


def test_demo_without_api_key_errors_clearly(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    outputs_dir = tmp_path / "outputs"
    monkeypatch.setattr("vib_agent.cli._OUTPUTS_DIR", outputs_dir)
    result = runner.invoke(app, ["demo"])
    assert result.exit_code != 0
    assert "ANTHROPIC_API_KEY" in result.output


def test_help_still_works():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("demo", "analyze", "eval"):
        assert command in result.output
