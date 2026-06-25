from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config_loader import ROOT_DIR, load_yaml


@dataclass
class AgentProfile:
    id: str
    name: str
    avatar: str = "🤖"
    backend: str = "mock"
    model_name: str = ""
    enabled: bool = True
    identity: str = ""
    # 人格
    traits: list[str] = field(default_factory=list)
    tone: str = ""
    expertise: str = ""           # 专业方向，用于主持者路由决策
    # 目标
    goal_public: list[str] = field(default_factory=list)
    goal_private: list[str] = field(default_factory=list)
    # 记忆
    long_term: list[str] = field(default_factory=list)

    def system_prompt(self) -> str:
        parts = [f"你是{self.name}，{self.identity}。"]
        if self.traits:
            parts.append(f"性格：{', '.join(self.traits)}。")
        if self.tone:
            parts.append(f"说话风格：{self.tone}。")
        if self.goal_public:
            parts.append(f"目标：{'; '.join(self.goal_public)}。")
        if self.long_term:
            parts.append(f"你的固有认知：{'; '.join(self.long_term)}。")
        parts.append("不要说「作为AI」。直接以角色身份发言。")
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "avatar": self.avatar,
            "backend": self.backend,
            "enabled": self.enabled,
            "identity": self.identity,
            "expertise": self.expertise,
        }


def _parse_agent(data: dict[str, Any]) -> AgentProfile:
    personality = data.get("personality") or {}
    speaking = data.get("speaking_style") or {}
    goals = data.get("goals") or {}
    memory = data.get("memory") or {}

    return AgentProfile(
        id=str(data.get("id", "")),
        name=str(data.get("name", data.get("id", ""))),
        avatar=str(data.get("avatar", "🤖")),
        backend=str(data.get("backend", "mock")),
        model_name=str(data.get("model_name", "")),
        enabled=bool(data.get("enabled", True)),
        identity=str(data.get("identity", "")),
        expertise=str(data.get("expertise", data.get("identity", ""))),
        traits=list(personality.get("traits") or []),
        tone=str(speaking.get("tone", "")),
        goal_public=list(goals.get("public") or []),
        goal_private=list(goals.get("private") or []),
        long_term=list(memory.get("long_term") or []),
    )


def clear_agent_cache() -> None:
    load_all_agents.cache_clear()


@lru_cache(maxsize=1)
def load_all_agents() -> dict[str, AgentProfile]:
    agents_dir = ROOT_DIR / "agents"
    result: dict[str, AgentProfile] = {}
    for path in sorted(agents_dir.glob("*.yaml")):
        try:
            data = load_yaml(path)
            agent = _parse_agent(data)
            if agent.id:
                result[agent.id] = agent
        except Exception as exc:
            print(f"[agent_loader] skip {path.name}: {exc}")
    return result
