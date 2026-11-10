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

_active = 0
_active_lock = threading.Lock()


def request(email: str) -> None:
    """Starts a background request for the sign-up email to ``email``; returns at once."""
    url = (os.environ.get(URL_ENV) or "").rstrip("/")
    secret = os.environ.get(SECRET_ENV) or ""
    if not url or not secret:
        return
    global _active
    with _active_lock:
        _active += 1
    threading.Thread(
        target=_post, args=(url, secret, email), name="signup-email", daemon=True
    ).start()


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
