"""Fetch a STRATIFIED STARTER SUBSET of MAFAULDA into data/mafaulda/ (gitignored).

Phase 8. The full database is 13 GB and the per-category archives are 0.3-3.7 GB
each; the official host exposes plain directory listings, so this fetches the ~61
individual CSVs the phase actually needs (~1.1 GB) rather than any archive.

Subset (per the Phase 8 spec):
  normal:                 10 files spanning the speed range (incl. lowest + highest)
  imbalance:              all 7 weights (6/10/15/20/25/30/35 g) x 3 speeds = 21
  horizontal misalignment: 4 severities x 3 speeds = 12
  vertical misalignment:   6 severities x 3 speeds = 18
  bearing files:          NOT fetched (Part D is note-only this phase)

Filenames on the host ARE the rotation frequency in Hz (e.g. 12.288.csv = 737 rpm),
so target speeds are matched to the nearest available file per directory. Manual-URL
fallback: every file's URL is printed on failure so it can be retrieved by hand,
same pattern as scripts/fetch_mfpt.py.
"""

from __future__ import annotations

import io
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://www02.smt.ufrj.br/~offshore/mfs/database/mafaulda"
DEST = Path(__file__).resolve().parents[1] / "data" / "mafaulda"

# Spec: low ~15 Hz, mid ~30 Hz, high ~55 Hz
TARGET_SPEEDS = (15.0, 30.0, 55.0)

IMBALANCE_WEIGHTS = ("6g", "10g", "15g", "20g", "25g", "30g", "35g")
HORIZONTAL = ("0.5mm", "1.0mm", "1.5mm", "2.0mm")
VERTICAL = ("0.51mm", "0.63mm", "1.27mm", "1.40mm", "1.78mm", "1.90mm")


# The host 403s urllib's default User-Agent; a normal one is served fine.
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def _open(url: str, timeout: int):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=timeout
    )


def list_csvs(url: str) -> list[float]:
    """Directory listing -> the rotation frequencies available there."""
    with _open(url, 60) as r:
        html = r.read().decode("latin-1")
    return sorted(float(m) for m in re.findall(r'href="([0-9.]+)\.csv"', html))


class _HttpRangeFile(io.RawIOBase):
    """Minimal seekable read-only file over HTTP range requests.

    Needed because the host serves vertical-misalignment/ ONLY as a category
    archive: every path under vertical-misalignment/<severity>/ returns 403
    (both the directory index and the individual CSVs), while its siblings
    (normal/, imbalance/<w>/, horizontal-misalignment/<sev>/) list and serve
    files normally. Rather than pull the whole 2.0 GB zip for the 18 files the
    subset needs, this lets `zipfile` read the central directory and then
    range-fetch only those members (~317 MB). The server advertises range
    support (206 Partial Content), verified before use.
    """

    def __init__(self, url: str):
        self.url = url
        self._pos = 0
        with _open(url, 60) as r:  # HEAD via GET of 0 bytes is unreliable here
            self._size = int(r.headers["Content-Length"])

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._size}[whence]
        self._pos = max(0, base + offset)
        return self._pos

    def tell(self) -> int:
        return self._pos

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = self._size - self._pos
        if size == 0 or self._pos >= self._size:
            return b""
        end = min(self._pos + size, self._size) - 1
        req = urllib.request.Request(
            self.url, headers={"User-Agent": _UA, "Range": f"bytes={self._pos}-{end}"}
        )
        with urllib.request.urlopen(req, timeout=300) as r:
            data = r.read()
        self._pos += len(data)
        return data

    def readinto(self, b) -> int:
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)


def fetch_from_zip(archive_url: str, severities: tuple[str, ...], targets: tuple[float, ...]) -> int:
    """Range-fetch, for each severity, the members nearest each target speed.

    Speeds are enumerated from the ARCHIVE'S OWN member names, never guessed:
    each vertical severity has its own distinct speed set (0.51mm has 51 files
    spanning 12.493-61.645 Hz, 1.27mm has 50 spanning 12.083-62.259, etc.), so
    reusing normal/'s speed list finds almost nothing.
    """
    print(f"  [range-fetch] reading central directory of {archive_url.rsplit('/',1)[-1]}")
    zf = zipfile.ZipFile(_HttpRangeFile(archive_url))
    names = [n for n in zf.namelist() if n.endswith(".csv")]

    by_sev: dict[str, list[tuple[float, str]]] = {}
    for n in names:
        m = re.search(r"([0-9.]+mm)/([0-9.]+)\.csv$", n)
        if m:
            by_sev.setdefault(m.group(1), []).append((float(m.group(2)), n))
    for v in by_sev.values():
        v.sort()

    ok = 0
    for sev in severities:
        avail = by_sev.get(sev)
        if not avail:
            print(f"  ! severity {sev!r} not in archive (have: {sorted(by_sev)})", file=sys.stderr)
            continue
        speeds = [s for s, _ in avail]
        for t in targets:
            s = nearest(speeds, t)
            member = next(n for sp, n in avail if sp == s)
            rel = f"vertical-misalignment/{sev}/{fmt(s)}.csv"
            dest = DEST / rel
            if dest.exists() and dest.stat().st_size > 1_000_000:
                print(f"  = {rel} (cached)")
                ok += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as fh:
                fh.write(src.read())
            print(f"  + {rel} ({dest.stat().st_size/1e6:.1f} MB)")
            ok += 1
    return ok


def nearest(available: list[float], target: float) -> float:
    return min(available, key=lambda s: abs(s - target))


def fmt(speed: float) -> str:
    """Reproduce the host's filename spelling for a listed speed."""
    s = f"{speed:.4f}".rstrip("0").rstrip(".")
    return s


def download(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 1_000_000:
        print(f"  = {dest.name} (cached)")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _open(url, 300) as r, open(dest, "wb") as fh:
            fh.write(r.read())
    except Exception as exc:  # noqa: BLE001
        print(f"  ! FAILED {dest.name}: {exc}\n    manual URL: {url}", file=sys.stderr)
        return False
    print(f"  + {dest.name} ({dest.stat().st_size/1e6:.1f} MB)")
    return True


def plan() -> list[tuple[str, str, str]]:
    """Returns [(category_dir, remote_url, local_relpath)]."""
    items: list[tuple[str, str, str]] = []

    # --- normal: 10 spanning the range, including lowest + highest ---
    speeds = list_csvs(f"{BASE}/normal/")
    picks = [speeds[0], speeds[-1]]  # spec: incl. lowest + highest
    step = (len(speeds) - 1) / 9.0
    for i in range(1, 9):
        picks.append(speeds[int(round(i * step))])
    picks = sorted(set(picks))
    while len(picks) < 10:  # de-dup collapsed a pick; backfill
        for s in speeds:
            if s not in picks:
                picks.append(s)
                break
        picks = sorted(set(picks))
    for s in picks[:10]:
        items.append(("normal", f"{BASE}/normal/{fmt(s)}.csv", f"normal/{fmt(s)}.csv"))

    # --- imbalance: 7 weights x 3 speeds ---
    for w in IMBALANCE_WEIGHTS:
        avail = list_csvs(f"{BASE}/imbalance/{w}/")
        for t in TARGET_SPEEDS:
            s = nearest(avail, t)
            items.append((f"imbalance/{w}", f"{BASE}/imbalance/{w}/{fmt(s)}.csv",
                          f"imbalance/{w}/{fmt(s)}.csv"))

    # --- horizontal misalignment: 4 x 3 ---
    for sev in HORIZONTAL:
        avail = list_csvs(f"{BASE}/horizontal-misalignment/{sev}/")
        for t in TARGET_SPEEDS:
            s = nearest(avail, t)
            items.append((f"horizontal-misalignment/{sev}",
                          f"{BASE}/horizontal-misalignment/{sev}/{fmt(s)}.csv",
                          f"horizontal-misalignment/{sev}/{fmt(s)}.csv"))

    # NOTE: vertical-misalignment is NOT here — every path under
    # vertical-misalignment/<severity>/ is 403 on this host (index and files
    # alike), unlike its siblings. It is fetched from the category archive in
    # main() via fetch_from_zip(). Verified 2026-07-17:
    #   imbalance/6g/ -> 200, horizontal-misalignment/0.5mm/ -> 200,
    #   normal/ -> 200, vertical-misalignment/ -> 200,
    #   vertical-misalignment/0.51mm/ -> 403 (and its .csv files -> 403).
    return items


def main() -> int:
    items = plan()
    print(f"Directly-fetchable subset: {len(items)} files -> {DEST}\n")
    ok = 0
    for cat, url, rel in items:
        if download(url, DEST / rel):
            ok += 1

    # vertical-misalignment: category archive only (see plan()'s note)
    n_vertical = len(VERTICAL) * len(TARGET_SPEEDS)
    print(f"\nvertical-misalignment: {n_vertical} files via remote-zip range fetch\n")
    try:
        ok += fetch_from_zip(f"{BASE}/vertical-misalignment.zip", VERTICAL, TARGET_SPEEDS)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! range-fetch failed ({exc}); fall back to the full archive by hand:\n"
              f"    curl -O {BASE}/vertical-misalignment.zip  (2.0 GB)", file=sys.stderr)

    total = len(items) + n_vertical
    have = len(list(DEST.rglob("*.csv")))
    size = sum(f.stat().st_size for f in DEST.rglob("*.csv")) / 1e9
    print(f"\n{have}/{total} files present ({size:.2f} GB)")
    return 0 if have == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
