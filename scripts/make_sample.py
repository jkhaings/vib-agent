"""Regenerate the public sample report — `GET /sample-report.pdf`.

    set -a; . ./.env.api; set +a          # or export ANTHROPIC_API_KEY yourself
    ./.venv/bin/python -m scripts.make_sample

Session V2-WIRE. The sample is a **drafted** report, so this makes one real
drafting call; everything else on the path is deterministic. It writes into
`outputs/demo_package/bpfo_synthetic/`, which is where `webapp/app.py` serves
the sample from.

THE FIXTURE IS SYNTHETIC, AND THAT IS THE POINT. `make_case("bpfo", seed=1)` is
generated from a seed, so the sample the public sees is not, and can never
accidentally become, a reading anyone actually took. The CWRU corpus this sample
used to be drawn from is real laboratory data and is available locally
(`data/cwru/`), but a public shop-window document is exactly the wrong place to
start blurring that line.

The narrative differs run to run — it is model-written. The numbers do not: they
come from the same `pipeline.run_analysis` the `--no-llm` path uses, and the
drafted report is refused if any of them disagrees.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from vib_agent.agent.loop import run_agent_analysis  # noqa: E402
from vib_agent.config import load_config, load_thresholds  # noqa: E402
from vib_agent.synth.generator import make_case  # noqa: E402

#: `route` is named explicitly. A bare `load_thresholds()` resolves
#: `active_profile`, which is a default rather than a decision, and the sample
#: must be analysed under the same profile every upload is.
PROFILE = "route"
OUT = _REPO / "outputs" / "demo_package" / "bpfo_synthetic"


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set. The sample is a DRAFTED report, so this "
            "needs one real drafting call.\n"
            "  set -a; . ./.env.api; set +a\n"
            "  ./.venv/bin/python -m scripts.make_sample",
            file=sys.stderr,
        )
        return 2

    iso_table = load_config("iso_zones")["zones"]
    thresholds = load_thresholds(PROFILE)
    rules = load_config("next_measurements")
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)

    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    result = run_agent_analysis(
        case, iso_table=iso_table, thresholds=thresholds, rules=rules,
        out_dir=OUT, pdf=True, profile=PROFILE,
    )

    pdf = OUT / "report.pdf"
    print(f"machine   : {case.machine.name}")
    print(f"zone      : {result.iso.iso_zone if result.iso else '—'}")
    print(f"gate      : {result.quality_gate.overall}")
    print(f"findings  : {[f.fault for f in result.findings]}")
    print(f"report.pdf: {pdf.stat().st_size:,} bytes" if pdf.exists() else "report.pdf: NOT WRITTEN")
    if not pdf.exists():
        print("No PDF engine was available — the sample was NOT refreshed.", file=sys.stderr)
        return 1
    print(f"\nWrote {OUT.relative_to(_REPO)}/report.pdf")
    # Session SROUTE: this used to print "Served by webapp/app.py at /sample-report.pdf from
    # ...", asserting a serving relationship this script never checks. It was wrong for a day
    # -- the route still pointed at the old CWRU demo -- and the confident wording is exactly
    # what hid it. State the write path; name the check that actually proves the rest.
    print(
        "This script does NOT verify what the webapp serves. That is pinned by\n"
        "  tests/test_webapp_e2e.py::test_sample_report_serves_bpfo_synthetic_bytes\n"
        "-- run pytest to confirm /sample-report.pdf serves these exact bytes."
    )
    print("OPEN IT before committing: the standing rule is that `outcome=done` is not a grade.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
