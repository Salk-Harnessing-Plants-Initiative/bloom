"""Integration tests for the bloom_* schema-USAGE grants (issue #333).

Run against the live compose stack. Migrations are applied first, then
`supabase/grants/schema_grants.sql` is applied as supabase_admin (by
`make migrate-local` locally, and by the `Apply bloom_* schema-USAGE grants` step in
CI compose-health-check). Uses the `pg_conn` fixture (connects as POSTGRES_USER =
supabase_admin, the schema owner).
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
GRANTS_FILE = REPO_ROOT / "supabase" / "grants" / "schema_grants.sql"
_GRANT_USAGE = re.compile(
    r"GRANT\s+USAGE\s+ON\s+SCHEMA\s+(\w+)\s+TO\s+([^;]+);", re.IGNORECASE
)


def _expected_pairs() -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for schema, roles in _GRANT_USAGE.findall(GRANTS_FILE.read_text(encoding="utf-8")):
        for role in roles.split(","):
            pairs.add((schema, role.strip()))
    return sorted(pairs)


EXPECTED_PAIRS = _expected_pairs()


@pytest.mark.parametrize("schema,role", EXPECTED_PAIRS)
def test_expected_schema_usage_granted(pg_conn, schema, role):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT has_schema_privilege(%s, %s, 'USAGE')", (role, schema))
        assert cur.fetchone()[0] is True, f"{role} is missing USAGE on {schema}"


@pytest.mark.parametrize("role", ["bloom_user", "bloom_admin", "bloom_agent"])
def test_auth_usage_absent_for_user_admin_agent(pg_conn, role):
    """#341 intentional read-only gap: these roles must NOT have auth USAGE."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT has_schema_privilege(%s, 'auth', 'USAGE')", (role,))
        assert cur.fetchone()[0] is False, (
            f"{role} unexpectedly has auth USAGE — #341 settled this as an "
            "intentional gap; widening it needs its own review"
        )


def test_raw_grant_noops_as_postgres(pg_conn):
    """Why schema_grants.sql must be applied as supabase_admin, not via db push.

    A raw `GRANT USAGE ON SCHEMA storage` as `postgres` (what db push downgrades to)
    silently no-ops. All mutations are rolled back.
    """
    try:
        with pg_conn.cursor() as cur:
            cur.execute("REVOKE USAGE ON SCHEMA storage FROM bloom_agent")
            cur.execute("SET LOCAL ROLE postgres")
            cur.execute("GRANT USAGE ON SCHEMA storage TO bloom_agent")
            cur.execute("RESET ROLE")
            cur.execute("SELECT has_schema_privilege('bloom_agent','storage','USAGE')")
            assert cur.fetchone()[0] is False, "raw grant as postgres should no-op"
            # Applied as supabase_admin (the fixture's role) it sticks.
            cur.execute("GRANT USAGE ON SCHEMA storage TO bloom_agent")
            cur.execute("SELECT has_schema_privilege('bloom_agent','storage','USAGE')")
            assert cur.fetchone()[0] is True, "grant as supabase_admin should stick"
    finally:
        pg_conn.rollback()


def test_bloom_agent_can_resolve_storage_objects(pg_conn):
    """The #333 symptom, end to end: bloom_agent must resolve storage.objects.

    Without schema USAGE the bloommcp write path failed with
    `relation "objects" does not exist` / `permission denied for schema storage`.
    Asserting the (storage, bloom_agent) grant pair is present is necessary but not
    sufficient — this proves the role can actually reference the relation. Read-only
    query in a rolled-back txn; bloom_agent holds SELECT on storage.objects.
    """
    try:
        with pg_conn.cursor() as cur:
            cur.execute("SET LOCAL ROLE bloom_agent")
            # Raises (permission denied for schema / relation does not exist) if
            # schema USAGE is missing; resolves cleanly once the grant is applied.
            cur.execute("SELECT count(*) FROM storage.objects")
            assert cur.fetchone()[0] is not None
            cur.execute("RESET ROLE")
    finally:
        pg_conn.rollback()
