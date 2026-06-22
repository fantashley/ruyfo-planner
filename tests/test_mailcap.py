"""Tests for the outbound-email rate caps (app/mailcap.py)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app import mailcap
from app.mailcap import EmailSend


@pytest.fixture
def engine(monkeypatch):
    """Fresh in-memory DB wired into the session helper mailcap uses."""
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr("app.db.engine", eng)
    return eng


def test_allow_when_under_caps(engine):
    assert mailcap.allow("a@example.com") is True


def test_record_then_allow_blocks_after_recipient_cap(engine, monkeypatch):
    monkeypatch.setenv("RUYFO_EMAIL_RECIPIENT_DAILY_CAP", "2")
    monkeypatch.setenv("RUYFO_EMAIL_DAILY_CAP", "100")

    mailcap.record("a@example.com")
    assert mailcap.allow("a@example.com") is True  # 1 sent, cap 2
    mailcap.record("a@example.com")
    assert mailcap.allow("a@example.com") is False  # 2 sent, hit cap
    # a different recipient is unaffected by another address's count
    assert mailcap.allow("b@example.com") is True


def test_global_cap_blocks_all_recipients(engine, monkeypatch):
    monkeypatch.setenv("RUYFO_EMAIL_DAILY_CAP", "2")
    monkeypatch.setenv("RUYFO_EMAIL_RECIPIENT_DAILY_CAP", "100")

    mailcap.record("a@example.com")
    mailcap.record("b@example.com")
    # global cap reached regardless of who the next one is for
    assert mailcap.allow("c@example.com") is False


def test_old_sends_fall_out_of_the_window(engine, monkeypatch):
    monkeypatch.setenv("RUYFO_EMAIL_RECIPIENT_DAILY_CAP", "1")
    stale = datetime.now(timezone.utc) - timedelta(days=1, hours=1)
    with Session(engine) as s:
        s.add(EmailSend(recipient="a@example.com", sent_at=stale))
        s.commit()
    # the only prior send is >24h old, so it doesn't count
    assert mailcap.allow("a@example.com") is True


def test_non_positive_cap_falls_back_to_default(engine, monkeypatch):
    # a misconfigured 0 must not wedge all outbound mail
    monkeypatch.setenv("RUYFO_EMAIL_DAILY_CAP", "0")
    assert mailcap.allow("a@example.com") is True


def test_record_writes_recipient_and_kind(engine):
    mailcap.record("a@example.com", kind="organizer_link")
    with Session(engine) as s:
        row = s.exec(select(EmailSend)).one()
    assert row.recipient == "a@example.com"
    assert row.kind == "organizer_link"
