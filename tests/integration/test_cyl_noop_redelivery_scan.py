"""
Integration tests for `fix-cyl-noop-redelivery-scan-resolution` (bloom#900, bloom#875).

A no-op re-delivery of an already-ingested envelope must mark the re-delivering
Workflow's own `cyl_pipeline_run_scans` row 'written', resolving the scan from the
existing source's own `cyl_trait_sources.scan_id` (with #880's run-scan-row lookup as
the backup when that is NULL), and must never relink a row another source already
carries.

LOCAL ONLY, and never migrates the shared dev DB: an autouse fixture applies this
change's migration body inside the test's own transaction on `pg_conn` and rolls it
back. Tests therefore use savepoints only, never commit()/rollback() mid-test, and
never a second connection (it would see the committed, older body). CI's
`compose-health-check` applies every migration to a fresh DB first.
"""

import json
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.integration.cyl_recipe_helpers import (  # noqa: E402
    REPO_ROOT,
    acl_set,
    seed_source,
    sql_body,
)
from tests.integration.test_cyl_writeback_rpc import (  # noqa: E402
    _call,
    _envelope,
    _seed_run_scan_for_writeback,
    _seed_scan,
    _source_snapshot,
    _trait,
)

MIGRATION_GLOB = "*_resolve_cyl_noop_redelivery_scan_from_source.sql"
ROLLBACK_GLOB = "*_resolve_cyl_noop_redelivery_scan_from_source_rollback.sql"
SIGNATURE = "public.insert_cyl_result_envelope(jsonb,text)"
# A line only the new body carries: the fallback's relink guard (design D8).
NEW_BODY_MARKER = "AND (source_id IS NULL OR source_id = v_source_id)"
RPC_ACL = {
    (r, "EXECUTE")
    for r in (
        "postgres",
        "service_role",
        "bloom_writer",
        "bloom_admin",
        "bloom_workflows",
    )
}


def _exactly_one(directory: str, glob: str):
    matches = sorted((REPO_ROOT / "supabase" / directory).glob(glob))
    assert (
        len(matches) == 1
    ), f"expected exactly one {directory}/{glob}, found {matches}"
    return matches[0]


def _live_body(cur) -> str:
    cur.execute("SELECT pg_get_functiondef(%s::regprocedure)", (SIGNATURE,))
    return cur.fetchone()[0]


@pytest.fixture(autouse=True)
def new_body(pg_conn):
    """Apply this change's migration body inside the test's transaction, and roll it
    back afterwards. Fails (not skips) when the migration file is missing."""
    path = _exactly_one("migrations", MIGRATION_GLOB)
    with pg_conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute(sql_body(path))
        applied = _live_body(cur)
    assert pg_conn.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS
    yield
    try:
        if pg_conn.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS:
            with pg_conn.cursor() as cur:
                assert (
                    _live_body(cur) == applied
                ), "the applied body was lost mid-test (a rollback/commit inside the test?)"
    finally:
        pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _wf() -> str:
    return f"wf-900-{uuid.uuid4().hex[:12]}"


def _key() -> str:
    return f"key-900-{uuid.uuid4().hex}"


def _deliver(cur, imgs, idem, *, workflow=None):
    env = _envelope(imgs, idempotency_key=idem, traits=[_trait("t900", 1.5)])
    return _call(cur, env, argo_workflow_name=workflow)


def _row(cur, workflow, scan_id):
    """(ctid, status, source_id) of one run-scan row; ctid changes on any UPDATE."""
    cur.execute(
        "SELECT ctid::text, status, source_id FROM cyl_pipeline_run_scans "
        "WHERE argo_workflow_name = %s AND scan_id = %s",
        (workflow, scan_id),
    )
    return cur.fetchone()


def _manual_origin(cur, scan=None):
    """A source first ingested with no workflow name (a manual `cyl ingest-result`)."""
    scan_id, imgs = scan or _seed_scan(cur)
    idem = _key()
    first = _deliver(cur, imgs, idem)
    assert first["was_noop"] is False and first["status_update_matched"] is None
    return scan_id, imgs, idem, first["source_id"]


def _assert_noop_return(res, *, matched):
    assert res["was_noop"] is True
    assert res["status_update_matched"] is matched
    assert res["scan_id"] is None
    assert res["trait_count"] == 0 and res["blob_count"] == 0


# --------------------------------------------------------------------------- #
# The bloom#900 shapes: a source no run-scan row carries
# --------------------------------------------------------------------------- #


def test_manual_origin_noop_is_marked_written(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs, idem, src = _manual_origin(cur)
        wb = _wf()
        _seed_run_scan_for_writeback(cur, scan_id, wb)
        before = _source_snapshot(cur, idem)
        res = _deliver(cur, imgs, idem, workflow=wb)
        _assert_noop_return(res, matched=True)
        assert res["source_id"] == src
        assert _row(cur, wb, scan_id)[1:] == ("written", src)
        assert _source_snapshot(cur, idem) == before
        cur.execute(
            "SELECT count(*) FROM cyl_scan_traits WHERE scan_id = %s AND source_id <> %s",
            (scan_id, src),
        )
        assert cur.fetchone()[0] == 0


def test_hand_submitted_origin_noop_is_marked_written(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs = _seed_scan(cur)
        idem, wa, wb = _key(), _wf(), _wf()
        first = _deliver(cur, imgs, idem, workflow=wa)  # no run-scan rows carry wa
        assert first["was_noop"] is False and first["status_update_matched"] is False
        _seed_run_scan_for_writeback(cur, scan_id, wb)
        before = _source_snapshot(cur, idem)
        res = _deliver(cur, imgs, idem, workflow=wb)
        _assert_noop_return(res, matched=True)
        assert _row(cur, wb, scan_id)[1:] == ("written", first["source_id"])
        assert _source_snapshot(cur, idem) == before


def test_backfilled_source_noop_is_marked_written(pg_conn):
    # Staging's source 228 got its scan_id from the recipe backfill, not the RPC; its
    # recipe and run columns came from the backfill too. Model that row directly.
    with pg_conn.cursor() as cur:
        scan_id, imgs = _seed_scan(cur)
        idem = _key()
        env = _envelope(imgs, idempotency_key=idem)
        src = seed_source(cur, json.dumps(env["provenance"]), idempotency_key=idem)
        cur.execute(
            "UPDATE cyl_trait_sources SET scan_id = %s WHERE id = %s", (scan_id, src)
        )
        wb = _wf()
        _seed_run_scan_for_writeback(cur, scan_id, wb)
        res = _call(cur, env, argo_workflow_name=wb)
        _assert_noop_return(res, matched=True)
        assert _row(cur, wb, scan_id)[1:] == ("written", src)


# --------------------------------------------------------------------------- #
# Lookup order and backup (design D1)
# --------------------------------------------------------------------------- #


def test_source_scan_id_wins_over_a_carrying_row(pg_conn):
    with pg_conn.cursor() as cur:
        s1, imgs1 = _seed_scan(cur)
        s2, _ = _seed_scan(cur)
        idem, wa, wb = _key(), _wf(), _wf()
        _seed_run_scan_for_writeback(cur, s1, wa)
        src = _deliver(cur, imgs1, idem, workflow=wa)["source_id"]
        # Make the carrying row disagree with the source's own recorded scan.
        cur.execute(
            "UPDATE cyl_pipeline_run_scans SET scan_id = %s WHERE argo_workflow_name = %s",
            (s2, wa),
        )
        _seed_run_scan_for_writeback(cur, s1, wb)
        _seed_run_scan_for_writeback(cur, s2, wb)
        s2_before = _row(cur, wb, s2)
        res = _deliver(cur, imgs1, idem, workflow=wb)
        _assert_noop_return(res, matched=True)
        assert _row(cur, wb, s1)[1:] == ("written", src)
        assert _row(cur, wb, s2) == s2_before


def test_run_scan_lookup_is_the_backup_when_source_scan_is_null(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs = _seed_scan(cur)
        idem, wa, wb = _key(), _wf(), _wf()
        _seed_run_scan_for_writeback(cur, scan_id, wa)
        src = _deliver(cur, imgs, idem, workflow=wa)["source_id"]
        cur.execute("UPDATE cyl_trait_sources SET scan_id = NULL WHERE id = %s", (src,))
        _seed_run_scan_for_writeback(cur, scan_id, wb)
        res = _deliver(cur, imgs, idem, workflow=wb)
        _assert_noop_return(res, matched=True)
        assert _row(cur, wb, scan_id)[1:] == ("written", src)


# --------------------------------------------------------------------------- #
# Residual no-match cases: status_update_matched false, nothing changed
# --------------------------------------------------------------------------- #


def test_noop_with_no_recorded_scan_and_no_carrying_row_reports_no_match(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs, idem, src = _manual_origin(cur)
        cur.execute("UPDATE cyl_trait_sources SET scan_id = NULL WHERE id = %s", (src,))
        wb = _wf()
        _seed_run_scan_for_writeback(cur, scan_id, wb)
        before = _row(cur, wb, scan_id)
        _assert_noop_return(_deliver(cur, imgs, idem, workflow=wb), matched=False)
        assert _row(cur, wb, scan_id) == before


def test_noop_under_workflow_that_did_not_dispatch_the_scan_matches_nothing(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs, idem, _ = _manual_origin(cur)
        s2, _ = _seed_scan(cur)
        wb = _wf()
        _seed_run_scan_for_writeback(cur, s2, wb)
        before = _row(cur, wb, s2)
        _assert_noop_return(_deliver(cur, imgs, idem, workflow=wb), matched=False)
        assert _row(cur, wb, s2) == before


def test_noop_does_not_resurrect_a_failed_row(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs, idem, _ = _manual_origin(cur)
        wb = _wf()
        _seed_run_scan_for_writeback(cur, scan_id, wb, status="failed")
        before = _row(cur, wb, scan_id)
        _assert_noop_return(_deliver(cur, imgs, idem, workflow=wb), matched=False)
        assert _row(cur, wb, scan_id) == before


def test_noop_does_not_replace_another_source_link(pg_conn):
    # Source Y was first delivered under wf-a (so #880's carrying-row lookup would find
    # it as well); wf-b then writes a fresh source X for the same scan; a no-op
    # re-delivery of Y under wf-b must leave wf-b's row linked to X (design D8).
    with pg_conn.cursor() as cur:
        scan_id, imgs = _seed_scan(cur)
        wa, wb = _wf(), _wf()
        key_y, key_x = _key(), _key()
        _seed_run_scan_for_writeback(cur, scan_id, wa)
        _deliver(cur, imgs, key_y, workflow=wa)
        _seed_run_scan_for_writeback(cur, scan_id, wb)
        x = _deliver(cur, imgs, key_x, workflow=wb)
        assert x["was_noop"] is False and x["status_update_matched"] is True
        before = _row(cur, wb, scan_id)
        assert before[1:] == ("written", x["source_id"])
        _assert_noop_return(_deliver(cur, imgs, key_y, workflow=wb), matched=False)
        assert _row(cur, wb, scan_id) == before


def test_fresh_delivery_after_a_noop_link_wins(pg_conn):
    # The relink guard is one-directional: a no-op never replaces a link, but a fresh
    # delivery (step 9) always does, so the row ends on the source this run produced.
    with pg_conn.cursor() as cur:
        scan_id, imgs, key_y, y = _manual_origin(cur)
        wb = _wf()
        _seed_run_scan_for_writeback(cur, scan_id, wb)
        _assert_noop_return(_deliver(cur, imgs, key_y, workflow=wb), matched=True)
        assert _row(cur, wb, scan_id)[1:] == ("written", y)
        x = _deliver(cur, imgs, _key(), workflow=wb)
        assert x["was_noop"] is False and x["status_update_matched"] is True
        assert _row(cur, wb, scan_id)[1:] == ("written", x["source_id"])


def test_same_key_different_scan_noop_marks_only_the_recorded_scan(pg_conn):
    with pg_conn.cursor() as cur:
        s1, _, idem, src = _manual_origin(cur)
        s2, imgs2 = _seed_scan(cur)
        wb = _wf()
        _seed_run_scan_for_writeback(cur, s1, wb)
        _seed_run_scan_for_writeback(cur, s2, wb)
        s2_before = _row(cur, wb, s2)
        res = _deliver(cur, imgs2, idem, workflow=wb)  # same key, S2's image_ids
        _assert_noop_return(res, matched=True)
        assert _row(cur, wb, s1)[1:] == ("written", src)
        assert _row(cur, wb, s2) == s2_before
        cur.execute("SELECT count(*) FROM cyl_scan_traits WHERE scan_id = %s", (s2,))
        assert cur.fetchone()[0] == 0


def test_manual_origin_chain_across_two_workflows(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs, idem, src = _manual_origin(cur)
        for wf in (_wf(), _wf()):
            _seed_run_scan_for_writeback(cur, scan_id, wf)
            _assert_noop_return(_deliver(cur, imgs, idem, workflow=wf), matched=True)
            assert _row(cur, wf, scan_id)[1:] == ("written", src)


def test_noop_without_workflow_name_touches_no_run_scan_row(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs, idem, _ = _manual_origin(cur)
        wb = _wf()
        _seed_run_scan_for_writeback(cur, scan_id, wb)
        before = _row(cur, wb, scan_id)
        _assert_noop_return(_deliver(cur, imgs, idem), matched=None)
        assert _row(cur, wb, scan_id) == before


def test_manual_origin_redelivery_survives_reconciliation(pg_conn):
    # The run-level symptom of bloom#900: after end-of-batch reconciliation the
    # rescued scan counts as done, and only the scan with no result counts as failed.
    with pg_conn.cursor() as cur:
        s1, imgs1, idem, src = _manual_origin(cur)
        s2, _ = _seed_scan(cur)
        wb = _wf()
        _seed_run_scan_for_writeback(cur, s1, wb)
        _seed_run_scan_for_writeback(cur, s2, wb)
        _deliver(cur, imgs1, idem, workflow=wb)
        cur.execute(
            "SELECT public.fail_cyl_pipeline_run_scans_without_result(%s, %s)",
            (wb, "no result produced for this scan by write-back"),
        )
        cur.execute(
            "SELECT count(*) FILTER (WHERE status IN ('written', 'reused')), "
            "       count(*) FILTER (WHERE status = 'failed') "
            "  FROM cyl_pipeline_run_scans WHERE argo_workflow_name = %s",
            (wb,),
        )
        assert cur.fetchone() == (1, 1)
        assert _row(cur, wb, s1)[1:] == ("written", src)


def _scan_counts(cur) -> dict:
    """Sequential + index scans this transaction has made of the trait and blob tables."""
    cur.execute(
        "SELECT relname, coalesce(seq_scan, 0) + coalesce(idx_scan, 0) "
        "  FROM pg_stat_xact_user_tables "
        " WHERE relname IN ('cyl_scan_traits', 'cyl_scan_intermediates')"
    )
    counts = dict(cur.fetchall())
    # Both rows must be there; a renamed table would otherwise make {} == {} pass.
    assert set(counts) == {"cyl_scan_traits", "cyl_scan_intermediates"}, counts
    return counts


def _noop_scan_delta(cur) -> dict:
    """Scans of the two tables made by one no-op re-delivery that takes the fallback."""
    scan_id, imgs, idem, _ = _manual_origin(cur)
    wb = _wf()
    _seed_run_scan_for_writeback(cur, scan_id, wb)
    before = _scan_counts(cur)
    _assert_noop_return(_deliver(cur, imgs, idem, workflow=wb), matched=True)
    after = _scan_counts(cur)
    return {name: after[name] - before[name] for name in before}


def test_noop_reads_no_trait_or_blob_table(pg_conn):
    # Behavioural half of the timeout guard (design D1): the no-op path never scans
    # cyl_scan_traits (~28.8M rows on staging, no index leading on source_id).
    with pg_conn.cursor() as cur:
        assert _noop_scan_delta(cur) == {
            "cyl_scan_traits": 0,
            "cyl_scan_intermediates": 0,
        }


def test_trait_read_guard_detects_mutation(pg_conn):
    # Proves the test above can fail: a no-op path that reads cyl_scan_traits is caught.
    marker = "                 WHERE id = v_source_id;\n"
    injected = "                PERFORM 1 FROM public.cyl_scan_traits WHERE source_id = v_source_id LIMIT 1;\n"
    with pg_conn.cursor() as cur:
        original = _live_body(cur)
        assert original.count(marker) == 1
        cur.execute(original.replace(marker, marker + injected))
        try:
            assert _noop_scan_delta(cur)["cyl_scan_traits"] > 0
        finally:
            cur.execute(original)


# --------------------------------------------------------------------------- #
# Definition hygiene: ACL, hardening, idempotency, rollback
# --------------------------------------------------------------------------- #


def _assert_hardened(cur):
    assert acl_set(cur, SIGNATURE) == RPC_ACL
    cur.execute(
        "SELECT proowner::regrole::text, prosecdef, proconfig FROM pg_proc "
        "WHERE oid = %s::regprocedure",
        (SIGNATURE,),
    )
    owner, secdef, config = cur.fetchone()
    assert owner == "postgres" and secdef is True
    assert any(c.startswith("search_path=") for c in (config or [])), config
    cur.execute(
        "SELECT array_agg(pronargs ORDER BY pronargs) FROM pg_proc "
        "WHERE proname = 'insert_cyl_result_envelope' AND pronamespace = 'public'::regnamespace"
    )
    assert cur.fetchone()[0] == [2]


def test_redefinition_keeps_hardening_acl_and_single_overload(pg_conn):
    with pg_conn.cursor() as cur:
        _assert_hardened(cur)


def test_migration_body_is_idempotent(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(sql_body(_exactly_one("migrations", MIGRATION_GLOB)))
        _assert_hardened(cur)
        assert NEW_BODY_MARKER in _live_body(cur)


def test_rollback_restores_the_previous_behaviour(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs, idem, _ = _manual_origin(cur)
        wb = _wf()
        _seed_run_scan_for_writeback(cur, scan_id, wb)
        migration = sql_body(_exactly_one("migrations", MIGRATION_GLOB))
        cur.execute(sql_body(_exactly_one("rollbacks", ROLLBACK_GLOB)))
        try:
            assert NEW_BODY_MARKER not in _live_body(cur)
            _assert_hardened(cur)
            _assert_noop_return(_deliver(cur, imgs, idem, workflow=wb), matched=False)
            assert _row(cur, wb, scan_id)[1:] == ("queued", None)
        finally:
            # Re-apply, so the fixture's end-of-test check compares the body under test.
            cur.execute(migration)
