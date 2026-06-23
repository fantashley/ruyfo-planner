"""Tests for the reCAPTCHA gate (app/recaptcha.py) and its use in the forms."""

import json

from sqlmodel import Session, SQLModel, create_engine, select
from starlette.requests import Request

from app import main, recaptcha
from app.models import Event


# --------------------------------------------------------------------------- #
# app/recaptcha.py
# --------------------------------------------------------------------------- #

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(payload):
    def _open(url, data=None, timeout=None):
        return _FakeResp(payload)
    return _open


def _configure(monkeypatch):
    monkeypatch.setenv("RUYFO_RECAPTCHA_SITE_KEY", "site-key")
    monkeypatch.setenv("RUYFO_RECAPTCHA_SECRET", "secret-key")
    monkeypatch.delenv("RUYFO_RECAPTCHA_SECRET_FILE", raising=False)


def _disable(monkeypatch):
    for var in ("RUYFO_RECAPTCHA_SITE_KEY", "RUYFO_RECAPTCHA_SECRET",
                "RUYFO_RECAPTCHA_SECRET_FILE"):
        monkeypatch.delenv(var, raising=False)


def test_disabled_when_unconfigured_allows(monkeypatch):
    _disable(monkeypatch)
    assert recaptcha.is_configured() is False
    assert recaptcha.verify("anything") is True  # gate off → allow


def test_configured_empty_token_is_rejected_without_network(monkeypatch):
    _configure(monkeypatch)

    def _boom(*a, **k):  # must not be called for an empty token
        raise AssertionError("should not contact Google for an empty token")

    monkeypatch.setattr("app.recaptcha.urllib.request.urlopen", _boom)
    assert recaptcha.verify("") is False


def test_configured_success(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        "app.recaptcha.urllib.request.urlopen", _fake_urlopen({"success": True})
    )
    assert recaptcha.verify("tok") is True


def test_configured_failure(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        "app.recaptcha.urllib.request.urlopen",
        _fake_urlopen({"success": False, "error-codes": ["invalid-input-response"]}),
    )
    assert recaptcha.verify("tok") is False


def test_fails_closed_on_network_error(monkeypatch):
    _configure(monkeypatch)

    def _raise(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr("app.recaptcha.urllib.request.urlopen", _raise)
    assert recaptcha.verify("tok") is False


def test_secret_from_file(monkeypatch, tmp_path):
    _disable(monkeypatch)
    secret_file = tmp_path / "recaptcha-secret"
    secret_file.write_text("file-secret\n")
    monkeypatch.setenv("RUYFO_RECAPTCHA_SITE_KEY", "site-key")
    monkeypatch.setenv("RUYFO_RECAPTCHA_SECRET_FILE", str(secret_file))
    assert recaptcha.is_configured() is True
    assert recaptcha._secret() == "file-secret"


# --------------------------------------------------------------------------- #
# Form handlers reject when the challenge fails
# --------------------------------------------------------------------------- #

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


def _in_memory_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.db.engine", engine)
    return engine


def test_create_event_blocked_when_captcha_missing(monkeypatch):
    _configure(monkeypatch)
    engine = _in_memory_db(monkeypatch)

    resp = main.create_event(
        _request(), name="Bot Event", route_key="faribault_mankato",
        recaptcha_token="",
    )

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/?error=")
    with Session(engine) as s:
        assert s.exec(select(Event)).all() == []  # nothing created


def _render_index(**context) -> str:
    base = {"email_enabled": True, "recovered": False, "error": None}
    return main.templates.env.get_template("index.html").render({**base, **context})


def test_index_renders_explicit_recaptcha_for_both_forms():
    html = _render_index(recaptcha_enabled=True, recaptcha_site_key="site-key")
    # one widget on the create form and one on the recovery form
    assert html.count('class="g-recaptcha"') == 2
    # explicit rendering, not Google's automatic mode (which renders only one)
    assert "render=explicit" in html
    assert "grecaptcha.render(" in html


def test_index_omits_recaptcha_when_disabled():
    html = _render_index(recaptcha_enabled=False, recaptcha_site_key="")
    assert "g-recaptcha" not in html
    assert "recaptcha/api.js" not in html


def test_recover_blocked_when_captcha_missing(monkeypatch):
    _configure(monkeypatch)
    _in_memory_db(monkeypatch)

    sent: list = []
    monkeypatch.setattr(main.mailer, "is_configured", lambda: True)
    monkeypatch.setattr(
        main.mailer, "send",
        lambda *a, **k: sent.append(a) or True,
    )

    resp = main.recover_links(_request(), email="me@x.com", recaptcha_token="")

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/?error=")
    assert sent == []  # no mail attempted
