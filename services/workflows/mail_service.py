"""Bloom's internal mail service: the one place other containers ask to send email.

It listens on Docker's internal network only (no host port, no Caddy route) and admits a
caller only with MAIL_SERVICE_SECRET. Each route sends one fixed email; callers choose only
the recipient, within that route's rules. Today: the connector sign-up email.
"""

import hmac
import logging
import os
import re
import threading
import time

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

import mail

logger = logging.getLogger(__name__)

SECRET = os.environ.get("MAIL_SERVICE_SECRET") or ""
SITE_URL = (os.environ.get("SITE_URL") or "").rstrip("/")
# One email per address per this long; remembered in memory, so a restart may send one more.
RESEND_AFTER_SECONDS = 2 * 60 * 60
MAX_EMAIL_LENGTH = 254
# One address at salk.edu exactly: no spaces, commas or second @.
_SALK_ADDRESS = re.compile(r"^[^\s@,;<>]+@salk\.edu$")

SIGNUP_SUBJECT = "Create your Bloom account to use Bloom in Claude"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_last_sent: dict[str, float] = {}
_lock = threading.Lock()


class SignupRequest(BaseModel):
    email: str


def _check_secret(authorization: str | None) -> None:
    scheme, _, token = (authorization or "").partition(" ")
    if (
        not SECRET
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(token.encode(), SECRET.encode())
    ):
        raise HTTPException(status_code=401, detail="unauthorized")


def _signup_body(email: str) -> str:
    return (
        f"Someone signed in to Bloom from Claude as {email}, but there is no Bloom account "
        "for this address yet.\n\n"
        f"To use Bloom in Claude, open {SITE_URL}/login, choose Sign up, and create an "
        "account with this same address. Then reconnect Bloom in Claude.\n\n"
        "If you already have a Bloom account under a different address, contact the Bloom "
        "team. This message was sent from an address that doesn't receive replies.\n"
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/connector-signup")
def connector_signup(
    request: SignupRequest, authorization: str | None = Header(default=None)
) -> dict:
    """Emails a Salk user who has no Bloom account how to make one; at most every 2 hours."""
    _check_secret(authorization)
    email = request.email.strip().lower()
    if len(email) > MAX_EMAIL_LENGTH or not _SALK_ADDRESS.match(email):
        raise HTTPException(status_code=400, detail="one salk.edu address required")
    if not mail.configured() or not SITE_URL:
        return {"sent": False}
    with _lock:
        last = _last_sent.get(email)
        if last is not None and time.monotonic() - last < RESEND_AFTER_SECONDS:
            return {"sent": False}
        try:
            mail.send(mail.message(email, SIGNUP_SUBJECT, _signup_body(email)))
        except Exception as exc:  # noqa: BLE001 - reported to the caller as a code
            logger.warning(
                "mail: sign-up email to %s failed: %s", email, type(exc).__name__
            )
            raise HTTPException(status_code=502, detail="relay_error") from None
        _last_sent[email] = time.monotonic()
    logger.info("mail: sign-up email sent to %s", email)
    return {"sent": True}
