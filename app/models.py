from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Room(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    topic: str
    goal: str = ""
    agent_ids: str = ""          # 逗号分隔的 agent id 列表
    status: str = "running"      # running | done | error
    turn_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    room_id: int = Field(index=True)
    seq: int = 0                 # 消息序号，便于前端排序
    agent_id: str                # agent id，用户为 "user"，系统为 "system"
    agent_name: str
    agent_avatar: str = ""
    message_type: str = "text"   # text | tool_call | tool_result | system | artifact
    content: str = ""
    tool_name: Optional[str] = None
    tool_input: Optional[str] = None   # JSON string
    tool_output: Optional[str] = None  # JSON string
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Artifact(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    room_id: int = Field(index=True)
    artifact_type: str = "text"  # text | code | skill | report | data
    filename: str = ""
    content: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
