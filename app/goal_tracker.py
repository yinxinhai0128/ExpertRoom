from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class GoalProgress:
    current: int
    target: int
    achieved: bool
    description: str = ""


class GoalTracker:
    """解析用户目标，追踪讨论进度。"""

    def __init__(self, goal: str) -> None:
        self.goal = goal or ""
        self.target = self._parse_target(goal)

    def _parse_target(self, goal: str) -> int:
        """从目标字符串提取目标数量，如「产出 5 个 idea」→ 5。"""
        m = re.search(r"(\d+)\s*[个条点项份]", goal)
        if m:
            return int(m.group(1))
        return 0   # 0 = 无数量目标，由轮数上限或主持者决定

    def check(self, history: list[dict]) -> GoalProgress:
        """统计当前讨论进度。"""
        # 统计有实质内容的 agent 发言（不含系统消息和工具调用）
        substantive = [
            m for m in history
            if m.get("message_type", "text") == "text"
            and m.get("agent_id", "user") != "system"
            and len(m.get("content", "")) > 30
        ]
        current = len(substantive)

        if self.target > 0:
            achieved = current >= self.target
            description = f"已有 {current}/{self.target} 条实质发言"
        else:
            achieved = False
            description = f"已有 {current} 条发言（目标：{self.goal or '自由讨论'}）"

        return GoalProgress(
            current=current,
            target=self.target,
            achieved=achieved,
            description=description,
        )
