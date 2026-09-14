#!/usr/bin/env python3
"""EVAL-DEEP phases 2/3/6 — score the corpus JSONL produced by run_corpus.py.

Emits, per dataset: a confusion matrix, precision/recall per fault class, and
EXPLICIT miss / false-commit lists carrying the file path (brief item 3). Also
computes the phase-6 severity-gating evidence (committed low-zone pattern faults
on healthy recordings) and the phase-2 historical comparison.

SCORING FIDELITY — this is the whole point of the phase-2 diff, so the rules
below are copied from the shipped scorers rather than re-invented. A different
rule would manufacture a "regression" that is really a harness artifact.

  CWRU + MFPT rig   `eval/runner.py` scores the BEARING findings of
                    `result.findings` (prefix `bearing_`), and calls a file
                    correct iff findings[0].fault == expected. Healthy files
                    pass iff that list is empty. Ball files are RECORDED, no
                    gate. Reproduced here as `committed_bearing`.
  MFPT real-world   `eval/runner.py::_score_mfpt_real_world` two-outcome rule.
                    NOT reproduced here — it needs the tolerance-expanded
                    embedded-order targets and the recommendations channel.
                    Reported as recorded-only; see EVAL_PACKET.md.
  MAFAULDA          `scripts/run_mafaulda.py` scores `rca.primary_findings`,
                    NOT `result.findings` — Part A "clean" means zero RCA
                    primaries. Both views are reported below because they can
                    differ, and the historical numbers are the rca view.

Usage:
    python scripts/eval/score_corpus.py results/corpus_all.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

_MISALIGNMENT_FAMILY = {
    "angular_misalignment",
    "parallel_misalignment",
    "severe_misalignment",
    "misalignment_general",
    "bent_shaft",
}

# Findings that are not fault assertions — the explicit "clean machine" shape
# (CLAUDE.md: a no-findings reading gets a positive report shape rather than
# pressure to invent one). Must never be counted as a committed fault.
_NON_FAULT = {"no_significant_findings"}

# Non-bearing pattern detectors — the 1x/2x family. Used by the phase-6
# severity-gating count.
_PATTERN_FAULTS = _MISALIGNMENT_FAMILY | {
    "imbalance",
    "looseness",
    "belt_wear",
    "blade_pass",
    "resonance",
}


def load(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def committed_faults(row: dict) -> list[str]:
    return [f for f in (row.get("committed") or []) if f not in _NON_FAULT]


def rca_faults(row: dict) -> list[str]:
    return [m["fault"] for m in (row.get("rca_primary") or [])]


def top_conf(row: dict) -> str | None:
    """Confidence of the first REAL committed fault. `top_confidence` in the
    JSONL is findings[0]'s, which on a clean machine is the confidence of the
    `no_significant_findings` sentinel — reporting that as a fault confidence
    would read as 'high-confidence nothing'."""
    for d in row.get("committed_detail") or []:
        if d["fault"] not in _NON_FAULT:
            return d["confidence"]
    return None


# --------------------------------------------------------------------------


def confusion(rows: list[dict], gt_of, pred_of) -> tuple[dict, list[str], list[str]]:
    """(matrix[gt][pred] -> count, gt_labels, pred_labels)."""
    m: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        m[gt_of(r)][pred_of(r)] += 1
    gts = sorted(m)
    preds = sorted({p for c in m.values() for p in c})
    return m, gts, preds


def render_matrix(m, gts, preds, title: str) -> list[str]:
    w = max([len(g) for g in gts] + [12])
    out = [f"### {title}", "", "| gt \\ predicted | " + " | ".join(preds) + " | total |",
           "|" + "---|" * (len(preds) + 2)]
    for g in gts:
        tot = sum(m[g].values())
        out.append(f"| **{g}** | " + " | ".join(str(m[g].get(p, 0)) for p in preds) + f" | {tot} |")
    out.append("")
    return out


def prec_recall(rows: list[dict], classes: list[str], gt_of, pred_set_of) -> list[str]:
    """Per-class P/R where prediction is a SET (a file can commit >1 fault)."""
    out = ["| class | TP | FP | FN | precision | recall |", "|---|---|---|---|---|---|"]
    for c in classes:
        tp = fp = fn = 0
        for r in rows:
            g = gt_of(r) == c
            p = c in pred_set_of(r)
            if g and p:
                tp += 1
            elif p and not g:
                fp += 1
            elif g and not p:
                fn += 1
        prec = f"{tp / (tp + fp):.3f}" if (tp + fp) else "—"
        rec = f"{tp / (tp + fn):.3f}" if (tp + fn) else "—"
        out.append(f"| {c} | {tp} | {fp} | {fn} | {prec} | {rec} |")
    out.append("")
    return out


# --------------------------------------------------------------------------
# CWRU / MFPT rig — eval/runner.py's rule
# --------------------------------------------------------------------------


def score_bearing_rig(rows: list[dict], name: str) -> tuple[list[str], dict]:
    out = [f"## {name}", ""]
    errs = [r for r in rows if r.get("status") != "ok"]
    rows = [r for r in rows if r.get("status") == "ok"]
    if errs:
        out += [f"**{len(errs)} file(s) errored:**", ""]
        out += [f"- `{r['file']}` — {r.get('error')}" for r in errs] + [""]

    def top_bearing(r):
        cb = r.get("committed_bearing") or []
        return cb[0] if cb else "none"

    m, gts, preds = confusion(
        rows, lambda r: r.get("gt_fault") or f"none ({r.get('gt_family')})", top_bearing
    )
    out += render_matrix(m, gts, preds, "Confusion — ground truth vs top committed bearing finding")

    out += ["### Precision / recall per bearing class", ""]
    classes = sorted({r["gt_fault"] for r in rows if r.get("gt_fault")})
    out += prec_recall(
        rows, classes, lambda r: r.get("gt_fault"), lambda r: set(r.get("committed_bearing") or [])
    )

    # runner.py's own pass bar.
    #
    # GRADED-SCOPE EXCLUSION (CWRU only): config/cwru.json states "0.028in+
    # diameters remain out of v1 scope (never independently verified; not
    # authorized)", and outputs/validation_summary.md files the 0.028in eval
    # case as "RECORDED (no gate) -- it does not count toward the IR/OR pass
    # bar". The historical 20/24 is therefore over the 24 graded IR/OR files.
    # Excluded here for the same reason, and reported separately below rather
    # than dropped silently.
    normal = [r for r in rows if r.get("gt_family") == "normal"]
    ir_or_all = [r for r in rows if r.get("gt_family") in ("outer", "inner")]
    # CWRU only — for MFPT `gt_severity_param` is the load in lbs, not a
    # defect diameter in inches, so this predicate must never see it.
    ungraded = [
        r
        for r in ir_or_all
        if r.get("dataset") == "cwru"
        and isinstance(r.get("gt_severity_param"), (int, float))
        and r["gt_severity_param"] >= 0.028
    ]
    ir_or = [r for r in ir_or_all if r not in ungraded]
    ball = [r for r in rows if r.get("gt_family") == "ball"]

    false_pos = [r for r in normal if r.get("committed_bearing")]
    correct = [r for r in ir_or if top_bearing(r) == r.get("gt_fault")]
    misses = [r for r in ir_or if top_bearing(r) != r.get("gt_fault")]

    pct = 100.0 * len(correct) / len(ir_or) if ir_or else 0.0
    out += [
        "### Pass bar (eval/runner.py's rule)",
        "",
        f"- Healthy clean (0 false bearing calls): **{len(normal) - len(false_pos)}/{len(normal)}**",
        f"- IR/OR primary-diagnosis correct: **{len(correct)}/{len(ir_or)} ({pct:.1f}%)**",
        f"- Ball files: {len(ball)} recorded, informational only (no gate).",
        "",
    ]
    if ungraded:
        out += [
            f"- Out-of-graded-scope IR/OR files excluded from the bar "
            f"(0.028in+, per `config/cwru.json`): **{len(ungraded)}** — "
            + ", ".join(f"`{r['file']}`" for r in ungraded),
            "",
            "  Recorded, not graded:",
            "",
        ]
        out += [
            f"  - `{r['file']}` — expected `{r['gt_fault']}`, committed "
            f"`{top_bearing(r)}` (conf {top_conf(r)})"
            for r in ungraded
        ] + [""]

    out += ["### MISSES (IR/OR, explicit)", ""]
    out += (
        [f"- `{r['file']}` — expected `{r['gt_fault']}`, committed `{top_bearing(r)}` "
         f"(conf {top_conf(r)})" for r in misses]
        or ["- none"]
    ) + [""]

    out += ["### FALSE COMMITS (healthy files, explicit)", ""]
    out += (
        [f"- `{r['file']}` — committed `{', '.join(r['committed_bearing'])}` "
         f"(conf {top_conf(r)})" for r in false_pos]
        or ["- none"]
    ) + [""]

    return out, {
        "normal_clean": f"{len(normal) - len(false_pos)}/{len(normal)}",
        "ir_or": f"{len(correct)}/{len(ir_or)}",
        "ir_or_pct": pct,
        "ball_recorded": len(ball),
        "misses": [r["file"] for r in misses],
        "false_commits": [r["file"] for r in false_pos],
        "errors": len(errs),
    }


# --------------------------------------------------------------------------
# MAFAULDA — run_mafaulda.py's rule (rca.primary_findings)
# --------------------------------------------------------------------------


def score_mafaulda(rows: list[dict]) -> tuple[list[str], dict]:
    out = ["## MAFAULDA", ""]
    errs = [r for r in rows if r.get("status") != "ok"]
    rows = [r for r in rows if r.get("status") == "ok"]
    if errs:
        out += [f"**{len(errs)} file(s) errored:**", ""]
        out += [f"- `{r['file']}` — {r.get('error')}" for r in errs] + [""]

    normal = [r for r in rows if r["gt_family"] == "normal"]
    imb = [r for r in rows if r["gt_family"] == "imbalance"]
    mis = [r for r in rows if r["gt_family"].endswith("misalignment")]

    def top_pred(r):
        f = rca_faults(r)
        return f[0] if f else "none"

    m, gts, preds = confusion(rows, lambda r: r["gt_family"], top_pred)
    out += render_matrix(m, gts, preds, "Confusion — directory ground truth vs top RCA primary")

    out += ["### Precision / recall (family level)", ""]
    out += [
        "| class | TP | FP | FN | precision | recall |",
        "|---|---|---|---|---|---|",
    ]
    for label, member, gt_pred in (
        ("imbalance", lambda fs: "imbalance" in fs, lambda r: r["gt_family"] == "imbalance"),
        (
            "misalignment_family",
            lambda fs: bool(set(fs) & _MISALIGNMENT_FAMILY),
            lambda r: r["gt_family"].endswith("misalignment"),
        ),
    ):
        tp = fp = fn = 0
        for r in rows:
            g, p = gt_pred(r), member(rca_faults(r))
            if g and p:
                tp += 1
            elif p and not g:
                fp += 1
            elif g and not p:
                fn += 1
        prec = f"{tp / (tp + fp):.3f}" if (tp + fp) else "—"
        rec = f"{tp / (tp + fn):.3f}" if (tp + fn) else "—"
        out.append(f"| {label} | {tp} | {fp} | {fn} | {prec} | {rec} |")
    out.append("")

    clean = [r for r in normal if not rca_faults(r)]
    hit_imb = [r for r in imb if "imbalance" in rca_faults(r)]
    hit_mis = [r for r in mis if set(rca_faults(r)) & _MISALIGNMENT_FAMILY]

    out += [
        "### Pass bar (scripts/run_mafaulda.py's rule — RCA primaries)",
        "",
        f"- Part A normal clean (HARD LINE, zero RCA primaries): **{len(clean)}/{len(normal)}**",
        f"- Part B imbalance primary (bar ≥90%): **{len(hit_imb)}/{len(imb)} "
        f"({100.0 * len(hit_imb) / len(imb) if imb else 0:.1f}%)**",
        f"- Part C misalignment family (bar ≥80%): **{len(hit_mis)}/{len(mis)} "
        f"({100.0 * len(hit_mis) / len(mis) if mis else 0:.1f}%)**",
        "",
        "### FALSE COMMITS on healthy files (explicit)",
        "",
    ]
    fps = [r for r in normal if rca_faults(r)]
    out += (
        [f"- `{r['file']}` — RCA primary `{', '.join(rca_faults(r))}`; "
         f"committed findings `{', '.join(committed_faults(r)) or 'none'}`" for r in fps]
        or ["- none"]
    ) + [""]

    out += ["### MISSES — imbalance (explicit)", ""]
    out += (
        [f"- `{r['file']}` (severity {r.get('gt_severity_param')}) — "
         f"RCA primary `{', '.join(rca_faults(r)) or 'none'}`"
         for r in imb if r not in hit_imb]
        or ["- none"]
    ) + [""]

    out += ["### MISSES — misalignment family (explicit)", ""]
    out += (
        [f"- `{r['file']}` (severity {r.get('gt_severity_param')}) — "
         f"RCA primary `{', '.join(rca_faults(r)) or 'none'}`"
         for r in mis if r not in hit_mis]
        or ["- none"]
    ) + [""]

    return out, {
        "normal_clean": f"{len(clean)}/{len(normal)}",
        "imbalance": f"{len(hit_imb)}/{len(imb)}",
        "misalignment": f"{len(hit_mis)}/{len(mis)}",
        "false_commits": [r["file"] for r in fps],
        "errors": len(errs),
    }


# --------------------------------------------------------------------------
# MFPT real-world + wind turbine — recorded, not classified
# --------------------------------------------------------------------------


def score_real_world(rows: list[dict]) -> list[str]:
    out = ["## MFPT real-world files (recorded — not officially fault-labelled)", "",
           "No answer key exists for these three (NO FABRICATED GROUND TRUTH). "
           "`eval/runner.py`'s two-outcome rule is not reproduced here; what is "
           "recorded is what the pipeline committed.", "",
           "| file | gate | committed | confidence | differential | # recs |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        out.append(
            f"| `{r['file']}` | {r.get('gate')} | "
            f"{', '.join(committed_faults(r)) or 'none'} | {top_conf(r) or '—'} | "
            f"{', '.join(d['fault'] for d in (r.get('differential') or [])) or 'none'} | "
            f"{r.get('n_recommendations')} |"
        )
    return out + [""]


def score_wind_turbine(rows: list[dict]) -> tuple[list[str], dict]:
    out = ["## Wind turbine — 50-day run-to-failure timeline", "",
           "Scored as a TIMELINE, not a per-file classification (no per-file fault "
           "label exists). Terminal confirmed fault: inner race.", ""]
    errs = [r for r in rows if r.get("status") != "ok"]
    ok = [r for r in rows if r.get("status") == "ok"]
    if errs:
        out += [f"**{len(errs)} errored:** " + ", ".join(f"`{r['file']}`" for r in errs), ""]

    committed_any = [r for r in ok if committed_faults(r)]
    bearing_any = [r for r in ok if r.get("committed_bearing")]
    last = ok[-1] if ok else None
    final5 = ok[-5:]

    slopes = [r.get("trend_slope") for r in ok if r.get("trend_slope") is not None]
    out += [
        f"- Files processed: **{len(ok)}** (chronological, cumulative history)",
        f"- Files committing ANY fault: **{len(committed_any)}**",
        f"- Files committing a BEARING fault: **{len(bearing_any)}**",
        f"- Final trend status: **{last.get('trend_status') if last else '—'}** "
        f"(severity {last.get('trend_severity') if last else '—'}, "
        f"slope {last.get('trend_slope') if last else '—'} g/day, "
        f"r² {last.get('trend_r_squared') if last else '—'})",
        f"- Trend slope sign over span: {'positive' if slopes and slopes[-1] > 0 else 'not positive'}",
        "",
        "### Final 5 recordings (the spec's RCA-progression window)",
        "",
        "| file | day | gate | trend | committed |",
        "|---|---|---|---|---|",
    ]
    for r in final5:
        out.append(
            f"| `{r['file']}` | {r.get('gt_day_index')} | {r.get('gate')} | "
            f"{r.get('trend_status')}/{r.get('trend_severity')} | "
            f"{', '.join(committed_faults(r)) or 'none'} |"
        )
    out.append("")
    return out, {
        "files": len(ok),
        "committed_any": len(committed_any),
        "bearing_any": len(bearing_any),
        "final_trend": last.get("trend_status") if last else None,
        "final_trend_severity": last.get("trend_severity") if last else None,
        "final_slope": last.get("trend_slope") if last else None,
        "errors": len(errs),
    }


# --------------------------------------------------------------------------
# phase 6 — severity-gating evidence
# --------------------------------------------------------------------------


def severity_gating(all_rows: list[dict]) -> list[str]:
    """Across healthy/baseline recordings: how often is a PATTERN fault
    (imbalance / misalignment family / looseness …) committed while the ISO
    zone is low (A/B) or not assessable? This is evidence for an open design
    question, NOT a defect to fix here."""
    healthy = [
        r
        for r in all_rows
        if r.get("status") == "ok"
        and (
            r.get("gt_family") == "normal"
            or (r.get("dataset") == "mafaulda" and r.get("gt_family") == "normal")
        )
    ]
    out = ["## Stretch — severity-gating evidence (healthy recordings)", "",
           "Committed PATTERN faults (imbalance / misalignment family / looseness / "
           "belt / blade-pass / resonance) on recordings whose ground truth is healthy, "
           "broken out by the ISO zone the same report carried. Reported as measured "
           "frequency for a beta analyst's design question — explicitly not fixed here.", "",
           f"Healthy recordings in corpus: **{len(healthy)}**", "",
           "| dataset | file | iso_zone | committed pattern faults | confidence |",
           "|---|---|---|---|---|"]
    hits = 0
    zone_counter: Counter = Counter()
    for r in healthy:
        pat = [f for f in committed_faults(r) if f in _PATTERN_FAULTS]
        if not pat:
            continue
        hits += 1
        zone_counter[r.get("iso_zone")] += 1
        out.append(
            f"| {r['dataset']} | `{r['file']}` | {r.get('iso_zone')} | "
            f"{', '.join(pat)} | {top_conf(r)} |"
        )
    if not hits:
        out.append("| — | *(none)* | — | — | — |")
    out += ["", f"**Committed pattern faults on healthy recordings: {hits}/{len(healthy)}**", ""]
    if zone_counter:
        out += ["Zone breakdown: " + ", ".join(f"`{k}`×{v}" for k, v in zone_counter.items()), ""]

    # zone distribution across the whole corpus, for context
    zc = Counter(r.get("iso_zone") for r in all_rows if r.get("status") == "ok")
    out += ["### ISO zone distribution across the whole corpus", "",
            "| zone | files |", "|---|---|"]
    for k, v in sorted(zc.items(), key=lambda kv: -kv[1]):
        out.append(f"| {k} | {v} |")
    out.append("")
    return out


# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    p = Path(args.jsonl)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent / p
    rows = load(p)

    by_ds = defaultdict(list)
    for r in rows:
        by_ds[r.get("dataset")].append(r)

    doc = ["# Corpus scorecard", "",
           f"Source: `{p.name}` — {len(rows)} records. Profile: `route`.", ""]
    summary: dict = {}

    if by_ds.get("cwru"):
        sec, s = score_bearing_rig(by_ds["cwru"], "CWRU")
        doc += sec
        summary["cwru"] = s
    if by_ds.get("mfpt"):
        rig = [r for r in by_ds["mfpt"] if r.get("gt_family") != "real_world"]
        rw = [r for r in by_ds["mfpt"] if r.get("gt_family") == "real_world"]
        sec, s = score_bearing_rig(rig, "MFPT rig files")
        doc += sec
        summary["mfpt_rig"] = s
        if rw:
            doc += score_real_world(rw)
    if by_ds.get("mafaulda"):
        sec, s = score_mafaulda(by_ds["mafaulda"])
        doc += sec
        summary["mafaulda"] = s
    if by_ds.get("wind_turbine"):
        sec, s = score_wind_turbine(by_ds["wind_turbine"])
        doc += sec
        summary["wind_turbine"] = s

    doc += severity_gating(rows)

    text = "\n".join(doc) + "\n"
    out = Path(args.out) if args.out else p.with_suffix(".scorecard.md")
    if not out.is_absolute():
        out = Path(__file__).resolve().parent / out
    out.write_text(text)
    out.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2))
    print(text)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
