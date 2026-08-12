"""
Integration test fixtures for Bloom v2.

Tests run against the live compose stack via nginx on port 80.
Requires: docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
"""

import os
import pytest
import urllib.request
import json
from pathlib import Path


def _load_env(env_file: str) -> dict[str, str]:
    """Load key=value pairs from an env file."""
    env = {}
    path = Path(__file__).parent.parent.parent / env_file
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


# Load env vars — prefer .env.prod locally, fall back to .env.ci in CI, then to
# .env.dev for a local compose-dev run (so `make test-integration` against the
# dev stack picks up its generated credentials instead of silently skipping).
# Order matters: a left-to-right `or` returns the first non-empty mapping, so
# CI (.env.ci) and prod (.env.prod) keep precedence over .env.dev.
_env = _load_env(".env.prod") or _load_env(".env.ci") or _load_env(".env.dev")

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost")
ANON_KEY = os.environ.get("ANON_KEY", _env.get("ANON_KEY", ""))
SERVICE_ROLE_KEY = os.environ.get("SERVICE_ROLE_KEY", _env.get("SERVICE_ROLE_KEY", ""))
# `null` and `[]` are the compose defaults for an unprovisioned stack, not a JWKS.
JWT_JWKS = os.environ.get("JWT_JWKS", _env.get("JWT_JWKS", "")).strip()


@pytest.fixture
def base_url():
    return BASE_URL


@pytest.fixture
def anon_key():
    return ANON_KEY


@pytest.fixture
def service_role_key():
    return SERVICE_ROLE_KEY


@pytest.fixture
def jwks_configured() -> bool:
    """Whether this stack signs sessions asymmetrically (ES256) or with HS256."""
    return bool(JWT_JWKS) and JWT_JWKS not in ("null", "[]")


def api_request(
    path: str,
    api_key: str = None,
    method: str = "GET",
    data: dict = None,
    bearer: str = None,
) -> tuple[int, dict | str]:
    """Make an HTTP request to the stack via nginx.

    `bearer` sends an end-user session token while `api_key` stays the gateway
    credential — the split a logged-in browser request actually makes.
    """
    url = f"{BASE_URL}{path}"
    headers = {}
    if api_key:
        headers["apikey"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"

    body = None
    if data:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode()
            try:
                return resp.status, json.loads(content)
            except json.JSONDecodeError:
                return resp.status, content
    except urllib.error.HTTPError as e:
        content = e.read().decode()
        try:
            return e.code, json.loads(content)
        except json.JSONDecodeError:
            return e.code, content


def api_response_headers(path: str, api_key: str = None):
    """Return the response headers for a GET, including on error responses.

    Returns the raw `http.client.HTTPMessage`, not a dict, so repeated headers
    stay distinguishable via `.get_all()`. That distinction is the point: Caddy
    sets its headers before the handler chain and `reverse_proxy` then *adds*
    the upstream's, so a header both sides emit arrives twice. Where the two
    values differ, `Referrer-Policy` and `Permissions-Policy` resolve
    last-wins — the upstream silently overrides the edge. A dict, or an `in`
    check, cannot see that.

    HTTP errors are returned rather than raised: the headers are asserted on
    Caddy-generated 404s and 502s too.
    """
    url = f"{BASE_URL}{path}"
    headers = {}
    if api_key:
        headers["apikey"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.headers
    except urllib.error.HTTPError as e:
        return e.headers


@pytest.fixture
def api():
    """Fixture that returns the api_request helper."""
    return api_request


@pytest.fixture
def api_headers():
    """Fixture that returns the api_response_headers helper."""
    return api_response_headers


# -----------------------------------------------------------------------------
# Database fixtures — connect directly to Postgres via the host-exposed port
# (127.0.0.1:${POSTGRES_HOST_PORT}) for assertions that need SQL, not just HTTP.
# Used by test_migrations.py.
# -----------------------------------------------------------------------------

POSTGRES_USER = os.environ.get("POSTGRES_USER", _env.get("POSTGRES_USER", "supabase_admin"))
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", _env.get("POSTGRES_PASSWORD", ""))
POSTGRES_DB = os.environ.get("POSTGRES_DB", _env.get("POSTGRES_DB", "postgres"))
POSTGRES_HOST_PORT = os.environ.get("POSTGRES_HOST_PORT", _env.get("POSTGRES_HOST_PORT", "5432"))


@pytest.fixture
def pg_conninfo() -> str:
    """The same connection string `pg_conn` connects with, exposed separately
    for tests that need a *second*, independent connection (e.g. genuine
    concurrency tests) rather than the one `pg_conn` already opened."""
    return (
        f"host=127.0.0.1 port={POSTGRES_HOST_PORT} "
        f"dbname={POSTGRES_DB} user={POSTGRES_USER} password={POSTGRES_PASSWORD}"
    )


@pytest.fixture
def pg_conn(pg_conninfo):
    """
    Connect to Postgres via the host-exposed port. Requires `psycopg[binary]`.

    If `POSTGRES_PASSWORD` is set in the environment we treat a DB as
    expected-available and FAIL on missing psycopg — a silent skip there
    masks the whole point of the migration-runner integration tests. If
    no password is configured (local dev without a compose stack) we skip.
    """
    try:
        import psycopg  # type: ignore
    except ImportError:
        if POSTGRES_PASSWORD:
            pytest.fail(
                "psycopg not installed in a DB-configured environment. "
                "Install with `uv pip install 'psycopg[binary]'` or add "
                "`--with 'psycopg[binary]'` to the pytest invocation."
            )
        pytest.skip("psycopg not installed and no POSTGRES_PASSWORD set — local-dev skip")

    conn = psycopg.connect(pg_conninfo)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def supabase_db_url():
    """Postgres connection URL formatted for `supabase db push --db-url`."""
    return (
        f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@127.0.0.1:"
        f"{POSTGRES_HOST_PORT}/{POSTGRES_DB}?sslmode=disable"
    )
