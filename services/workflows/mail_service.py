"""Bloom's internal mail service, where other containers ask to send email (the run poller and
GoTrue still send directly for now).

It listens on Docker's internal network only (no host port, no Caddy route) and admits a
caller only with MAIL_SERVICE_SECRET. Each route sends one fixed email; callers choose only
the recipient, within that route's rules. Today: the connector sign-up email.
"""

import collections
import hmac
import json
import logging
import logging.handlers
import os
import re
import threading
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import mail

logger = logging.getLogger(__name__)

SECRET = os.environ.get("MAIL_SERVICE_SECRET") or ""
# A shorter secret is treated as unset: every caller is refused.
MIN_SECRET_LENGTH = 32
SITE_URL = (os.environ.get("SITE_URL") or "").rstrip("/")
# One email per address per this long; remembered in memory, so a restart may send one more.
RESEND_AFTER_SECONDS = 2 * 60 * 60
# At most this many emails in any hour, whoever they go to: caps what a leaked secret can send.
MAX_SENDS_PER_HOUR = 20
SEND_CAP_WINDOW_SECONDS = 60 * 60
# A log of every outcome kept on a volume, so it survives restarts; capped at 5 x 1 MB.
LOG_DIR = os.environ.get("MAIL_LOG_DIR") or ""
LOG_FILE_BYTES = 1_000_000
LOG_FILE_COUNT = 5
MAX_EMAIL_LENGTH = 254
# Largest request read: a sign-up request is about 40 bytes.
MAX_BODY_BYTES = 1024
# One address at salk.edu exactly, from the characters Salk addresses use.
_SALK_ADDRESS = re.compile(r"^[a-z0-9._%+-]+@salk\.edu$")

SIGNUP_SUBJECT = "Create your Bloom account to use Bloom in Claude"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_last_sent: dict[str, float] = {}
_recent_sends: collections.deque[float] = collections.deque()
_lock = threading.Lock()


def setup_logging(log_dir: str = LOG_DIR) -> None:
    """Outcomes go to stdout and, with MAIL_LOG_DIR, to a rotating mail.log there."""
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        logger.addHandler(stream)
    if log_dir:
        try:
            handler = logging.handlers.RotatingFileHandler(
                os.path.join(log_dir, "mail.log"),
                maxBytes=LOG_FILE_BYTES,
                backupCount=LOG_FILE_COUNT,
            )
        except OSError as exc:
            logger.error(
                "can't write the mail log in %s: %s", log_dir, type(exc).__name__
            )
        else:
            handler.setFormatter(fmt)
            logger.addHandler(handler)


setup_logging()

if SECRET and len(SECRET) < MIN_SECRET_LENGTH:
    logger.error(
        "MAIL_SERVICE_SECRET is under %d characters; refusing every caller",
        MIN_SECRET_LENGTH,
    )


class SignupRequest(BaseModel):
    email: str


def _has_secret(authorization: str) -> bool:
    scheme, _, token = authorization.partition(" ")
    return (
        len(SECRET) >= MIN_SECRET_LENGTH
        and scheme.lower() == "bearer"
        and hmac.compare_digest(token.encode(), SECRET.encode())
    )


class GuardBeforeBody:
    """Checks the secret, then the declared size, from the headers alone, before anything
    reads the body: a caller without the secret can't make the service parse or buffer it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] == "/health":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        if not _has_secret(authorization):
            logger.warning("refused a caller without the right secret")
            await _reply(send, 401, "unauthorized")
            return
        length = headers.get(b"content-length")
        if scope["method"] != "GET":
            if length is None or not length.isdigit():
                await _reply(send, 411, "length_required")
                return
            if int(length) > MAX_BODY_BYTES:
                logger.warning("refused a %s-byte request", int(length))
                await _reply(send, 413, "too_large")
                return
        await self.app(scope, _capped(receive), send)


def _capped(receive):
    """Stops reading once more than MAX_BODY_BYTES arrive, whatever was declared."""
    seen = 0

    async def capped():
        nonlocal seen
        message = await receive()
        seen += len(message.get("body", b""))
        if seen > MAX_BODY_BYTES:
            return {"type": "http.disconnect"}
        return message

    return capped


async def _reply(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


app.add_middleware(GuardBeforeBody)


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
async def health() -> dict:
    return {"ok": True}


@app.post("/connector-signup")
def connector_signup(request: SignupRequest) -> dict:
    """Emails a Salk user who has no Bloom account how to make one; at most every 2 hours.
    GuardBeforeBody has already checked the caller's secret and the request's size."""
    email = request.email.strip().lower()
    if len(email) > MAX_EMAIL_LENGTH or not _SALK_ADDRESS.match(email):
        logger.warning("refused a request for an address that isn't one Salk address")
        raise HTTPException(status_code=400, detail="one salk.edu address required")
    if not mail.configured() or not SITE_URL:
        logger.info(
            "skipped %s: mail is not configured (SMTP_HOST or SITE_URL unset)", email
        )
        return {"sent": False}
    # The lock covers only the quick checks: the address and an hourly slot are reserved,
    # then the send runs outside it, so a slow relay holds up only this one email.
    with _lock:
        now = time.monotonic()
        last = _last_sent.get(email)
        if last is not None and now - last < RESEND_AFTER_SECONDS:
            logger.info("skipped %s: emailed in the last 2 hours", email)
            return {"sent": False}
        while _recent_sends and now - _recent_sends[0] >= SEND_CAP_WINDOW_SECONDS:
            _recent_sends.popleft()
        if len(_recent_sends) >= MAX_SENDS_PER_HOUR:
            logger.error(
                "refused %s: hourly limit of %d reached", email, MAX_SENDS_PER_HOUR
            )
            raise HTTPException(status_code=429, detail="hourly_limit")
        _last_sent[email] = now
        _recent_sends.append(now)
    try:
        mail.send(mail.message(email, SIGNUP_SUBJECT, _signup_body(email)))
    except Exception as exc:  # noqa: BLE001 - reported to the caller as a code
        with _lock:
            # Give the reservation back, so a later retry can send.
            if _last_sent.get(email) == now:
                del _last_sent[email]
            if now in _recent_sends:
                _recent_sends.remove(now)
        logger.warning("failed %s: relay error %s", email, type(exc).__name__)
        raise HTTPException(status_code=502, detail="relay_error") from None
    logger.info("sent sign-up email to %s", email)
    return {"sent": True}
