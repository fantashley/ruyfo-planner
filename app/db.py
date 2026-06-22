"""SQLite engine + session helpers."""

from __future__ import annotations

import os
import secrets

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

DB_PATH = os.environ.get("RUYFO_DB", "ruyfo.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})

_SQLITE_SCHEMA_ADDITIONS = {
    "event": {
        "has_sag": "BOOLEAN NOT NULL DEFAULT 0",
        "organizer_token": "VARCHAR NOT NULL DEFAULT ''",
        "participant_token": "VARCHAR NOT NULL DEFAULT ''",
        "readonly_token": "VARCHAR NOT NULL DEFAULT ''",
        "organizer_email": "VARCHAR NOT NULL DEFAULT ''",
        # Nullable: SQLite rejects a CURRENT_TIMESTAMP default on ADD COLUMN,
        # and pre-existing rows have no known creation date.
        "created_at": "TIMESTAMP",
    },
    "participant": {
        "loaner_for": "VARCHAR NOT NULL DEFAULT ''",
        "bag_count": "INTEGER NOT NULL DEFAULT 0",
        "share_household_car": "BOOLEAN NOT NULL DEFAULT 0",
        "sag_extra_miles": "INTEGER NOT NULL DEFAULT 20",
        "joins_ride": "BOOLEAN NOT NULL DEFAULT 0",
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


def _backfill_event_tokens(db_engine) -> None:
    """Populate capability-link tokens for events from older SQLite databases."""
    if db_engine.dialect.name != "sqlite":
        return

    inspector = inspect(db_engine)
    if "event" not in set(inspector.get_table_names()):
        return

    columns = {col["name"] for col in inspector.get_columns("event")}
    token_columns = ("organizer_token", "participant_token", "readonly_token")
    if not set(token_columns).issubset(columns):
        return

    with db_engine.begin() as conn:
        event_ids = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT id FROM event "
                    "WHERE organizer_token = '' "
                    "OR participant_token = '' "
                    "OR readonly_token = ''"
                )
            )
        ]
        for event_id in event_ids:
            conn.execute(
                text(
                    "UPDATE event SET "
                    "organizer_token = CASE WHEN organizer_token = '' THEN :organizer ELSE organizer_token END, "
                    "participant_token = CASE WHEN participant_token = '' THEN :participant ELSE participant_token END, "
                    "readonly_token = CASE WHEN readonly_token = '' THEN :readonly ELSE readonly_token END "
                    "WHERE id = :event_id"
                ),
                {
                    "organizer": secrets.token_urlsafe(24),
                    "participant": secrets.token_urlsafe(24),
                    "readonly": secrets.token_urlsafe(24),
                    "event_id": event_id,
                },
            )


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _add_missing_sqlite_columns(engine)
    _backfill_event_tokens(engine)


def get_session() -> Session:
    return Session(engine)
