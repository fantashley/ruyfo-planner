"""Tests for organizer-link email recovery."""

from sqlmodel import SQLModel, Session, create_engine, select
from starlette.requests import Request

from app import main
from app.models import Event


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "http_version": "1.1",
            "scheme": "https",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(b"host", b"ruyfo.example")],
            "server": ("ruyfo.example", 443),
            "client": ("test", 1),
        }
    )


def _setup(monkeypatch):
    """Point the app at a fresh in-memory DB and capture sent mail."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.db.engine", engine)

    sent: list[dict] = []
    monkeypatch.setattr(main.mailer, "is_configured", lambda: True)
    monkeypatch.setattr(
        main.mailer,
        "send",
        lambda to, subject, body, kind="": sent.append({"to": to, "subject": subject, "body": body}) or True,
    )
    return engine, sent


def test_normalize_email():
    assert main._normalize_email("  Foo@Bar.COM ") == "foo@bar.com"
    assert main._normalize_email("not-an-email") == ""
    assert main._normalize_email("missing@tld") == ""
    assert main._normalize_email("") == ""


def test_create_event_sends_no_email(monkeypatch):
    engine, sent = _setup(monkeypatch)

    resp = main.create_event(
        _request(), name="Fall Ride", route_key="faribault_mankato",
    )

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/e/")
    assert resp.headers["location"].endswith("?created=1")

    with Session(engine) as s:
        ev = s.exec(select(Event)).one()
        assert ev.organizer_email == ""  # nothing on file until confirmed
        assert ev.pending_email == ""
    assert sent == []  # creation never mails


def test_request_recovery_email_stores_pending_and_sends_benign_confirmation(monkeypatch):
    engine, sent = _setup(monkeypatch)
    main.create_event(_request(), name="SecretEvent", route_key="faribault_mankato")
    with Session(engine) as s:
        ev = s.exec(select(Event)).one()
        token, org_token = ev.organizer_token, ev.organizer_token

    resp = main.request_recovery_email(_request(), token=token, email="Me@X.com")
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("?email_pending=1")

    with Session(engine) as s:
        ev = s.exec(select(Event)).one()
        assert ev.pending_email == "me@x.com"   # pending, normalized
        assert ev.organizer_email == ""          # NOT yet usable for recovery
        assert ev.email_verify_token            # a confirm token was minted

    # exactly one mail, to the typed address, with a confirm link and NO payload
    assert len(sent) == 1
    body = sent[0]["body"]
    assert sent[0]["to"] == "me@x.com"
    assert "/verify-email/" in body
    assert "SecretEvent" not in body            # no event name
    assert org_token not in body                # no organizer link


def test_confirm_recovery_email_promotes_pending(monkeypatch):
    engine, _ = _setup(monkeypatch)
    main.create_event(_request(), name="Ride", route_key="faribault_mankato")
    with Session(engine) as s:
        token = s.exec(select(Event)).one().organizer_token
    main.request_recovery_email(_request(), token=token, email="me@x.com")
    with Session(engine) as s:
        verify_token = s.exec(select(Event)).one().email_verify_token

    resp = main.confirm_recovery_email(_request(), verify_token=verify_token)
    assert resp.status_code == 200
    assert b"confirmed" in resp.body

    with Session(engine) as s:
        ev = s.exec(select(Event)).one()
        assert ev.organizer_email == "me@x.com"  # promoted to confirmed
        assert ev.pending_email == ""
        assert ev.email_verify_token == ""


def test_confirm_with_bad_token_changes_nothing(monkeypatch):
    engine, _ = _setup(monkeypatch)
    main.create_event(_request(), name="Ride", route_key="faribault_mankato")
    with Session(engine) as s:
        token = s.exec(select(Event)).one().organizer_token
    main.request_recovery_email(_request(), token=token, email="me@x.com")

    resp = main.confirm_recovery_email(_request(), verify_token="not-a-real-token")
    assert resp.status_code == 200
    assert b"expired" in resp.body or b"already used" in resp.body

    with Session(engine) as s:
        ev = s.exec(select(Event)).one()
        assert ev.organizer_email == ""   # still only pending
        assert ev.pending_email == "me@x.com"


def test_recover_only_matches_confirmed_not_pending(monkeypatch):
    engine, sent = _setup(monkeypatch)
    with Session(engine) as s:
        s.add(Event(name="Confirmed", route_key="faribault_mankato",
                    organizer_email="me@x.com"))
        s.add(Event(name="OnlyPending", route_key="faribault_mankato",
                    pending_email="me@x.com", email_verify_token="tok"))
        s.commit()

    resp = main.recover_links(_request(), email="me@x.com")
    assert resp.headers["location"] == "/?recovered=1"
    assert len(sent) == 1
    body = sent[0]["body"]
    assert "Confirmed" in body
    assert "OnlyPending" not in body  # unconfirmed address never matched


def test_recover_emails_all_matching_events(monkeypatch):
    engine, sent = _setup(monkeypatch)
    with Session(engine) as s:
        s.add(Event(name="Spring", route_key="faribault_mankato", organizer_email="me@x.com"))
        s.add(Event(name="Fall", route_key="faribault_mankato", organizer_email="me@x.com"))
        s.add(Event(name="Other", route_key="faribault_mankato", organizer_email="someone@y.com"))
        s.commit()

    resp = main.recover_links(_request(), email="ME@x.com")

    assert resp.status_code == 303
    assert resp.headers["location"] == "/?recovered=1"
    assert len(sent) == 1
    body = sent[0]["body"]
    assert sent[0]["to"] == "me@x.com"
    assert "Spring" in body and "Fall" in body
    assert "Other" not in body


def test_recover_unknown_email_is_neutral_and_silent(monkeypatch):
    engine, sent = _setup(monkeypatch)
    with Session(engine) as s:
        s.add(Event(name="Spring", route_key="faribault_mankato", organizer_email="me@x.com"))
        s.commit()

    resp = main.recover_links(_request(), email="stranger@nowhere.com")

    # Same redirect as a hit — no enumeration signal — and no mail sent.
    assert resp.headers["location"] == "/?recovered=1"
    assert sent == []
