"""Phase 8 Part C — five drafted PDFs through the LIVE local webapp.

Real HTTP uploads to a running uvicorn instance, invite-gated, drafted end to
end through the real agent path (real ANTHROPIC_API_KEY, real Anthropic API
calls). Not --no-llm, not a TestClient.

Spec: 1 normal, 2 imbalance (across weights), 2 misalignment (across
severities) -> drafted PDFs into outputs/field_validation/mafaulda/.

Usage (key scoped to this call only, per the runsheet's AUTH rule):
    set -a; source ./.env.api; set +a; python scripts/run_part_c_mafaulda.py

HONESTY NOTE: these reports will reflect the SAME detector behavior the eval run
found (bearing false positives from the 3x/5x-vs-BPFO/BPFI collision; the ISO
Zone A artifact on acceleration-only data). That is the point of a product-path
check — it shows what an analyst actually receives today, warts included, not a
cleaned-up version.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("PART_C_BASE", "http://127.0.0.1:8012")
INVITE = os.environ.get("PART_C_INVITE", "maf8")
OUT_DIR = _REPO_ROOT / "outputs" / "field_validation" / "mafaulda"
DATA = _REPO_ROOT / "data" / "mafaulda"


def pick_files() -> list[tuple[str, Path, str]]:
    """(label, path, why) — 1 normal, 2 imbalance, 2 misalignment, mid speeds."""

    def mid(paths: list[Path]) -> Path:
        s = sorted(paths, key=lambda p: float(p.stem))
        return s[len(s) // 2]

    picks: list[tuple[str, Path, str]] = []
    picks.append(("normal_mid", mid(list((DATA / "normal").glob("*.csv"))),
                  "spec: 1 normal file"))
    picks.append(("imbalance_6g", mid(list((DATA / "imbalance" / "6g").glob("*.csv"))),
                  "spec: imbalance, lightest weight (6 g)"))
    picks.append(("imbalance_35g", mid(list((DATA / "imbalance" / "35g").glob("*.csv"))),
                  "spec: imbalance, heaviest weight (35 g)"))
    picks.append(("horizontal_mis_0.5mm", mid(list((DATA / "horizontal-misalignment" / "0.5mm").glob("*.csv"))),
                  "spec: misalignment, mildest horizontal shim (0.5 mm)"))
    picks.append(("vertical_mis_1.90mm", mid(list((DATA / "vertical-misalignment" / "1.90mm").glob("*.csv"))),
                  "spec: misalignment, severe vertical shim (1.90 mm)"))
    return picks


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not in env — scope it: set -a; source ./.env.api; set +a; ...", file=sys.stderr)
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    picks = pick_files()
    print(f"Part C: {len(picks)} MAFAULDA uploads -> {BASE}\n")
    summary, joblog = [], []

    for label, path, why in picks:
        size = path.stat().st_size
        print(f"[{label}] {path.relative_to(DATA)} ({size/1e6:.1f} MB) — {why}")
        t0 = time.time()
        with open(path, "rb") as fh:
            r = requests.post(
                f"{BASE}/api/jobs",
                files={"file": ("upload.csv", fh, "text/csv")},
                data={
                    "invite_code": INVITE,
                    "machine_alias": "ABVT-rig",
                    "rpm": "1770",  # nominal; the tach channel is authoritative
                    "iso_group": "2",
                    "iso_support": "rigid",
                    "mode": "mafaulda",
                },
                timeout=180,
            )
        if r.status_code != 202:
            print(f"    UPLOAD FAILED {r.status_code}: {r.text[:300]}\n")
            summary.append({"label": label, "state": "upload_failed", "status": r.status_code,
                            "body": r.text[:300]})
            continue

        job_id = r.json()["job_id"]
        state, payload = "queued", {}
        deadline = time.time() + 400
        while time.time() < deadline:
            g = requests.get(f"{BASE}/api/jobs/{job_id}", timeout=30)
            payload = g.json()
            state = payload.get("state", "?")
            if state not in ("queued", "running"):
                break
            time.sleep(2)
        dur_ms = (time.time() - t0) * 1000

        pdf_saved = False
        if state == "done":
            pr = requests.get(f"{BASE}/api/jobs/{job_id}/report.pdf", timeout=90)
            if pr.status_code == 200 and pr.content[:4] == b"%PDF":
                (OUT_DIR / f"{label}.pdf").write_bytes(pr.content)
                pdf_saved = True

        print(f"    job={job_id} state={state} {dur_ms:.0f}ms pdf={'saved' if pdf_saved else 'NO'}\n")
        summary.append({"label": label, "file": str(path.relative_to(DATA)), "job_id": job_id,
                        "state": state, "pdf_saved": pdf_saved, "why": why, "duration_ms": round(dur_ms, 1)})
        joblog.append(f"{label:22s} {job_id}  mafaulda_csv  {size:9d}  {dur_ms:9.1f}  {state}")

    (OUT_DIR / "part_c_summary.json").write_text(json.dumps(summary, indent=2))
    (OUT_DIR / "joblog.txt").write_text(
        "Phase 8 Part C -- live product-path verification job log\n"
        "Real HTTP uploads to a live uvicorn instance, real Anthropic API drafting calls.\n"
        "Label/kind/cost only, per the privacy model in RUNBOOK.md / CLAUDE.md.\n\n"
        f"{'label':22s} {'job_id':32s}  {'kind':12s}  {'size':>9s}  {'dur_ms':>9s}  outcome\n"
        + "\n".join(joblog) + "\n"
    )
    done = sum(1 for s in summary if s.get("state") == "done")
    print(f"{done}/{len(picks)} done, PDFs -> {OUT_DIR}")
    return 0 if done == len(picks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
