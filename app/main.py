from __future__ import annotations

import json
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

from app.agent_loader import clear_agent_cache, load_all_agents
from app.config_loader import ROOT_DIR, get_config
from app.database import get_session, init_db
from app.models import Artifact, Message, Room
from app.room_session import (
    RoomSession,
    _sessions,
    get_session_if_exists,
    remove_session,
)

app = FastAPI(title="ExpertRoom")

_static = ROOT_DIR / "static"
app.mount("/static", StaticFiles(directory=_static), name="static")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ── 静态页面 ──────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return (_static / "index.html").read_text(encoding="utf-8")


# ── 健康检查 ──────────────────────────────────────────────────

@app.get("/api/health")
def health(db: Session = Depends(get_session)):
    try:
        db.exec(select(Room).limit(1)).first()
        db_ok = True
    except Exception:
        db_ok = False
    agents = load_all_agents()
    return {
        "status": "ok" if db_ok else "error",
        "agents_loaded": len(agents),
    }


# ── 配置 / Agent ──────────────────────────────────────────────

@app.get("/api/config")
def read_config():
    config = get_config()
    agents = load_all_agents()
    return {
        "app": config.get("app", {}),
        "agents": {aid: a.to_dict() for aid, a in agents.items() if a.enabled},
    }


@app.get("/api/agents")
def list_agents():
    agents = load_all_agents()
    return {aid: a.to_dict() for aid, a in agents.items() if a.enabled}


# ── 房间 CRUD ──────────────────────────────────────────────────

class CreateRoomRequest(BaseModel):
    topic: str
    goal: str = ""
    agent_ids: list[str] = []


@app.post("/api/rooms", status_code=201)
def create_room(payload: CreateRoomRequest, db: Session = Depends(get_session)):
    topic = payload.topic.strip()
    if not topic:
        raise HTTPException(400, "topic cannot be empty")

    all_agents = load_all_agents()
    config = get_config()
    default_ids = config.get("app", {}).get("default_agents", [])
    agent_ids = payload.agent_ids or default_ids
    valid_ids = [aid for aid in agent_ids if aid in all_agents and all_agents[aid].enabled]
    if not valid_ids:
        raise HTTPException(400, "no valid agents selected")

    room = Room(
        topic=topic,
        goal=payload.goal.strip(),
        agent_ids=",".join(valid_ids),
        status="running",
    )
    db.add(room)
    db.commit()
    db.refresh(room)
    return {"id": room.id, "topic": room.topic, "goal": room.goal,
            "agent_ids": valid_ids, "status": room.status}


@app.get("/api/rooms")
def list_rooms(db: Session = Depends(get_session)):
    rooms = list(db.exec(select(Room).order_by(Room.created_at.desc()).limit(20)))
    return [{"id": r.id, "topic": r.topic, "goal": r.goal,
             "status": r.status, "turn_count": r.turn_count,
             "created_at": r.created_at.isoformat()} for r in rooms]


@app.get("/api/rooms/{room_id}")
def get_room(room_id: int, db: Session = Depends(get_session)):
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(404, "room not found")
    return {"id": room.id, "topic": room.topic, "goal": room.goal,
            "agent_ids": room.agent_ids.split(","),
            "status": room.status, "turn_count": room.turn_count}


@app.get("/api/rooms/{room_id}/messages")
def get_messages(room_id: int, db: Session = Depends(get_session)):
    msgs = list(db.exec(
        select(Message).where(Message.room_id == room_id).order_by(Message.seq.asc())
    ))
    return [{"id": m.id, "agent_id": m.agent_id, "agent_name": m.agent_name,
             "avatar": m.agent_avatar, "message_type": m.message_type,
             "content": m.content, "tool_name": m.tool_name,
             "created_at": m.created_at.isoformat()} for m in msgs]


@app.get("/api/rooms/{room_id}/artifacts")
def get_artifacts(room_id: int, db: Session = Depends(get_session)):
    arts = list(db.exec(
        select(Artifact).where(Artifact.room_id == room_id)
    ))
    return [{"id": a.id, "artifact_type": a.artifact_type,
             "filename": a.filename, "content": a.content} for a in arts]


# ── SSE 讨论流 ────────────────────────────────────────────────

@app.get("/api/rooms/{room_id}/stream")
async def stream_room(room_id: int, db: Session = Depends(get_session)):
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(404, "room not found")
    if room.status == "done":
        raise HTTPException(400, "room already done")

    session = RoomSession(room_id, db)
    _sessions[room_id] = session

    async def generator():
        try:
            async for event in session.run():
                yield {"data": json.dumps(event, ensure_ascii=False)}
        except Exception as exc:
            yield {"data": json.dumps({"type": "error", "content": str(exc)},
                                       ensure_ascii=False)}
        finally:
            remove_session(room_id)

    return EventSourceResponse(generator())


# ── 用户插话 ──────────────────────────────────────────────────

class InjectRequest(BaseModel):
    content: str


@app.post("/api/rooms/{room_id}/inject")
async def inject_message(room_id: int, payload: InjectRequest,
                          db: Session = Depends(get_session)):
    content = payload.content.strip()
    if not content:
        raise HTTPException(400, "content cannot be empty")
    session = get_session_if_exists(room_id)
    if session is None:
        raise HTTPException(404, "no active session for this room — start stream first")
    await session.inject_user_message(content)
    return {"ok": True}


# ── 停止讨论 ──────────────────────────────────────────────────

@app.post("/api/rooms/{room_id}/stop")
async def stop_room(room_id: int, db: Session = Depends(get_session)):
    session = get_session_if_exists(room_id)
    if session:
        session.stop()
    room = db.get(Room, room_id)
    if room:
        room.status = "done"
        db.add(room)
        db.commit()
    return {"ok": True}


# ── Agent CRUD ────────────────────────────────────────────────

_BACKENDS = ["hermes", "openclaw", "codex", "claude", "mock"]
_AGENTS_DIR = ROOT_DIR / "agents"


class AgentPayload(BaseModel):
    name: str
    avatar: str = "🤖"
    backend: str = "hermes"
    identity: str = ""
    expertise: str = ""
    traits: list[str] = []
    tone: str = ""
    goals: list[str] = []
    long_term: list[str] = []
    enabled: bool = True


def _agent_to_dict_full(agent_id: str) -> dict:
    """加载 YAML 原始数据，用于编辑器回显。"""
    import yaml
    path = _AGENTS_DIR / f"{agent_id}.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def _write_agent_yaml(agent_id: str, payload: AgentPayload) -> None:
    import yaml
    data = {
        "id": agent_id,
        "name": payload.name,
        "avatar": payload.avatar,
        "backend": payload.backend,
        "enabled": payload.enabled,
        "identity": payload.identity,
        "expertise": payload.expertise,
        "personality": {
            "traits": payload.traits,
        },
        "speaking_style": {
            "tone": payload.tone,
        },
        "goals": {
            "public": payload.goals,
        },
        "memory": {
            "long_term": payload.long_term,
        },
    }
    path = _AGENTS_DIR / f"{agent_id}.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    clear_agent_cache()


@app.get("/api/agents/{agent_id}/detail")
def get_agent_detail(agent_id: str):
    data = _agent_to_dict_full(agent_id)
    if not data:
        raise HTTPException(404, "agent not found")
    return data


@app.post("/api/agents", status_code=201)
def create_agent(agent_id: str, payload: AgentPayload):
    if not agent_id.isidentifier():
        raise HTTPException(400, "agent_id must be a valid identifier (letters/digits/underscore)")
    path = _AGENTS_DIR / f"{agent_id}.yaml"
    if path.exists():
        raise HTTPException(409, f"agent '{agent_id}' already exists")
    if payload.backend not in _BACKENDS:
        raise HTTPException(400, f"backend must be one of {_BACKENDS}")
    _write_agent_yaml(agent_id, payload)
    return {"id": agent_id, "ok": True}


@app.put("/api/agents/{agent_id}")
def update_agent(agent_id: str, payload: AgentPayload):
    path = _AGENTS_DIR / f"{agent_id}.yaml"
    if not path.exists():
        raise HTTPException(404, "agent not found")
    if payload.backend not in _BACKENDS:
        raise HTTPException(400, f"backend must be one of {_BACKENDS}")
    _write_agent_yaml(agent_id, payload)
    return {"id": agent_id, "ok": True}


@app.delete("/api/agents/{agent_id}")
def delete_agent(agent_id: str):
    path = _AGENTS_DIR / f"{agent_id}.yaml"
    if not path.exists():
        raise HTTPException(404, "agent not found")
    path.unlink()
    clear_agent_cache()
    return {"ok": True}


# ── Artifact 下载 ─────────────────────────────────────────────

@app.get("/api/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: int, db: Session = Depends(get_session)):
    art = db.get(Artifact, artifact_id)
    if not art:
        raise HTTPException(404, "artifact not found")
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        art.content,
        headers={"Content-Disposition": f'attachment; filename="{art.filename}"'},
        media_type="text/markdown; charset=utf-8",
    )
