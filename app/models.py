from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Room(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    topic: str
    goal: str = ""
    agent_ids: str = ""           # comma-separated agent id list
    status: str = "ready"         # draft|ready|running|paused|synthesizing|done|failed
    discussion_mode: str = "moderated"  # round_robin|moderated|panel
    turn_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    room_id: int = Field(index=True)
    seq: int = 0
    agent_id: str
    agent_name: str
    agent_avatar: str = ""
    message_type: str = "text"    # text | system | artifact
    content: str = ""
    tool_name: Optional[str] = None
    tool_input: Optional[str] = None
    tool_output: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Artifact(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    room_id: int = Field(index=True)
    artifact_type: str = "text"   # text | code | report | data
    filename: str = ""
    content: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
