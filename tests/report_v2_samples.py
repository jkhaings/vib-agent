"""Regenerate the Session H operator-review samples, KEYLESSLY.

    python -m tests.report_v2_samples

Writes deterministic reports WITH evidence figures into
`outputs/report_v2_samples/`, plus an INDEX.md thumbnail sheet. Zero API calls
(`ANTHROPIC_API_KEY` is removed before anything is imported), zero randomness —
re-running produces byte-identical markdown and PNGs.

It NEVER touches the tracked `outputs/demo_package/cwru_or021_6_0/report.pdf`:
swapping the shipped sample report is an operator decision, post-merge.

Lives here rather than in `scripts/` because Session H does not own `scripts/`.
Not named `test_*`, so pytest does not collect it.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

os.environ.pop("ANTHROPIC_API_KEY", None)  # keyless: no drafting call is possible

from vib_agent.adapters.uploads import parse_upload  # noqa: E402
from vib_agent.adapters.uploads.common import UploadForm  # noqa: E402
from vib_agent.config import load_config, load_thresholds  # noqa: E402
from vib_agent.models import Case  # noqa: E402
from vib_agent.pipeline import run_analysis  # noqa: E402
from vib_agent.report import charts as charts_mod  # noqa: E402
from vib_agent.report.generate import render_report  # noqa: E402
from vib_agent.synth.generator import make_case, make_history  # noqa: E402
from vib_agent.webapp import assembly as A  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
OUT_DIR = _REPO / "outputs" / "report_v2_samples"
_KIT = Path(__file__).parent / "fixtures" / "multiaxis_kit"

_PROFILE = "route"
_KIT_RPM = 1780.0


def _config() -> dict[str, Any]:
    return {
        "iso_table": load_config("iso_zones")["zones"],
        "thresholds": load_thresholds(_PROFILE),
        "rules": load_config("next_measurements"),
        "bearings": load_config("bearings"),
    }


# ── the three sample cases ───────────────────────────────────────────────


def demo_bearing_case(cfg: dict[str, Any]) -> Case:
    """The `vib demo` case: seeded BPFO on a 6206 — the same bearing-fault shape
    the CWRU benchmark exercises. Stands in for the CWRU demo case, whose .mat
    corpus is gitignored and absent from a fresh checkout."""
    return make_case("bpfo", iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], seed=1)


def trend_case(cfg: dict[str, Any]) -> Case:
    """Same reading with a 30-day rising history, so the trend figure renders."""
    base = demo_bearing_case(cfg)
    return base.model_copy(
        update={"history": make_history(30, 1.2, 5.2, noise_pct=0.12, cadence="daily", seed=7)}
    )


def multiaxis_trio(cfg: dict[str, Any]) -> Case:
    """Multi-axis kit Case A2 — three real CSVs through three direction slots,
    merged by the same assembly layer the webapp runs."""
    parsed = []
    for filename, direction in (
        ("caseA_H.csv", "radial_h"), ("caseA_V.csv", "radial_v"), ("caseA_A.csv", "axial"),
    ):
        form = UploadForm(machine_alias="Kit machine A", rpm=_KIT_RPM, iso_group="2",
                          iso_support="rigid", machine_type="motor", bearing_model=None)
        case, kind, note = parse_upload(_KIT / filename, form, bearings_cfg=cfg["bearings"])
        parsed.append(A.ParsedChannel(direction, False, case, kind, note))
    outcome = A.merge_channels(
        parsed, [], iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
        rules=cfg["rules"], speed_tolerance_pct=5.0,
    )
    return outcome.case


SAMPLES = (
    ("demo_bpfo_6206", demo_bearing_case),
    ("multiaxis_trio_caseA", multiaxis_trio),
    ("trend_30day_rising", trend_case),
)


# ── render + index ───────────────────────────────────────────────────────


def _emit(name: str, case: Case, cfg: dict[str, Any]) -> dict[str, Any]:
    out = OUT_DIR / name
    shutil.rmtree(out, ignore_errors=True)
    result = run_analysis(
        case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], rules=cfg["rules"]
    )
    render_report(result, case.machine, out, pdf=True, case=case,
                  thresholds=cfg["thresholds"], profile=_PROFILE)
    pdf = out / "report.pdf"
    figures = [p.name for p in charts_mod.chart_files(out)]
    print(f"{name}: {charts_mod.status_text(result)}")
    print(f"  figures: {figures}")
    print(f"  pdf    : {pdf.stat().st_size if pdf.exists() else 'not rendered (no PDF engine)'}")
    return {
        "name": name,
        "status": charts_mod.status_text(result),
        "figures": figures,
        "pdf_bytes": pdf.stat().st_size if pdf.exists() else 0,
    }


def _write_index(records: list[dict[str, Any]]) -> Path:
    lines = [
        "# Session H — report v2 samples",
        "",
        "Deterministic, keyless (zero API calls). Regenerate with "
        "`python -m tests.report_v2_samples`.",
        "",
    ]
    for record in records:
        lines += [
            f"## {record['name']}",
            "",
            f"- Status badge: `{record['status']}`",
            f"- Report: `{record['name']}/report.md`"
            + (f", `{record['name']}/report.pdf` ({record['pdf_bytes']:,} bytes)"
               if record["pdf_bytes"] else " (no PDF engine available)"),
            "",
        ]
        for figure in record["figures"]:
            lines += [f"![{figure}]({record['name']}/charts/{figure})", ""]
    path = OUT_DIR / "INDEX.md"
    path.write_text("\n".join(lines))
    return path


def main() -> None:
    cfg = _config()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not (_KIT / "caseA_H.csv").exists():
        raise SystemExit(f"multi-axis kit fixtures missing: {_KIT}")
    records = [_emit(name, builder(cfg), cfg) for name, builder in SAMPLES]
    print(f"\nwrote {_write_index(records)}")


if __name__ == "__main__":
    main()
