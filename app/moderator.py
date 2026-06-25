from __future__ import annotations

import re
from typing import Any

from app.adapters.factory import create_adapter
from app.agent_loader import AgentProfile
from app.config_loader import get_config


_ROUTE_PROMPT = """\
你是讨论主持人，负责决定下一个发言者。你不产出任何内容，只输出一个 agent_id 或 SYNTHESIZE。

【讨论目标】
{goal}

【参与者】
{agent_list}

【最近发言记录】
{history}

【选择规则】
1. 选能最大程度推进目标的参与者
2. 如果有悬而未答的问题，选最适合回答的人
3. 如果需要网络信息或代码分析，选 backend 为 claude 的 agent
4. 避免同一个 agent 连续发言超过 2 次
5. 如果讨论已覆盖目标所要求的内容，或轮数已足够，输出 SYNTHESIZE

只输出一个词：agent_id（如 manong / shanhe / jiaoshou）或 SYNTHESIZE。不要解释。
"""


class ModeratorAgent:
    """纯路由器：读取讨论历史，决定下一个发言者，不生成任何对话内容。"""

    def __init__(self, agents: dict[str, AgentProfile]) -> None:
        self.agents = agents
        self._rr_index = 0          # 轮询计数，mock 模式用
        self._consecutive: dict[str, int] = {}   # 连续发言次数

    def _round_robin(self, exclude: str | None = None) -> str:
        ids = [aid for aid in self.agents if self.agents[aid].enabled]
        if not ids:
            return "SYNTHESIZE"
        for _ in range(len(ids)):
            idx = self._rr_index % len(ids)
            self._rr_index += 1
            candidate = ids[idx]
            if candidate != exclude:
                return candidate
        return ids[0]

    def pick_next(self, history: list[dict], goal: str) -> str:
        """返回 agent_id 或 'SYNTHESIZE'。同步调用，由调用方用 asyncio.to_thread 包装。"""
        config = get_config()
        backend = config.get("moderator", {}).get("backend", "mock")

        if backend == "mock":
            # mock 模式：简单轮询，避免连续发言超过 2 次
            last = history[-1].get("agent_id") if history else None
            return self._round_robin(
                exclude=last if self._consecutive.get(last, 0) >= 2 else None
            )

        # 真实模型路由
        agent_list = "\n".join(
            f"- {aid}（{a.name}，{a.expertise or a.identity}，backend={a.backend}）"
            for aid, a in self.agents.items()
            if a.enabled
        )
        recent = history[-8:] if len(history) > 8 else history
        history_text = "\n".join(
            f"{m.get('agent_name', m.get('agent_id', '?'))}：{m.get('content', '')[:120]}"
            for m in recent
            if m.get("message_type", "text") == "text"
        )

        prompt = _ROUTE_PROMPT.format(
            goal=goal or "自由讨论",
            agent_list=agent_list,
            history=history_text or "（尚无发言）",
        )

        adapter_config = config.get("adapters", {}).get(backend, {})
        try:
            result = create_adapter(backend, adapter_config).run(prompt)
            token = (result.text or "").strip().split()[0] if result.text else ""
            if token == "SYNTHESIZE":
                return "SYNTHESIZE"
            if token in self.agents:
                self._update_consecutive(token)
                return token
        except Exception as exc:
            print(f"[moderator] routing failed, fallback round-robin: {exc}")

        return self._round_robin()

    def _update_consecutive(self, agent_id: str) -> None:
        for aid in list(self._consecutive):
            if aid != agent_id:
                self._consecutive[aid] = 0
        self._consecutive[agent_id] = self._consecutive.get(agent_id, 0) + 1
