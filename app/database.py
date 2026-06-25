from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from app.models import Artifact, Message, Room  # noqa: F401 — ensure tables registered

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "expertroom.db"
_engine = create_engine(f"sqlite:///{_DB_PATH}", echo=False)


def init_db() -> None:
    SQLModel.metadata.create_all(_engine)


def get_session():
    with Session(_engine) as session:
        yield session
