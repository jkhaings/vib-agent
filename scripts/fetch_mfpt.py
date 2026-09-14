#!/usr/bin/env python3
"""Fetch (or verify presence of) the MFPT Bearing Fault Dataset into
data/mfpt/ (gitignored). Mirrors scripts/fetch_cwru.py's pattern: best
effort, never blocks the suite.

The dataset's original home (mfpt.org/fault-data-sets/, data-acoustics.com)
no longer serves the file directly — mfpt.org now redirects to its parent
organization (ASNT) and data-acoustics.com is unreachable. The official ZIP
is still retrievable via the Wayback Machine, at the exact URL the live
mfpt.org page linked before it went away (verified working during Phase 7).
On ANY failure (network, wrong URL, blocked egress) this script prints
manual download instructions and exits 0 — tests/test_mfpt_adapter.py's
end-to-end cases skip cleanly when data/mfpt/ is empty.

Usage:
    python scripts/fetch_mfpt.py
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vib_agent.config import load_config  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "mfpt"
MANUAL_URLS = [
    "https://mfpt.org/fault-data-sets/  (now redirects to https://www.asnt.org/about/managed-affiliates/mfpt/)",
    "https://github.com/mathworks/RollingElementBearingFaultDiagnosis-Data "
    "(GitHub mirror — rig files only, NOT the 3 real-world files in folder 6)",
]

# Verified working during Phase 7 — the Wayback Machine's copy of the exact
# ZIP the live mfpt.org page used to link. The bare origin URL is tried
# first in case the site is ever restored; the archived copy is the
# actually-reliable fallback today.
_ZIP_URL_CANDIDATES = [
    "https://www.mfpt.org/wp-content/uploads/2020/02/MFPT-Fault-Data-Sets-20200227T131140Z-001.zip",
    "http://web.archive.org/web/20250625000501/"
    "https://www.mfpt.org/wp-content/uploads/2020/02/MFPT-Fault-Data-Sets-20200227T131140Z-001.zip",
]

_TIMEOUT_SECONDS = 60
_ZIP_ROOT = "MFPT Fault Data Sets"

# The Wayback Machine serves a small JS "loading" interstitial (200 OK,
# text/html) instead of the archived binary when a request has no `Accept`
# header — Python's urllib sends none by default, unlike curl or a browser.
# Setting both headers below reliably gets the real archived file.
_REQUEST_HEADERS = {"User-Agent": "curl/8.7.1", "Accept": "*/*"}


def _download_zip(dest: Path) -> bool:
    for url in _ZIP_URL_CANDIDATES:
        try:
            req = urllib.request.Request(url, headers=_REQUEST_HEADERS)
            with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # noqa: S310
                dest.write_bytes(resp.read())
            if dest.exists() and dest.stat().st_size > 1_000_000:
                return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError):
            pass
        dest.unlink(missing_ok=True)
    return False


def main() -> int:
    mfpt_cfg = load_config("mfpt")
    rig_files = {k: v for k, v in mfpt_cfg["rig_files"].items() if not k.startswith("_")}
    real_world_files = {k: v for k, v in mfpt_cfg["real_world_files"].items() if not k.startswith("_")}
    all_files = {**rig_files, **real_world_files}

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    missing_names = [name for name in all_files if not (DATA_DIR / f"{name}.mat").exists()]
    if not missing_names:
        print(f"All {len(all_files)} files present in {DATA_DIR}")
        print("Run: vib eval --suite mfpt")
        return 0

    print(f"Fetching MFPT dataset ZIP ({len(missing_names)} of {len(all_files)} files missing)...")
    zip_path = DATA_DIR / "_mfpt_download.zip"
    if not _download_zip(zip_path):
        print()
        print("Could not auto-fetch the dataset (this is expected/non-blocking).")
        print("Download manually from one of:")
        for url in MANUAL_URLS:
            print(f"  {url}")
        print()
        print("Extract 'MFPT Fault Data Sets/' and place the .mat files into data/mfpt/")
        print("using the names below (source path -> save as):")
        for name, meta in all_files.items():
            print(f"  {meta['zip_path']}  ->  data/mfpt/{name}.mat")
        print()
        print("MFPT e2e tests auto-skip until these files are present.")
        return 0

    extracted_any = False
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for name in missing_names:
                zip_member = f"{_ZIP_ROOT}/{all_files[name]['zip_path']}"
                dest = DATA_DIR / f"{name}.mat"
                try:
                    with zf.open(zip_member) as src, dest.open("wb") as out:
                        out.write(src.read())
                    extracted_any = True
                    print(f"  {name}: extracted")
                except KeyError:
                    print(f"  {name}: NOT FOUND in archive at {zip_member!r} — archive layout may have changed")
    finally:
        zip_path.unlink(missing_ok=True)

    if not extracted_any:
        print("Extraction failed for all files — see messages above.")
        return 0

    still_missing = [name for name in all_files if not (DATA_DIR / f"{name}.mat").exists()]
    if still_missing:
        print()
        print(f"{len(still_missing)} file(s) still missing: {still_missing}")
        return 0

    print()
    print(f"All {len(all_files)} files present in {DATA_DIR}")
    print("Run: vib eval --suite mfpt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
