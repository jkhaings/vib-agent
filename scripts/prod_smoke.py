"""Production smoke test for the vib-agent webapp (RUN v6-A).

Exercises a LIVE deployment over the real HTTP path: the 6-format compat kit +
multiaxis cases (A2 trio, D speed-mismatch, E dead-axial, F duplicate-direction
4xx) + the v6-A security posture (headers, CSP, closed schema, /healthz, per-IP
429). Structural/keyless assertions — a live server has a real key so jobs draft
(done); the smoke accepts done OR degraded and never inspects report content, so
it costs nothing to assert and is deterministic.

Usage:
    python scripts/prod_smoke.py --base-url http://localhost:8000 --invite-code <code>
    # or: PROD_SMOKE_INVITE=<code> python scripts/prod_smoke.py --base-url ...

Quota note: this posts up to ~10 jobs (compat 6 + A2/D/E + F). The default
per-code daily cap and per-IP hourly cap are both 10, so run it against a fresh
deployment or a generous smoke invite code, and not repeatedly within the hour.
Exits non-zero on any failure.
"""

from __future__ import annotations

import argparse
import csv
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import requests
from scipy.io import savemat, wavfile

_REPO = Path(__file__).resolve().parents[1]
_KIT = _REPO / "tests" / "fixtures" / "multiaxis_kit"
_TERMINAL_OK = {"done", "degraded"}

# ── scorecard ─────────────────────────────────────────────────────────────────
_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


# ── compat-kit builders (self-contained, config-exact shapes) ─────────────────
def _spectrum_csv(p: Path, peak_hz: float = 107.03) -> None:
    n, fmax = 400, 200.0
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["freq_hz", "amplitude"])
        for i in range(n):
            fr = i * fmax / n
            w.writerow([fr, 0.5 if abs(fr - peak_hz) < fmax / n else 0.01])


def _trend_csv(p: Path) -> None:
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "overall_rms"])
        base = 1_700_000_000
        for i in range(30):
            ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(base + i * 86400))
            w.writerow([ts, 2.0 + i * 0.05])


def _spectrum_xlsx(p: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["freq_hz", "amplitude"])
    for i in range(400):
        fr = i * 200.0 / 400
        ws.append([fr, 0.5 if abs(fr - 107.03) < 0.5 else 0.01])
    wb.save(p)


def _uff(p: Path) -> None:
    import pyuff

    fs = 12000.0
    t = np.arange(int(fs)) / fs
    sig = np.sin(2 * np.pi * 3000 * t) * (1 + 0.5 * np.sin(2 * np.pi * 107.03 * t))
    dset = pyuff.prepare_58(
        func_type=1, rsp_node=1, rsp_dir=1, ref_node=1, ref_dir=1,
        data=sig.tolist(), x=t.tolist(), abscissa_spacing=1,
        orddenom_spec_data_type=0, ordinate_spec_data_type=0,
        abscissa_spec_data_type=0, z_axis_spec_data_type=0,
    )
    pyuff.UFF(str(p)).write_sets([dset], mode="add")


def _wav(p: Path) -> None:
    fs = 12000
    t = np.arange(fs) / fs
    sig = np.sin(2 * np.pi * 3000 * t) * (1 + 0.5 * np.sin(2 * np.pi * 107.03 * t))
    norm = sig / np.max(np.abs(sig)) * 0.8
    wavfile.write(str(p), fs, (norm * 32767).astype(np.int16))


def _cwru_mat(p: Path) -> None:
    fs = 12000.0
    t = np.arange(int(fs)) / fs
    sig = np.sin(2 * np.pi * 3000 * t) * (1 + 0.6 * np.sin(2 * np.pi * 107 * t))
    sig += 0.02 * np.random.default_rng(1).standard_normal(t.size)
    savemat(str(p), {"X097_DE_time": sig.reshape(-1, 1)})


def _build_compat_kit(tmp: Path) -> list[tuple[str, Path, dict]]:
    """(label, path, extra-form) for one file per supported single-file family."""
    specs = [
        ("csv-spectrum", "spectrum.csv", _spectrum_csv, {"mode": "spectrum"}),
        ("csv-trend", "trend.csv", _trend_csv, {"mode": "trend"}),
        ("xlsx", "spectrum.xlsx", _spectrum_xlsx, {"mode": "spectrum"}),
        ("uff", "response.uff", _uff, {}),
        ("wav", "signal.wav", _wav, {}),
        ("mat-cwru", "cwru.mat", _cwru_mat, {"bearing_model": "6205"}),
    ]
    out = []
    for label, name, builder, extra in specs:
        path = tmp / name
        builder(path)
        out.append((label, path, extra))
    return out


# ── HTTP ──────────────────────────────────────────────────────────────────────
class Client:
    def __init__(self, base: str, invite: str | None):
        self.base = base.rstrip("/")
        self.invite = invite
        self.s = requests.Session()

    def _form(self, **over) -> dict:
        d = {"invite_code": self.invite or "", "machine_alias": "Smoke",
             "rpm": "1780", "iso_group": "2", "iso_support": "rigid"}
        d.update({k: str(v) for k, v in over.items()})
        return d

    def post_single(self, path: Path, **form):
        with open(path, "rb") as fh:
            return self.s.post(f"{self.base}/api/jobs",
                               files={"file": (path.name, fh, "application/octet-stream")},
                               data=self._form(**form), timeout=60)

    def post_multi(self, files_dirs, **form):
        handles, files, data = [], {}, self._form(**form)
        names = ["file", "file_2", "file_3"]
        dirs = ["direction", "direction_2", "direction_3"]
        for i, (p, d) in enumerate(files_dirs):
            fh = open(p, "rb")
            handles.append(fh)
            files[names[i]] = (p.name, fh, "application/octet-stream")
            if d is not None:
                data[dirs[i]] = d
        try:
            return self.s.post(f"{self.base}/api/jobs", files=files, data=data, timeout=60)
        finally:
            for fh in handles:
                fh.close()

    def poll(self, job_id: str, timeout_s: float = 180):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            r = self.s.get(f"{self.base}/api/jobs/{job_id}", timeout=30)
            if r.status_code != 200:
                return {"state": f"http-{r.status_code}"}
            data = r.json()
            if data["state"] not in ("queued", "running"):
                return data
            time.sleep(2.0)  # realistic cadence — stays under the 60/min per-IP window
        return {"state": "TIMEOUT"}

    def get(self, path: str):
        return self.s.get(f"{self.base}{path}", timeout=30)


def _run_terminal(cl: Client, resp, label: str) -> dict:
    if resp.status_code != 202:
        check(f"{label}: accepted (202)", False, f"got {resp.status_code}: {resp.text[:120]}")
        return {}
    job_id = resp.json()["job_id"]
    data = cl.poll(job_id)
    ok = data.get("state") in _TERMINAL_OK
    check(f"{label}: reached terminal (done/degraded)", ok, f"state={data.get('state')}")
    if ok:
        pdf = cl.get(f"/api/jobs/{job_id}/report.pdf")
        check(f"{label}: report PDF 200", pdf.status_code == 200 and pdf.content[:4] == b"%PDF",
              f"status={pdf.status_code}")
    return data


# ── the smoke ─────────────────────────────────────────────────────────────────
def run(base: str, invite: str | None) -> int:
    cl = Client(base, invite)
    have_kit = (_KIT / "caseA_H.csv").exists()

    print(f"\n== security posture @ {base} ==")
    r = cl.get("/")
    check("/ security headers", all(
        r.headers.get(h) == v for h, v in {
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
            "referrer-policy": "strict-origin-when-cross-origin",
        }.items()), str({k: r.headers.get(k) for k in ("x-content-type-options", "x-frame-options")}))
    check("/ CSP present (default-src 'self')", "default-src 'self'" in (r.headers.get("content-security-policy") or ""))
    check("job route Cache-Control: no-store",
          cl.get("/api/jobs/smoke-nonexistent").headers.get("cache-control") == "no-store")
    check("/docs closed (404)", cl.get("/docs").status_code == 404)
    check("/openapi.json closed (404)", cl.get("/openapi.json").status_code == 404)
    hz = cl.get("/healthz")
    check("/healthz 200 + version", hz.status_code == 200 and "version" in hz.json(),
          hz.text[:120])

    if not invite:
        print("\n(no --invite-code / PROD_SMOKE_INVITE — skipping the job-posting checks)")
        return _summary()

    print("\n== duplicate-direction guard (F) ==")
    if have_kit:
        rF = cl.post_multi([(_KIT / "caseA_H.csv", "axial"), (_KIT / "caseA_V.csv", "axial")])
        detail = ""
        try:
            detail = rF.json().get("detail", "")
        except Exception:
            pass
        check("F: duplicate axial → 400 'both marked'",
              rF.status_code == 400 and "both marked" in detail, f"{rF.status_code} {detail[:80]}")

    print("\n== compat kit (6 formats, live) ==")
    with tempfile.TemporaryDirectory() as td:
        for label, path, extra in _build_compat_kit(Path(td)):
            _run_terminal(cl, cl.post_single(path, **extra), f"compat:{label}")

    if have_kit:
        print("\n== multiaxis A2 / D / E ==")
        a2 = _run_terminal(cl, cl.post_multi(
            [(_KIT / "caseA_H.csv", "radial_h"), (_KIT / "caseA_V.csv", "radial_v"),
             (_KIT / "caseA_A.csv", "axial")]), "A2 trio")
        if a2:
            chans = {c["direction"]: c["status"] for c in (a2.get("channels") or {}).get("channels", [])}
            check("A2: three channels all ok", chans == {"radial_h": "ok", "radial_v": "ok", "axial": "ok"},
                  str(chans))

        rD = cl.post_multi([(_KIT / "caseD_H.csv", "radial_h"), (_KIT / "caseD_V.csv", "radial_v")])
        if rD.status_code == 202:
            d = cl.poll(rD.json()["job_id"])
            warn = bool((d.get("channels") or {}).get("speed_warning"))
            check("D: speed warning OR fail-closed", warn or d.get("state") == "gate_fail",
                  f"state={d.get('state')} warn={warn}")
        else:
            check("D: accepted", False, str(rD.status_code))

        e = _run_terminal(cl, cl.post_multi(
            [(_KIT / "caseE_H.csv", "radial_h"), (_KIT / "caseE_A.csv", "axial")],
            bearing_model="6205"), "E pair")
        if e:
            estat = {c["direction"]: c["status"] for c in (e.get("channels") or {}).get("channels", [])}
            labels = [f["label"] for f in (e.get("result_summary") or {}).get("faults", [])]
            check("E: axial excluded (not ok)", estat.get("axial") not in (None, "ok"), str(estat))
            check("E: outer-race family committed",
                  any("outer-race" in lbl.lower() for lbl in labels), str(labels))

    print("\n== per-IP rate limit (GET burst — must be LAST; pollutes the window) ==")
    codes = [cl.get("/privacy").status_code for _ in range(70)]
    check("per-IP 429 fires on a >60/min GET burst", 429 in codes,
          f"{codes.count(200)}x200 {codes.count(429)}x429")

    return _summary()


def _summary() -> int:
    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"\n=== prod smoke: {passed}/{total} checks passed ===")
    for name, ok, detail in _RESULTS:
        if not ok:
            print(f"    FAIL {name} — {detail}")
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Live smoke test for the vib-agent webapp.")
    ap.add_argument("--base-url", required=True, help="e.g. http://localhost:8000")
    ap.add_argument("--invite-code", default=os.environ.get("PROD_SMOKE_INVITE"),
                    help="invite code for job POSTs (or PROD_SMOKE_INVITE env). Omit for posture-only.")
    args = ap.parse_args()
    return run(args.base_url, args.invite_code)


if __name__ == "__main__":
    raise SystemExit(main())
