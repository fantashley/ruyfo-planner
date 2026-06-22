"""Outbound-email rate caps — a backstop against the app being used as a spam
relay.

The real defense against bot-driven mail is at the edge (a challenge in front of
``POST /events`` and ``/recover``), but this origin-side cap limits the blast
radius if anything ever gets through: it bounds how much mail can leave in a
rolling 24h window, both globally and per recipient. Every send routes through
:func:`app.mailer.send`, which consults :func:`allow` before sending and calls
:func:`record` after a successful send.

State lives in its own tiny SQLite table (not in ``models`` — that would drag in
the solver/OR-Tools just to count emails). The window check and the insert are
not transactionally atomic, so under heavy concurrency the cap can overshoot by
a few; that's fine for a backstop whose job is to stop runaway volume, not to be
an exact quota.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
from sqlmodel import Field, SQLModel, select

from .db import get_session

log = logging.getLogger("ruyfo.mailcap")


class EmailSend(SQLModel, table=True):
    """One row per email that actually went out — the audit trail the caps read."""

    id: Optional[int] = Field(default=None, primary_key=True)
    recipient: str = Field(index=True)
    kind: str = ""  # free-form tag for the sender ("organizer_link", "recovery", …)
    sent_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), index=True
    )


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "").strip())
    except ValueError:
        return default
    # a non-positive cap would wedge all mail; treat it as "use the default"
    return value if value > 0 else default


def _daily_cap() -> int:
    """Max emails the whole app will send in any rolling 24h window."""
    return _int_env("RUYFO_EMAIL_DAILY_CAP", 100)


def _recipient_daily_cap() -> int:
    """Max emails to any single address in a rolling 24h window (anti-mailbomb)."""
    return _int_env("RUYFO_EMAIL_RECIPIENT_DAILY_CAP", 5)


def _count_since(s, cutoff: datetime, recipient: str | None = None) -> int:
    stmt = select(func.count(EmailSend.id)).where(EmailSend.sent_at >= cutoff)
    if recipient is not None:
        stmt = stmt.where(EmailSend.recipient == recipient)
    return s.exec(stmt).one()


def allow(to: str) -> bool:
    """Whether sending one more email to ``to`` stays under both caps."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    with get_session() as s:
        if _count_since(s, cutoff) >= _daily_cap():
            log.warning("global daily email cap reached; suppressing email to %s", to)
            return False
        if _count_since(s, cutoff, recipient=to) >= _recipient_daily_cap():
            log.warning("per-recipient daily email cap reached for %s", to)
            return False
    return True


def record(to: str, kind: str = "") -> None:
    """Log a successful send so it counts against the caps."""
    with get_session() as s:
        s.add(EmailSend(recipient=to, kind=kind))
        s.commit()
