"""Phase 0 smoke tests: package imports, configs load, CLI responds."""

from typer.testing import CliRunner

import vib_agent
from vib_agent.cli import app
from vib_agent.config import load_config


def test_package_imports() -> None:
    assert vib_agent.__version__


def test_configs_load() -> None:
    zones = load_config("iso_zones")
    assert zones["zones"]["1_rigid"]["ab"] == 2.3

    bearings = load_config("bearings")
    assert "6205" in bearings["bearings"]

    thresholds = load_config("thresholds")
    assert thresholds["profiles"]["streaming"]["zscore"]["flag"] == 3.5

    agent = load_config("agent")
    assert agent["max_tool_iterations"] == 15


def test_cli_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("demo", "analyze", "eval"):
        assert command in result.output
