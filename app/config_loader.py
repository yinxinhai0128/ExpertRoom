from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent


def load_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT_DIR / p
    if not p.exists():
        raise FileNotFoundError(f"YAML not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {p}")
    return data


@lru_cache(maxsize=1)
def get_config() -> dict[str, Any]:
    return load_yaml("config.yaml")


def clear_config_cache() -> None:
    get_config.cache_clear()
    try:
        from app.agent_loader import load_all_agents
        load_all_agents.cache_clear()
    except Exception:
        pass
