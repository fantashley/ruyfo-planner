"""Outbound transactional email (organizer-link recovery).

Config comes from the environment so secrets live in the deploy plumbing
(see ~/dotfiles) rather than this repo. Defaults target Fastmail's SMTP. When
SMTP isn't configured the sender is a no-op so the app — and tests — run fine
without email; callers can check :func:`is_configured` to adjust the UI.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

from . import mailcap

log = logging.getLogger("ruyfo.mailer")


def _password() -> str:
    """SMTP password, read from a file if RUYFO_SMTP_PASSWORD_FILE is set.

    The file form plays nicely with secret managers (sops/agenix) that drop a
    credential at a path rather than baking it into the world-readable Nix store.
    """
    path = os.environ.get("RUYFO_SMTP_PASSWORD_FILE", "").strip()
    if path:
        try:
            return open(path, encoding="utf-8").read().strip()
        except OSError as exc:
            log.warning("could not read RUYFO_SMTP_PASSWORD_FILE: %s", exc)
            return ""
    return os.environ.get("RUYFO_SMTP_PASSWORD", "")


def _host() -> str:
    return os.environ.get("RUYFO_SMTP_HOST", "smtp.fastmail.com").strip()


def _port() -> int:
    try:
        return int(os.environ.get("RUYFO_SMTP_PORT", "465"))
    except ValueError:
        return 465


def _user() -> str:
    return os.environ.get("RUYFO_SMTP_USER", "").strip()


def _from() -> str:
    return os.environ.get("RUYFO_EMAIL_FROM", "").strip() or _user()


def is_configured() -> bool:
    """Whether enough SMTP config is present to actually send mail."""
    return bool(_user() and _password() and _from())


def send(to: str, subject: str, body: str, kind: str = "") -> bool:
    """Send a plain-text email. Returns True if it went out.

    Never raises into the request path: a misconfigured or flaky SMTP server
    logs a warning and returns False rather than 500-ing the page.

    Subject to the outbound rate caps in :mod:`app.mailcap`: a send that would
    exceed the global or per-recipient 24h cap is suppressed (returns False)
    rather than relayed. ``kind`` is a free-form tag stored in the audit trail.
    """
    if not is_configured():
        log.info("SMTP not configured; skipping email to %s", to)
        return False

    if not mailcap.allow(to):
        return False

    msg = EmailMessage()
    msg["From"] = _from()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    host, port = _host(), _port()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=10) as smtp:
                smtp.login(_user(), _password())
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=10) as smtp:
                smtp.starttls()
                smtp.login(_user(), _password())
                smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        log.warning("failed to send email to %s: %s", to, exc)
        return False
    mailcap.record(to, kind)
    return True
