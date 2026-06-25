from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, text

from app.models import Artifact, Message, Room  # noqa: F401 — ensure tables registered

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "expertroom.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_engine = create_engine(f"sqlite:///{_DB_PATH}", echo=False)


def init_db() -> None:
    SQLModel.metadata.create_all(_engine)
    _migrate()


def _migrate() -> None:
    """Apply additive schema migrations. Safe to run multiple times."""
    with _engine.connect() as conn:
        # Add discussion_mode column if missing (existing rows default to 'panel'
        # to preserve their original all-agents-parallel behavior)
        try:
            conn.execute(text(
                "ALTER TABLE room ADD COLUMN discussion_mode TEXT NOT NULL DEFAULT 'panel'"
            ))
            conn.commit()
        except Exception:
            pass  # column already exists


def get_session():
    with Session(_engine) as session:
        yield session
