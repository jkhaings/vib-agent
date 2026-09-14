"""Phase 7B Part C — four drafted PDFs through the LIVE local webapp.

Real HTTP uploads to a running uvicorn instance, invite-gated, drafted end to
end through the real agent path (real ANTHROPIC_API_KEY, real Anthropic API
calls). Not the --no-llm path, not a TestClient.

Usage (key scoped to this call only, per the runsheet's AUTH rule):
    set -a; source ./.env.api; set +a; python scripts/run_part_c_wind_turbine.py

DAY SELECTION — the spec asks for (a) an early healthy day, (b) the first
watch-crossing day, (c) the first flag / first RCA>=medium day, (d) the final
day. (b) and (c) DO NOT EXIST in this run: Layer 2 never armed (sum_w peaked at
29.74 vs min_readings 30), and the RCA progression is blocked on bearing
geometry. Rather than skip them or invent crossings, they are substituted with
the nearest events the system ACTUALLY produced — the first Layer 4 trend alert
and the peak-RMS day — and labelled as substitutions in the results doc.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("PART_C_BASE", "http://127.0.0.1:8011")
INVITE = os.environ.get("PART_C_INVITE", "wt7b1")
OUT_DIR = _REPO_ROOT / "outputs" / "field_validation" / "wind_turbine"
TIMELINE = _REPO_ROOT / "outputs" / "wind_turbine_timeline.json"


def pick_days() -> list[tuple[str, str, str]]:
    """Returns [(label, filename, why)] — chosen from the real timeline."""
    rows = json.load(open(TIMELINE))
    by_day = {r["day"]: r for r in rows}

    # (b) substitute: first day Layer 4 emitted a non-ok severity.
    first_alert = next((r for r in rows if r["trend_severity"] not in (None, "ok")), None)
    # (c) substitute: peak overall RMS day.
    peak = max(rows, key=lambda r: r["overall_g"])

    picks = [
        ("early_healthy_day01", by_day[1],
         "spec (a) 'an early healthy day' — first recording, 2013-03-07"),
        (f"first_trend_alert_day{first_alert['day']:02d}", first_alert,
         f"SUBSTITUTE for spec (b) 'first watch-crossing day' — Welford NEVER armed "
         f"(sum_w peaked 29.74 vs min_readings 30), so no watch crossing exists. "
         f"This is the first alert the system actually produced: Layer 4 trend "
         f"severity ok -> {first_alert['trend_severity']}"),
        (f"peak_rms_day{peak['day']:02d}", peak,
         f"SUBSTITUTE for spec (c) 'first flag or first RCA>=medium day' — no flag "
         f"(Layer 2 never armed) and no RCA (bearing geometry BLOCKED). This is the "
         f"clearest degradation signal that exists: peak overall RMS {peak['overall_g']:.4f}g"),
        (f"final_day{rows[-1]['day']:02d}", rows[-1],
         "spec (d) 'the final day' — 2013-04-25, end of the run-to-failure"),
    ]
    return [(label, r["file"], why) for label, r, why in picks]


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not in env — scope it: set -a; source ./.env.api; set +a; ...", file=sys.stderr)
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    picks = pick_days()
    print(f"Part C: {len(picks)} uploads -> {BASE}\n")
    summary = []
    joblog = []

    for label, filename, why in picks:
        path = _REPO_ROOT / "data" / "wind_turbine" / filename
        size = path.stat().st_size
        print(f"[{label}] {filename} ({size/1e6:.1f} MB)")
        print(f"    why: {why[:100]}{'...' if len(why) > 100 else ''}")

        t0 = time.time()
        with open(path, "rb") as fh:
            r = requests.post(
                f"{BASE}/api/jobs",
                files={"file": (filename, fh, "application/octet-stream")},
                data={
                    "invite_code": INVITE,
                    "machine_alias": "WT-HS-Bearing",
                    "rpm": "1802",  # nominal; the file's own tach is authoritative
                    "iso_group": "2",
                    "iso_support": "rigid",
                },
                timeout=120,
            )
        if r.status_code != 202:
            print(f"    UPLOAD FAILED {r.status_code}: {r.text[:300]}")
            summary.append({"label": label, "file": filename, "state": "upload_failed",
                            "status": r.status_code, "body": r.text[:300]})
            continue

        job_id = r.json()["job_id"]
        state, payload = "queued", {}
        deadline = time.time() + 300
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
            pr = requests.get(f"{BASE}/api/jobs/{job_id}/report.pdf", timeout=60)
            if pr.status_code == 200 and pr.content[:4] == b"%PDF":
                (OUT_DIR / f"{label}.pdf").write_bytes(pr.content)
                pdf_saved = True

        print(f"    job={job_id} state={state} {dur_ms:.0f}ms pdf={'saved' if pdf_saved else 'NO'}\n")
        summary.append({"label": label, "file": filename, "job_id": job_id, "state": state,
                        "pdf_saved": pdf_saved, "why": why, "duration_ms": round(dur_ms, 1)})
        joblog.append(f"{filename:28s} {job_id}  wind_turbine_mat  {size:9d}  {dur_ms:9.1f}  {state}")

    (OUT_DIR / "part_c_summary.json").write_text(json.dumps(summary, indent=2))
    (OUT_DIR / "joblog.txt").write_text(
        "Phase 7B Part C — live product-path verification job log\n"
        "Real HTTP uploads to a live uvicorn instance, real Anthropic API drafting calls.\n"
        "Label/kind/cost only, per the privacy model in RUNBOOK.md / CLAUDE.md.\n\n"
        f"{'file':28s} {'job_id':32s}  {'kind':16s}  {'size':>9s}  {'dur_ms':>9s}  outcome\n"
        + "\n".join(joblog) + "\n"
    )
    done = sum(1 for s in summary if s.get("state") == "done")
    print(f"{done}/{len(picks)} done, PDFs -> {OUT_DIR}")
    return 0 if done == len(picks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
