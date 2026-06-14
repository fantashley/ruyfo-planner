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
        lambda to, subject, body: sent.append({"to": to, "subject": subject, "body": body}) or True,
    )
    return engine, sent


def test_normalize_email():
    assert main._normalize_email("  Foo@Bar.COM ") == "foo@bar.com"
    assert main._normalize_email("not-an-email") == ""
    assert main._normalize_email("missing@tld") == ""
    assert main._normalize_email("") == ""


def test_create_event_with_email_stores_and_sends(monkeypatch):
    engine, sent = _setup(monkeypatch)

    resp = main.create_event(
        _request(), name="Fall Ride", route_key="faribault_mankato",
        organizer_email="Rider@Example.com",
    )

    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith("/e/") and location.endswith("?created=1")

    with Session(engine) as s:
        ev = s.exec(select(Event)).one()
        assert ev.organizer_email == "rider@example.com"

    assert len(sent) == 1
    assert sent[0]["to"] == "rider@example.com"
    assert ev.organizer_token in sent[0]["body"]
    assert "https://ruyfo.example/e/" in sent[0]["body"]


def test_create_event_without_email_does_not_send(monkeypatch):
    engine, sent = _setup(monkeypatch)

    main.create_event(
        _request(), name="No Email", route_key="faribault_mankato", organizer_email="",
    )

    with Session(engine) as s:
        ev = s.exec(select(Event)).one()
        assert ev.organizer_email == ""
    assert sent == []


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
