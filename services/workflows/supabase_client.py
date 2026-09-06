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

# supabase-py defaults postgrest_client_timeout to 120s (postgrest/constants.py's
# DEFAULT_POSTGREST_CLIENT_TIMEOUT). Neither the dispatch worker's
# stop_grace_period (30s) nor the status poller's own, larger one (60s, sized
# for its own N-GETs-per-sweep worst case — see design.md) actually covers
# that — a hung RPC could still be SIGKILLed mid-request — and every RPC either
# service calls (claim_cyl_pipeline_batch/complete_cyl_pipeline_batch/
# fail_cyl_pipeline_batch/update_cyl_pipeline_run_status) is a single-row,
# small, indexed operation with no large-payload case to protect, so a tight
# bound is safe for both. But app_client() is also shared with pipeline.py's
# trigger_pipeline() (up to MAX_SCAN_IDS=5000 scans, up to 200 sequential
# enqueue_cyl_pipeline_batch calls plus a bulk insert) and video.py — neither
# has that same small-payload guarantee, so this bound is opt-in per caller,
# not a new module-wide default; only dispatch_worker.py and status_poller.py
# pass it. Unlike bloommcp's own supabase_client.py (which deliberately keeps
# the 120s default globally because its one shared client also serves
# multi-million-row experiment fetches), this service's callers have different
# enough payload profiles to warrant a per-call override instead of one shared
# constant.
SINGLE_ROW_RPC_TIMEOUT_SECONDS = 10


def app_client(*, timeout_seconds: float | None = None):
    """Return a Supabase client signed in as the workflows app user.

    `timeout_seconds`, when given, overrides supabase-py's postgrest_client_timeout
    default (120s) for every request made through the returned client. Leave unset
    to keep that default — only dispatch_worker.py's and status_poller.py's RPCs
    are small/bounded enough to safely tighten it (see SINGLE_ROW_RPC_TIMEOUT_SECONDS
    above).
    """
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

    from supabase import ClientOptions, create_client

    options = (
        ClientOptions(postgrest_client_timeout=timeout_seconds)
        if timeout_seconds is not None
        else None
    )
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY, options=options)
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
