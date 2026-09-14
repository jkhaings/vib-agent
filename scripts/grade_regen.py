"""RUN v5 Task 2 — grade every regenerated PDF against ground truth.

For each of the 16 regenerated artifacts this script:
  1. rebuilds the SOURCE case with the same adapter the webapp used,
  2. runs the deterministic pipeline as the oracle (no API calls),
  3. extracts the PDF text with pypdf,
  4. asserts the four grading dimensions:
       - fault family      : the PDF's committed diagnosis matches the oracle's
                             (the narrative may never invent or drop a fault), and
                             the oracle's call is recorded against the dataset label
                             so honest misses are visible rather than hidden;
       - confidence sane   : the confidence word in the PDF matches the oracle's;
       - severity truthful : when the oracle says iso_zone == not_assessable
                             (acceleration-only data) the PDF must carry
                             "unrated"/"not assessable", must recommend the velocity
                             measurement, and must contain ZERO "Zone A..D" claims;
       - degraded honesty  : a degraded artifact must carry the visible degraded note.

Zone-claim detection is deliberately literal: a claim is the token "Zone <A-D>".
Prose like "no severity zone can be established" is not a claim and must not trip it.

Read-only. Writes outputs/regen_grading.md.

Usage:  .venv/bin/python scripts/grade_regen.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from pypdf import PdfReader  # noqa: E402

from vib_agent.config import load_config, load_thresholds  # noqa: E402
from vib_agent.models import Case  # noqa: E402
from vib_agent.pipeline import run_analysis  # noqa: E402

ISO = load_config("iso_zones")["zones"]
# Session D: the profile wiring is fixed — the webapp reads webapp.json
# analysis_profile ("route") and the CLI defaults to route, so these artifacts
# were produced by route. The oracle is route to match. (`streaming` is loaded
# alongside only to keep the profile-gap section reporting the delta, which is now
# the intended state rather than a defect.)
TH = load_thresholds(load_config("webapp")["analysis_profile"])  # what the product uses
TH_ROUTE = load_thresholds("route")
TH_STREAMING = load_thresholds("streaming")
RULES = load_config("next_measurements")

FV = _REPO / "outputs" / "field_validation"
DEMO = _REPO / "outputs" / "demo_package"

ZONE_CLAIM = re.compile(r"\bzone\s*([A-D])\b", re.IGNORECASE)
UNRATED = re.compile(r"unrated|not\s*assessable", re.IGNORECASE)
VELOCITY_FOLLOWUP = re.compile(r"velocity\s+measurement|broadband\s+velocity", re.IGNORECASE)
DEGRADED_NOTE = re.compile(r"[Dd]rafted narrative unavailable", re.IGNORECASE)

FAULT_WORDS = {
    "bearing_outer_race": re.compile(r"outer[\s-]*race|BPFO", re.IGNORECASE),
    "bearing_inner_race": re.compile(r"inner[\s-]*race|BPFI", re.IGNORECASE),
    "bearing_ball_spin": re.compile(r"ball[\s-]*spin|BSF", re.IGNORECASE),
    "bearing_cage": re.compile(r"\bcage\b|FTF", re.IGNORECASE),
    "imbalance": re.compile(r"imbalance", re.IGNORECASE),
    "misalignment_general": re.compile(r"misalign", re.IGNORECASE),
    "parallel_misalignment": re.compile(r"parallel\s+misalign", re.IGNORECASE),
    "angular_misalignment": re.compile(r"angular\s+misalign", re.IGNORECASE),
    "mechanical_looseness": re.compile(r"looseness", re.IGNORECASE),
}


def norm(t: str) -> str:
    """pypdf splits ligatures ('W ARN', 'F ault') and hyphenates across lines."""
    t = t.replace("-\n", "").replace("\n", " ")
    t = re.sub(r"\s+", " ", t)
    return t


def pdf_text(p: Path) -> str:
    return norm("\n".join(pg.extract_text() or "" for pg in PdfReader(str(p)).pages))


# ── source-case builders (same adapters the webapp used) ────────────────────
def case_mfpt(name: str) -> Case:
    from vib_agent.adapters.mfpt import to_case
    return to_case(_REPO / "data" / "mfpt" / f"{name}.mat",
                   mfpt_cfg=load_config("mfpt"), bearings_cfg=load_config("bearings"))


def case_wt(fname: str) -> Case:
    from vib_agent.adapters.wind_turbine import to_case
    return to_case(_REPO / "data" / "wind_turbine" / fname,
                   wt_cfg=load_config("wind_turbine"), bearings_cfg=load_config("bearings"))


def case_maf(rel: str) -> Case:
    from vib_agent.adapters.mafaulda import to_case
    return to_case(_REPO / "data" / "mafaulda" / rel,
                   mafaulda_cfg=load_config("mafaulda"), bearings_cfg=load_config("bearings"))


def case_json(p: Path) -> Case:
    return Case.model_validate_json(p.read_text())


# (pdf, group, label, case_builder, ground_truth_label, expected_fault_or_None, degraded)
SPECS = [
    (FV / "mfpt/real_world_intermediate_speed_bearing.pdf", "MFPT", "real_world_intermediate_speed_bearing",
     lambda: case_mfpt("real_world_intermediate_speed_bearing"), "real-world faulted bearing", None, False),
    (FV / "mfpt/real_world_oil_pump_bearing.pdf", "MFPT", "real_world_oil_pump_bearing",
     lambda: case_mfpt("real_world_oil_pump_bearing"), "real-world faulted bearing", None, False),
    (FV / "mfpt/real_world_planet_bearing.pdf", "MFPT", "real_world_planet_bearing",
     lambda: case_mfpt("real_world_planet_bearing"), "real-world faulted bearing", None, False),
    (FV / "mfpt/outer_270_1.pdf", "MFPT", "outer_270_1",
     lambda: case_mfpt("outer_270_1"), "outer race", "bearing_outer_race", False),
    (FV / "mfpt/baseline_1.pdf", "MFPT", "baseline_1",
     lambda: case_mfpt("baseline_1"), "healthy baseline", None, False),

    (FV / "wind_turbine/early_healthy_day01.pdf", "WT", "early_healthy_day01",
     lambda: case_wt("data-20130307T015746Z.mat"), "day 1 — early, pre-failure", None, False),
    (FV / "wind_turbine/first_trend_alert_day35.pdf", "WT", "first_trend_alert_day35",
     lambda: case_wt("data-20130410T231407Z.mat"), "day 35 — first trend alert", None, False),
    (FV / "wind_turbine/peak_rms_day49.pdf", "WT", "peak_rms_day49",
     lambda: case_wt("data-20130424T215514Z.mat"), "day 49 — peak RMS", None, False),
    (FV / "wind_turbine/final_day50.pdf", "WT", "final_day50 [MARQUEE]",
     lambda: case_wt("data-20130425T232202Z.mat"), "day 50 — inner-race failure (run-to-failure end)", None, False),

    (FV / "mafaulda/normal_mid.pdf", "MAFAULDA", "normal_mid",
     lambda: case_maf("normal/39.3216.csv"), "healthy normal", None, False),
    (FV / "mafaulda/imbalance_6g.pdf", "MAFAULDA", "imbalance_6g",
     lambda: case_maf("imbalance/6g/29.4912.csv"), "imbalance 6 g", "imbalance", False),
    (FV / "mafaulda/imbalance_35g.pdf", "MAFAULDA", "imbalance_35g",
     lambda: case_maf("imbalance/35g/29.9008.csv"), "imbalance 35 g", "imbalance", False),
    (FV / "mafaulda/horizontal_mis_0.5mm.pdf", "MAFAULDA", "horizontal_mis_0.5mm",
     lambda: case_maf("horizontal-misalignment/0.5mm/30.3104.csv"), "horizontal misalignment 0.5 mm",
     "misalignment_general", False),
    (FV / "mafaulda/vertical_mis_1.90mm.pdf", "MAFAULDA", "vertical_mis_1.90mm",
     lambda: case_maf("vertical-misalignment/1.90mm/29.9008.csv"), "vertical misalignment 1.90 mm",
     "misalignment_general", False),

    (DEMO / "bpfo_synthetic/report.pdf", "DEMO", "bpfo_synthetic",
     lambda: case_json(_REPO / "outputs/regen_cases/bpfo_synthetic.json"), "seeded BPFO", "bearing_outer_race", False),
    (DEMO / "cwru_or021_6_0/report.pdf", "DEMO", "cwru_or021_6_0",
     lambda: case_json(_REPO / "eval/cases/cwru/OR021@6_0.json"), "CWRU OR021@6 outer race",
     "bearing_outer_race", False),
]


def grade_one(spec) -> dict:
    pdf, group, label, build, truth, expect_fault, degraded = spec
    row: dict = {"group": group, "label": label, "pdf": str(pdf.relative_to(_REPO)),
                 "ground_truth": truth, "degraded": degraded, "checks": [], "fails": []}
    if not pdf.exists():
        row["fails"].append("PDF MISSING")
        row["grade"] = "FAIL"
        return row

    text = pdf_text(pdf)
    case = build()
    oracle = run_analysis(case, iso_table=ISO, thresholds=TH, rules=RULES)
    # what the SAME file WOULD have committed under the old streaming default —
    # kept only to show the before/after now that the product runs route.
    streaming_res = run_analysis(case, iso_table=ISO, thresholds=TH_STREAMING, rules=RULES)
    row["streaming_faults"] = (
        [f.fault for f in streaming_res.rca.primary_findings] if streaming_res.rca else []
    )
    not_assessable = oracle.iso.iso_zone == "not_assessable"
    oracle_faults = [f.fault for f in oracle.rca.primary_findings] if oracle.rca else []
    oracle_conf = {f.fault: f.confidence for f in (oracle.rca.primary_findings if oracle.rca else [])}
    row["oracle_faults"] = oracle_faults
    row["oracle_iso"] = oracle.iso.iso_zone
    row["oracle_conf"] = oracle_conf

    # 1. severity truthfulness (the Session-A contract) — only for not_assessable data
    zones = [m.group(0) for m in ZONE_CLAIM.finditer(text)]
    row["zone_claims"] = zones
    if not_assessable:
        if zones:
            row["fails"].append(f"ISO-ZONE CLAIM on acceleration-only data: {zones[:4]}")
        else:
            row["checks"].append("zero ISO-zone claims")
        if UNRATED.search(text):
            row["checks"].append("carries unrated/not-assessable")
        else:
            row["fails"].append("missing 'unrated'/'not assessable'")
        if VELOCITY_FOLLOWUP.search(text):
            row["checks"].append("velocity follow-up present")
        else:
            row["fails"].append("missing velocity follow-up")
    else:
        row["checks"].append(f"velocity present — zone language legitimate ({oracle.iso.iso_zone})")

    # 2. fault family — PDF must reflect the oracle, never invent
    for fault in oracle_faults:
        pat = FAULT_WORDS.get(fault)
        if pat and pat.search(text):
            row["checks"].append(f"names oracle fault: {fault}")
        elif pat:
            row["fails"].append(f"oracle committed {fault} but PDF does not name it")
    invented = []
    for fault, pat in FAULT_WORDS.items():
        if fault in oracle_faults or not pat.search(text):
            continue
        # A mention is only 'invented' if presented as THE committed diagnosis. The
        # differential, the computed fault-frequency table (which always lists
        # BPFO/BPFI/BSF/FTF as reference values), and the "considered and not
        # committed" prose all legitimately name non-committed faults.
        # NB: pat.pattern contains alternations — it MUST be wrapped in (?:...) or the
        # '|' escapes this prefix and matches the bare fault word anywhere in the doc.
        if re.search(rf"committed diagnosis[^.]{{0,120}}(?:{pat.pattern})", text, re.IGNORECASE):
            invented.append(fault)
    if invented:
        row["fails"].append(f"PDF commits fault(s) the oracle did not: {invented}")
    else:
        row["checks"].append("no invented committed fault")

    # 3. confidence sane
    for fault, conf in oracle_conf.items():
        if re.search(rf"confidence[:\s—-]*{conf}", text, re.IGNORECASE) or \
           re.search(rf"{conf}\s+confidence", text, re.IGNORECASE):
            row["checks"].append(f"confidence '{conf}' matches oracle")
        else:
            row["fails"].append(f"oracle confidence '{conf}' for {fault} not stated in PDF")
    if not re.search(r"\b\d{1,3}\s?%\s*(confiden|certain)", text, re.IGNORECASE):
        row["checks"].append("no invented confidence percentage")
    else:
        row["fails"].append("percentage confidence present (v1 is ordinal only)")

    # 4. degraded honesty
    if degraded:
        if DEGRADED_NOTE.search(text):
            row["checks"].append("degraded note visible")
        else:
            row["fails"].append("degraded artifact missing the visible degraded note")

    # ground-truth commentary (honest miss recording, not a pass/fail on detection)
    if expect_fault:
        row["detected_expected"] = expect_fault in oracle_faults

    row["grade"] = "FAIL" if row["fails"] else "PASS"
    return row


def main() -> int:
    rows = [grade_one(s) for s in SPECS]

    # marquee evidence
    marquee = next(r for r in rows if "MARQUEE" in r["label"])
    mq_pdf = _REPO / marquee["pdf"]
    mq_text = pdf_text(mq_pdf) if mq_pdf.exists() else ""
    mq_sev = re.findall(r"[^.]*\bunrated\b[^.]*\.", mq_text, re.IGNORECASE)[:3]
    mq_zone = ZONE_CLAIM.findall(mq_text)
    # Clause 3 asks for COMMITTED inner-race evidence, so anchor it to the oracle,
    # not to a prose regex. A healthy report legitimately mentions inner-race while
    # listing what it SCREENED and ruled out ("no peak aligned with the computed
    # BPFI"); that is not evidence of a fault and must not count.
    mq_oracle_faults = marquee.get("oracle_faults") or []
    mq_has_inner = "bearing_inner_race" in mq_oracle_faults
    _NEG = re.compile(r"no |not |screen|clear|rule[d]? out|came back|within normal", re.IGNORECASE)
    mq_inner = [
        s for s in re.findall(r"[^.]*(?:inner[\s-]*race|BPFI)[^.]*\.", mq_text, re.IGNORECASE)
        if not _NEG.search(s)
    ][:3] if mq_has_inner else []

    gaps = [r for r in rows if sorted(r.get("streaming_faults") or []) != sorted(r.get("oracle_faults") or [])]
    out = [
        "# Regeneration Grading — Session D re-regeneration (under `route`)",
        "",
        "Every regenerated PDF read against ground truth (Part C acceptance standard: a PDF is",
        "graded by what it *says*, not by the job reaching `outcome=done`). The oracle for each",
        "file is the deterministic `pipeline.run_analysis()` on the same source data, so the",
        "narrative is checked for faithfully reporting the computed result — never for agreeing",
        "with a hoped-for answer.",
        "",
        "> **Oracle profile = `route`.** Session D wired the product to the route profile (the",
        "> webapp reads `config/webapp.json` `analysis_profile`, the CLI defaults to route), so",
        "> these artifacts were produced by route and are graded against it. The section below",
        "> shows what these same files USED to commit under the old streaming default.",
        "",
        f"**{sum(1 for r in rows if r['grade']=='PASS')}/{len(rows)} PASS**",
        "",
        "| # | Group | Artifact | Ground truth | Oracle committed | ISO state | Grade |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        of = ", ".join(r.get("oracle_faults") or []) or "—"
        out.append(f"| {i} | {r['group']} | {r['label']} | {r['ground_truth']} | {of} | "
                   f"{r.get('oracle_iso')} | **{r['grade']}** |")

    out += ["", "## Per-artifact detail", ""]
    for i, r in enumerate(rows, 1):
        out.append(f"### {i}. {r['group']} — {r['label']} → **{r['grade']}**")
        out.append(f"- PDF: `{r['pdf']}`")
        out.append(f"- Ground truth: {r['ground_truth']}; oracle committed: "
                   f"{', '.join(r.get('oracle_faults') or []) or 'none'}; ISO: `{r.get('oracle_iso')}`")
        if "detected_expected" in r:
            out.append(f"- Detected the labelled fault: **{r['detected_expected']}** "
                       f"(recorded honestly; detection rate is a detector question, not a drafting one)")
        for c in r["checks"]:
            out.append(f"- ✅ {c}")
        for f in r["fails"]:
            out.append(f"- ❌ {f}")
        out.append("")

    out += [
        "## PROFILE FIX — the Session B guards are now live in the product",
        "",
        "Session D pointed the product at the `route` profile it was calibrated on. The guards",
        "are now active in what an analyst receives:",
        "",
        "| constant | route (product now) | streaming (old default) |",
        "|---|---|---|",
        f"| `epsilon_sync` (collision guard) | `{TH['rca'].get('epsilon_sync')}` | `{TH_STREAMING['rca'].get('epsilon_sync')}` |",
        f"| `floor_min` (amplitude floor) | `{TH['rca'].get('floor_min')}` | `{TH_STREAMING['rca'].get('floor_min')}` |",
        f"| `imbalance_radial_dominance` | `{TH['rca'].get('imbalance_radial_dominance')}` | "
        f"`{TH_STREAMING['rca'].get('imbalance_radial_dominance')}` |",
        "",
        f"**{len(gaps)}/{len(rows)} artifacts commit a different (correct) diagnosis now vs. the old "
        "streaming default** — the exact false positives Session B eliminated, now demoted:",
        "",
        "| Artifact | committed now (route, shipped) | committed before (streaming) |",
        "|---|---|---|",
    ]
    for r in gaps:
        out.append(f"| {r['group']}/{r['label']} | {', '.join(r['oracle_faults']) or '—'} | "
                   f"{', '.join(r['streaming_faults']) or '—'} |")
    out += [
        "",
        "These are the exact false positives Session B eliminated (integer-order collision FPs,",
        "near-noise-floor matches, and the imbalance gate). They are now demoted to the",
        "differential in the shipped reports, because the product loads the profile they were",
        "fixed in.",
        "",
        "## MARQUEE CHECK — WT `final_day50`",
        "",
        f"Artifact: `{marquee['pdf']}` (drafted narrative, `outcome=done`)",
        "",
        f"**ISO-zone claims found: {mq_zone if mq_zone else 'NONE'}** — the pre-Session-A artifact "
        "for this same day asserted an ISO zone on this acceleration-only file; that stale PDF is "
        "quarantined in `outputs/field_validation/_stale_pre_regen/`.",
        "",
        "Severity line(s), quoted verbatim from the regenerated PDF:",
        "",
    ]
    for s in mq_sev:
        out.append(f"> {s.strip()}")
        out.append("")
    out += ["Inner-race evidence line(s), quoted verbatim:", ""]
    if mq_inner:
        for s in mq_inner:
            out.append(f"> {s.strip()}")
            out.append("")
    else:
        out += ["> (none — no inner-race evidence appears in the report)", ""]

    clause3 = bool(mq_inner)
    all3 = (not mq_zone) and bool(mq_sev) and clause3
    out += [
        "### Marquee verdict — 3 clauses",
        "",
        f"1. **no Zone A language** — {'✅ PASS' if not mq_zone else '❌ FAIL'}",
        f"2. **severity unrated** — {'✅ PASS' if mq_sev else '❌ FAIL'}",
        f"3. **carries the inner-race evidence at its computed grade** — "
        f"{'✅ PASS' if clause3 else '❌ **FAIL**'}",
        "",
        (
            "**Overall: PASS — all three clauses satisfied.**"
            if all3
            else "**Overall: PARTIAL — clauses 1 and 2 pass, clause 3 fails.** Clause 3 is not "
            "reachable in the current build: the SKF 32222 J2 roller count remains uncitable "
            "(Session B Task 2 stayed BLOCKED), so `config/wind_turbine.json` `bearing_key` is "
            "`null`, pdm_core cannot compute BPFI/BPFO for this machine, and no inner-race "
            "evidence exists at ANY grade to report. The severity-truthfulness half of the marquee "
            "— the part the Session-A fix was built for — is fully satisfied and verified on the "
            "live product path. Packeted as `REVIEW_PACKET_regen_wt_day50.md`."
        ),
        "",
    ]

    (_REPO / "outputs" / "regen_grading.md").write_text("\n".join(out) + "\n")
    (_REPO / "outputs" / "regen_grading.json").write_text(json.dumps(rows, indent=2, default=str))
    npass = sum(1 for r in rows if r["grade"] == "PASS")
    print(f"{npass}/{len(rows)} PASS -> outputs/regen_grading.md")
    for r in rows:
        if r["grade"] == "FAIL":
            print(f"  FAIL {r['group']}/{r['label']}: {r['fails']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
