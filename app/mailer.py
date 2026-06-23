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


def _alert_to() -> str:
    """Operator address that gets notified of noteworthy events (creations, …).

    Empty disables operator alerts entirely — the app and tests run fine without
    it, just like an unconfigured SMTP setup.
    """
    return os.environ.get("RUYFO_ALERT_EMAIL", "").strip()


def is_configured() -> bool:
    """Whether enough SMTP config is present to actually send mail."""
    return bool(_user() and _password() and _from())


def alerts_enabled() -> bool:
    """Whether operator alerts can go out (SMTP configured + an alert address)."""
    return bool(is_configured() and _alert_to())


def _deliver(msg: EmailMessage) -> bool:
    """Hand a built message to SMTP. Returns True if it went out.

    Never raises: a misconfigured or flaky server logs a warning and returns
    False rather than 500-ing the request path that triggered the send.
    """
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
        log.warning("failed to send email to %s: %s", msg["To"], exc)
        return False
    return True


def _build(to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = _from()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def send(to: str, subject: str, body: str, kind: str = "") -> bool:
    """Send a plain-text email to a (possibly user-supplied) address.

    Subject to the outbound rate caps in :mod:`app.mailcap`: a send that would
    exceed the global or per-recipient 24h cap is suppressed (returns False)
    rather than relayed. ``kind`` is a free-form tag stored in the audit trail.
    """
    if not is_configured():
        log.info("SMTP not configured; skipping email to %s", to)
        return False

    if not mailcap.allow(to):
        return False

    if not _deliver(_build(to, subject, body)):
        return False
    mailcap.record(to, kind)
    if alerts_enabled():
        _alert_sent(to, kind)
    return True


def _alert_sent(to: str, kind: str) -> None:
    """Operator heads-up that an email just went out — SMTP-volume monitoring.

    Fired from :func:`send` for *every* successful outbound message (recovery
    links, the recovery-email confirmation, …), so the operator can watch how
    much mail the public forms are driving and spot abuse. The notification
    itself goes via :func:`send_alert`, which never re-enters :func:`send`, so
    this can't loop. The reported count excludes these operator alerts so it
    reflects real outbound volume, not the monitoring traffic.
    """
    label = kind or "email"
    count = mailcap.sent_last_24h(exclude_kind="alert")
    send_alert(
        f"RUYFO email sent: {label} (#{count} in 24h)",
        "An email just went out through the RUYFO SMTP setup.\n\n"
        f"  To:    {to}\n"
        f"  Kind:  {label}\n\n"
        f"Outbound emails (excluding these alerts) in the last 24h: {count}.\n",
    )


def send_alert(subject: str, body: str, kind: str = "alert") -> bool:
    """Notify the operator (``RUYFO_ALERT_EMAIL``) of a noteworthy event.

    Unlike :func:`send`, the recipient is a fixed operator-owned address rather
    than anything a visitor supplies, so the per-recipient anti-mailbomb cap
    doesn't apply (and would otherwise silently drop alerts once a handful of
    events were created in a day). The global daily cap still applies as a
    runaway-volume backstop, and every alert is recorded in the audit trail.
    """
    to = _alert_to()
    if not (is_configured() and to):
        log.info("operator alerts not configured; skipping alert %r", subject)
        return False

    if not mailcap.allow(to, per_recipient=False):
        return False

    if not _deliver(_build(to, subject, body)):
        return False
    mailcap.record(to, kind)
    return True
