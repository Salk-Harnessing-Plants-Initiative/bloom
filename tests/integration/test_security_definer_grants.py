"""No SECURITY DEFINER function in ``public`` is executable by ``anon`` or, unless
deliberately allowlisted, by ``authenticated`` (repin-cyl-contract-a9).

Supabase's default privileges grant EXECUTE on every new ``public`` function to
``anon`` and ``authenticated`` directly, so ``REVOKE ... FROM PUBLIC`` alone does not
remove them (see ``20260730120000_create_cyl_pipeline_runs.sql``). A SECURITY DEFINER
function runs as its owner and bypasses RLS, so an extra grantee is an extra writer.
The repo convention is ``REVOKE ... FROM PUBLIC, anon, authenticated`` followed by
grants to named roles; this catalog-wide check catches the next function that copies
an older ``FROM PUBLIC``-only line.

Reads the live catalog, so it runs in CI's ``compose-health-check`` job against the
fully migrated database. Read-only.
"""

import pytest

psycopg = pytest.importorskip("psycopg")

# SECURITY DEFINER functions that authenticated may call on purpose. Read-only only;
# add an entry with the reason, never to silence a writer.
AUTHENTICATED_ALLOWED = {
    # Read-only summary counter the web app calls for signed-in users
    # (20260817150000; anon is revoked there).
    "compute_cyl_experiment_summary_counts_live",
}

_DEFINERS_REACHABLE_BY = """
    SELECT p.oid::regprocedure::text, p.proname
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public'
       AND p.prosecdef
       AND has_function_privilege(%s, p.oid, 'EXECUTE')
     ORDER BY 1
"""


def test_the_catalog_has_security_definer_functions_to_check(pg_conn):
    # Guards the two checks below against passing vacuously on an empty schema.
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.prosecdef"
        )
        assert cur.fetchone()[0] > 0
    pg_conn.rollback()


def test_no_security_definer_function_in_public_is_executable_by_anon(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_DEFINERS_REACHABLE_BY, ("anon",))
        exposed = [sig for sig, _ in cur.fetchall()]
    pg_conn.rollback()
    assert exposed == [], (
        f"anon can EXECUTE these SECURITY DEFINER functions: {exposed}. "
        "Revoke FROM PUBLIC, anon, authenticated and grant named roles only."
    )


def test_security_definer_functions_executable_by_authenticated_are_allowlisted(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_DEFINERS_REACHABLE_BY, ("authenticated",))
        exposed = [sig for sig, name in cur.fetchall() if name not in AUTHENTICATED_ALLOWED]
    pg_conn.rollback()
    assert exposed == [], (
        f"authenticated can EXECUTE these SECURITY DEFINER functions: {exposed}. "
        "Revoke it, or add a read-only function to AUTHENTICATED_ALLOWED with its reason."
    )
