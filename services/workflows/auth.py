"""
Caller authentication (Layer 1) + per-user rate limiting for the workflows API.

Every application route requires a valid Supabase user JWT. The token is
validated by delegating to Supabase (`GET /auth/v1/user`), so the service never
needs `JWT_SECRET` — a compromised endpoint cannot mint tokens. This is separate
from the server's own `bloom_workflows` login (Layer 2, see supabase_client.py).
"""

import os
import time
import threading
from collections import defaultdict

import httpx
from fastapi import Header, HTTPException

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")

# Simple per-instance fixed-window limiter. Per-user; per-process until a shared
# store is warranted (see change design.md). Bounds the expensive encode route.
RATE_LIMIT = int(os.environ.get("WORKFLOWS_RATE_LIMIT", "5"))
RATE_WINDOW_SECONDS = int(os.environ.get("WORKFLOWS_RATE_WINDOW_SECONDS", "60"))
_hits: dict[str, list[float]] = defaultdict(list)
_hits_lock = threading.Lock()


def require_supabase_user(authorization: str = Header(default=None)) -> str:
    """Validate the caller's Supabase JWT via /auth/v1/user; return the user id."""
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise HTTPException(
            status_code=500,
            detail="workflows service not configured: missing SUPABASE_URL/SUPABASE_ANON_KEY",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="Authorization Bearer token required"
        )
    token = authorization[len("Bearer ") :].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Bearer token required")

    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={
                    "apikey": SUPABASE_ANON_KEY,
                    "Authorization": f"Bearer {token}",
                },
            )
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"auth check failed: {exc}"
        ) from exc

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user_id = (resp.json() or {}).get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: no user id")
    return user_id


def enforce_rate_limit(user_id: str) -> None:
    """Raise 429 if the user has exceeded RATE_LIMIT calls in the window."""
    now = time.time()
    with _hits_lock:
        recent = [t for t in _hits[user_id] if now - t < RATE_WINDOW_SECONDS]
        if len(recent) >= RATE_LIMIT:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded ({RATE_LIMIT}/{RATE_WINDOW_SECONDS}s); retry later",
                headers={"Retry-After": str(RATE_WINDOW_SECONDS)},
            )
        recent.append(now)
        _hits[user_id] = recent
