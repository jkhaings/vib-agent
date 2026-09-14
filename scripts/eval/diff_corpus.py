#!/usr/bin/env python3
"""DQ-0 — the corpus zero-diff gate: compare two `run_corpus.py` JSONLs
field-by-field on the scored fields.

This commits the two-run comparison that previously existed only as prose in
`outputs/SESSION_PDMFIX.md` §"Corpus". Every latched session runs
`run_corpus.py --dataset all` and diffs its output against the committed
baseline with this script; the required result is zero field differences, or a
named, justified expected-diff list written into that session's close-out
before the latch closes.

Rows are keyed by their `file` field (unique per corpus row). Only the scored
fields are compared — run-variant fields (`ts`, `elapsed_s`, `traceback`) are
ignored by construction. Values are compared as parsed JSON with exact
equality: the pipeline is deterministic, so floats must match exactly too.

Usage:
    python scripts/eval/diff_corpus.py <baseline.jsonl> <new.jsonl>

Exit codes:
    0  same file keys and zero field differences
    1  one or more field differences (each named on a DIFF line)
    2  structural failure — unreadable file, bad JSON, duplicate or missing
       `file` keys, dataset_error rows, or key sets that do not match
       (a comparison over mismatched corpora would be meaningless)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The 14 scored fields — the exact set the PDMFIX two-run comparison used.
SCORED_FIELDS: tuple[str, ...] = (
    "gate",
    "iso_zone",
    "iso_severity",
    "rca_status",
    "committed",
    "severity_rms",
    "n_recommendations",
    "committed_bearing",
    "rca_primary",
    "top_fault",
    "top_confidence",
    "stage",
    "differential",
    "recommendations",
)


class StructuralError(Exception):
    """The two files cannot be meaningfully compared."""


def load_rows(path: Path) -> dict[str, dict]:
    """JSONL -> {file: row}. Raises StructuralError on anything that would
    make the comparison lie: bad JSON, a row with no `file` key (dataset-level
    failure rows), or two rows claiming the same file."""
    if not path.is_file():
        raise StructuralError(f"not a file: {path}")
    rows: dict[str, dict] = {}
    with path.open() as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise StructuralError(f"{path}:{lineno}: bad JSON ({exc.msg})") from exc
            key = row.get("file")
            if not key:
                raise StructuralError(
                    f"{path}:{lineno}: row has no 'file' key "
                    f"(status={row.get('status')!r}) — a corpus containing a "
                    "dataset-level failure is not comparable"
                )
            if key in rows:
                raise StructuralError(f"{path}:{lineno}: duplicate file key {key!r}")
            rows[key] = row
    if not rows:
        raise StructuralError(f"{path}: no rows")
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("baseline", type=Path, help="the committed reference JSONL")
    ap.add_argument("new", type=Path, help="the fresh run to compare against it")
    args = ap.parse_args(argv)

    try:
        base = load_rows(args.baseline)
        new = load_rows(args.new)
    except StructuralError as exc:
        print(f"STRUCTURAL: {exc}")
        return 2

    same_keys = base.keys() == new.keys()
    print(
        f"{args.baseline.name}  {len(base)} rows   "
        f"{args.new.name}  {len(new)} rows   same keys: {same_keys}"
    )
    if not same_keys:
        for key in sorted(base.keys() - new.keys()):
            print(f"  ONLY IN BASELINE: {key}")
        for key in sorted(new.keys() - base.keys()):
            print(f"  ONLY IN NEW:      {key}")
        print("STRUCTURAL: file-key sets differ — fields not compared")
        return 2

    keys = sorted(base)
    total = 0
    print()
    for field in SCORED_FIELDS:
        differing = [k for k in keys if base[k].get(field) != new[k].get(field)]
        total += len(differing)
        print(f"  {field:20s} {len(differing)} files differ")
        for k in differing:
            print(f"    DIFF {field} {k}:")
            print(f"      baseline: {json.dumps(base[k].get(field))}")
            print(f"      new:      {json.dumps(new[k].get(field))}")

    print(f"\nTOTAL FIELD DIFFERENCES: {total}")
    return 1 if total > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
