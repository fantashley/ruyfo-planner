"""Google reCAPTCHA v2 verification for the unauthenticated forms.

The event-creation and link-recovery forms are open to the world, so a bot can
drive them to create junk events and send mail to arbitrary addresses. A
reCAPTCHA challenge on those two POSTs stops the automated submissions.

Config comes from the environment (see ~/dotfiles for the deploy plumbing). The
site key is public (it ships in the HTML); the secret is private and supports a
``_FILE`` form for secret managers, like the mailer password. When either is
missing the gate is **disabled** (:func:`verify` returns True), so local dev and
the test suite run without it — callers check :func:`is_configured` to decide
whether to render the widget.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request

log = logging.getLogger("ruyfo.recaptcha")

VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"


def _secret() -> str:
    """reCAPTCHA secret, read from a file if RUYFO_RECAPTCHA_SECRET_FILE is set."""
    path = os.environ.get("RUYFO_RECAPTCHA_SECRET_FILE", "").strip()
    if path:
        try:
            return open(path, encoding="utf-8").read().strip()
        except OSError as exc:
            log.warning("could not read RUYFO_RECAPTCHA_SECRET_FILE: %s", exc)
            return ""
    return os.environ.get("RUYFO_RECAPTCHA_SECRET", "").strip()


def site_key() -> str:
    """Public site key embedded in the widget; "" when unconfigured."""
    return os.environ.get("RUYFO_RECAPTCHA_SITE_KEY", "").strip()


def is_configured() -> bool:
    """Whether both keys are present, i.e. the challenge should be enforced."""
    return bool(site_key() and _secret())


def verify(token: str, remote_ip: str | None = None) -> bool:
    """Validate a reCAPTCHA response token with Google.

    Returns True when the gate is disabled (unconfigured). When configured, an
    empty token or a failed/unreachable verification returns False — this is an
    anti-abuse gate, so it fails closed rather than letting submissions through
    when in doubt.
    """
    if not is_configured():
        return True
    if not token:
        return False

    data = {"secret": _secret(), "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    body = urllib.parse.urlencode(data).encode()

    try:
        with urllib.request.urlopen(VERIFY_URL, body, timeout=10) as resp:
            result = json.loads(resp.read())
    except (OSError, ValueError) as exc:
        log.warning("reCAPTCHA verification request failed: %s", exc)
        return False
    return bool(result.get("success"))
