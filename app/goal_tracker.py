from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class GoalProgress:
    current: int
    target: int
    achieved: bool
    description: str = ""
    # structured checklist
    problem_defined: bool = False
    solutions_count: int = 0
    risks_identified: bool = False
    tradeoffs_discussed: bool = False
    next_actions_ready: bool = False
    summary_ready: bool = False

    def to_dict(self) -> dict:
        return {
            "current": self.current,
            "target": self.target,
            "achieved": self.achieved,
            "description": self.description,
            "checklist": {
                "problem_defined": self.problem_defined,
                "solutions_count": self.solutions_count,
                "risks_identified": self.risks_identified,
                "tradeoffs_discussed": self.tradeoffs_discussed,
                "next_actions_ready": self.next_actions_ready,
                "summary_ready": self.summary_ready,
            },
        }


_PROBLEM_WORDS = re.compile(r"问题|挑战|困难|痛点|需求|现状|背景|issue|problem|challenge", re.I)
_SOLUTION_WORDS = re.compile(r"方案|解决|建议|可以|应该|proposal|solution|suggest|recommend", re.I)
_RISK_WORDS = re.compile(r"风险|危险|问题|隐患|缺陷|陷阱|risk|danger|caveat|pitfall|concern", re.I)
_TRADEOFF_WORDS = re.compile(r"但是|然而|权衡|代价|不过|相比|vs|however|tradeoff|trade.off|downside|drawback", re.I)
_ACTION_WORDS = re.compile(r"下一步|接下来|行动|执行|落地|first step|next step|action|implement|deploy", re.I)


class GoalTracker:
    def __init__(self, goal: str) -> None:
        self.goal = goal or ""
        self.target = self._parse_target(goal)

    def _parse_target(self, goal: str) -> int:
        # Chinese: 5个建议 / 3条方案
        m = re.search(r"(\d+)\s*[个条点项份]", goal)
        if m:
            return int(m.group(1))
        # English count noun: "3 ideas", "5 recommendations"
        m = re.search(
            r"(\d+)\s*(?:ideas?|recommendations?|options?|suggestions?|actions?|plans?|points?|examples?)",
            goal, re.I,
        )
        if m:
            return int(m.group(1))
        # English verb + number: "generate 3", "list 5 options"
        m = re.search(
            r"(?:produce|generate|give|list|create|find|identify)\s+(\d+)",
            goal, re.I,
        )
        if m:
            return int(m.group(1))
        return 0

    def check(self, history: list[dict]) -> GoalProgress:
        agent_msgs = [
            m for m in history
            if m.get("message_type", "text") == "text"
            and m.get("agent_id", "user") not in ("user", "system")
            and len(m.get("content", "")) > 20
        ]
        current = len(agent_msgs)
        all_text = " ".join(m.get("content", "") for m in agent_msgs)

        problem_defined = bool(_PROBLEM_WORDS.search(all_text)) if agent_msgs else False
        solutions = sum(1 for m in agent_msgs if _SOLUTION_WORDS.search(m.get("content", "")))
        risks_identified = bool(_RISK_WORDS.search(all_text)) if current >= 2 else False
        tradeoffs_discussed = bool(_TRADEOFF_WORDS.search(all_text)) if current >= 3 else False
        next_actions_ready = bool(_ACTION_WORDS.search(all_text)) if current >= 4 else False

        if self.target > 0:
            achieved = current >= self.target
            description = f"已有 {current}/{self.target} 条实质发言"
        else:
            # heuristic: done when all checklist items green after at least 5 msgs
            achieved = (current >= 5 and problem_defined and solutions >= 2
                        and risks_identified and next_actions_ready)
            description = f"已有 {current} 条发言"

        return GoalProgress(
            current=current,
            target=self.target,
            achieved=achieved,
            description=description,
            problem_defined=problem_defined,
            solutions_count=solutions,
            risks_identified=risks_identified,
            tradeoffs_discussed=tradeoffs_discussed,
            next_actions_ready=next_actions_ready,
            summary_ready=False,  # set by caller when artifact exists
        )
