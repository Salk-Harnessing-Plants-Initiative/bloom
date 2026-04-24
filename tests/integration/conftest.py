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


# Load env vars — prefer .env.prod locally, fall back to .env.ci in CI
_env = _load_env(".env.prod") or _load_env(".env.ci")

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost")
ANON_KEY = os.environ.get("ANON_KEY", _env.get("ANON_KEY", ""))
SERVICE_ROLE_KEY = os.environ.get("SERVICE_ROLE_KEY", _env.get("SERVICE_ROLE_KEY", ""))


@pytest.fixture
def base_url():
    return BASE_URL


@pytest.fixture
def anon_key():
    return ANON_KEY


@pytest.fixture
def service_role_key():
    return SERVICE_ROLE_KEY


def api_request(path: str, api_key: str = None, method: str = "GET", data: dict = None) -> tuple[int, dict | str]:
    """Make an HTTP request to the stack via nginx."""
    url = f"{BASE_URL}{path}"
    headers = {}
    if api_key:
        headers["apikey"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"

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


@pytest.fixture
def api():
    """Fixture that returns the api_request helper."""
    return api_request


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
def pg_conn():
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

    conninfo = (
        f"host=127.0.0.1 port={POSTGRES_HOST_PORT} "
        f"dbname={POSTGRES_DB} user={POSTGRES_USER} password={POSTGRES_PASSWORD}"
    )
    conn = psycopg.connect(conninfo)
    try:
        yield conn
    finally:
        conn.close()
