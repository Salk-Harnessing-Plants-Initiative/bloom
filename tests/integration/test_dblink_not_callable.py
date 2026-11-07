"""
Only the superuser may call dblink (revoked in supabase/grants/schema_grants.sql).

The first-boot script installs the dblink extension into `public`, where PostgREST exposes every
function as /rest/v1/rpc/<name> and Postgres lets PUBLIC execute it by default. dblink opens
connections from the database server to other hosts. The grants file moves it to `extensions`,
which PostgREST doesn't serve, and revokes it from every grantee but the owner.

Every non-superuser role is checked, so a new role or one that inherits access is caught too.
Asserted with has_function_privilege, never by calling the functions.
"""

import json
import os
import urllib.error
import urllib.request

import pytest

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


def test_no_non_superuser_role_can_call_dblink(pg_conn, dblink_functions):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT rolname FROM pg_roles WHERE NOT rolsuper")
        checked = {r[0] for r in cur.fetchall()}
        cur.execute(
            """
            SELECT r.rolname, f FROM pg_roles r CROSS JOIN unnest(%s::text[]) AS f
             WHERE NOT r.rolsuper AND has_function_privilege(r.oid, f::regprocedure, 'EXECUTE')
             ORDER BY 1, 2
            """,
            (dblink_functions,),
        )
        callable_by = cur.fetchall()
    assert {
        "anon",
        "authenticated",
        "service_role",
        "bloom_agent",
        "bloom_workflows",
        "postgres",
    } <= checked
    assert callable_by == [], f"non-superuser roles can execute dblink: {callable_by}"


def test_public_cannot_call_dblink(pg_conn, dblink_functions):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.oid::regprocedure::text FROM pg_proc p
             WHERE p.oid = ANY (%s::regprocedure[])
               AND EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
                            WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE')
            """,
            (dblink_functions,),
        )
        callable_by_public = [r[0] for r in cur.fetchall()]
    assert callable_by_public == [], f"PUBLIC can execute {callable_by_public}"


def test_dblink_is_not_in_a_schema_postgrest_serves(pg_conn, dblink_functions):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace"
            " WHERE e.extname = 'dblink'"
        )
        assert cur.fetchone()[0] == "extensions"


def test_postgrest_does_not_serve_the_extensions_schema(base_url, service_role_key):
    """An API test, so adding `extensions` to PGRST_DB_SCHEMAS can't slip past the DB checks."""
    req = urllib.request.Request(
        f"{base_url}/api/rest/v1/",  # /api/* -> kong -> postgrest
        headers={
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Accept-Profile": "extensions",
        },
    )
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=10)
    assert err.value.code == 406
    assert json.loads(err.value.read().decode())["code"] == "PGRST106"
