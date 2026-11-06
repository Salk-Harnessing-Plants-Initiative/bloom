"""
Integration tests for migration 20261106210000_revoke_dblink_from_api_roles.sql.

The first-boot script installs the dblink extension into `public`, where PostgREST exposes every
function as /rest/v1/rpc/<name> and Postgres lets PUBLIC execute it by default. No API role may
call dblink: it opens connections from the database server to other hosts. The superuser that
runs the first-boot script keeps it.

Asserted with has_function_privilege, never by calling the functions.
"""

import os

import pytest

API_ROLES = (
    "anon",
    "authenticated",
    "service_role",
    "bloom_user",
    "bloom_writer",
    "bloom_admin",
    "bloom_agent",
    "bloom_workflows",
)
IN_CI = os.environ.get("GITHUB_ACTIONS") == "true"


@pytest.fixture
def dblink_functions(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.oid::regprocedure::text FROM pg_proc p
              JOIN pg_depend d ON d.classid = 'pg_proc'::regclass AND d.objid = p.oid
                              AND d.deptype = 'e'
              JOIN pg_extension e ON e.oid = d.refobjid AND e.extname = 'dblink'
            """
        )
        functions = [r[0] for r in cur.fetchall()]
    if not functions:
        message = "dblink is not installed; the first-boot script installs it"
        if IN_CI:
            pytest.fail(message)
        pytest.skip(message)
    return functions


@pytest.mark.parametrize("role", API_ROLES + ("public",))
def test_no_api_role_can_call_dblink(pg_conn, dblink_functions, role):
    with pg_conn.cursor() as cur:
        if role == "public":
            cur.execute(
                """
                SELECT p.oid::regprocedure::text FROM pg_proc p
                 WHERE p.oid::regprocedure::text = ANY (%s)
                   AND EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
                                WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE')
                """,
                (dblink_functions,),
            )
        else:
            cur.execute(
                """
                SELECT f FROM unnest(%s::text[]) AS f
                 WHERE has_function_privilege(%s, f::regprocedure, 'EXECUTE')
                """,
                (dblink_functions, role),
            )
        callable_by_role = [r[0] for r in cur.fetchall()]
    assert callable_by_role == [], f"{role} can execute {callable_by_role}"


def test_the_superuser_keeps_dblink(pg_conn, dblink_functions):
    """The first-boot script creates the _supabase database through dblink_exec as the superuser."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        assert cur.fetchone()[0] is True
        cur.execute(
            "SELECT has_function_privilege('public.dblink_exec(text,text)', 'EXECUTE')"
        )
        assert cur.fetchone()[0] is True
