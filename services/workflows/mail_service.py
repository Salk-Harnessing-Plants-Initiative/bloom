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
# Callers without the secret: stdout only, so they can't flood mail.log's record of sends.
unauthenticated = logging.getLogger(f"{__name__}.unauthenticated")
unauthenticated.propagate = False

SECRET = os.environ.get("MAIL_SERVICE_SECRET") or ""
# A shorter secret is treated as unset: every caller is refused.
MIN_SECRET_LENGTH = 32
SITE_URL = (os.environ.get("SITE_URL") or "").rstrip("/")
# Who people write to about their account; named in the email and set as its Reply-To.
CONTACT = (os.environ.get("MAIL_CONTACT") or "").strip()
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
    unauthenticated.setLevel(logging.INFO)
    for target in (logger, unauthenticated):
        if not any(type(h) is logging.StreamHandler for h in target.handlers):
            stream = logging.StreamHandler()
            stream.setFormatter(fmt)
            target.addHandler(stream)
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
        "the caller secret is too short (32 characters minimum); refusing everyone"
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
            unauthenticated.warning("refused a caller without the right secret")
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
    if CONTACT:
        ask = f"contact the Bloom team at {CONTACT}."
    else:
        ask = "contact the Bloom team."
    return (
        f"You tried to use Bloom in Claude as {email}, but Bloom couldn't find an active "
        "account for this address.\n\n"
        "To use Bloom in Claude:\n"
        f"1. Open {SITE_URL}/login and choose Sign up.\n"
        "2. Create your account with this same Salk address and a new Bloom password "
        "(not your Salk password).\n"
        '3. Try the "Bloom Connector" in Claude again.\n\n'
        "If you think you already have a Bloom account, for example under a different "
        f"address, {ask}\n"
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
        mail.send(mail.message(email, SIGNUP_SUBJECT, _signup_body(email), CONTACT))
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
