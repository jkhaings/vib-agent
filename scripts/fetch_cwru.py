#!/usr/bin/env python3
"""Fetch (or verify presence of) the CWRU Bearing Data Center 12kHz
Drive-End starter set into data/cwru/ (gitignored).

Best-effort only. The download URL pattern below is not independently
verified against a live connection in this environment — confirm file
numbers against the official page before relying on it. On ANY failure
(network, wrong URL, blocked egress, wrong file number) this script prints
manual download instructions and exits 0. It must never block the test
suite or CI: tests/test_cwru_adapter.py's end-to-end cases skip cleanly
when data/cwru/ is empty.

Usage:
    python scripts/fetch_cwru.py
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vib_agent.config import load_config  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "cwru"
MANUAL_URL = "https://engineering.case.edu/bearingdatacenter/download-data-file"

# Best-effort guess at the direct-file URL pattern — NOT independently
# verified in this environment. Confirm against MANUAL_URL if this 404s.
_URL_CANDIDATES = [
    "https://engineering.case.edu/sites/default/files/{num}.mat",
    "https://engineering.case.edu/sites/default/files/{num}_0.mat",
]


_TIMEOUT_SECONDS = 10


def _try_download(dest: Path, num: int) -> bool:
    for pattern in _URL_CANDIDATES:
        url = pattern.format(num=num)
        try:
            with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as resp:  # noqa: S310
                dest.write_bytes(resp.read())
            if dest.exists() and dest.stat().st_size > 0:
                return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError):
            pass
        if dest.exists() and dest.stat().st_size == 0:
            dest.unlink()
    return False


def main() -> int:
    full = "--full" in sys.argv
    cwru_cfg = load_config("cwru")
    key = "full_de_files" if full else "starter_files"
    files = {k: v for k, v in cwru_cfg[key].items() if not k.startswith("_")}
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    missing: list[tuple[str, int]] = []
    for name, num in files.items():
        dest = DATA_DIR / f"{name}.mat"
        if dest.exists():
            print(f"  {name}: already present")
            continue
        print(f"  {name}: fetching (CWRU file #{num})...")
        if not _try_download(dest, num):
            print(f"    -> failed")
            missing.append((name, num))

    if missing:
        print()
        print("Could not auto-fetch all files (this is expected/non-blocking).")
        print(f"Download manually from: {MANUAL_URL}")
        print()
        print("Missing files (CWRU file number -> save as):")
        for name, num in missing:
            print(f"  #{num:>3}  ->  data/cwru/{name}.mat")
        print()
        print("CWRU e2e tests auto-skip until these files are present.")
        return 0

    print()
    print(f"All {len(files)} files present in {DATA_DIR}")
    print("Run: vib eval --suite cwru")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
