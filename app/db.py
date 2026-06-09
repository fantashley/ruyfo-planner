"""SQLite engine + session helpers."""

from __future__ import annotations

import os

from sqlmodel import Session, SQLModel, create_engine

DB_PATH = os.environ.get("RUYFO_DB", "ruyfo.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)
