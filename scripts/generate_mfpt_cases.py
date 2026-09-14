#!/usr/bin/env python3
"""Generate eval/cases/mfpt/*.json from every data/mfpt/*.mat file via
adapters.mfpt.to_case(). Run after scripts/fetch_mfpt.py has populated
data/mfpt/.

Usage:
    python scripts/generate_mfpt_cases.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vib_agent.adapters.mfpt import to_case  # noqa: E402
from vib_agent.config import load_config  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DATA_DIR = _REPO_ROOT / "data" / "mfpt"
_CASES_DIR = _REPO_ROOT / "eval" / "cases" / "mfpt"


def main() -> int:
    mat_files = sorted(_DATA_DIR.glob("*.mat"))
    if not mat_files:
        print(f"No .mat files found in {_DATA_DIR}. Run scripts/fetch_mfpt.py first.")
        return 1

    mfpt_cfg = load_config("mfpt")
    bearings_cfg = load_config("bearings")

    _CASES_DIR.mkdir(parents=True, exist_ok=True)
    for mat_path in mat_files:
        case = to_case(mat_path, mfpt_cfg=mfpt_cfg, bearings_cfg=bearings_cfg)
        out_path = _CASES_DIR / f"{case.name}.json"
        out_path.write_text(case.model_dump_json(indent=2))
        print(f"  {mat_path.name} -> {out_path.relative_to(_REPO_ROOT)}")

    print(f"\nGenerated {len(mat_files)} case(s) in {_CASES_DIR}")
    print("Run: vib eval --suite mfpt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
