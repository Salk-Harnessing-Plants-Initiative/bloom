"""
Integration tests for `record_bloommcp_usage` (bloom#406, openspec
add-bloommcp-caller-identity — `20260730000000_create_bloommcp_usage.sql`).

Real-Postgres upsert/concurrency semantics for the `bloommcp_usage` aggregate —
placed here (root-level `tests/integration/`, not `bloommcp/tests/` with
`@pytest.mark.integration`, which means something different in bloommcp's own
`pyproject.toml` — "full-fixture statsmodels/umap oracle tests" — and is
excluded from every automated CI job) so it runs in CI's `compose-health-check`
job, matching the existing `pg_conn`/`test_cyl_writeback_rpc.py` convention.

LOCAL ONLY: `pg_conn` connects to 127.0.0.1 on POSTGRES_HOST_PORT as
`supabase_admin` (BYPASSRLS) — every test rolls back.
"""

from __future__ import annotations

import threading

import pytest

# Skip the whole module if psycopg isn't available (matches the sibling tests).
psycopg = pytest.importorskip("psycopg")

RPC = "public.record_bloommcp_usage"


def _call(cur, identity: str, action: str):
    cur.execute(f"SELECT {RPC}(%s, %s)", (identity, action))


def _row(cur, identity: str):
    cur.execute(
        "SELECT identity, first_seen, last_seen, request_count, last_action "
        "FROM bloommcp_usage WHERE identity = %s",
        (identity,),
    )
    return cur.fetchone()


def test_first_call_from_new_identity_creates_a_row(pg_conn):
    identity = "11111111-1111-1111-1111-111111111111"
    with pg_conn.cursor() as cur:
        _call(cur, identity, "qc_clean")
        row = _row(cur, identity)
    pg_conn.rollback()

    assert row is not None
    _, first_seen, last_seen, request_count, last_action = row
    assert request_count == 1
    assert last_action == "qc_clean"
    assert first_seen == last_seen


def test_repeat_call_increments_count_and_updates_last_action(pg_conn):
    identity = "22222222-2222-2222-2222-222222222222"
    with pg_conn.cursor() as cur:
        _call(cur, identity, "qc_clean")
        first_row = _row(cur, identity)

        _call(cur, identity, "pca_analysis")
        second_row = _row(cur, identity)
    pg_conn.rollback()

    first_seen_1, last_seen_1, count_1, action_1 = (
        first_row[1],
        first_row[2],
        first_row[3],
        first_row[4],
    )
    first_seen_2, last_seen_2, count_2, action_2 = (
        second_row[1],
        second_row[2],
        second_row[3],
        second_row[4],
    )

    assert count_1 == 1 and action_1 == "qc_clean"
    assert count_2 == 2 and action_2 == "pca_analysis"
    assert first_seen_2 == first_seen_1  # unchanged
    assert last_seen_2 >= last_seen_1


def test_two_anonymous_calls_collapse_into_one_row(pg_conn):
    with pg_conn.cursor() as cur:
        _call(cur, "anonymous", "core_list_available_experiments")
        _call(cur, "anonymous", "qc_clean")

        cur.execute("SELECT count(*) FROM bloommcp_usage WHERE identity = 'anonymous'")
        (n_rows,) = cur.fetchone()
        row = _row(cur, "anonymous")
    pg_conn.rollback()

    assert n_rows == 1
    assert row[3] == 2  # request_count
    assert row[4] == "qc_clean"  # last_action, from the second call


def test_concurrent_first_calls_from_same_new_identity_do_not_lose_an_update(
    pg_conn, pg_conninfo
):
    """Two overlapping transactions both INSERT a brand-new identity for the
    first time; the second to commit must see its update applied on top of
    the first (request_count = 2), not silently discarded. Needs two
    independent connections (`pg_conn` gives one already committed/rolled-back
    per test) — `pg_conninfo` (tests/integration/conftest.py) is the same
    connection string `pg_conn` itself uses."""
    identity = "33333333-3333-3333-3333-333333333333"
    conn_a = psycopg.connect(pg_conninfo)
    conn_b = psycopg.connect(pg_conninfo)
    try:
        cur_a = conn_a.cursor()
        cur_b = conn_b.cursor()

        # A inserts first and holds the row lock (no commit yet).
        _call(cur_a, identity, "action_a")

        # B's insert conflicts on the same new PK and must wait for A's lock.
        # Run it on a thread so this test doesn't deadlock itself.
        b_done = threading.Event()

        def _run_b():
            _call(cur_b, identity, "action_b")
            conn_b.commit()
            b_done.set()

        b_thread = threading.Thread(target=_run_b)
        b_thread.start()
        assert not b_done.wait(timeout=0.5)  # B is genuinely blocked on A's lock

        conn_a.commit()
        assert b_done.wait(timeout=5.0)  # B proceeds once A releases the lock
        b_thread.join()

        with pg_conn.cursor() as cur:
            row = _row(cur, identity)
        pg_conn.rollback()
    finally:
        conn_a.close()
        conn_b.close()

    assert row[3] == 2  # request_count — not 1 (lost update)
