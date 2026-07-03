"""
Supabase client for the workflows service, signed in as a dedicated, least-
privilege app user (not the DB superuser). The app user's grants/RLS/storage
policies — set up in Supabase — are what actually bound what this service can do.

Creds come from env (WORKFLOWS_SUPABASE_EMAIL / _PASSWORD); SUPABASE_URL and
SUPABASE_ANON_KEY point at the gateway. A sign-in failure here is a service
misconfiguration (500), not a caller error.
"""

import os

from fastapi import HTTPException

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")
APP_EMAIL = os.environ.get("WORKFLOWS_SUPABASE_EMAIL")
APP_PASSWORD = os.environ.get("WORKFLOWS_SUPABASE_PASSWORD")


def app_client():
    """Return a Supabase client signed in as the workflows app user."""
    # Validate config BEFORE importing supabase so a misconfigured service fails
    # fast and cleanly without loading the (heavy) client stack.
    missing = [
        name
        for name, val in [
            ("SUPABASE_URL", SUPABASE_URL),
            ("SUPABASE_ANON_KEY", SUPABASE_ANON_KEY),
            ("WORKFLOWS_SUPABASE_EMAIL", APP_EMAIL),
            ("WORKFLOWS_SUPABASE_PASSWORD", APP_PASSWORD),
        ]
        if not val
    ]
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"workflows service not configured: missing {', '.join(missing)}",
        )

    from supabase import create_client

    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    try:
        res = client.auth.sign_in_with_password(
            {"email": APP_EMAIL, "password": APP_PASSWORD}
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"workflows app-user sign-in failed: {exc}"
        ) from exc
    if not getattr(res, "session", None):
        raise HTTPException(status_code=500, detail="workflows app-user sign-in failed")
    return client
