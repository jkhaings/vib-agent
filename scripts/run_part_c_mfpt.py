"""RUN v5 Part C — five MFPT drafted PDFs through the LIVE local webapp.

Real HTTP uploads to a running uvicorn instance, invite-gated, drafted end to
end through the real agent path (real ANTHROPIC_API_KEY, real Anthropic API
calls). Not --no-llm, not a TestClient.

The five files are the ones pinned by the original Phase 7B Part C run (see
outputs/field_validation_part_c.md): the three real-world field bearings plus one
rig outer-race and one rig baseline, so this regeneration is directly comparable
to that record. Previously driven ad hoc; scripted here so the run is repeatable.

Usage (key scoped to this call only, per the runsheet's AUTH rule):
    set -a; source ./.env.api; set +a; python scripts/run_part_c_mfpt.py

The MFPT .mat adapter reads the shaft rate from the file itself (rpm =
rate*60), so the `rpm` form field below is nominal machine metadata only and
does not drive the analysis.
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
INVITE = os.environ.get("PART_C_INVITE", "mfpt5")
OUT_DIR = _REPO_ROOT / "outputs" / "field_validation" / "mfpt"
DATA = _REPO_ROOT / "data" / "mfpt"

# (label, filename, why) — pinned to the original Part C set.
PICKS: list[tuple[str, str, str]] = [
    ("real_world_intermediate_speed_bearing", "real_world_intermediate_speed_bearing.mat",
     "real-world field bearing (Session B holdout: honest-uncertainty PASS)"),
    ("real_world_oil_pump_bearing", "real_world_oil_pump_bearing.mat",
     "real-world field bearing (known FAIL: bearing content outside top-N)"),
    ("real_world_planet_bearing", "real_world_planet_bearing.mat",
     "real-world field bearing (known FAIL: bearing content outside top-N)"),
    ("outer_270_1", "outer_270_1.mat", "rig outer-race fault, 270 lb load"),
    ("baseline_1", "baseline_1.mat", "rig baseline (healthy) — must stay clean"),
]


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not in env — scope it: set -a; source ./.env.api; set +a; ...",
              file=sys.stderr)
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Part C: {len(PICKS)} MFPT uploads -> {BASE}\n")
    summary, joblog = [], []

    for label, fname, why in PICKS:
        path = DATA / fname
        if not path.exists():
            print(f"[{label}] MISSING {path}\n")
            summary.append({"label": label, "state": "missing_input"})
            continue
        size = path.stat().st_size
        print(f"[{label}] {fname} ({size/1e6:.1f} MB) — {why}")
        t0 = time.time()
        with open(path, "rb") as fh:
            r = requests.post(
                f"{BASE}/api/jobs",
                files={"file": (fname, fh, "application/octet-stream")},
                data={
                    "invite_code": INVITE,
                    "machine_alias": "MFPT-rig",
                    "rpm": "1500",  # nominal; the .mat's own `rate` field is authoritative
                    "iso_group": "2",
                    "iso_support": "rigid",
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
        summary.append({"label": label, "file": fname, "job_id": job_id, "state": state,
                        "pdf_saved": pdf_saved, "why": why, "duration_ms": round(dur_ms, 1),
                        "degraded_reason": payload.get("degraded_reason")})
        joblog.append(f"{label:38s} {job_id}  mfpt_mat  {size:9d}  {dur_ms:9.1f}  {state}")

    (OUT_DIR / "part_c_summary.json").write_text(json.dumps(summary, indent=2))
    (OUT_DIR / "joblog.txt").write_text(
        "RUN v5 Part C (MFPT) -- live product-path verification job log\n"
        "Real HTTP uploads to a live uvicorn instance, real Anthropic API drafting calls.\n"
        "Label/kind/cost only, per the privacy model in RUNBOOK.md / CLAUDE.md.\n\n"
        f"{'label':38s} {'job_id':32s}  {'kind':8s}  {'size':>9s}  {'dur_ms':>9s}  outcome\n"
        + "\n".join(joblog) + "\n"
    )
    done = sum(1 for s in summary if s.get("state") == "done")
    print(f"{done}/{len(PICKS)} done, PDFs -> {OUT_DIR}")
    return 0 if done == len(PICKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
