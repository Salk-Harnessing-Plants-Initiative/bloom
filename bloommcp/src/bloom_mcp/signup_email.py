"""Asks Bloom's internal mail service to email a Salk user the connector refused for having no
Bloom account. Runs in the background so the refusal never waits, and doesn't read the answer:
the mail service keeps the log of what it sent. Only an unreachable service is logged here.

Needs MAIL_SERVICE_URL and MAIL_SERVICE_SECRET; without either nothing is sent (dev, CI).
"""

from __future__ import annotations

import logging
import os
import threading
import time

import httpx

logger = logging.getLogger(__name__)

URL_ENV = "MAIL_SERVICE_URL"
SECRET_ENV = "MAIL_SERVICE_SECRET"
TIMEOUT_SECONDS = 5
# Each Claude connect is refused again; ask at most once per address in this window to cut
# repeat requests. The mail service sends each address the email only once.
ASK_AGAIN_AFTER_SECONDS = 2 * 60 * 60
# Requests in flight at once; past this a new one is skipped (the person will retry).
MAX_IN_FLIGHT = 2
# Addresses remembered at most; expired ones are dropped first, then the oldest.
MAX_REMEMBERED = 1024

_active = 0
_asked: dict[str, float] = {}
_active_lock = threading.Lock()


def _forget_old(now: float) -> None:
    for address in [a for a, t in _asked.items() if now - t >= ASK_AGAIN_AFTER_SECONDS]:
        del _asked[address]
    while len(_asked) > MAX_REMEMBERED:
        del _asked[next(iter(_asked))]


def request(email: str) -> None:
    """Starts a background request for the sign-up email to ``email``; returns at once.
    Skipped within ASK_AGAIN_AFTER_SECONDS of the last ask, or when two are in flight."""
    url = (os.environ.get(URL_ENV) or "").rstrip("/")
    secret = os.environ.get(SECRET_ENV) or ""
    if not url or not secret:
        return
    global _active
    now = time.monotonic()
    with _active_lock:
        asked = _asked.get(email)
        if asked is not None and now - asked < ASK_AGAIN_AFTER_SECONDS:
            return
        if _active >= MAX_IN_FLIGHT:
            return
        _active += 1
        _asked[email] = now
        _forget_old(now)
    try:
        threading.Thread(
            target=_post, args=(url, secret, email), name="signup-email", daemon=True
        ).start()
    except Exception as exc:  # noqa: BLE001 - an email must never affect the refusal
        with _active_lock:
            _active -= 1
            _asked.pop(email, None)
        logger.warning(
            "could not start the sign-up email request: %s", type(exc).__name__
        )


def _post(url: str, secret: str, email: str) -> None:
    global _active
    try:
        # Fire and forget: the mail service decides and logs the outcome in its own log.
        httpx.post(
            f"{url}/connector-signup",
            json={"email": email},
            headers={"Authorization": f"Bearer {secret}"},
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException:
        pass  # The service may still send; its log says.
    except Exception as exc:  # noqa: BLE001 - an email must never affect the refusal
        # Not reached: forget the address so the person's next try asks again.
        with _active_lock:
            _asked.pop(email, None)
        logger.warning("could not reach the mail service: %s", type(exc).__name__)
    finally:
        with _active_lock:
            _active -= 1


def _threads_idle(timeout: float) -> bool:
    """For tests: wait until no request is in flight."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _active_lock:
            if _active == 0:
                return True
        time.sleep(0.01)
    return False
