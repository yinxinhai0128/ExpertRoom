from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

from app.agent_loader import clear_agent_cache, load_all_agents
from app.config_loader import ROOT_DIR, get_config
from app.database import get_session, init_db
from app.goal_tracker import GoalTracker
from app.models import Artifact, Message, Room
from app.room_session import (
    RoomSession,
    _sessions,
    get_session_if_exists,
    remove_session,
    synthesize_from_history,
)

app = FastAPI(title="ExpertRoom")

_static = ROOT_DIR / "static"
app.mount("/static", StaticFiles(directory=_static), name="static")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ── Static ────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return (_static / "index.html").read_text(encoding="utf-8")


# ── Health ────────────────────────────────────────────────────

@app.get("/api/health")
def health(db: Session = Depends(get_session)):
    try:
        db.exec(select(Room).limit(1)).first()
        db_ok = True
    except Exception:
        db_ok = False
    agents = load_all_agents()
    return {"status": "ok" if db_ok else "error", "agents_loaded": len(agents)}


@app.get("/api/adapters/health")
def adapters_health():
    """Return availability info for each adapter backend."""
    config = get_config()
    results: dict = {}

    # mock is always available
    results["mock"] = {"available": True, "reason": "always available", "command": None}

    # CLI-based adapters: check if command is on PATH
    cli_backends = {
        "claude":   config.get("adapters", {}).get("claude",   {}).get("command", "claude.CMD"),
        "codex":    config.get("adapters", {}).get("codex",    {}).get("command", "codex"),
        "hermes":   config.get("adapters", {}).get("hermes",   {}).get("command", "hermes.exe"),
        "openclaw": None,  # openclaw uses WSL, checked separately
    }

    for backend, cmd in cli_backends.items():
        if backend == "openclaw":
            # openclaw needs wsl.exe + the configured distro
            wsl = shutil.which("wsl") or shutil.which("wsl.exe")
            distro = config.get("adapters", {}).get("openclaw", {}).get("distro", "")
            if wsl and distro:
                results[backend] = {"available": True, "reason": "wsl found", "command": wsl}
            else:
                reason = "wsl not found" if not wsl else f"distro not configured"
                results[backend] = {"available": False, "reason": reason, "command": wsl}
        else:
            found = shutil.which(cmd) if cmd else None
            results[backend] = {
                "available": bool(found),
                "reason": "ok" if found else f"command not found: {cmd}",
                "command": found,
            }

    return results


# ── Config / Agents ───────────────────────────────────────────

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


# ── Room CRUD ─────────────────────────────────────────────────

class CreateRoomRequest(BaseModel):
    topic: str
    goal: str = ""
    agent_ids: list[str] = []
    discussion_mode: str = "moderated"


@app.post("/api/rooms", status_code=201)
def create_room(payload: CreateRoomRequest, db: Session = Depends(get_session)):
    topic = payload.topic.strip()
    if not topic:
        raise HTTPException(400, "topic cannot be empty")

    mode = payload.discussion_mode
    if mode not in ("round_robin", "moderated", "panel"):
        raise HTTPException(400, "discussion_mode must be round_robin, moderated, or panel")

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
        status="ready",
        discussion_mode=mode,
    )
    db.add(room)
    db.commit()
    db.refresh(room)
    return _room_dict(room)


@app.get("/api/rooms")
def list_rooms(db: Session = Depends(get_session)):
    rooms = list(db.exec(select(Room).order_by(Room.created_at.desc()).limit(20)))
    return [_room_dict(r) for r in rooms]


@app.get("/api/rooms/{room_id}")
def get_room(room_id: int, db: Session = Depends(get_session)):
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(404, "room not found")
    return _room_dict(room)


def _room_dict(room: Room) -> dict:
    return {
        "id": room.id,
        "topic": room.topic,
        "goal": room.goal,
        "agent_ids": [a for a in room.agent_ids.split(",") if a],
        "status": room.status,
        "discussion_mode": getattr(room, "discussion_mode", "panel"),
        "turn_count": room.turn_count,
        "created_at": room.created_at.isoformat(),
        "active_session": room.id in _sessions,
    }


@app.get("/api/rooms/{room_id}/messages")
def get_messages(room_id: int, db: Session = Depends(get_session)):
    msgs = list(db.exec(
        select(Message).where(Message.room_id == room_id).order_by(Message.seq.asc())
    ))
    return [{"id": m.id, "agent_id": m.agent_id, "agent_name": m.agent_name,
             "avatar": m.agent_avatar, "message_type": m.message_type,
             "content": m.content, "created_at": m.created_at.isoformat()} for m in msgs]


@app.get("/api/rooms/{room_id}/artifacts")
def get_artifacts(room_id: int, db: Session = Depends(get_session)):
    arts = list(db.exec(select(Artifact).where(Artifact.room_id == room_id)))
    return [{"id": a.id, "artifact_type": a.artifact_type,
             "filename": a.filename, "content": a.content} for a in arts]


@app.get("/api/rooms/{room_id}/progress")
def get_progress(room_id: int, db: Session = Depends(get_session)):
    """Compute progress from stored messages — safe to call without an active session."""
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(404, "room not found")
    msgs = list(db.exec(
        select(Message).where(Message.room_id == room_id).order_by(Message.seq.asc())
    ))
    history = [
        {"agent_id": m.agent_id, "agent_name": m.agent_name,
         "message_type": m.message_type, "content": m.content}
        for m in msgs
    ]
    tracker = GoalTracker(room.goal or "")
    progress = tracker.check(history)
    result = progress.to_dict()
    # Mark summary_ready if a report artifact exists
    has_report = db.exec(
        select(Artifact).where(
            Artifact.room_id == room_id,
            Artifact.artifact_type == "report",
        )
    ).first() is not None
    result["checklist"]["summary_ready"] = has_report
    return result


# ── Room Lifecycle ────────────────────────────────────────────

# Statuses from which a new SSE stream can be opened
_STARTABLE = {"ready", "running", "paused", "stopped"}
_ACTIVE    = {"running", "paused", "stopped"}


@app.post("/api/rooms/{room_id}/start")
def start_room(room_id: int, db: Session = Depends(get_session)):
    """Validate that a room can be streamed. Client connects SSE to stream_url next."""
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(404, "room not found")
    if room.status not in _STARTABLE:
        raise HTTPException(400, f"cannot start room with status '{room.status}'")
    return {"ok": True, "stream_url": f"/api/rooms/{room_id}/stream"}


@app.post("/api/rooms/{room_id}/pause")
async def pause_room(room_id: int, db: Session = Depends(get_session)):
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(404, "room not found")
    session = get_session_if_exists(room_id)
    if session:
        session.pause()
    room.status = "paused"
    room.updated_at = datetime.utcnow()
    db.add(room)
    db.commit()
    return {"ok": True}


@app.post("/api/rooms/{room_id}/resume")
async def resume_room(room_id: int, db: Session = Depends(get_session)):
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(404, "room not found")
    if room.status not in ("paused",):
        raise HTTPException(400, f"room is not paused (status: {room.status})")
    room.status = "running"
    room.updated_at = datetime.utcnow()
    db.add(room)
    db.commit()
    session = get_session_if_exists(room_id)
    if session:
        session.resume()
    return {"ok": True, "stream_url": f"/api/rooms/{room_id}/stream"}


@app.post("/api/rooms/{room_id}/stop")
async def stop_room(room_id: int, db: Session = Depends(get_session)):
    """Stop active generation. Room moves to 'stopped' so /synthesize can still be called."""
    session = get_session_if_exists(room_id)
    if session:
        session.stop()
        remove_session(room_id)  # P0: evict immediately so _cancelled check takes effect
    room = db.get(Room, room_id)
    if room:
        room.status = "stopped"
        room.updated_at = datetime.utcnow()
        db.add(room)
        db.commit()
    return {"ok": True}


@app.post("/api/rooms/{room_id}/synthesize")
async def synthesize_room(room_id: int, db: Session = Depends(get_session)):
    """Generate (or regenerate) the summary artifact from stored messages."""
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(404, "room not found")

    # P1: freeze — stop any active session before reading messages
    active = get_session_if_exists(room_id)
    if active:
        active.stop()
        remove_session(room_id)
        await asyncio.sleep(1)  # grace period for in-flight adapter threads

    # Read a stable snapshot from DB
    msgs = list(db.exec(
        select(Message).where(Message.room_id == room_id).order_by(Message.seq.asc())
    ))
    history = [
        {"agent_id": m.agent_id, "agent_name": m.agent_name,
         "message_type": m.message_type, "content": m.content}
        for m in msgs
    ]

    content, is_mock = await synthesize_from_history(room.topic, room.goal or "", history)

    # upsert artifact
    existing = db.exec(
        select(Artifact).where(
            Artifact.room_id == room_id,
            Artifact.artifact_type == "report"
        )
    ).first()
    if existing:
        existing.content = content
        db.add(existing)
        db.commit()
        db.refresh(existing)
        artifact = existing
    else:
        artifact = Artifact(
            room_id=room_id,
            artifact_type="report",
            filename=f"room_{room_id}_summary.md",
            content=content,
        )
        db.add(artifact)
        db.commit()
        db.refresh(artifact)

    room.status = "done"
    room.updated_at = datetime.utcnow()
    db.add(room)
    db.commit()

    return {"artifact_id": artifact.id, "content": content, "is_mock": is_mock}


# ── SSE Stream ────────────────────────────────────────────────

@app.get("/api/rooms/{room_id}/stream")
async def stream_room(room_id: int, db: Session = Depends(get_session)):
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(404, "room not found")
    if room.status not in _STARTABLE:
        raise HTTPException(400, f"room status '{room.status}' cannot be streamed")

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


# ── Inject ────────────────────────────────────────────────────

class InjectRequest(BaseModel):
    content: str
    target_agent_id: str = ""


@app.post("/api/rooms/{room_id}/inject")
async def inject_message(room_id: int, payload: InjectRequest,
                          db: Session = Depends(get_session)):
    content = payload.content.strip()
    if not content:
        raise HTTPException(400, "content cannot be empty")
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(404, "room not found")
    if room.status == "done":
        raise HTTPException(400, "room is already done")

    target = payload.target_agent_id.strip()
    # Prefix target into content so it's visible in history and readable by agents
    display_content = f"@{target} {content}" if target else content

    session = get_session_if_exists(room_id)
    if session:
        # Active session: let it save to DB and handle target scheduling
        await session.inject_user_message(display_content, target_agent_id=target)
    else:
        # No active session: save directly so message persists for next resume
        seq_result = db.exec(
            select(func.max(Message.seq)).where(Message.room_id == room_id)
        ).one()
        next_seq = (seq_result or 0) + 1
        db.add(Message(
            room_id=room_id, seq=next_seq,
            agent_id="user", agent_name="你", agent_avatar="🧑",
            message_type="text", content=display_content,
        ))
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
        "personality": {"traits": payload.traits},
        "speaking_style": {"tone": payload.tone},
        "goals": {"public": payload.goals},
        "memory": {"long_term": payload.long_term},
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
        raise HTTPException(400, "agent_id must be a valid identifier")
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


# ── Artifact Download ─────────────────────────────────────────

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
