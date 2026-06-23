"""Tests for operator alerts on event creation / recovery-email confirmation."""

from email.message import EmailMessage

from sqlmodel import SQLModel, Session, create_engine, select
from starlette.requests import Request

from app import mailcap, mailer
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
    """Fresh in-memory DB; capture operator alerts and ordinary mail separately."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.db.engine", engine)

    monkeypatch.setattr(main.mailer, "is_configured", lambda: True)
    monkeypatch.setattr(main.mailer, "send", lambda *a, **k: True)

    alerts: list[dict] = []
    monkeypatch.setattr(
        main.mailer,
        "send_alert",
        lambda subject, body, kind="alert": alerts.append(
            {"subject": subject, "body": body, "kind": kind}
        )
        or True,
    )
    return engine, alerts


def test_create_event_alerts_operator_with_no_email_yet(monkeypatch):
    _setup_engine, alerts = _setup(monkeypatch)

    main.create_event(_request(), name="Fall Ride", route_key="faribault_mankato")

    assert len(alerts) == 1
    assert alerts[0]["kind"] == "event_created"
    assert "Fall Ride" in alerts[0]["subject"]
    body = alerts[0]["body"]
    assert "Fall Ride" in body
    assert "Sakatah Trail" in body  # route name, not the raw key
    assert "(none)" in body  # no recovery email at creation by design


def test_confirm_recovery_email_alerts_operator_with_the_email(monkeypatch):
    engine, alerts = _setup(monkeypatch)

    main.create_event(_request(), name="Spring Ride", route_key="faribault_mankato")
    with Session(engine) as s:
        token = s.exec(select(Event)).one().organizer_token
    main.request_recovery_email(_request(), token=token, email="Me@X.com")
    with Session(engine) as s:
        verify_token = s.exec(select(Event)).one().email_verify_token

    alerts.clear()  # drop the creation alert; we only care about confirmation here
    main.confirm_recovery_email(_request(), verify_token=verify_token)

    assert len(alerts) == 1
    assert alerts[0]["kind"] == "email_confirmed"
    assert "Spring Ride" in alerts[0]["subject"]
    assert "me@x.com" in alerts[0]["body"]  # the confirmed address, normalized


def test_bad_confirm_token_does_not_alert(monkeypatch):
    engine, alerts = _setup(monkeypatch)
    main.create_event(_request(), name="Ride", route_key="faribault_mankato")
    with Session(engine) as s:
        token = s.exec(select(Event)).one().organizer_token
    main.request_recovery_email(_request(), token=token, email="me@x.com")

    alerts.clear()
    main.confirm_recovery_email(_request(), verify_token="not-a-real-token")

    assert alerts == []  # nothing was confirmed, so nothing to announce


# --------------------------------------------------------------------------- #
# mailer.send_alert itself: gated on config, and exempt from the per-recipient cap
# --------------------------------------------------------------------------- #


def _mail_setup(monkeypatch, *, alert_to="ops@ruyfo.example"):
    """In-memory DB for the mailcap audit table + a stubbed SMTP transport."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.db.engine", engine)

    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(mailer, "_alert_to", lambda: alert_to)

    delivered: list[EmailMessage] = []
    monkeypatch.setattr(mailer, "_deliver", lambda msg: delivered.append(msg) or True)
    return delivered


def test_send_alert_noop_without_alert_address(monkeypatch):
    delivered = _mail_setup(monkeypatch, alert_to="")
    assert mailer.alerts_enabled() is False
    assert mailer.send_alert("Subject", "Body") is False
    assert delivered == []


def test_send_alert_delivers_to_operator(monkeypatch):
    delivered = _mail_setup(monkeypatch)
    assert mailer.alerts_enabled() is True
    assert mailer.send_alert("Hi", "There") is True
    assert len(delivered) == 1
    assert delivered[0]["To"] == "ops@ruyfo.example"
    assert delivered[0]["Subject"] == "Hi"


def test_send_alert_skips_per_recipient_cap(monkeypatch):
    delivered = _mail_setup(monkeypatch)
    # Drive the per-recipient cap (default 5) well past its limit.
    for _ in range(8):
        assert mailer.send_alert("New event", "body") is True
    assert len(delivered) == 8


def test_send_alert_still_honors_global_cap(monkeypatch):
    delivered = _mail_setup(monkeypatch)
    monkeypatch.setenv("RUYFO_EMAIL_DAILY_CAP", "3")
    results = [mailer.send_alert("New event", "body") for _ in range(5)]
    assert results == [True, True, True, False, False]
    assert len(delivered) == 3


def test_allow_per_recipient_flag(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.db.engine", engine)
    monkeypatch.setenv("RUYFO_EMAIL_RECIPIENT_DAILY_CAP", "1")

    for _ in range(3):
        mailcap.record("ops@ruyfo.example", "alert")
    # over the per-recipient cap, but the global default (100) is fine
    assert mailcap.allow("ops@ruyfo.example") is False
    assert mailcap.allow("ops@ruyfo.example", per_recipient=False) is True
