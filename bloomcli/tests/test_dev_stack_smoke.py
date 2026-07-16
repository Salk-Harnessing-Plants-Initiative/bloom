"""Dev-stack smoke test: verify the local Supabase stack serves and bloomctl can
round-trip ``cyl ingest-result`` against it.

Catches the failure modes we hit in practice: a wedged Kong gateway (empty replies
on ``/rest``/``/auth``) and a db-dev that isn't migrated (no write-back RPC).

Run it after ``make dev-up`` (from the repo root), sourcing the dev env first::

    set -a; . ./.env.dev; set +a
    BLOOMCTL_DEV_SMOKE=1 uv run --extra test --with psycopg \
      --project bloomcli pytest bloomcli/tests/test_dev_stack_smoke.py -v

Gated: self-skips (module level) unless ``BLOOMCTL_DEV_SMOKE`` is set and the dev
env vars are present, and it's marked ``integration`` so the default suite
(``-m "not integration"``) and CI never run it. It uses the local ``service_role``
key (a dev credential that maps to the ``service_role`` DB role, which holds
EXECUTE on the RPC) rather than an interactive login.
"""

import json
import os

import pytest

# Skip cleanly at *collection* if psycopg (not a bloomctl dep) is unavailable.
psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.integration

if not os.environ.get("BLOOMCTL_DEV_SMOKE"):
    pytest.skip(
        "set BLOOMCTL_DEV_SMOKE=1 (after `set -a; . ./.env.dev; set +a`) to run the dev smoke",
        allow_module_level=True,
    )

_MISSING = [
    k
    for k in ("POSTGRES_PASSWORD", "SERVICE_ROLE_KEY", "SUPABASE_PUBLIC_URL")
    if not os.environ.get(k)
]
if _MISSING:
    pytest.skip(
        f"dev env not sourced (missing {_MISSING}); run `set -a; . ./.env.dev; set +a`",
        allow_module_level=True,
    )

from pathlib import Path  # noqa: E402  (imported after the skip guard)

import httpx  # noqa: E402
from click.testing import CliRunner  # noqa: E402
from cyl_it_helpers import cleanup, envelope_for, seed_scan  # noqa: E402
from supabase import create_client  # noqa: E402

import bloomctl.cli as climod  # noqa: E402
from bloomctl.cli import cli  # noqa: E402

API_URL = os.environ["SUPABASE_PUBLIC_URL"].rstrip("/")
ANON = os.environ.get("ANON_KEY", "")
SERVICE_KEY = os.environ["SERVICE_ROLE_KEY"]
DSN = (
    f"postgres://{os.environ.get('POSTGRES_USER', 'supabase_admin')}:{os.environ['POSTGRES_PASSWORD']}"
    f"@127.0.0.1:{os.environ.get('POSTGRES_HOST_PORT', '5434')}/{os.environ.get('POSTGRES_DB', 'postgres')}"
)
FIXTURE = Path(__file__).parent / "fixtures" / "scan0K9E8BI.result.json"


def test_dev_gateway_rest_and_auth_serve_200():
    """Kong must proxy REST + auth (catches the wedged-gateway empty-reply failure)."""
    rest = httpx.get(f"{API_URL}/rest/v1/", headers={"apikey": ANON}, timeout=10)
    assert rest.status_code == 200, f"REST gateway returned {rest.status_code} (expected 200)"
    auth = httpx.get(f"{API_URL}/auth/v1/health", headers={"apikey": ANON}, timeout=10)
    assert auth.status_code == 200, f"auth gateway returned {auth.status_code} (expected 200)"


def test_dev_db_has_writeback_rpc():
    """db-dev must be migrated with the write-back RPC (catches an unmigrated stack)."""
    with psycopg.connect(DSN, autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM pg_proc WHERE proname = 'insert_cyl_result_envelope'")
        assert cur.fetchone()[0] == 1, (
            "insert_cyl_result_envelope RPC missing on db-dev — run `make migrate-local`"
        )


def test_cli_ingest_round_trip_over_http(monkeypatch):
    """Seed a scan, run `cyl ingest-result` against the live RPC over Kong, assert
    the write + the idempotent no-op, then clean up."""
    conn = psycopg.connect(DSN, autocommit=True)
    cur = conn.cursor()
    scan_id, img_ids = seed_scan(cur)
    envelope, idem = envelope_for(FIXTURE, img_ids)

    # A local service_role client stands in for the interactive login profile.
    client = create_client(API_URL, SERVICE_KEY)
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)

    try:
        r1 = CliRunner().invoke(
            cli, ["cyl", "ingest-result", "-", "--json"], input=json.dumps(envelope)
        )
        assert r1.exit_code == 0, r1.output
        out1 = json.loads(r1.output)
        assert out1["was_noop"] is False
        assert out1["scan_id"] == scan_id
        assert out1["trait_count"] == 2

        cur.execute("SELECT count(*) FROM cyl_trait_sources WHERE idempotency_key = %s", (idem,))
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT count(*) FROM cyl_scan_traits st "
            "JOIN cyl_trait_sources s ON s.id = st.source_id "
            "WHERE s.idempotency_key = %s",
            (idem,),
        )
        assert cur.fetchone()[0] == 2

        r2 = CliRunner().invoke(
            cli, ["cyl", "ingest-result", "-", "--json"], input=json.dumps(envelope)
        )
        assert r2.exit_code == 0, r2.output
        assert json.loads(r2.output)["was_noop"] is True
        cur.execute("SELECT count(*) FROM cyl_trait_sources WHERE idempotency_key = %s", (idem,))
        assert cur.fetchone()[0] == 1
    finally:
        cleanup(cur, scan_id, idem)
        conn.close()
