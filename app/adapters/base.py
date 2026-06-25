from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    tool_use_id: str = ""
    content: str = ""


@dataclass
class AgentResult:
    text: str = ""
    raw: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


class AgentRuntimeError(RuntimeError):
    pass


class BaseAdapter(ABC):
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config or {}

    @abstractmethod
    def run(self, prompt: str) -> AgentResult:
        """同步调用模型，返回结果。"""
