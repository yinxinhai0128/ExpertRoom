from __future__ import annotations

from typing import Any

from app.adapters.base import BaseAdapter
from app.adapters.claude import ClaudeAdapter
from app.adapters.codex import CodexAdapter
from app.adapters.hermes import HermesAdapter
from app.adapters.mock import MockAdapter
from app.adapters.openclaw import OpenClawAdapter

_ADAPTERS: dict[str, type[BaseAdapter]] = {
    "mock": MockAdapter,
    "claude": ClaudeAdapter,
    "hermes": HermesAdapter,
    "openclaw": OpenClawAdapter,
    "codex": CodexAdapter,
}


def create_adapter(backend: str, config: dict[str, Any]) -> BaseAdapter:
    cls = _ADAPTERS.get(backend)
    if cls is None:
        raise ValueError(f"Unknown adapter backend: {backend!r}")
    return cls(config)
