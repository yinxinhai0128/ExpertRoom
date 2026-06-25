from __future__ import annotations

import random
from typing import Any

from app.adapters.base import AgentResult, BaseAdapter

_RESPONSES = [
    "这个想法很有意思，从工程角度来看，核心挑战在于状态管理和并发控制。",
    "我觉得可以先从最小可行方案开始：砍掉所有非必要功能，只保留核心流程。",
    "有没有考虑过用户心智模型？很多产品失败是因为用户根本不理解它在解决什么问题。",
    "关键是找到那个「啊哈时刻」——用户第一次真正感受到价值的瞬间在哪里？",
    "这个方向值得深挖。不过要警惕过度工程化，先验证假设再扩展。",
    "从市场时机看，这个窗口期大概还有 12-18 个月，竞争对手也在往这个方向走。",
    "我认为最大风险不在技术，而在团队执行力和资源分配决策。",
    "可以参考 X 的做法——他们用了类似的路径，但在第二阶段做了关键调整。",
    "这个问题的本质是一个优先级问题：速度和质量在这个阶段哪个更重要？",
    "有意思。我来补充一个反直觉的视角：也许问题不是怎么做，而是要不要做。",
]

_MOCK_TOOL_RESPONSES = [
    "搜索结果：找到 3 篇相关文章，核心观点是该领域在 2024 年增速达到 47%。",
    "代码分析完成：主要瓶颈在第 42 行的循环，时间复杂度 O(n²)，建议改用哈希表。",
]

_MODERATOR_RESPONSES = []  # 在 moderator.py 里单独处理


class MockAdapter(BaseAdapter):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._call_count = 0

    def run(self, prompt: str) -> AgentResult:
        self._call_count += 1
        prompt_lower = prompt.lower()

        # 主持者路由调用 —— 特殊处理
        if "只输出一个词" in prompt or "agent_id" in prompt:
            return AgentResult(text="__mock_route__", raw="__mock_route__")

        # 综合调用
        if "综合" in prompt or "总结" in prompt or "synthesize" in prompt_lower:
            text = (
                "【讨论总结】\n"
                "经过多轮头脑风暴，核心 idea 已经浮现：\n"
                "1. 从最小可行方案入手，快速验证核心假设\n"
                "2. 关注用户第一次体验到价值的「啊哈时刻」\n"
                "3. 警惕过度工程化，优先保证核心流程稳定\n\n"
                "建议下一步行动：写一个 2 页纸的产品原型描述，明确目标用户和核心场景。"
            )
            return AgentResult(text=text, raw=text)

        response = random.choice(_RESPONSES)
        return AgentResult(text=response, raw=response)
