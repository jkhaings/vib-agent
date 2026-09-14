"""Loaders for config/*.json. All tunable constants live in config files, never in code."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


@lru_cache(maxsize=None)
def load_config(name: str) -> dict[str, Any]:
    """Load a config file by stem, e.g. load_config('iso_zones')."""
    path = CONFIG_DIR / f"{name}.json"
    with path.open() as f:
        return json.load(f)


def load_thresholds(profile: str | None = None) -> dict[str, Any]:
    """Load the thresholds config resolved to a single profile (default: active_profile)."""
    cfg = load_config("thresholds")
    profile = profile or cfg["active_profile"]
    return cfg["profiles"][profile]
