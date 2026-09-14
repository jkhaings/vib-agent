"""Typer CLI: vib demo / analyze / eval."""

from __future__ import annotations

import os
from pathlib import Path

import typer

from vib_agent.config import load_config, load_thresholds
from vib_agent.models import AnalysisResult, Case
from vib_agent.pipeline import run_analysis
from vib_agent.report.generate import render_report

app = typer.Typer(
    name="vib",
    help="Vibration analysis agent: data file in, drafted condition report out.",
    no_args_is_help=True,
)

_OUTPUTS_DIR = Path(__file__).resolve().parents[2] / "outputs"

# Session D: the CLI analyses third-party/route-collected case files, so it defaults to
# the "route" profile. "streaming" is the NCD sensor pipeline's frozen production profile
# and stays selectable explicitly (--profile streaming) for NCD-shaped data. Never rely on
# load_thresholds()'s active_profile default here -- that indirection is what silently
# pointed the product at streaming until RUN v5 measured it.
_DEFAULT_PROFILE = "route"
_PROFILE_OPT = typer.Option(
    _DEFAULT_PROFILE, "--profile",
    help="Threshold profile: 'route' for uploaded/third-party data (default), "
         "'streaming' for the NCD sensor pipeline.",
)


def _run_deterministic(case: Case, out_dir: Path, *, pdf: bool, profile: str = _DEFAULT_PROFILE) -> AnalysisResult:
    """Shared --no-llm path: run the pipeline and write the report bundle."""
    iso_table = load_config("iso_zones")["zones"]
    thresholds = load_thresholds(profile)
    rules = load_config("next_measurements")
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    written = render_report(
        result, case.machine, out_dir, pdf=pdf,
        case=case, thresholds=thresholds, profile=profile,
    )
    for label, path in written.items():
        typer.echo(f"  {label}: {path}")
    return result


def _run_agent(case: Case, out_dir: Path, *, pdf: bool, profile: str = _DEFAULT_PROFILE) -> AnalysisResult:
    """Agent path: one pipeline.run_analysis() call (ground truth) drafted
    into a narrative report by Claude, machine-checked against the same
    AnalysisResult before it's written. See agent/loop.py."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        typer.echo(
            "ANTHROPIC_API_KEY is not set -- the agent path needs it. "
            "Use --no-llm to run the deterministic pipeline instead."
        )
        raise typer.Exit(code=1)

    import anthropic

    from vib_agent.agent.loop import AgentAnalysisError, run_agent_analysis

    iso_table = load_config("iso_zones")["zones"]
    thresholds = load_thresholds(profile)
    rules = load_config("next_measurements")
    try:
        result = run_agent_analysis(
            case,
            iso_table=iso_table,
            thresholds=thresholds,
            rules=rules,
            out_dir=out_dir,
            pdf=pdf,
            client=anthropic.Anthropic(),
            profile=profile,
        )
    except AgentAnalysisError as exc:
        typer.echo(f"Agent drafting failed the consistency check: {exc}")
        raise typer.Exit(code=1) from exc

    for label, filename in (
        ("markdown", "report.md"),
        ("analysis_json", "analysis.json"),
        ("pdf", "report.pdf"),
    ):
        path = out_dir / filename
        if path.exists():
            typer.echo(f"  {label}: {path}")
    return result


def _echo_verdict(result: AnalysisResult) -> None:
    gate = result.quality_gate.overall
    if gate == "fail":
        typer.echo("Verdict: INSUFFICIENT DATA — see recommended follow-up measurements.")
        return
    # S12FIX (HANDOFF-08-27 §5.1): the fault screen crashed, so there is no
    # verdict to echo. Printing "findings: none" here would be the clean bill in
    # its shortest form -- and a script reading only the exit code would take a
    # crashed analysis for a healthy machine, which is why this exits non-zero.
    if result.rca is not None and result.rca.status == "error":
        typer.echo(
            "Verdict: ANALYSIS FAILED — the fault screen did not complete, so no diagnosis "
            f"was made ({result.rca.reason}). The data passed its quality checks; this is a "
            "failure of the analysis, not of the measurement. Nothing here says the machine "
            "is healthy."
        )
        raise typer.Exit(code=1)
    faults = [f.fault for f in result.findings]
    findings_str = ", ".join(faults) if faults else "none"
    if result.iso is not None and result.iso.iso_zone == "not_assessable":
        typer.echo(f"Verdict: ISO severity unrated (no velocity data); findings: {findings_str}.")
        return
    zone = result.iso.iso_zone if result.iso else "?"
    typer.echo(f"Verdict: ISO Zone {zone}; findings: {findings_str}.")


@app.command()
def demo(
    no_llm: bool = typer.Option(False, "--no-llm", help="Deterministic pipeline, zero API calls."),
    pdf: bool = typer.Option(False, "--pdf", help="Also render PDF (requires weasyprint)."),
    profile: str = _PROFILE_OPT,
) -> None:
    """Run the seeded BPFO case through the full agent path → outputs/demo/."""
    # Imported lazily so the demo's synthetic data machinery isn't a hard
    # dependency of the CLI's import path.
    from vib_agent.synth.generator import make_case

    iso_table = load_config("iso_zones")["zones"]
    thresholds = load_thresholds(profile)
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)

    out_dir = _OUTPUTS_DIR / "demo"
    if no_llm:
        typer.echo(f"Running seeded BPFO case (deterministic, no LLM, profile={profile})…")
        result = _run_deterministic(case, out_dir, pdf=pdf, profile=profile)
    else:
        typer.echo(f"Running seeded BPFO case (agent path, profile={profile})…")
        result = _run_agent(case, out_dir, pdf=pdf, profile=profile)
    _echo_verdict(result)


@app.command()
def analyze(
    case_file: Path = typer.Argument(..., help="Case JSON file to analyze."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Deterministic pipeline, zero API calls."),
    pdf: bool = typer.Option(False, "--pdf", help="Also render PDF (requires weasyprint)."),
    profile: str = _PROFILE_OPT,
) -> None:
    """Analyze a case file and write a drafted report to outputs/<case>/."""
    if not case_file.exists():
        typer.echo(f"Case file not found: {case_file}")
        raise typer.Exit(code=1)

    case = Case.model_validate_json(case_file.read_text())
    out_dir = _OUTPUTS_DIR / case_file.stem
    if no_llm:
        typer.echo(f"Analyzing {case_file} (deterministic, no LLM, profile={profile})…")
        result = _run_deterministic(case, out_dir, pdf=pdf, profile=profile)
    else:
        typer.echo(f"Analyzing {case_file} (agent path, profile={profile})…")
        result = _run_agent(case, out_dir, pdf=pdf, profile=profile)
    _echo_verdict(result)


@app.command()
def eval(
    suite: str = typer.Option(
        "", "--suite", help="Which eval suite to run, e.g. 'cwru' or 'mfpt'. Omit for the full Phase 5 scorecard."
    ),
) -> None:
    """Run all eval cases and print the scorecard. Exits non-zero below thresholds."""
    if suite in ("cwru", "mfpt"):
        import importlib.util

        runner_path = Path(__file__).resolve().parents[2] / "eval" / "runner.py"
        spec = importlib.util.spec_from_file_location("eval_runner", runner_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        entry = module.run_cwru_suite if suite == "cwru" else module.run_mfpt_suite
        raise typer.Exit(code=0 if entry() else 1)

    if suite:
        typer.echo(f"Unknown suite: {suite!r} (known: 'cwru', 'mfpt').")
        raise typer.Exit(code=1)

    typer.echo("Not implemented yet — Phase 5 (full eval harness) required. Try --suite cwru.")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
