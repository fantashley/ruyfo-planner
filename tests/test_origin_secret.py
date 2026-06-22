"""Tests for the X-Origin-Secret gate (app/main._require_origin_secret)."""

import asyncio

from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app import main


def _request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
    )


def _run(headers: dict[str, str]):
    """Run the middleware with a passthrough downstream; return its response."""

    async def call_next(request):
        return PlainTextResponse("ok")

    return asyncio.run(main._require_origin_secret(_request(headers), call_next))


def _clear(monkeypatch):
    monkeypatch.delenv("RUYFO_ORIGIN_SECRET", raising=False)
    monkeypatch.delenv("RUYFO_ORIGIN_SECRET_FILE", raising=False)


def test_no_secret_configured_passes_through(monkeypatch):
    _clear(monkeypatch)
    resp = _run({})
    assert resp.status_code == 200
    assert resp.body == b"ok"


def test_secret_set_but_header_missing_is_forbidden(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("RUYFO_ORIGIN_SECRET", "s3cr3t")
    assert _run({}).status_code == 403


def test_secret_set_wrong_header_is_forbidden(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("RUYFO_ORIGIN_SECRET", "s3cr3t")
    assert _run({"X-Origin-Secret": "nope"}).status_code == 403


def test_secret_set_correct_header_passes_through(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("RUYFO_ORIGIN_SECRET", "s3cr3t")
    resp = _run({"X-Origin-Secret": "s3cr3t"})
    assert resp.status_code == 200
    assert resp.body == b"ok"


def test_secret_read_from_file(monkeypatch, tmp_path):
    _clear(monkeypatch)
    secret_file = tmp_path / "origin-secret"
    secret_file.write_text("from-a-file\n")  # trailing newline is stripped
    monkeypatch.setenv("RUYFO_ORIGIN_SECRET_FILE", str(secret_file))

    assert main._origin_secret() == "from-a-file"
    assert _run({"X-Origin-Secret": "from-a-file"}).status_code == 200
    assert _run({}).status_code == 403
