#!/usr/bin/env python3
"""Generate eval/cases/cwru/*.json from every data/cwru/*.mat file via
adapters.cwru.to_case(). Run after scripts/fetch_cwru.py has populated
data/cwru/.

Usage:
    python scripts/generate_cwru_cases.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vib_agent.adapters.cwru import to_case  # noqa: E402
from vib_agent.config import load_config  # noqa: E402
from vib_agent.models import BearingSpec  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DATA_DIR = _REPO_ROOT / "data" / "cwru"
_CASES_DIR = _REPO_ROOT / "eval" / "cases" / "cwru"


def main() -> int:
    mat_files = sorted(_DATA_DIR.glob("*.mat"))
    if not mat_files:
        print(f"No .mat files found in {_DATA_DIR}. Run scripts/fetch_cwru.py first.")
        return 1

    cwru_cfg = load_config("cwru")
    raw_bearing = load_config("bearings")["bearings"][cwru_cfg["bearing_key"]]
    bearing_spec = BearingSpec(**{k: v for k, v in raw_bearing.items() if not k.startswith("_")})

    _CASES_DIR.mkdir(parents=True, exist_ok=True)
    for mat_path in mat_files:
        case = to_case(mat_path, cwru_cfg=cwru_cfg, bearing_spec=bearing_spec)
        out_path = _CASES_DIR / f"{case.name}.json"
        out_path.write_text(case.model_dump_json(indent=2))
        print(f"  {mat_path.name} -> {out_path.relative_to(_REPO_ROOT)}")

    print(f"\nGenerated {len(mat_files)} case(s) in {_CASES_DIR}")
    print("Run: vib eval --suite cwru")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
