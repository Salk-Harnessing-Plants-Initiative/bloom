"""Postgres connection URL composition for the bloom langchain agent.

Extracted from agent.py so the URL logic can be unit-tested in isolation
without importing the rest of agent.py (which runs LLM model auto-detection
as a module-level side effect and requires LOCAL_LLM_URL to be set).
"""
import os
from urllib.parse import quote


REQUIRED_VARS = (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
)


def compose_postgres_url() -> str:
    """Build a postgresql://user:password@host:port/db URL from env vars.

    The password is percent-encoded so characters with reserved URL meanings
    (@, :, /, #, %, ?, &) can't corrupt the URL. Without encoding, a
    password like `p@ss` produces `postgresql://user:p@ss@host/db` which
    libpq parses as user `user:p` on host `ss` — the connection fails with
    a confusing error that looks nothing like "bad password."

    Raises:
        RuntimeError: if any of the 5 required POSTGRES_* env vars is
            missing or empty. The error names the missing vars so the
            operator can fix them fast at 2am.
    """
    missing = [k for k in REQUIRED_VARS if not os.getenv(k)]
    if missing:
        raise RuntimeError(
            f"Database configuration incomplete: missing env vars {missing}. "
            "Set POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB."
        )
    return "postgresql://{user}:{password}@{host}:{port}/{db}".format(
        user=os.environ["POSTGRES_USER"],
        password=quote(os.environ["POSTGRES_PASSWORD"], safe=""),
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        db=os.environ["POSTGRES_DB"],
    )
