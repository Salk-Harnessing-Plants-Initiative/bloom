"""
Integration test fixtures for Bloom v2.

Tests run against the live compose stack via nginx on port 80.
Requires: docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
"""

import base64
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

# Kong's basic-auth credentials for the Studio route. Sent unconditionally by the
# tests that reach a console hostname: a server that does not require auth ignores
# the header, so this holds whether or not the gate is in place.
DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", _env.get("DASHBOARD_USERNAME", ""))
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", _env.get("DASHBOARD_PASSWORD", ""))


@pytest.fixture(scope="session")
def dashboard_auth():
    """`Authorization` header for Kong's basic-auth gate, or `{}` if unconfigured."""
    if not (DASHBOARD_USERNAME and DASHBOARD_PASSWORD):
        return {}
    token = base64.b64encode(
        f"{DASHBOARD_USERNAME}:{DASHBOARD_PASSWORD}".encode()
    ).decode()
    return {"Authorization": f"Basic {token}"}


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


def api_response_headers(path: str, api_key: str = None, host: str = None, extra_headers: dict = None):
    """Return the response headers for a GET, including on error responses.

    Returns the raw `http.client.HTTPMessage`, not a dict, so repeated headers
    stay distinguishable via `.get_all()`. That distinction is the point: Caddy
    sets its headers before the handler chain and `reverse_proxy` then *adds*
    the upstream's, so a header both sides emit arrives twice. Where the two
    values differ, `Referrer-Policy` and `Permissions-Policy` resolve
    last-wins — the upstream silently overrides the edge. A dict, or an `in`
    check, cannot see that.

    HTTP errors are returned rather than raised: the headers are asserted on
    Caddy-generated 404s and 502s too. A transport failure (Caddy down, DNS,
    timeout) is re-raised naming the route — callers fetch several routes into
    one fixture, so an unlabelled URLError there surfaces as every dependent
    test erroring with a traceback pointing at the fixture, not the route.

    `host` overrides the Host header, so the console hostnames can be reached
    over the same connection — they resolve to the same Caddy either way, and
    which site block serves the request is decided by Host alone. `extra_headers`
    carries credentials where a hostname is gated, so an assertion lands on the
    surface itself rather than on the gate's error page.
    """
    url = f"{BASE_URL}{path}"
    headers = {}
    if api_key:
        headers["apikey"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"
    if host:
        headers["Host"] = host
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.headers
    except urllib.error.HTTPError as e:
        return e.headers
    except (urllib.error.URLError, TimeoutError) as e:
        raise AssertionError(f"could not reach {url} (Host: {host or 'default'}): {e}") from e


@pytest.fixture
def api():
    """Fixture that returns the api_request helper."""
    return api_request


@pytest.fixture(scope="session")
def api_headers():
    """Fixture that returns the api_response_headers helper.

    Session-scoped so module-scoped fixtures can depend on it and fetch each
    route once, rather than once per assertion.
    """
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
def authenticator_conninfo() -> str:
    """Connect as `authenticator` -- the login role PostgREST/Supavisor use for every live RPC
    call before `SET ROLE`-ing to `service_role`/`anon`/`authenticated` per the caller's JWT.
    Uses the same `POSTGRES_PASSWORD` `authenticator` is already provisioned with (its own
    `ALTER USER ... WITH PASSWORD` is fed from the same value PostgREST's `PGRST_DB_URI` uses) --
    no separate secret needed. `authenticator` alone carries
    `session_preload_libraries=safeupdate` (bloom#806); `pg_conninfo`'s `supabase_admin` never
    loads it, which is why tests using only that connection can't exercise that guard."""
    return (
        f"host=127.0.0.1 port={POSTGRES_HOST_PORT} "
        f"dbname={POSTGRES_DB} user=authenticator password={POSTGRES_PASSWORD}"
    )


@pytest.fixture
def supabase_db_url():
    """Postgres connection URL formatted for `supabase db push --db-url`."""
    return (
        f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@127.0.0.1:"
        f"{POSTGRES_HOST_PORT}/{POSTGRES_DB}?sslmode=disable"
    )


# -----------------------------------------------------------------------------
# Single-cell loaders: accounts that sign in as a writer or an admin, and the
# clean-up their loads need, since writes through the API commit.
# -----------------------------------------------------------------------------

SCRNA_API_URL = f"{BASE_URL}/api"

# Every table a single-cell load writes, children before parents.
SCRNA_TABLES = ("scrna_de_genes", "scrna_de", "scrna_de_runs", "scrna_counts",
                "scrna_genes", "scrna_cells", "scrna_cluster_stats",
                "scrna_cluster_neighbors", "scrna_clusters")


@pytest.fixture(scope="session")
def scrna_accounts():
    """A writer and an admin account, created through the auth admin API and
    deleted when the session ends."""
    import uuid

    if not SERVICE_ROLE_KEY:
        pytest.skip("no SERVICE_ROLE_KEY — the stack is not configured")
    made = {}
    for role, flag in (("writer", "is_writer"), ("admin", "is_admin")):
        # Sign-ups are limited to @salk.edu (check_email_trigger).
        email = f"scrna-{role}-{uuid.uuid4().hex[:8]}@salk.edu"
        password = uuid.uuid4().hex
        status, body = api_request(
            "/api/auth/v1/admin/users", api_key=SERVICE_ROLE_KEY, method="POST",
            data={"email": email, "password": password, "email_confirm": True,
                  "app_metadata": {flag: True}},
        )
        assert status in (200, 201), f"could not create the {role} account: {status} {body}"
        made[role] = {"email": email, "password": password, "id": body["id"]}
    yield made
    for account in made.values():
        api_request(f"/api/auth/v1/admin/users/{account['id']}",
                    api_key=SERVICE_ROLE_KEY, method="DELETE")


@pytest.fixture
def scrna_api() -> tuple[str, str]:
    """The API URL and anon key the loaders are given as --api-url and --anon-key."""
    return SCRNA_API_URL, ANON_KEY


@pytest.fixture
def scrna_species(pg_conninfo):
    """A species of its own for one test. Loads through the API commit, so every
    dataset under it is removed afterwards with its rows and counts objects."""
    import uuid

    import psycopg

    tag = uuid.uuid4().hex[:10]
    with psycopg.connect(pg_conninfo, autocommit=True) as conn:
        (species_id,) = conn.execute(
            "INSERT INTO public.species (common_name, genus, species) "
            "VALUES (%s, %s, %s) RETURNING id",
            (f"ingest-{tag}", f"Ingestus-{tag}", f"testis-{tag}"),
        ).fetchone()
    yield species_id
    with psycopg.connect(pg_conninfo) as conn, conn.transaction():
        found = conn.execute("SELECT id, btrim(name) FROM public.scrna_datasets "
                             "WHERE species_id = %s", (species_id,)).fetchall()
        ids = [i for i, _ in found]
        for table in SCRNA_TABLES:
            conn.execute(f"DELETE FROM public.{table} WHERE dataset_id = ANY(%s)", (ids,))
        conn.execute("DELETE FROM public.scrna_datasets WHERE id = ANY(%s)", (ids,))
        conn.execute("DELETE FROM public.species WHERE id = %s", (species_id,))
    # By prefix, so objects a stopped load uploaded without a row go too.
    import re

    paths = []
    for dataset_id, name in found:
        cleaned = re.sub(r"\s+", "_", name)
        prefix = f"counts/{cleaned}_{dataset_id}_/"
        status, listed = api_request("/api/storage/v1/object/list/scrna",
                                     api_key=SERVICE_ROLE_KEY, method="POST",
                                     data={"prefix": prefix, "limit": 10000})
        assert status == 200, f"could not list {prefix}: {status} {listed}"
        paths += [prefix + o["name"] for o in listed]
    if paths:
        status, body = api_request("/api/storage/v1/object/scrna", api_key=SERVICE_ROLE_KEY,
                                   method="DELETE", data={"prefixes": paths})
        assert status == 200, f"could not remove the counts objects: {status} {body}"
