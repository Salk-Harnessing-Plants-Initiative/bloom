"""Integration tests for the bloom_* schema-USAGE grant mechanism (issue #333).

Run against the live compose stack (the `compose-health-check` job applies
migrations first, and the init layer installs the grant helper). Uses the
`pg_conn` fixture (connects as POSTGRES_USER = supabase_admin, the schema owner).
"""

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
MATRIX = json.loads(
    (REPO_ROOT / "supabase" / "grants" / "bloom_grant_matrix.json").read_text(
        encoding="utf-8"
    )
)["schema_usage"]
EXPECTED_PAIRS = sorted(
    (schema, role) for schema, roles in MATRIX.items() for role in roles
)


def test_grant_helper_exists_secdef_and_owned_by_supabase_admin(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT p.prosecdef, pg_get_userbyid(p.proowner) "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.proname = 'bloom_grant_schema_usage'"
        )
        rows = cur.fetchall()
    assert rows, "grant helper public.bloom_grant_schema_usage is missing"
    prosecdef, owner = rows[0]
    assert prosecdef is True, "helper must be SECURITY DEFINER"
    assert owner == "supabase_admin", f"helper owned by {owner}, not supabase_admin"


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


def test_raw_grant_noops_as_postgres_but_helper_sticks(pg_conn):
    """The core proof: as postgres a raw grant no-ops; the helper makes it stick.

    All mutations are rolled back so the stack is unchanged.
    """
    try:
        with pg_conn.cursor() as cur:
            # As the owner (supabase_admin), revoke so we start from f.
            cur.execute("REVOKE USAGE ON SCHEMA storage FROM bloom_agent")
            # As postgres (what db push downgrades to), a raw grant silently no-ops.
            cur.execute("SET LOCAL ROLE postgres")
            cur.execute("GRANT USAGE ON SCHEMA storage TO bloom_agent")
            cur.execute("RESET ROLE")
            cur.execute("SELECT has_schema_privilege('bloom_agent','storage','USAGE')")
            assert cur.fetchone()[0] is False, "raw grant as postgres should no-op"
            # The helper, called even as postgres, grants with the owner's authority.
            cur.execute("SET LOCAL ROLE postgres")
            cur.execute("SELECT public.bloom_grant_schema_usage('storage','bloom_agent')")
            cur.execute("RESET ROLE")
            cur.execute("SELECT has_schema_privilege('bloom_agent','storage','USAGE')")
            assert cur.fetchone()[0] is True, "helper call should make the grant stick"
    finally:
        pg_conn.rollback()


def test_helper_is_idempotent(pg_conn):
    """Re-applying a grant via the helper changes nothing and does not error."""
    try:
        with pg_conn.cursor() as cur:
            cur.execute("SELECT public.bloom_grant_schema_usage('storage','bloom_agent')")
            cur.execute("SELECT public.bloom_grant_schema_usage('storage','bloom_agent')")
            cur.execute("SELECT has_schema_privilege('bloom_agent','storage','USAGE')")
            assert cur.fetchone()[0] is True
    finally:
        pg_conn.rollback()


@pytest.mark.parametrize("role", ["anon", "authenticated", "bloom_user", "bloom_agent"])
def test_unprivileged_roles_cannot_execute_helper(pg_conn, role):
    import psycopg

    try:
        with pg_conn.cursor() as cur:
            cur.execute(f"SET LOCAL ROLE {role}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute(
                    "SELECT public.bloom_grant_schema_usage('storage','bloom_user')"
                )
    finally:
        pg_conn.rollback()


def test_helper_rejects_out_of_whitelist_arguments(pg_conn):
    import psycopg

    for schema, role in [("vault", "bloom_user"), ("auth", "postgres")]:
        try:
            with pg_conn.cursor() as cur:
                with pytest.raises(psycopg.errors.RaiseException):
                    cur.execute(
                        "SELECT public.bloom_grant_schema_usage(%s, %s)", (schema, role)
                    )
        finally:
            pg_conn.rollback()
