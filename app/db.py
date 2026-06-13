"""SQLite engine + session helpers."""

from __future__ import annotations

import os

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

DB_PATH = os.environ.get("RUYFO_DB", "ruyfo.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})

_SQLITE_SCHEMA_ADDITIONS = {
    "event": {
        "has_sag": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "participant": {
        "loaner_for": "VARCHAR NOT NULL DEFAULT ''",
        "bag_count": "INTEGER NOT NULL DEFAULT 0",
        "share_household_car": "BOOLEAN NOT NULL DEFAULT 0",
        "sag_extra_miles": "INTEGER NOT NULL DEFAULT 20",
    },
}


def _add_missing_sqlite_columns(db_engine) -> None:
    """Backfill columns added after early local SQLite databases were created."""
    if db_engine.dialect.name != "sqlite":
        return

    inspector = inspect(db_engine)
    existing_tables = set(inspector.get_table_names())
    with db_engine.begin() as conn:
        for table, columns in _SQLITE_SCHEMA_ADDITIONS.items():
            if table not in existing_tables:
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing_columns:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _add_missing_sqlite_columns(engine)


def get_session() -> Session:
    return Session(engine)
