"""
Integration tests for `cyl_scan_latest_source` (bloom#637, supersedes PR #654's Phase 1).

One row per scan (`scan_id` PK, `max_source_id`), maintained by a trigger on `cyl_scan_traits`,
instead of a boolean stored on every trait row. `cyl_scan_traits_source.is_latest` is now computed
by joining to this table (see `test_cyl_read_path.py` for the view's own equivalence tests) rather
than a live window aggregate.

LOCAL ONLY: the `pg_conn`/`pg_conninfo` fixtures connect to 127.0.0.1 on POSTGRES_HOST_PORT as
`supabase_admin` (BYPASSRLS); every test using `pg_conn` alone rolls back. The concurrency tests
use two independent `pg_conninfo` connections with real threads and explicit interleaving, and
clean up via explicit `DELETE` (their effects are committed, not rolled back).
"""

import re
import threading
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.integration.test_cyl_read_path import (  # noqa: E402
    _deliver,
    _seed_experiment_scan,
    _trait,
)

REPO_ROOT = Path(__file__).parent.parent.parent
_TS = "20260817010000_create_cyl_scan_latest_source"
MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_TS}.sql"
ROLLBACK = REPO_ROOT / "supabase" / "rollbacks" / f"{_TS}_rollback.sql"

# The two later migrations in this change, whose rollbacks must run BEFORE this one's -- this
# file's own rollback test exercises the full documented order, not just this migration in
# isolation (see the rollback SQL files' own "ROLLBACK ORDER" header comments).
_TRAIT_COUNTS_TS = "20260817020000_create_cyl_experiment_trait_counts"
TRAIT_COUNTS_MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_TRAIT_COUNTS_TS}.sql"
TRAIT_COUNTS_ROLLBACK = REPO_ROOT / "supabase" / "rollbacks" / f"{_TRAIT_COUNTS_TS}_rollback.sql"
_REWRITE_TS = "20260817030000_rewrite_get_experiment_summary_counts"
REWRITE_MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_REWRITE_TS}.sql"
REWRITE_ROLLBACK = REPO_ROOT / "supabase" / "rollbacks" / f"{_REWRITE_TS}_rollback.sql"


def _sql_body(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def _cleanup_seeded_experiment(cur, experiment_id):
    """Delete every row `_seed_experiment_scan` (plus any `_deliver`/`_mint_source_and_trait`
    calls against it) created, in dependency order. The real-connection concurrency tests below
    commit rather than roll back, so without this their seeded species/experiments/waves/
    accessions/plants/scans/images accumulate permanently in a long-lived local dev DB across
    every run -- caught in round-2 review. Does not clean up `cyl_trait_sources`/`cyl_traits`
    rows (smaller footprint, harder to attribute precisely without tracking ids per call)."""
    cur.execute("SELECT species_id FROM cyl_experiments WHERE id=%s", (experiment_id,))
    row = cur.fetchone()
    species_id = row[0] if row else None
    cur.execute(
        "SELECT DISTINCT p.accession_id FROM cyl_plants p "
        "JOIN cyl_waves w ON w.id = p.wave_id WHERE w.experiment_id=%s",
        (experiment_id,),
    )
    accession_ids = [r[0] for r in cur.fetchall() if r[0] is not None]
    # cyl_scan_traits/cyl_scan_latest_source have NO further FK from cyl_scans in the CASCADE
    # direction (cyl_scan_traits.scan_id is NO ACTION) -- must be emptied first, or the
    # cyl_experiments delete below fails with a foreign-key violation instead of cascading.
    cur.execute(
        "DELETE FROM cyl_scan_traits WHERE scan_id IN "
        "(SELECT s.id FROM cyl_scans s JOIN cyl_plants p ON p.id = s.plant_id "
        " JOIN cyl_waves w ON w.id = p.wave_id WHERE w.experiment_id=%s)",
        (experiment_id,),
    )
    cur.execute(
        "DELETE FROM cyl_scan_latest_source WHERE scan_id IN "
        "(SELECT s.id FROM cyl_scans s JOIN cyl_plants p ON p.id = s.plant_id "
        " JOIN cyl_waves w ON w.id = p.wave_id WHERE w.experiment_id=%s)",
        (experiment_id,),
    )
    # Cascades to cyl_waves -> cyl_plants -> cyl_scans -> cyl_images.
    cur.execute("DELETE FROM cyl_experiments WHERE id=%s", (experiment_id,))
    if accession_ids:
        cur.execute("DELETE FROM accessions WHERE id = ANY(%s)", (accession_ids,))
    if species_id is not None:
        cur.execute("DELETE FROM species WHERE id=%s", (species_id,))


def _max_source(cur, scan_id):
    cur.execute(
        "SELECT max_source_id FROM cyl_scan_latest_source WHERE scan_id=%s", (scan_id,)
    )
    row = cur.fetchone()
    return row[0] if row else None


# --------------------------------------------------------------------------- #
# Test helper self-check
# --------------------------------------------------------------------------- #


def test_cleanup_seeded_experiment_removes_every_row(pg_conn):
    """`_cleanup_seeded_experiment` is itself untested -- round-3 review flagged this: every
    real-connection concurrency test below depends on it to avoid leaking seeded
    species/experiments/waves/accessions/plants/scans/images/traits into the long-lived local dev
    DB (it commits rather than rolling back), so a silent gap in its own delete-order coverage
    would go unnoticed until the dev DB accumulated orphans across many test runs. Seeds one
    experiment with two trait-bearing deliveries (so cyl_scan_traits and cyl_scan_latest_source
    both have real rows to remove), calls the helper directly, and asserts zero rows remain
    across every table it touches."""
    with pg_conn.cursor() as cur:
        experiment_id, scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])
        _deliver(cur, imgs, "reproc", traits=[_trait("length", 2.0)])

        cur.execute("SELECT species_id FROM cyl_experiments WHERE id=%s", (experiment_id,))
        species_id = cur.fetchone()[0]
        cur.execute(
            "SELECT DISTINCT p.accession_id FROM cyl_plants p "
            "JOIN cyl_waves w ON w.id = p.wave_id WHERE w.experiment_id=%s",
            (experiment_id,),
        )
        accession_ids = [r[0] for r in cur.fetchall() if r[0] is not None]

        # Sanity check: there's real data to clean up before we assert it's gone.
        assert _max_source(cur, scan_id) is not None
        cur.execute("SELECT count(*) FROM cyl_scan_traits WHERE scan_id=%s", (scan_id,))
        assert cur.fetchone()[0] > 0

        _cleanup_seeded_experiment(cur, experiment_id)

        cur.execute("SELECT count(*) FROM cyl_scan_traits WHERE scan_id=%s", (scan_id,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM cyl_scan_latest_source WHERE scan_id=%s", (scan_id,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM cyl_scans WHERE id=%s", (scan_id,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM cyl_experiments WHERE id=%s", (experiment_id,))
        assert cur.fetchone()[0] == 0
        for accession_id in accession_ids:
            cur.execute("SELECT count(*) FROM accessions WHERE id=%s", (accession_id,))
            assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM species WHERE id=%s", (species_id,))
        assert cur.fetchone()[0] == 0
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Trigger maintenance
# --------------------------------------------------------------------------- #


def test_fresh_insert_creates_row_with_source_max(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        src = _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])
        assert _max_source(cur, scan_id) == src
    pg_conn.rollback()


def test_rerun_updates_max_source_to_new_higher_source(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        s1 = _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])
        s2 = _deliver(cur, imgs, "reproc", traits=[_trait("length", 2.0)])
        assert s2 > s1
        assert _max_source(cur, scan_id) == s2
    pg_conn.rollback()


def test_deleting_latest_promotes_next_highest(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        s1 = _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])
        s2 = _deliver(cur, imgs, "reproc", traits=[_trait("length", 2.0)])
        cur.execute(
            "DELETE FROM cyl_scan_traits WHERE scan_id=%s AND source_id=%s", (scan_id, s2)
        )
        assert _max_source(cur, scan_id) == s1
    pg_conn.rollback()


def test_all_null_source_scan_has_null_max_source(pg_conn):
    """Legacy pre-source-tracking data: a scan whose only rows have source_id IS NULL. max(source_id)
    over an all-NULL group is NULL (aggregates ignore NULLs; an all-NULL input has nothing to
    aggregate), and cyl_scan_traits_source's IS NOT DISTINCT FROM comparison then correctly treats
    those rows as latest (NULL IS NOT DISTINCT FROM NULL = true) -- see design.md D1/D3."""
    with pg_conn.cursor() as cur:
        _, scan_id, _imgs = _seed_experiment_scan(cur)
        cur.execute(
            "INSERT INTO cyl_traits (name) VALUES (%s) RETURNING id", (f"t-{uuid.uuid4().hex[:8]}",)
        )
        trait_id = cur.fetchone()[0]
        cur.execute("SET LOCAL ROLE bloom_admin")
        cur.execute(
            "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value) "
            "VALUES (%s, NULL, %s, %s)",
            (scan_id, trait_id, 1.0),
        )
        cur.execute("RESET ROLE")
        assert _max_source(cur, scan_id) is None
        cur.execute(
            "SELECT is_latest FROM cyl_scan_traits_source WHERE scan_id=%s", (scan_id,)
        )
        assert cur.fetchone()[0] is True
    pg_conn.rollback()


def test_deleting_all_rows_leaves_null_max_source_no_error(pg_conn):
    """A scan whose last trait row is deleted has nothing left to aggregate -- max(source_id) over
    zero rows is NULL, and the trigger's upsert must not error (the ghost row it leaves behind is
    harmless: cyl_scan_traits_source's inner join has nothing left to match against it).

    Asserts the row count separately from `_max_source`'s return value: `_max_source` returns
    `None` both when the row exists with `max_source_id = NULL` (the claim this test makes) and
    when no row exists at all (e.g. a buggy trigger that DELETEs on an empty aggregate instead of
    upserting NULL) -- caught in round-2 review as the same class of non-discriminating assertion
    round 1 was fixed for elsewhere."""
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])
        cur.execute("DELETE FROM cyl_scan_traits WHERE scan_id=%s", (scan_id,))
        cur.execute(
            "SELECT count(*) FROM cyl_scan_latest_source WHERE scan_id=%s", (scan_id,)
        )
        assert cur.fetchone()[0] == 1  # the ghost row exists...
        assert _max_source(cur, scan_id) is None  # ...with max_source_id = NULL, not absent
    pg_conn.rollback()


def test_cross_scan_update_recomputes_both_scans(pg_conn):
    """A row's scan_id can be reassigned (bloom_admin correcting a mis-attributed trait row) --
    caught in round-2 review: the trigger's original `COALESCE(NEW.scan_id, OLD.scan_id)` only
    ever recomputed NEW.scan_id on an UPDATE (never NULL), silently leaving the OLD scan's row
    stale. If the reassigned row held the old scan's max_source_id, every remaining row for that
    scan would evaluate is_latest = false until an unrelated future write touched it again."""
    with pg_conn.cursor() as cur:
        _, scan_a, imgs_a = _seed_experiment_scan(cur)
        _, scan_b, imgs_b = _seed_experiment_scan(cur)
        # scan_a: two sources: an older one (stays) and a newer one (gets reassigned to scan_b).
        old_source = _deliver(cur, imgs_a, "old", traits=[_trait("length", 1.0)])
        new_source = _deliver(cur, imgs_a, "new", traits=[_trait("length", 2.0)])
        assert _max_source(cur, scan_a) == new_source

        # scan_b already has its own source (minted after new_source, so numerically higher --
        # the test's assertions below don't depend on which of b_source/new_source is greater).
        b_source = _deliver(cur, imgs_b, "b", traits=[_trait("length", 3.0)])
        assert _max_source(cur, scan_b) == b_source

        # Reassign the new_source row from scan_a to scan_b (a break-glass correction).
        cur.execute("SET LOCAL ROLE bloom_admin")
        cur.execute(
            "UPDATE cyl_scan_traits SET scan_id=%s WHERE scan_id=%s AND source_id=%s",
            (scan_b, scan_a, new_source),
        )
        cur.execute("RESET ROLE")

        # scan_a's remaining row is old_source -- its max_source_id must fall back to it, not
        # stay stuck at the now-departed new_source.
        assert _max_source(cur, scan_a) == old_source
        # scan_b now has two sources (its own + the reassigned one) -- max_source_id must reflect
        # the true max across both, not just its own original source.
        assert _max_source(cur, scan_b) == max(b_source, new_source)
    pg_conn.rollback()


def test_multi_row_cross_scan_update_recomputes_both_scans(pg_conn):
    """A single UPDATE statement can reassign MULTIPLE rows' scan_id at once (e.g. a bulk
    correction), not just one -- code-quality round-3 finding: the sequential single-row test above
    only ever exercises the trigger firing exactly once. A single multi-row UPDATE fires the
    FOR EACH ROW trigger once per affected row, all queued to run at end-of-statement over the
    fully-mutated heap (not immediately per row -- see design.md's note on AFTER-trigger timing);
    this test locks in that the redundant, repeated recomputes those multiple firings produce for
    the SAME (scan_a, scan_b) pair still converge to the correct final state, not a stale
    intermediate one."""
    with pg_conn.cursor() as cur:
        _, scan_a, imgs_a = _seed_experiment_scan(cur)
        _, scan_b, imgs_b = _seed_experiment_scan(cur)
        # scan_a: three sources -- one stays, two get reassigned to scan_b in ONE statement.
        keep_source = _deliver(cur, imgs_a, "keep", traits=[_trait("length", 1.0)])
        move_source_1 = _deliver(cur, imgs_a, "move1", traits=[_trait("length", 2.0)])
        move_source_2 = _deliver(cur, imgs_a, "move2", traits=[_trait("length", 3.0)])
        assert _max_source(cur, scan_a) == move_source_2

        b_source = _deliver(cur, imgs_b, "b", traits=[_trait("length", 4.0)])
        assert _max_source(cur, scan_b) == b_source

        # Reassign BOTH move_source_1 and move_source_2 rows from scan_a to scan_b in one statement
        # -- the trigger fires twice, both times with the identical (OLD=scan_a, NEW=scan_b) pair.
        cur.execute("SET LOCAL ROLE bloom_admin")
        cur.execute(
            "UPDATE cyl_scan_traits SET scan_id=%s "
            "WHERE scan_id=%s AND source_id IN (%s, %s)",
            (scan_b, scan_a, move_source_1, move_source_2),
        )
        cur.execute("RESET ROLE")

        # scan_a's only remaining row is keep_source.
        assert _max_source(cur, scan_a) == keep_source
        # scan_b now holds its own source plus both reassigned ones -- the true max across all three.
        assert _max_source(cur, scan_b) == max(b_source, move_source_1, move_source_2)
    pg_conn.rollback()


def test_concurrent_opposite_direction_cross_scan_reassignments_do_not_deadlock(pg_conninfo, pg_conn):
    """The trigger's sorted lock acquisition (least/greatest of OLD/NEW scan_id, added for D2b's
    cross-scan fix) exists specifically so two concurrent cross-scan reassignments moving rows in
    OPPOSITE directions between the same two scans cannot deadlock each other -- caught in
    round-3 review as having zero concurrency coverage (test_cross_scan_update_recomputes_both_scans
    above is purely sequential, so it could not have caught a missing or wrongly-ordered lock
    acquisition; only a real two-connection interleaving can).

    Uses a `threading.Barrier` so both connections issue their UPDATE at (as close to) the same
    instant as possible -- NOT "A runs to completion, then B starts", which can only ever prove B
    blocks on a lock A already holds and can never produce a genuine circular wait. This distinction
    matters here specifically: an early version of this test used the sequential construction and
    passed unchanged even after the trigger's lock order was deliberately reverted to unsorted
    NEW-then-OLD (verified against a local Postgres) -- because that construction can never let two
    connections each grab a *different* first lock before requesting the other's. Rebuilding it
    with a barrier and repeating several times reliably reproduced a genuine
    `DeadlockDetected: Process ... waits for ... advisory lock ...; blocked by process ...` under the
    unsorted trigger (reproduced empirically: 1 deadlock in 3 barrier-synced attempts), and produced
    zero deadlocks across 15 attempts against the correct sorted implementation -- confirming this
    construction actually discriminates sorted from unsorted, unlike the sequential one."""
    conn_a = psycopg.connect(pg_conninfo)
    conn_b = psycopg.connect(pg_conninfo)
    try:
        for attempt in range(5):
            with pg_conn.cursor() as cur:
                experiment_id_1, scan_1, imgs_1 = _seed_experiment_scan(cur)
                experiment_id_2, scan_2, imgs_2 = _seed_experiment_scan(cur)
                source_1 = _deliver(cur, imgs_1, f"s1-{attempt}", traits=[_trait("length", 1.0)])
                source_2 = _deliver(cur, imgs_2, f"s2-{attempt}", traits=[_trait("length", 2.0)])
                cur.execute(
                    "SELECT id FROM cyl_scan_traits WHERE scan_id=%s AND source_id=%s",
                    (scan_1, source_1),
                )
                row_1_id = cur.fetchone()[0]
                cur.execute(
                    "SELECT id FROM cyl_scan_traits WHERE scan_id=%s AND source_id=%s",
                    (scan_2, source_2),
                )
                row_2_id = cur.fetchone()[0]
            pg_conn.commit()  # scans/rows must be visible to both connections below

            barrier = threading.Barrier(2)
            results = {}

            def _run_a():
                try:
                    cur_a = conn_a.cursor()
                    barrier.wait(timeout=5.0)
                    # A moves row_1 scan_1 -> scan_2.
                    cur_a.execute(
                        "UPDATE cyl_scan_traits SET scan_id=%s WHERE id=%s", (scan_2, row_1_id)
                    )
                    conn_a.commit()
                    results["a"] = None
                except Exception as exc:  # noqa: BLE001 -- captured for the assertion below, not swallowed
                    conn_a.rollback()
                    results["a"] = exc

            def _run_b():
                try:
                    cur_b = conn_b.cursor()
                    barrier.wait(timeout=5.0)
                    # B moves row_2 scan_2 -> scan_1 -- the opposite direction, same two scans.
                    cur_b.execute(
                        "UPDATE cyl_scan_traits SET scan_id=%s WHERE id=%s", (scan_1, row_2_id)
                    )
                    conn_b.commit()
                    results["b"] = None
                except Exception as exc:  # noqa: BLE001
                    conn_b.rollback()
                    results["b"] = exc

            a_thread = threading.Thread(target=_run_a)
            b_thread = threading.Thread(target=_run_b)
            a_thread.start()
            b_thread.start()
            a_thread.join(timeout=15.0)
            b_thread.join(timeout=15.0)

            assert results.get("a") is None, f"attempt {attempt}: txn A failed: {results.get('a')}"
            assert results.get("b") is None, f"attempt {attempt}: txn B failed: {results.get('b')}"

            with pg_conn.cursor() as cur:
                # The two rows swapped scans: scan_1 now holds row_2's source, scan_2 holds row_1's.
                assert _max_source(cur, scan_1) == source_2
                assert _max_source(cur, scan_2) == source_1
                _cleanup_seeded_experiment(cur, experiment_id_1)
                _cleanup_seeded_experiment(cur, experiment_id_2)
            pg_conn.commit()
    finally:
        conn_a.close()
        conn_b.close()


def test_direct_bloom_admin_write_is_maintained(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, _imgs = _seed_experiment_scan(cur)
        cur.execute("INSERT INTO cyl_trait_sources (name) VALUES (%s) RETURNING id", (f"src-{uuid.uuid4().hex[:8]}",))
        source_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO cyl_traits (name) VALUES (%s) RETURNING id", (f"t-{uuid.uuid4().hex[:8]}",)
        )
        trait_id = cur.fetchone()[0]
        cur.execute("SET LOCAL ROLE bloom_admin")
        cur.execute(
            "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value) "
            "VALUES (%s, %s, %s, %s)",
            (scan_id, source_id, trait_id, 1.23),
        )
        cur.execute("RESET ROLE")
        assert _max_source(cur, scan_id) == source_id
    pg_conn.rollback()


def test_trigger_function_metadata(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT prosecdef, proconfig FROM pg_proc "
            "WHERE proname = 'maintain_cyl_scan_latest_source'"
        )
        prosecdef, proconfig = cur.fetchone()
        assert prosecdef is True
        assert any(c.startswith("search_path=") for c in (proconfig or []))
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# RLS -- caught in review: Supabase auto-grants anon/authenticated/service_role full
# INSERT/SELECT/UPDATE/DELETE/TRUNCATE on every new public-schema table by default. Without
# ENABLE ROW LEVEL SECURITY, that default grant is a real, unauthenticated write path -- not
# just a read leak -- into a table this design's whole correctness argument depends on.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "role",
    [
        "bloom_agent",
        "bloom_user",
        "bloom_admin",
        "authenticated",
        # bloom_writer has no policy of its own on this table -- it inherits bloom_user's via
        # `GRANT bloom_user TO bloom_writer` (20260519130000_add_bloom_writer_role.sql). Verified
        # here rather than just asserted, per round-2 review.
        "bloom_writer",
    ],
)
def test_intended_read_roles_see_real_rows(pg_conn, role):
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        src = _deliver(cur, imgs, "k", traits=[_trait("length", 1.0)])
        cur.execute(f"SET LOCAL ROLE {role}")
        cur.execute(
            "SELECT max_source_id FROM cyl_scan_latest_source WHERE scan_id=%s", (scan_id,)
        )
        assert cur.fetchone() == (src,)
        cur.execute("RESET ROLE")
    pg_conn.rollback()


def test_anon_sees_no_rows_despite_real_data_existing(pg_conn):
    """RLS enabled with no anon policy means SELECT succeeds but is silently filtered to zero
    rows -- not an error, but confirm it actually happens rather than assuming it from the
    migration's policy list."""
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "k", traits=[_trait("length", 1.0)])
        cur.execute("SET LOCAL ROLE anon")
        cur.execute(
            "SELECT max_source_id FROM cyl_scan_latest_source WHERE scan_id=%s", (scan_id,)
        )
        assert cur.fetchall() == []
        cur.execute("RESET ROLE")
    pg_conn.rollback()


def test_anon_cannot_write_despite_the_raw_table_grant(pg_conn):
    """The regression this table's RLS closes: Supabase's default privileges give anon a raw
    INSERT/UPDATE/DELETE grant on this table regardless of any policy -- confirmed still true
    below -- so RLS (not the grant) is the only thing standing between an unauthenticated caller
    and directly corrupting is_latest for any scan. Before this table's ENABLE ROW LEVEL SECURITY
    was added, this INSERT would have succeeded."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_table_privilege('anon', 'cyl_scan_latest_source', 'INSERT')"
        )
        assert cur.fetchone()[0] is True  # the raw grant genuinely exists

        cur.execute("SET LOCAL ROLE anon")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "INSERT INTO cyl_scan_latest_source (scan_id, max_source_id) VALUES (-1, 1)"
            )
    pg_conn.rollback()


def test_anon_cannot_truncate(pg_conn):
    """RLS does NOT govern TRUNCATE -- a Postgres limitation, not a policy gap -- so the INSERT
    denial above does not imply TRUNCATE is also blocked. Caught in round-3 review: confirmed
    `SET LOCAL ROLE anon; TRUNCATE public.cyl_scan_latest_source;` succeeded before the explicit
    `REVOKE TRUNCATE ... FROM anon, authenticated` was added. Blast radius was real:
    cyl_scan_traits_source INNER JOINs this table, so truncating it would have zeroed out
    is_latest for every scan system-wide, not just this change's own new counts."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_table_privilege('anon', 'cyl_scan_latest_source', 'TRUNCATE')"
        )
        assert cur.fetchone()[0] is False

        cur.execute("SET LOCAL ROLE anon")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("TRUNCATE public.cyl_scan_latest_source")
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Backfill correctness
# --------------------------------------------------------------------------- #


def test_backfill_matches_hand_computed_oracle(pg_conn):
    with pg_conn.cursor() as cur:
        # Seed pre-existing data directly, bypassing the trigger, to simulate un-backfilled rows.
        cur.execute(
            "ALTER TABLE cyl_scan_traits DISABLE TRIGGER maintain_cyl_scan_latest_source_after_write"
        )
        scan_ids = []
        for _ in range(3):
            _, scan_id, _imgs = _seed_experiment_scan(cur)
            scan_ids.append(scan_id)
            for i in range(2):
                cur.execute("INSERT INTO cyl_trait_sources (name) VALUES (%s) RETURNING id", (f"src-{uuid.uuid4().hex[:8]}",))
                source_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO cyl_traits (name) VALUES (%s) RETURNING id",
                    (f"t-{uuid.uuid4().hex[:8]}",),
                )
                trait_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value) "
                    "VALUES (%s, %s, %s, %s)",
                    (scan_id, source_id, trait_id, 1.0),
                )
        cur.execute("ALTER TABLE cyl_scan_traits ENABLE TRIGGER maintain_cyl_scan_latest_source_after_write")
        cur.execute("DELETE FROM cyl_scan_latest_source WHERE scan_id = ANY(%s)", (scan_ids,))

        cur.execute(
            "INSERT INTO cyl_scan_latest_source (scan_id, max_source_id) "
            "SELECT scan_id, max(source_id) FROM cyl_scan_traits "
            "WHERE scan_id = ANY(%s) GROUP BY scan_id "
            "ON CONFLICT (scan_id) DO UPDATE SET max_source_id = EXCLUDED.max_source_id",
            (scan_ids,),
        )

        for scan_id in scan_ids:
            cur.execute(
                "SELECT max(source_id) FROM cyl_scan_traits WHERE scan_id=%s", (scan_id,)
            )
            (oracle,) = cur.fetchone()
            assert _max_source(cur, scan_id) == oracle
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Concurrency — the exact race reproduced empirically during design (design.md D2)
# --------------------------------------------------------------------------- #


def _mint_source_and_trait(cur, label):
    """Pre-allocate one cyl_trait_sources row and one cyl_traits row. Used to fix source_id
    ordering BEFORE either racing connection touches cyl_scan_traits -- see the concurrency tests
    below for why the ordering (not just the interleaving) is what makes them a real reproduction."""
    cur.execute(
        "INSERT INTO cyl_trait_sources (name) VALUES (%s) RETURNING id",
        (f"src-{label}-{uuid.uuid4().hex[:8]}",),
    )
    source_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_traits (name) VALUES (%s) RETURNING id",
        (f"trait-{label}-{uuid.uuid4().hex[:8]}",),
    )
    trait_id = cur.fetchone()[0]
    return source_id, trait_id


def _insert_trait_row(cur, scan_id, source_id, trait_id):
    cur.execute(
        "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value) "
        "VALUES (%s, %s, %s, %s)",
        (scan_id, source_id, trait_id, 1.0),
    )


def test_concurrent_first_insert_to_same_new_scan_converges_to_true_max(pg_conninfo, pg_conn):
    """Two connections both deliver the FIRST-EVER rows for the same brand-new scan_id, under
    different source_ids, interleaved so both are in-flight before either commits. The trigger's
    advisory lock must serialize these so the final max_source_id is the true higher of the two
    -- not whichever transaction committed last with a value computed before it could see the
    other's data (the exact race this design's pg_advisory_xact_lock(scan_id) closes).

    Construction matters, not just interleaving: `cyl_trait_sources.id` is a monotonic identity,
    so BOTH ids are minted upfront (fixing which is truly the max) before either connection issues
    its `cyl_scan_traits` insert. The connection using the HIGHER id goes first and commits first;
    the connection using the LOWER id goes second, blocks on the advisory lock, and resolves last.
    This is the only ordering where the bug (the last-resolving party's pre-block-computed value
    winning) would actually produce a WRONG result if the lock were absent -- if the higher-id
    connection were the one to resolve last instead, its own pre-block value would already equal
    the true max, and the test would pass whether or not the lock exists (this exact mistake was
    caught in review; verify by temporarily removing `pg_advisory_xact_lock` from
    `maintain_cyl_scan_latest_source()` and confirming this test then fails)."""
    with pg_conn.cursor() as cur:
        experiment_id, scan_id, imgs = _seed_experiment_scan(cur)
        # Minted in this order so the names match actual numeric ordering (cyl_trait_sources.id
        # is a monotonic identity -- whichever is minted first gets the lower id).
        source_low, trait_low = _mint_source_and_trait(cur, "low")
        source_high, trait_high = _mint_source_and_trait(cur, "high")
    pg_conn.commit()  # scan/images/sources must be visible to both connections below
    assert source_low < source_high
    true_max = max(source_high, source_low)

    conn_a = psycopg.connect(pg_conninfo)
    conn_b = psycopg.connect(pg_conninfo)
    try:
        cur_a = conn_a.cursor()
        cur_b = conn_b.cursor()

        # A uses the id that must NOT win if the lock is missing, and commits first.
        _insert_trait_row(cur_a, scan_id, source_high, trait_high)

        b_done = threading.Event()

        def _run_b():
            # B uses the id a broken implementation would incorrectly let win, and resolves last.
            _insert_trait_row(cur_b, scan_id, source_low, trait_low)
            conn_b.commit()
            b_done.set()

        b_thread = threading.Thread(target=_run_b)
        b_thread.start()
        assert not b_done.wait(timeout=0.5)  # B is genuinely blocked on A's advisory lock

        conn_a.commit()
        assert b_done.wait(timeout=5.0)  # B proceeds once A releases the lock
        b_thread.join()

        with pg_conn.cursor() as cur:
            assert _max_source(cur, scan_id) == true_max
    finally:
        with pg_conn.cursor() as cur:
            _cleanup_seeded_experiment(cur, experiment_id)
        pg_conn.commit()
        conn_a.close()
        conn_b.close()


def test_concurrent_rerun_of_existing_scan_converges_to_true_max(pg_conninfo, pg_conn):
    """Same race, but for an EXISTING scan (already has a cyl_scan_latest_source row) instead of a
    brand-new one -- the shape PR #654's own testing originally found broken for its column-based
    design. Same construction constraint as the brand-new-scan test above: the connection using
    the numerically LOWER new source_id must be the one that blocks and resolves last, or this
    test cannot distinguish locked from unlocked behavior."""
    with pg_conn.cursor() as cur:
        experiment_id, scan_id, imgs = _seed_experiment_scan(cur)
        original_source = _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])
        # Minted in this order so the names match actual numeric ordering (see the sibling test
        # above for why this matters).
        source_low, trait_low = _mint_source_and_trait(cur, "low")
        source_high, trait_high = _mint_source_and_trait(cur, "high")
    pg_conn.commit()
    assert original_source < source_low < source_high
    true_max = max(original_source, source_high, source_low)

    conn_a = psycopg.connect(pg_conninfo)
    conn_b = psycopg.connect(pg_conninfo)
    try:
        cur_a = conn_a.cursor()
        cur_b = conn_b.cursor()

        _insert_trait_row(cur_a, scan_id, source_high, trait_high)

        b_done = threading.Event()

        def _run_b():
            _insert_trait_row(cur_b, scan_id, source_low, trait_low)
            conn_b.commit()
            b_done.set()

        b_thread = threading.Thread(target=_run_b)
        b_thread.start()
        assert not b_done.wait(timeout=0.5)

        conn_a.commit()
        assert b_done.wait(timeout=5.0)
        b_thread.join()

        with pg_conn.cursor() as cur:
            assert _max_source(cur, scan_id) == true_max
    finally:
        with pg_conn.cursor() as cur:
            _cleanup_seeded_experiment(cur, experiment_id)
        pg_conn.commit()
        conn_a.close()
        conn_b.close()


def test_write_concurrent_with_backfill_migration_is_not_lost(pg_conninfo, pg_conn):
    """cyl-trait-writeback spec scenario: 'A write concurrent with the backfill migration is not
    lost'.

    What this test actually verifies (narrower than its name, corrected in round-2 review): that
    `LOCK TABLE cyl_scan_traits IN SHARE MODE` genuinely blocks a concurrent write-back-shaped
    INSERT, and that the write completes correctly once the lock releases. It does NOT reconstruct
    the specific migration-application hazard design.md D3 describes (a writer transaction whose
    snapshot predates `CREATE TRIGGER` itself) -- this test DB already has the trigger installed
    for the whole suite, so there's no "trigger doesn't exist yet" state left to simulate without
    tearing down and replaying the actual migration inside the test. That gap is real but accepted:
    the generic locking behavior this test does verify is the mechanism design.md's argument
    depends on, even though the full end-to-end migration-application race isn't independently
    replayed here."""
    with pg_conn.cursor() as cur:
        experiment_id, scan_id, imgs = _seed_experiment_scan(cur)
    pg_conn.commit()

    lock_conn = psycopg.connect(pg_conninfo)
    writer_conn = psycopg.connect(pg_conninfo)
    try:
        lock_conn.execute("LOCK TABLE cyl_scan_traits IN SHARE MODE")

        writer_done = threading.Event()
        source_holder = {}

        def _run_writer():
            with writer_conn.cursor() as cur:
                source_holder["id"] = _deliver(
                    cur, imgs, "concurrent", traits=[_trait("length", 1.0)]
                )
            writer_conn.commit()
            writer_done.set()

        writer_thread = threading.Thread(target=_run_writer)
        writer_thread.start()
        assert not writer_done.wait(timeout=0.5)  # genuinely blocked by the SHARE MODE lock

        lock_conn.commit()  # releases the lock, as the real migration's COMMIT would
        assert writer_done.wait(timeout=5.0)  # the write proceeds and completes
        writer_thread.join()

        with pg_conn.cursor() as cur:
            assert _max_source(cur, scan_id) == source_holder["id"]
    finally:
        with pg_conn.cursor() as cur:
            _cleanup_seeded_experiment(cur, experiment_id)
        pg_conn.commit()
        lock_conn.close()
        writer_conn.close()


# --------------------------------------------------------------------------- #
# Migration hygiene
# --------------------------------------------------------------------------- #


def test_migration_body_is_idempotent(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION))
        cur.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='cyl_scan_latest_source'"
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgname='maintain_cyl_scan_latest_source_after_write'"
        )
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_rollback_guard_blocks_out_of_order_rollback(pg_conn):
    """This migration's rollback must refuse to run while 20260817020000's
    refresh_cyl_experiment_trait_counts() still references cyl_scan_latest_source -- proves the
    "ROLLBACK ORDER" guard in the rollback SQL is real, not just a comment."""
    with pg_conn.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException, match="Roll back 20260817020000"):
            cur.execute(_sql_body(ROLLBACK))
    pg_conn.rollback()


def test_rollback_restores_prior_state(pg_conn):
    """Exercises the full, documented reverse-chronological order (030000, then 020000, then this
    one) rather than this migration in isolation -- rolling this one back alone is now guarded
    against (see the sibling test above) and would no longer be a valid scenario to test."""
    with pg_conn.cursor() as cur:
        # Seed data BEFORE rolling back, so there's something for the restored live-computation
        # view to get right or wrong -- not just "the objects are gone."
        _, scan_id, imgs = _seed_experiment_scan(cur)
        s1 = _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])
        s2 = _deliver(cur, imgs, "reproc", traits=[_trait("length", 2.0)])

        cur.execute(_sql_body(REWRITE_ROLLBACK))
        cur.execute(_sql_body(TRAIT_COUNTS_ROLLBACK))
        cur.execute(_sql_body(ROLLBACK))
        cur.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='cyl_scan_latest_source'"
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgname='maintain_cyl_scan_latest_source_after_write'"
        )
        assert cur.fetchone()[0] == 0

        # The restored view must still get is_latest right via its live WindowAgg computation --
        # not just exist.
        cur.execute(
            "SELECT source_id, is_latest FROM cyl_scan_traits_source WHERE scan_id=%s "
            "ORDER BY source_id",
            (scan_id,),
        )
        rows = dict(cur.fetchall())
        assert rows == {s1: False, s2: True}

        # Re-apply all three, in forward order, so the rest of the suite (which assumes this
        # change is fully live) still works.
        cur.execute(_sql_body(MIGRATION))
        cur.execute(_sql_body(TRAIT_COUNTS_MIGRATION))
        cur.execute(_sql_body(REWRITE_MIGRATION))
        cur.execute(
            "SELECT is_latest FROM cyl_scan_traits_source WHERE scan_id=%s AND source_id=%s",
            (scan_id, s2),
        )
        assert cur.fetchone()[0] is True
    pg_conn.rollback()
