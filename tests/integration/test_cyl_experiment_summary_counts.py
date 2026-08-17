"""
Integration tests for the aggregate summary-counts function, originally added by bloom#625
(`fix-bloommcp-list-experiments-summary-rpc`) and rewritten by bloom#637/bloom#656
(`fix-cyl-scan-traits-latest-rollup`, `20260817030000_rewrite_get_experiment_summary_counts.sql`).

`get_experiment_summary_counts(experiment_id_ DEFAULT NULL, source_id_ DEFAULT NULL, run_id_ DEFAULT
NULL)`'s two counts now have different cost profiles when unpinned (both `source_id_`/`run_id_` NULL):
`n_plants` is a live `EXISTS` semi-join (cheap, always current); `n_traits` is read from
`cyl_experiment_trait_counts`, a cache refreshed by `refresh_cyl_experiment_trait_counts()` on an
external schedule rather than recomputed live -- so unpinned `n_traits` assertions below call that
refresh function first (`_refresh_trait_counts`), matching the documented staleness contract
(design.md D5), rather than expecting it to reflect just-written data instantly. Pinned
(`source_id_`/`run_id_`) calls remain fully live for both counts, same as before.

LOCAL ONLY: the `pg_conn` fixture connects to 127.0.0.1 on POSTGRES_HOST_PORT as `supabase_admin`
(BYPASSRLS) and every test rolls back. Seeding helpers are imported from `test_cyl_read_path.py` rather
than duplicated.
"""

import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.integration.test_cyl_read_path import (  # noqa: E402
    _deliver,
    _seed_experiment,
    _seed_experiment_scan,
    _seed_scan_in,
    _trait,
)
from tests.integration.test_cyl_experiment_traits import (  # noqa: E402
    _get_experiment_traits,
)

REPO_ROOT = Path(__file__).parent.parent.parent
_TS = "20260807000000_get_experiment_summary_counts"
MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_TS}.sql"
ROLLBACK = REPO_ROOT / "supabase" / "rollbacks" / f"{_TS}_rollback.sql"

_REWRITE_TS = "20260817030000_rewrite_get_experiment_summary_counts"
REWRITE_MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_REWRITE_TS}.sql"
REWRITE_ROLLBACK = REPO_ROOT / "supabase" / "rollbacks" / f"{_REWRITE_TS}_rollback.sql"

_READ_ROLES = ["bloom_agent", "bloom_user", "bloom_admin"]


def _sql_body(path: Path) -> str:
    """Migration/rollback body minus its BEGIN;/COMMIT; wrapper (CRLF-safe)."""
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _seed_scan_no_accession(cur, wave_id, *, n_images=2, plant_age_days=10):
    """A plant with `accession_id = NULL` under an existing wave -- `_seed_scan_in` always links
    a real accession, so this direct-insert is the only way to reach the exclusion case
    `get_experiment_traits`'s (and now this function's) inner join to `accessions` enforces.
    Returns (scan_id, image_ids)."""
    tok = uuid.uuid4().hex[:12]
    cur.execute(
        "INSERT INTO cyl_plants (wave_id, accession_id, germ_day, qr_code) "
        "VALUES (%s, NULL, %s, %s) RETURNING id",
        (wave_id, 5, f"qr-{tok}"),
    )
    plant_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_scans (plant_id, date_scanned, plant_age_days) "
        "VALUES (%s, %s, %s) RETURNING id",
        (plant_id, "2026-01-01", plant_age_days),
    )
    scan_id = cur.fetchone()[0]
    img_ids = []
    for _ in range(n_images):
        cur.execute(
            "INSERT INTO cyl_images (scan_id) VALUES (%s) RETURNING id", (scan_id,)
        )
        img_ids.append(cur.fetchone()[0])
    return scan_id, img_ids


def _get_summary_counts(cur, experiment_id=None, *, source_id=None, run_id=None):
    cur.execute(
        "SELECT experiment_id, n_plants, n_traits "
        "FROM get_experiment_summary_counts(%s, %s, %s)",
        (experiment_id, source_id, run_id),
    )
    return cur.fetchall()


def _refresh_trait_counts(cur):
    """n_traits is cache-backed for unpinned calls (design.md D5) -- call this before asserting
    on it so the assertion reflects the refresh contract, not an assumption of instant staleness."""
    cur.execute("SELECT public.refresh_cyl_experiment_trait_counts()")


def _assert_matches_get_experiment_traits(
    cur, experiment_id, *, source_id=None, run_id=None
):
    """The direct oracle: derive (n_plants, n_traits) from `get_experiment_traits`'s own rows the
    same way today's Python `list_experiments()` does, and assert the SQL aggregate agrees.

    `None` trait names are filtered out before taking `len(set(...))` -- `COUNT(DISTINCT
    trait_name)` ignores SQL NULLs, but a naive `len({r[1] for r in rows})` would count a Python
    `None` member once, so an unfiltered comparison would spuriously disagree for any fixture
    that happens to include a legacy row with an unresolved `trait_id` (not exercised by any
    fixture below today, but the filter is here so a future one doesn't silently break this
    helper instead of the SQL it's checking).

    Unpinned calls (`source_id`/`run_id` both `None`) read `n_traits` from the scheduled-refresh
    cache, so this refreshes it first -- the oracle comparison is about correctness once refreshed,
    not about staleness, which has its own dedicated tests in `test_cyl_experiment_trait_counts.py`.
    """
    if source_id is None and run_id is None:
        _refresh_trait_counts(cur)
    traits_rows = _get_experiment_traits(
        cur, experiment_id, source_id=source_id, run_id=run_id
    )
    plant_ids = {r[0] for r in traits_rows}
    trait_names = {r[1] for r in traits_rows if r[1] is not None}
    rows = _get_summary_counts(cur, experiment_id, source_id=source_id, run_id=run_id)
    if not traits_rows:
        assert rows == []
        return
    assert len(rows) == 1
    _, n_plants, n_traits = rows[0]
    assert (n_plants, n_traits) == (len(plant_ids), len(trait_names))


# --------------------------------------------------------------------------- #
# Requirement: Aggregate experiment summary counts
# --------------------------------------------------------------------------- #


def test_unpinned_counts_match_get_experiment_traits(pg_conn):
    with pg_conn.cursor() as cur:
        exp, wave = _seed_experiment(cur)
        _, imgs_a = _seed_scan_in(cur, wave)
        _, imgs_b = _seed_scan_in(cur, wave)
        _deliver(cur, imgs_a, "a", traits=[_trait("A", 1.0), _trait("B", 2.0)])
        _deliver(cur, imgs_b, "b", traits=[_trait("A", 3.0)])
        _assert_matches_get_experiment_traits(cur, exp)
    pg_conn.rollback()


def test_accession_null_plant_excluded_from_counts(pg_conn):
    """Regression test for the accessions join: a plant with no accession must be excluded from
    both n_plants and n_traits, matching get_experiment_traits's own inner-join exclusion of the
    same plant. This is the test that would have caught the join omission found during this
    proposal's own review -- do not delete it as "redundant" with the byte-for-byte oracle above.
    """
    with pg_conn.cursor() as cur:
        exp, wave = _seed_experiment(cur)
        _, imgs_ok = _seed_scan_in(cur, wave)
        _, imgs_no_acc = _seed_scan_no_accession(cur, wave)
        _deliver(cur, imgs_ok, "ok", traits=[_trait("A", 1.0)])
        _deliver(cur, imgs_no_acc, "no-acc", traits=[_trait("B", 2.0)])

        _refresh_trait_counts(cur)
        rows = _get_summary_counts(cur, exp)
        assert len(rows) == 1
        _, n_plants, n_traits = rows[0]
        # Only the accession-bearing plant/trait counts -- "B" (the no-accession plant's trait)
        # must not appear.
        assert (n_plants, n_traits) == (1, 1)
        _assert_matches_get_experiment_traits(cur, exp)
    pg_conn.rollback()


def test_pin_source_matches_get_experiment_traits(pg_conn):
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        old = _deliver(cur, imgs, "old", traits=[_trait("length", 10.0)])
        _deliver(cur, imgs, "new", traits=[_trait("length", 20.0)])
        _assert_matches_get_experiment_traits(cur, exp, source_id=old)
    pg_conn.rollback()


def test_pin_run_matches_get_experiment_traits(pg_conn):
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "old", run="r1", traits=[_trait("length", 10.0)])
        _deliver(cur, imgs, "new", run="r2", traits=[_trait("length", 20.0)])
        # "as of run X" agrees with get_experiment_traits even though r2 has since superseded r1.
        _assert_matches_get_experiment_traits(cur, exp, run_id="r1")
    pg_conn.rollback()


def test_both_source_and_run_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        src = _deliver(cur, imgs, "k", run="r", traits=[_trait("length", 1.0)])
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(
                "SELECT * FROM get_experiment_summary_counts(%s, %s, %s)",
                (exp, src, "r"),
            )
    pg_conn.rollback()


def test_empty_experiment_returns_no_rows(pg_conn):
    with pg_conn.cursor() as cur:
        exp, _ = _seed_experiment(cur)  # no scans at all
        assert _get_summary_counts(cur, exp) == []
    pg_conn.rollback()


def test_experiment_id_pin_isolates_to_one_experiment(pg_conn):
    with pg_conn.cursor() as cur:
        exp1, _, imgs1 = _seed_experiment_scan(cur)
        _deliver(cur, imgs1, "e1", traits=[_trait("length", 1.0)])
        exp2, _, imgs2 = _seed_experiment_scan(cur)
        _deliver(cur, imgs2, "e2", traits=[_trait("length", 2.0), _trait("width", 3.0)])

        _refresh_trait_counts(cur)
        rows1 = _get_summary_counts(cur, exp1)
        rows2 = _get_summary_counts(cur, exp2)
        assert len(rows1) == 1 and rows1[0][0] == exp1
        assert len(rows2) == 1 and rows2[0][0] == exp2
        assert rows1[0][1:] == (1, 1)
        assert rows2[0][1:] == (1, 2)
    pg_conn.rollback()


def test_bulk_unpinned_returns_one_row_per_experiment_with_data(pg_conn):
    with pg_conn.cursor() as cur:
        exp_with_data, _, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "k", traits=[_trait("length", 1.0)])
        exp_empty, _ = _seed_experiment(
            cur
        )  # no scans -- must be absent from the bulk result

        _refresh_trait_counts(cur)
        rows = {r[0]: r[1:] for r in _get_summary_counts(cur)}
        assert rows.get(exp_with_data) == (1, 1)
        assert exp_empty not in rows
    pg_conn.rollback()


def test_null_trait_value_still_counts_toward_n_traits(pg_conn):
    """A NULL trait_value (SQL NULL) still counts toward n_traits -- COUNT(DISTINCT
    trait_name) never inspects `value` at all, so a measured-but-null trait is not
    invisible to the count. This seeds `_trait("length", None)`, which the write-back
    RPC stores as SQL NULL for a non-finite (NaN/Infinity) measurement -- it does not
    exercise a literal IEEE-754 NaN/Infinity actually stored in the `real` column
    (bypassing the RPC), which n_traits' COUNT(DISTINCT trait_name) can't distinguish
    from any other non-NULL value anyway, since it never reads `value`.
    """
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "k", traits=[_trait("length", None)])
        _refresh_trait_counts(cur)
        rows = _get_summary_counts(cur, exp)
        assert len(rows) == 1
        _, n_plants, n_traits = rows[0]
        assert (n_plants, n_traits) == (1, 1)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# bloom#637 / bloom#656: unpinned n_plants live, n_traits cache-backed
# --------------------------------------------------------------------------- #


def test_unpinned_n_plants_is_unaffected_by_a_corrupted_cache(pg_conn):
    """n_plants must be computed live, not read from any cache -- corrupt the (unrelated)
    n_traits cache row and confirm n_plants is still correct, proving the two counts have
    independent code paths in the unpinned branch."""
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "k", traits=[_trait("length", 1.0)])
        _refresh_trait_counts(cur)
        cur.execute(
            "UPDATE cyl_experiment_trait_counts SET n_traits = 999 WHERE experiment_id = %s",
            (exp,),
        )
        rows = _get_summary_counts(cur, exp)
        assert len(rows) == 1
        _, n_plants, n_traits = rows[0]
        assert n_plants == 1  # correct, unaffected by the corrupted cache row
        assert n_traits == 999  # proves n_traits IS read from the cache, not recomputed
    pg_conn.rollback()


def test_unpinned_n_traits_reads_the_cache_not_a_live_recompute(pg_conn):
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "k", traits=[_trait("length", 1.0), _trait("width", 2.0)])
        _refresh_trait_counts(cur)
        _, _, n_traits_before = _get_summary_counts(cur, exp)[0]
        assert n_traits_before == 2

        # New trait data lands after the refresh -- n_traits must NOT reflect it yet.
        _deliver(cur, imgs, "reproc", traits=[_trait("height", 3.0)])
        _, _, n_traits_stale = _get_summary_counts(cur, exp)[0]
        assert n_traits_stale == n_traits_before

        _refresh_trait_counts(cur)
        _, _, n_traits_fresh = _get_summary_counts(cur, exp)[0]
        assert n_traits_fresh == 1  # the rerun's single new trait, "height"
    pg_conn.rollback()


def test_unpinned_call_no_live_join_over_cyl_scan_traits(pg_conn):
    """Structural confirmation the unpinned path doesn't drag cyl_scan_traits rows through a
    live join for n_traits -- EXPLAIN should show only a scan of the small cache table, not the
    5-way join chain bloom#637 exists to avoid."""
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "k", traits=[_trait("length", 1.0)])
        _refresh_trait_counts(cur)
        cur.execute(
            "EXPLAIN (FORMAT TEXT) SELECT * FROM get_experiment_summary_counts(%s, NULL, NULL)",
            (exp,),
        )
        plan = "\n".join(r[0] for r in cur.fetchall())
        assert "cyl_scan_traits_source" not in plan
    pg_conn.rollback()


def test_pinned_call_unaffected_by_cache_staleness(pg_conn):
    """Pinned (source_id_/run_id_) calls never read the cache at all -- confirm a pinned call
    is correct even when the cache has never been refreshed."""
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        old = _deliver(cur, imgs, "old", traits=[_trait("length", 10.0)])
        _deliver(cur, imgs, "new", traits=[_trait("length", 20.0), _trait("width", 5.0)])
        # No refresh call at all.
        rows = _get_summary_counts(cur, exp, source_id=old)
        assert len(rows) == 1
        _, n_plants, n_traits = rows[0]
        assert (n_plants, n_traits) == (1, 1)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Requirement: Bulk read grants match the existing per-trait read surface
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role", _READ_ROLES)
def test_read_roles_can_call_function(pg_conn, role):
    """`count(*)` never returns NULL even for a zero-row result -- asserting `is not None` against
    it is trivially always true regardless of whether `role` could actually call the function
    correctly (caught in round-2 review). Assert the real row content instead."""
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "k", traits=[_trait("length", 1.0)])
        _refresh_trait_counts(cur)
        cur.execute(f"SET LOCAL ROLE {role}")
        cur.execute(
            "SELECT n_plants, n_traits FROM get_experiment_summary_counts(%s, NULL, NULL)", (exp,)
        )
        assert cur.fetchone() == (1, 1)
        cur.execute("RESET ROLE")
    pg_conn.rollback()


def test_authenticated_has_execute_grant(pg_conn):
    """A bare `SET LOCAL ROLE authenticated` has no JWT/auth.uid() context, so assert the EXECUTE
    grant exists rather than exercising a call (mirrors the Tier 1 precedent's
    `test_authenticated_has_execute_grant`)."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_function_privilege("
            "'authenticated', 'get_experiment_summary_counts(bigint,bigint,text)', 'EXECUTE')"
        )
        assert cur.fetchone()[0] is True
    pg_conn.rollback()


@pytest.mark.parametrize(
    "fn_signature",
    [
        "get_experiment_summary_counts(bigint,bigint,text)",
        "compute_cyl_experiment_summary_counts_live(bigint,bigint,text)",
    ],
)
def test_anon_has_no_execute_grant(pg_conn, fn_signature):
    """Caught in review: the 20260817030000 rewrite's REVOKE originally only covered PUBLIC, not
    anon explicitly -- Supabase auto-grants EXECUTE on new public-schema functions to anon, so
    that alone left anon still able to call both. compute_cyl_experiment_summary_counts_live is
    SECURITY DEFINER, so anon calling it directly would have run with the definer's elevated
    privilege, bypassing whatever table grants anon itself lacks -- confirmed exploitable
    (empirically, against a local Postgres) before this fix."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_function_privilege('anon', %s, 'EXECUTE')", (fn_signature,)
        )
        assert cur.fetchone()[0] is False
    pg_conn.rollback()


@pytest.mark.parametrize(
    "fn_name",
    ["get_experiment_summary_counts", "compute_cyl_experiment_summary_counts_live"],
)
def test_function_search_path_is_pinned(pg_conn, fn_name):
    """No regression test previously guarded either function's `SET search_path` -- caught in
    round-4 review, matching test_cyl_scan_latest_source.py's existing
    test_trigger_function_metadata precedent for the trigger function. get_experiment_summary_counts
    itself was the one function in this change without a pinned search_path until round 4 (not
    exploitable -- every reference in its body is already schema-qualified, and it's SECURITY
    INVOKER -- but pinned anyway for consistency with every other function this change touches)."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT proconfig FROM pg_proc WHERE proname = %s", (fn_name,))
        (proconfig,) = cur.fetchone()
        assert any(c.startswith("search_path=") for c in (proconfig or []))
    pg_conn.rollback()


def test_migration_adds_no_write_capability():
    sql = MIGRATION.read_text().lower()
    assert "create policy" not in sql
    assert not re.search(
        r"grant\s+[^;]*\b(insert|update|delete|all)\b", sql
    ), "migration must not grant any write privilege"


def test_get_experiment_summary_counts_reachable_over_postgrest(api, service_role_key):
    """PostgREST/HTTP-layer coverage: proves the function is actually exposed through the REST
    gateway (grants resolve, PostgREST's schema cache includes it), not just callable via direct
    SQL. Skips when the gateway is unreachable (dev has none); CI's compose-health-check covers
    it.
    """
    import urllib.error

    try:
        status, body = api(
            "/api/rest/v1/rpc/get_experiment_summary_counts",
            api_key=service_role_key,
            method="POST",
            data={},
        )
    except (urllib.error.URLError, OSError) as e:
        pytest.skip(
            f"PostgREST gateway not reachable ({e}); CI compose-health-check covers this"
        )
    assert (
        status == 200
    ), f"expected 200 (all three args default to NULL), got {status}: {body}"


# --------------------------------------------------------------------------- #
# Requirement: Additive, non-destructive migration with a companion rollback
# --------------------------------------------------------------------------- #


def test_migration_body_is_idempotent(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION))  # re-apply on already-applied state
        cur.execute(
            "SELECT count(*) FROM pg_proc WHERE proname='get_experiment_summary_counts' "
            "AND pronargs=3"
        )
        assert cur.fetchone()[0] == 1
        # pre-existing read objects are untouched by the re-apply
        cur.execute(
            "SELECT count(*) FROM pg_proc WHERE proname='get_experiment_traits' AND pronargs=3"
        )
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_rollback_restores_prior_state(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(ROLLBACK))
        cur.execute(
            "SELECT count(*) FROM pg_proc WHERE proname='get_experiment_summary_counts'"
        )
        assert cur.fetchone()[0] == 0
        # pre-existing read objects are untouched by the rollback
        cur.execute(
            "SELECT count(*) FROM pg_proc WHERE proname='get_experiment_traits' AND pronargs=3"
        )
        assert cur.fetchone()[0] == 1
        # round-trip: re-apply the forward migration and confirm the function is back
        cur.execute(_sql_body(MIGRATION))
        cur.execute(
            "SELECT count(*) FROM pg_proc WHERE proname='get_experiment_summary_counts'"
        )
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# bloom#637/bloom#656 rewrite migration hygiene
# --------------------------------------------------------------------------- #


def test_rewrite_migration_body_is_idempotent(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(REWRITE_MIGRATION))
        cur.execute(
            "SELECT count(*) FROM pg_proc "
            "WHERE proname='compute_cyl_experiment_summary_counts_live' AND pronargs=3"
        )
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_rewrite_rollback_restores_prior_body(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(REWRITE_ROLLBACK))
        cur.execute(
            "SELECT count(*) FROM pg_proc WHERE proname='compute_cyl_experiment_summary_counts_live'"
        )
        assert cur.fetchone()[0] == 0
        # The rolled-back body is the bloom#625 live-join-only version -- confirm it still works.
        exp, _, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "k", traits=[_trait("length", 1.0)])
        cur.execute(
            "SELECT n_plants, n_traits FROM get_experiment_summary_counts(%s, NULL, NULL)", (exp,)
        )
        assert cur.fetchone() == (1, 1)
        # Round-trip: re-apply the rewrite and confirm the helper is back.
        cur.execute(_sql_body(REWRITE_MIGRATION))
        cur.execute(
            "SELECT count(*) FROM pg_proc WHERE proname='compute_cyl_experiment_summary_counts_live'"
        )
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()
