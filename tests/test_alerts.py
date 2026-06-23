"""Tests for operator alerts: event-creation notice + outbound-mail monitoring.

The point of these alerts is to watch how much mail the public forms drive
through the SMTP setup (abuse detection), so the per-send alert fires whenever
an email actually goes out — not only on a successful confirmation click.
"""

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
    _engine, alerts = _setup(monkeypatch)

    main.create_event(_request(), name="Fall Ride", route_key="faribault_mankato")

    assert len(alerts) == 1
    body = alerts[0]["body"]
    assert "Fall Ride" in alerts[0]["subject"]
    assert "Fall Ride" in body
    assert "Sakatah Trail" in body  # route name, not the raw key
    assert "(none)" in body  # no recovery email at creation by design


def test_confirm_click_does_not_alert(monkeypatch):
    # Confirming a recovery email sends nothing through SMTP — it just promotes a
    # pending address — so it's not an event the volume monitor should announce.
    engine, alerts = _setup(monkeypatch)
    main.create_event(_request(), name="Ride", route_key="faribault_mankato")
    with Session(engine) as s:
        token = s.exec(select(Event)).one().organizer_token
    main.request_recovery_email(_request(), token=token, email="me@x.com")
    with Session(engine) as s:
        verify_token = s.exec(select(Event)).one().email_verify_token

    alerts.clear()
    main.confirm_recovery_email(_request(), verify_token=verify_token)
    assert alerts == []


# --------------------------------------------------------------------------- #
# mailer.send: every outbound message also pings the operator
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


def test_send_pings_operator_with_recipient_and_kind(monkeypatch):
    delivered = _mail_setup(monkeypatch)

    assert mailer.send("user@x.com", "Confirm your email", "link", kind="verify") is True

    tos = [m["To"] for m in delivered]
    assert tos.count("user@x.com") == 1
    assert tos.count("ops@ruyfo.example") == 1  # exactly one alert — no self-loop
    alert = next(m for m in delivered if m["To"] == "ops@ruyfo.example")
    assert "verify" in alert["Subject"]
    assert "user@x.com" in alert.get_content()


def test_send_without_alert_address_sends_only_user_mail(monkeypatch):
    delivered = _mail_setup(monkeypatch, alert_to="")

    assert mailer.send("user@x.com", "Hi", "body", kind="verify") is True
    assert [m["To"] for m in delivered] == ["user@x.com"]


def test_send_alert_reports_real_volume_excluding_alerts(monkeypatch):
    delivered = _mail_setup(monkeypatch)

    mailer.send("a@x.com", "s", "b", kind="verify")
    mailer.send("b@x.com", "s", "b", kind="recovery")

    # the second send's alert sees two real emails — its own alert mail (and the
    # first send's alert) are excluded from the count
    last_alert = [m for m in delivered if m["To"] == "ops@ruyfo.example"][-1]
    assert "last 24h: 2" in last_alert.get_content()


def test_alert_mail_does_not_count_against_real_volume(monkeypatch):
    _mail_setup(monkeypatch)
    mailer.send("a@x.com", "s", "b", kind="verify")  # 1 real + 1 alert recorded
    assert mailcap.sent_last_24h() == 2
    assert mailcap.sent_last_24h(exclude_kind="alert") == 1


# --------------------------------------------------------------------------- #
# send_alert itself: gated on config, exempt from the per-recipient cap
# --------------------------------------------------------------------------- #


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
    # Drive past the per-recipient cap (default 5); alerts to the fixed operator
    # address must not be throttled by the anti-mailbomb rule.
    for _ in range(8):
        assert mailer.send_alert("ping", "body") is True
    assert len(delivered) == 8


def test_send_alert_still_honors_global_cap(monkeypatch):
    delivered = _mail_setup(monkeypatch)
    monkeypatch.setenv("RUYFO_EMAIL_DAILY_CAP", "3")
    results = [mailer.send_alert("ping", "body") for _ in range(5)]
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
