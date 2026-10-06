"""
Integration tests for letting the RNA-seq status poller (bloom_workflows) look up who started
a run, with rnaseq_run_requesters, so it can email them when the run finishes. The other
callers are unchanged, and anon and the plain authenticated role still can't call it.

LOCAL ONLY: `pg_conn` connects as `supabase_admin`; every test rolls back. Privileges are
checked with has_function_privilege, not by calling as a denied role.
"""

import pytest

from tests.integration.test_rnaseq_runs import _find_one, _sql_body

psycopg = pytest.importorskip("psycopg")

MIGRATION = _find_one("migrations", "*_let_workflows_look_up_run_requesters.sql")
ROLLBACK = _find_one("rollbacks", "*_let_workflows_look_up_run_requesters_rollback.sql")
FN = "public.rnaseq_run_requesters(bigint[])"
CALLERS = ("bloom_user", "bloom_writer", "bloom_admin", "bloom_workflows")
NOT_CALLERS = ("anon", "authenticated", "bloom_agent")


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        yield c
    pg_conn.rollback()


def _may_call(cur, role):
    cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, FN))
    return cur.fetchone()[0]


@pytest.mark.parametrize("role, allowed", [(r, True) for r in CALLERS]
                         + [(r, False) for r in NOT_CALLERS])
def test_the_poller_may_look_up_requesters_and_nobody_else_gains(cur, role, allowed):
    assert _may_call(cur, role) is allowed


def test_the_poller_reads_no_user_accounts_directly(cur):
    # Why it goes through the function.
    cur.execute("SELECT has_table_privilege('bloom_workflows', 'auth.users', 'SELECT')")
    assert cur.fetchone()[0] is False


def test_the_migration_can_be_run_again(cur):
    cur.execute(_sql_body(MIGRATION))
    assert _may_call(cur, "bloom_workflows") is True


def test_the_rollback_takes_back_only_the_pollers_call(cur):
    cur.execute(_sql_body(ROLLBACK))
    assert _may_call(cur, "bloom_workflows") is False
    for role in ("bloom_user", "bloom_writer", "bloom_admin"):
        assert _may_call(cur, role) is True, role
