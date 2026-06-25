from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any, AsyncGenerator

from sqlmodel import Session, select

from app.adapters.base import AgentResult, AgentRuntimeError
from app.adapters.factory import create_adapter
from app.agent_loader import AgentProfile, load_all_agents
from app.config_loader import get_config
from app.goal_tracker import GoalTracker
from app.models import Artifact, Message, Room
from app.moderator import ModeratorAgent


_AGENT_PROMPT = """\
{system_prompt}

【头脑风暴话题】
{topic}

【讨论目标】
{goal}

【讨论记录】
{history}

{user_inject}\
【你的任务】
基于以上讨论发言。可以直接反驳某人（请指名）、深化某个想法、或引入全新角度。
直接发言，不要加「我认为」「作为XXX」等前缀。畅所欲言，不限字数。
"""

_SYNTHESIZE_PROMPT = """\
你是一个中立的综合者。请整合以下头脑风暴讨论，输出一份清晰的总结文档。

【话题】
{topic}

【目标】
{goal}

【完整讨论记录】
{history}

输出格式（严格使用以下标题）：

## 核心结论

## 建议方案

## 关键 Ideas
（列出讨论中浮现的最有价值的想法，每条加序号）

## 主要争议与权衡

## 风险提示

## 下一步行动
（最直接可执行的 1-3 步）

## 专家贡献来源
（哪位专家提出了哪些关键观点，一行一条，格式：「专家名：观点摘要」）

用 Markdown 格式输出，简洁清晰。
"""


class RoomSession:
    """Single room async session, lives for the SSE connection lifetime."""

    def __init__(self, room_id: int, db: Session) -> None:
        self.room_id = room_id
        self.db = db
        # Queue carries (content, target_agent_id) tuples
        self._user_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # not paused initially
        self._seq = 0
        self._pending_target: str = ""  # agent_id to speak next, if user steered

    # ── Public interface ──────────────────────────────────────

    async def inject_user_message(self, content: str, target_agent_id: str = "") -> None:
        await self._user_queue.put((content, target_agent_id))

    def stop(self) -> None:
        self._stop_event.set()
        self._pause_event.set()  # unblock any waiting pause

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    async def run(self) -> AsyncGenerator[dict, None]:
        room = self.db.get(Room, self.room_id)
        if not room:
            yield self._evt("error", content="房间不存在")
            return

        config = get_config()
        max_rounds = int(config.get("moderator", {}).get("max_turns", 200))
        max_seconds = int(config.get("moderator", {}).get("max_seconds", 600))
        start_time = time.monotonic()

        all_agents = load_all_agents()
        agent_ids = [a.strip() for a in (room.agent_ids or "").split(",") if a.strip()]
        agents: dict[str, AgentProfile] = {
            aid: all_agents[aid] for aid in agent_ids
            if aid in all_agents and all_agents[aid].enabled
        }
        if not agents:
            yield self._evt("error", content="没有可用的 agent，请检查配置")
            return

        discussion_mode = getattr(room, "discussion_mode", "panel")
        moderator = ModeratorAgent(agents)
        goal_tracker = GoalTracker(room.goal or "")

        # Load existing messages into in-memory history
        existing = list(self.db.exec(
            select(Message).where(Message.room_id == self.room_id)
            .order_by(Message.seq.asc())
        ))
        history: list[dict] = []
        for m in existing:
            history.append({
                "agent_id": m.agent_id, "agent_name": m.agent_name,
                "message_type": m.message_type, "content": m.content,
            })
        self._seq = len(existing)

        # Update room status to running
        room.status = "running"
        room.updated_at = datetime.utcnow()
        self.db.add(room)
        self.db.commit()

        yield self._evt("system", content=f"讨论开始：{room.topic}")
        yield self._evt("system", content=f"目标：{room.goal or '自由讨论，碰撞想法'}")
        yield self._evt("system", content=f"讨论模式：{_mode_label(discussion_mode)}")
        yield self._evt("agents", agents={aid: a.to_dict() for aid, a in agents.items()})

        # Send current progress snapshot
        progress = goal_tracker.check(history)
        yield self._evt("goal_progress", **progress.to_dict())

        round_num = 0
        rr_index = 0  # round-robin cursor

        while round_num < max_rounds and not self._stop_event.is_set():
            # Respect time limit
            if (time.monotonic() - start_time) >= max_seconds:
                yield self._evt("system", content="⏱ 已达到时间上限，讨论结束")
                break

            # Pause: wait until resumed or stopped
            await self._pause_event.wait()
            if self._stop_event.is_set():
                break

            # Drain pending user injections
            user_inject_text = ""
            while True:
                try:
                    user_content, target_id = self._user_queue.get_nowait()
                    user_msg = self._save_msg("user", "你", "text", user_content, avatar="🧑")
                    history.append({"agent_id": "user", "agent_name": "你",
                                     "message_type": "text", "content": user_content})
                    yield self._evt("message", **self._msg_dict(user_msg))
                    user_inject_text += (
                        f"【用户刚才说】\n{user_content}\n"
                        "（可以回应，也可以继续自己的思路）\n\n"
                    )
                    # Schedule the targeted agent for the next turn
                    if target_id and target_id in agents:
                        self._pending_target = target_id
                except asyncio.QueueEmpty:
                    break

            # Select speaker(s) for this round
            # If user steered to a specific agent, honour it regardless of mode
            if self._pending_target and self._pending_target in agents:
                override = self._pending_target
                self._pending_target = ""
                speakers = [(override, agents[override])]
                yield self._evt("system",
                                content=f"🎯 用户指定 {agents[override].name} 发言")
            elif discussion_mode == "panel":
                speakers = list(agents.items())
            elif discussion_mode == "round_robin":
                ids = list(agents.keys())
                next_id = ids[rr_index % len(ids)]
                rr_index += 1
                speakers = [(next_id, agents[next_id])]
            else:  # moderated
                next_id = await asyncio.to_thread(
                    moderator.pick_next, history, room.goal or ""
                )
                if next_id == "SYNTHESIZE":
                    yield self._evt("system", content="🏁 主持人判断目标已达成，准备综合")
                    break
                if next_id not in agents:
                    next_id = list(agents.keys())[round_num % len(agents)]
                speakers = [(next_id, agents[next_id])]

            # Round separator (after round 0)
            if round_num > 0:
                yield self._evt("round_start", round=round_num + 1)

            history_text = self._render_history(history)

            if discussion_mode == "panel":
                # All speakers in parallel
                for aid, agent in speakers:
                    yield self._evt("thinking", agent_id=aid,
                                    agent_name=agent.name, avatar=agent.avatar)

                async def call_one_panel(a_id: str, a: AgentProfile) -> tuple:
                    prompt = _AGENT_PROMPT.format(
                        system_prompt=a.system_prompt(), topic=room.topic,
                        goal=room.goal or "自由讨论", history=history_text,
                        user_inject=user_inject_text,
                    )
                    try:
                        res = await asyncio.to_thread(self._call_adapter, a, prompt)
                        return (a_id, a, res, None)
                    except Exception as exc:
                        return (a_id, a, None, exc)

                tasks = [asyncio.create_task(call_one_panel(aid, ag))
                         for aid, ag in speakers]
                round_responses: list[dict] = []
                for fut in asyncio.as_completed(tasks):
                    a_id, agent, result, err = await fut
                    yield self._evt("thinking_done", agent_id=a_id)
                    if err:
                        err_msg = self._save_msg(a_id, agent.name, "system",
                                                 f"（调用失败：{err}）", avatar=agent.avatar)
                        yield self._evt("message", **self._msg_dict(err_msg))
                    else:
                        text = (result.text or "").strip()
                        if text:
                            msg = self._save_msg(a_id, agent.name, "text", text,
                                                 avatar=agent.avatar)
                            round_responses.append({
                                "agent_id": a_id, "agent_name": agent.name,
                                "message_type": "text", "content": text,
                            })
                            yield self._evt("message", **self._msg_dict(msg))
            else:
                # Single speaker (moderated or round_robin)
                a_id, agent = speakers[0]
                yield self._evt("thinking", agent_id=a_id,
                                agent_name=agent.name, avatar=agent.avatar)
                if discussion_mode == "moderated":
                    yield self._evt("system",
                                    content=f"🎙 {agent.name} 发言中…")

                prompt = _AGENT_PROMPT.format(
                    system_prompt=agent.system_prompt(), topic=room.topic,
                    goal=room.goal or "自由讨论", history=history_text,
                    user_inject=user_inject_text,
                )
                round_responses = []
                try:
                    result = await asyncio.to_thread(self._call_adapter, agent, prompt)
                    yield self._evt("thinking_done", agent_id=a_id)
                    text = (result.text or "").strip()
                    if text:
                        msg = self._save_msg(a_id, agent.name, "text", text,
                                             avatar=agent.avatar)
                        round_responses.append({
                            "agent_id": a_id, "agent_name": agent.name,
                            "message_type": "text", "content": text,
                        })
                        yield self._evt("message", **self._msg_dict(msg))
                except Exception as exc:
                    yield self._evt("thinking_done", agent_id=a_id)
                    err_msg = self._save_msg(a_id, agent.name, "system",
                                             f"（调用失败：{exc}）", avatar=agent.avatar)
                    yield self._evt("message", **self._msg_dict(err_msg))

            history.extend(round_responses)

            progress = goal_tracker.check(history)
            yield self._evt("goal_progress", **progress.to_dict())
            if progress.achieved:
                yield self._evt("system", content="✅ 目标已达成！")
                break

            round_num += 1
            await asyncio.sleep(0)

        # ── Synthesize ────────────────────────────────────────
        yield self._evt("synthesize_start")
        artifact_content = await self._synthesize(room.topic, room.goal or "", history)
        artifact = Artifact(
            room_id=self.room_id,
            artifact_type="report",
            filename=f"room_{self.room_id}_summary.md",
            content=artifact_content,
        )
        self.db.add(artifact)

        room.status = "done"
        room.turn_count = round_num
        room.updated_at = datetime.utcnow()
        self.db.add(room)
        self.db.commit()
        self.db.refresh(artifact)

        yield self._evt("artifact",
                        artifact_id=artifact.id,
                        artifact_type="report",
                        filename=artifact.filename,
                        content=artifact_content)
        yield self._evt("done", turns=round_num)

    # ── Internals ─────────────────────────────────────────────

    def _call_adapter(self, agent: AgentProfile, prompt: str) -> AgentResult:
        config = get_config()
        adapter_config = config.get("adapters", {}).get(agent.backend, {})
        return create_adapter(agent.backend, adapter_config).run(prompt)

    async def _synthesize(self, topic: str, goal: str, history: list[dict]) -> str:
        return await synthesize_from_history(topic, goal, history)

    def _render_history(self, history: list[dict]) -> str:
        lines = []
        for m in history:
            if m.get("message_type") not in ("text", None):
                continue
            name = m.get("agent_name", m.get("agent_id", "?"))
            content = m.get("content", "")
            if content:
                lines.append(f"{name}：{content}")
        return "\n".join(lines) if lines else "（尚无发言）"

    def _save_msg(self, agent_id: str, agent_name: str, message_type: str,
                  content: str, *, avatar: str = "") -> Message:
        self._seq += 1
        msg = Message(
            room_id=self.room_id, seq=self._seq,
            agent_id=agent_id, agent_name=agent_name,
            agent_avatar=avatar, message_type=message_type,
            content=content,
        )
        self.db.add(msg)
        self.db.commit()
        self.db.refresh(msg)
        return msg

    def _msg_dict(self, msg: Message) -> dict:
        return {
            "id": msg.id, "agent_id": msg.agent_id,
            "agent_name": msg.agent_name, "avatar": msg.agent_avatar,
            "content": msg.content, "message_type": msg.message_type,
        }

    def _evt(self, event_type: str, **kwargs) -> dict:
        return {"type": event_type, **kwargs}


# ── Standalone synthesize (used by /synthesize endpoint) ──────

async def synthesize_from_history(topic: str, goal: str, history: list[dict]) -> str:
    history_text = "\n".join(
        f"{m.get('agent_name', m.get('agent_id', '?'))}：{m.get('content', '')}"
        for m in history
        if m.get("message_type", "text") == "text" and m.get("content", "")
    ) or "（尚无发言）"

    prompt = _SYNTHESIZE_PROMPT.format(
        topic=topic, goal=goal or "自由讨论", history=history_text
    )
    config = get_config()
    backend = config.get("synthesizer", {}).get("backend", "mock")
    adapter_config = config.get("adapters", {}).get(backend, {})
    try:
        result = await asyncio.to_thread(
            create_adapter(backend, adapter_config).run, prompt
        )
        return result.text.strip() or "（综合生成为空）"
    except Exception as exc:
        return f"（综合失败：{exc}）"


def _mode_label(mode: str) -> str:
    return {"round_robin": "轮流发言", "moderated": "主持人调度", "panel": "圆桌并行"}.get(mode, mode)


# ── Session registry ──────────────────────────────────────────

_sessions: dict[int, RoomSession] = {}


def get_or_create_session(room_id: int, db: Session) -> RoomSession:
    if room_id not in _sessions or _sessions[room_id]._stop_event.is_set():
        _sessions[room_id] = RoomSession(room_id, db)
    return _sessions[room_id]


def get_session_if_exists(room_id: int) -> RoomSession | None:
    return _sessions.get(room_id)


def remove_session(room_id: int) -> None:
    _sessions.pop(room_id, None)
