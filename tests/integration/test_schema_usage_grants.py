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


@pytest.mark.parametrize("schema", ["storage", "auth"])
def test_schema_grant_sticks_iff_postgres_has_grant_option(pg_conn, schema):
    """Why schema_grants.sql is applied as supabase_admin, not via a db push migration.

    A `GRANT USAGE ON SCHEMA` only takes effect when run by a role with grant
    authority on that schema (the owner, or a USAGE ... WITH GRANT OPTION holder).
    `supabase db push` downgrades to `postgres`, so an in-migration schema grant
    sticks **iff** `postgres` holds grant option on that schema — otherwise it
    silently no-ops (`WARNING: no privileges were granted`). Applied as
    `supabase_admin` (the owner) it always sticks.

    This splits by schema/image and is the reason schema_grants.sql exists:

    - **auth**: no platform image grants `postgres` grant option on `auth`, so an
      in-migration `auth` grant ALWAYS no-ops → the supabase_admin path is genuinely
      required on every supported image (this is `bloom_writer`'s auth USAGE).
    - **storage**: newer `supabase/postgres` images (>= the 2025-07-09
      `grant_storage_schema_to_postgres_with_grant_option` migration — e.g. prod/CI's
      15.14.1.104) DO grant `postgres` grant option, so an in-migration storage grant
      sticks there; older images (dev's 15.8.1.060) do not, so it no-ops. The
      workaround is load-bearing on old images and idempotent belt-and-suspenders on
      new ones.

    Uses a throwaway role so no inherited privilege confounds the observation. All
    mutations are rolled back.
    """
    try:
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT has_schema_privilege('postgres', %s, 'USAGE WITH GRANT OPTION')",
                (schema,),
            )
            postgres_can_grant = cur.fetchone()[0]

            cur.execute("CREATE ROLE bloom_probe_tmp NOLOGIN")
            cur.execute("SET LOCAL ROLE postgres")
            cur.execute(f"GRANT USAGE ON SCHEMA {schema} TO bloom_probe_tmp")
            cur.execute("RESET ROLE")
            cur.execute(
                "SELECT has_schema_privilege('bloom_probe_tmp', %s, 'USAGE')", (schema,)
            )
            stuck_as_postgres = cur.fetchone()[0]
            assert stuck_as_postgres == postgres_can_grant, (
                f"on {schema}: grant as postgres should stick iff postgres has grant "
                f"option (grant_option={postgres_can_grant}, stuck={stuck_as_postgres})"
            )

            # Applied as supabase_admin (the fixture's role, the schema owner) it
            # always sticks — which is exactly how schema_grants.sql applies it.
            cur.execute(f"GRANT USAGE ON SCHEMA {schema} TO bloom_probe_tmp")
            cur.execute(
                "SELECT has_schema_privilege('bloom_probe_tmp', %s, 'USAGE')", (schema,)
            )
            assert cur.fetchone()[0] is True, (
                f"grant as supabase_admin on {schema} should stick"
            )
    finally:
        pg_conn.rollback()


def test_auth_grant_always_noops_in_migration(pg_conn):
    """The surviving, image-independent justification for schema_grants.sql.

    `postgres` must NOT hold grant option on `auth` on any supported image — so a
    raw `GRANT USAGE ON SCHEMA auth` inside a `db push` migration always no-ops, and
    `bloom_writer`'s auth USAGE genuinely requires the supabase_admin path. If a
    future platform image grants this, revisit whether the auth workaround is still
    needed (as already happened for storage).
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_schema_privilege('postgres', 'auth', 'USAGE WITH GRANT OPTION')"
        )
        assert cur.fetchone()[0] is False, (
            "postgres unexpectedly has grant option on auth — the platform image may "
            "now grant it (as it does for storage); revisit the auth workaround"
        )


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
