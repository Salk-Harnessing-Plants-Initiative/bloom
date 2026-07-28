"""
Integration tests for the bulk trait-read change (`add-bulk-trait-read-rpc`, bloom#546 Tier 1).

Adds two functions as a bulk sibling to the per-trait `get_scan_traits`:

- `get_experiment_traits(experiment_id_, source_id_ DEFAULT NULL, run_id_ DEFAULT NULL)` — every
  trait for one experiment in a single round trip, reusing `cyl_scan_traits_source`'s `is_latest`
  selection rule; same latest/pin-source/pin-run/both-set semantics as `get_scan_traits`.
- `list_experiment_trait_sources(experiment_id_)` — distinct real (non-NULL) sources contributing
  to the experiment.

LOCAL ONLY: the `pg_conn` fixture connects to 127.0.0.1 on POSTGRES_HOST_PORT as `supabase_admin`
(BYPASSRLS) and every test rolls back. Seeding helpers are imported from `test_cyl_read_path.py`
rather than duplicated.
"""

import re
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.integration.test_cyl_read_path import (  # noqa: E402
    _deliver,
    _get_scan_traits,
    _register_trait,
    _seed_experiment,
    _seed_experiment_scan,
    _seed_scan_in,
    _trait,
)

REPO_ROOT = Path(__file__).parent.parent.parent
_TS = "20260728000000_get_experiment_traits"
MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_TS}.sql"
ROLLBACK = REPO_ROOT / "supabase" / "rollbacks" / f"{_TS}_rollback.sql"

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


def _get_experiment_traits(cur, experiment_id, *, source_id=None, run_id=None):
    cur.execute(
        "SELECT scan_id, trait_name, source_id, trait_value "
        "FROM get_experiment_traits(%s, %s, %s)",
        (experiment_id, source_id, run_id),
    )
    return cur.fetchall()


def _assert_matches_get_scan_traits(cur, experiment_id, *, source_id=None, run_id=None):
    """Every trait get_experiment_traits returns must match get_scan_traits row-for-row.

    get_experiment_traits has no trait_name_ filter, so it can only be compared per-trait,
    grouping its own rows by trait_name and diffing each group's {(scan_id, trait_value)} set
    against get_scan_traits(experiment_id, trait_name, source_id_, run_id_) for that trait.
    Returns the grouping so callers can assert on specific traits without re-querying.
    """
    cur.execute(
        "SELECT trait_name, scan_id, trait_value FROM get_experiment_traits(%s, %s, %s)",
        (experiment_id, source_id, run_id),
    )
    by_trait = {}
    for trait_name, scan_id, trait_value in cur.fetchall():
        by_trait.setdefault(trait_name, set()).add((scan_id, trait_value))
    for trait_name, bulk_set in by_trait.items():
        per_trait_set = set(
            _get_scan_traits(
                cur, experiment_id, trait_name, source_id=source_id, run_id=run_id
            )
        )
        assert bulk_set == per_trait_set, (
            f"get_experiment_traits/get_scan_traits mismatch for trait {trait_name!r}: "
            f"{bulk_set} != {per_trait_set}"
        )
    return by_trait


# --------------------------------------------------------------------------- #
# Requirement: Bulk experiment-scoped trait reads
# --------------------------------------------------------------------------- #


def test_bulk_call_returns_all_traits_for_experiment(pg_conn):
    with pg_conn.cursor() as cur:
        exp, scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "k", traits=[_trait("A", 1.0), _trait("B", 2.0)])
        rows = _get_experiment_traits(cur, exp)
        names = {trait_name for _, trait_name, _, _ in rows}
        assert names == {"A", "B"}
        assert all(scan_id_ == scan_id for scan_id_, _, _, _ in rows)
    pg_conn.rollback()


def test_default_path_matches_get_scan_traits(pg_conn):
    with pg_conn.cursor() as cur:
        exp, scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "old", run="r1", traits=[_trait("length", 10.0)])
        _deliver(cur, imgs, "new", run="r2", traits=[_trait("length", 20.0)])
        by_trait = _assert_matches_get_scan_traits(cur, exp)
        assert by_trait["length"] == {(scan_id, 20.0)}
    pg_conn.rollback()


def test_pin_source_matches_get_scan_traits(pg_conn):
    with pg_conn.cursor() as cur:
        exp, scan_id, imgs = _seed_experiment_scan(cur)
        old = _deliver(cur, imgs, "old", traits=[_trait("length", 10.0)])
        _deliver(cur, imgs, "new", traits=[_trait("length", 20.0)])
        by_trait = _assert_matches_get_scan_traits(cur, exp, source_id=old)
        assert by_trait["length"] == {(scan_id, 10.0)}
    pg_conn.rollback()


def test_pin_run_matches_get_scan_traits(pg_conn):
    with pg_conn.cursor() as cur:
        exp, scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "old", run="r1", traits=[_trait("length", 10.0)])
        _deliver(cur, imgs, "new", run="r2", traits=[_trait("length", 20.0)])
        # "as of run X" returns r1's values even though r2 has since superseded them.
        by_trait = _assert_matches_get_scan_traits(cur, exp, run_id="r1")
        assert by_trait["length"] == {(scan_id, 10.0)}
    pg_conn.rollback()


def test_both_source_and_run_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        src = _deliver(cur, imgs, "k", run="r", traits=[_trait("length", 1.0)])
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(
                "SELECT * FROM get_experiment_traits(%s, %s, %s)", (exp, src, "r")
            )
    pg_conn.rollback()


def test_no_cross_source_mixing(pg_conn):
    with pg_conn.cursor() as cur:
        exp, scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "old", traits=[_trait("A", 1.0), _trait("B", 2.0)])
        _deliver(cur, imgs, "new", traits=[_trait("A", 10.0)])  # latest lacks B
        rows = _get_experiment_traits(cur, exp)
        names = {trait_name for _, trait_name, _, _ in rows}
        assert names == {"A"}  # B not backfilled from the older source
        assert [(scan_id_, tv) for scan_id_, tn, _, tv in rows if tn == "A"] == [
            (scan_id, 10.0)
        ]
    pg_conn.rollback()


def test_legacy_null_source_scan_returned_by_default(pg_conn):
    with pg_conn.cursor() as cur:
        exp, scan_id, _ = _seed_experiment_scan(cur)
        tid = _register_trait(cur, "legacy")
        cur.execute(
            "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value) "
            "VALUES (%s, NULL, %s, %s)",
            (scan_id, tid, 4.2),
        )
        rows = _get_experiment_traits(cur, exp)
        assert len(rows) == 1
        scan_id_, trait_name, source_id, trait_value = rows[0]
        # cyl_scan_traits.value is `real` (float4, ~7 sig figs) -- 4.2 narrows on the
        # float4->float8 widening, so compare with tolerance, not exact equality.
        assert (scan_id_, trait_name, source_id) == (scan_id, "legacy", None)
        assert trait_value == pytest.approx(4.2)
    pg_conn.rollback()


def test_non_finite_value_surfaced_as_null(pg_conn):
    with pg_conn.cursor() as cur:
        exp, scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "k", traits=[_trait("length", None)])
        rows = _get_experiment_traits(cur, exp)
        assert len(rows) == 1
        scan_id_, trait_name, _source_id, trait_value = rows[0]
        assert (scan_id_, trait_name, trait_value) == (scan_id, "length", None)
    pg_conn.rollback()


def test_cross_experiment_isolation_traits(pg_conn):
    with pg_conn.cursor() as cur:
        exp1, _, imgs1 = _seed_experiment_scan(cur)
        src1 = _deliver(cur, imgs1, "e1", run="r1", traits=[_trait("length", 1.0)])
        exp2, _, imgs2 = _seed_experiment_scan(cur)
        _deliver(cur, imgs2, "e2", run="r1", traits=[_trait("length", 2.0)])
        # experiment filter must stay conjoined under every argument combination
        assert _get_experiment_traits(cur, exp1, source_id=src1) != []
        assert _get_experiment_traits(cur, exp2, source_id=src1) == []
        assert _get_experiment_traits(cur, exp2, run_id="r1") != []
        rows_exp1_default = _get_experiment_traits(cur, exp1)
        rows_exp2_default = _get_experiment_traits(cur, exp2)
        assert {r[0] for r in rows_exp1_default}.isdisjoint(
            {r[0] for r in rows_exp2_default}
        )
    pg_conn.rollback()


def test_empty_experiment_returns_no_rows(pg_conn):
    with pg_conn.cursor() as cur:
        exp, _ = _seed_experiment(cur)  # no scans at all
        assert _get_experiment_traits(cur, exp) == []
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Requirement: Experiment trait-source listing
# --------------------------------------------------------------------------- #


def _list_sources(cur, experiment_id):
    cur.execute(
        "SELECT source_id, source_name, pipeline_run_id "
        "FROM list_experiment_trait_sources(%s)",
        (experiment_id,),
    )
    return cur.fetchall()


def test_list_sources_for_experiment(pg_conn):
    with pg_conn.cursor() as cur:
        exp, wave = _seed_experiment(cur)
        _, imgs_a = _seed_scan_in(cur, wave)
        _, imgs_b = _seed_scan_in(cur, wave)
        s1 = _deliver(cur, imgs_a, "a", run="run-1", traits=[_trait("length", 1.0)])
        s2 = _deliver(cur, imgs_b, "b", run="run-2", traits=[_trait("length", 2.0)])
        rows = _list_sources(cur, exp)
        assert {r[0] for r in rows} == {s1, s2}
        assert {r[2] for r in rows} == {"run-1", "run-2"}
    pg_conn.rollback()


def test_list_sources_includes_null_pipeline_run_id(pg_conn):
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        src = _deliver(cur, imgs, "nopr", run=None, traits=[_trait("length", 1.0)])
        rows = _list_sources(cur, exp)
        assert any(r[0] == src and r[2] is None for r in rows)
    pg_conn.rollback()


def test_list_sources_excludes_legacy_null_source(pg_conn):
    with pg_conn.cursor() as cur:
        exp, wave = _seed_experiment(cur)
        _, imgs_real = _seed_scan_in(cur, wave)
        real_src = _deliver(
            cur, imgs_real, "real", run="r1", traits=[_trait("length", 1.0)]
        )
        legacy_scan_id, _ = _seed_scan_in(cur, wave)
        tid = _register_trait(cur, "legacy")
        cur.execute(
            "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value) "
            "VALUES (%s, NULL, %s, %s)",
            (legacy_scan_id, tid, 1.0),
        )
        rows = _list_sources(cur, exp)
        assert {r[0] for r in rows} == {
            real_src
        }  # only the real source, not a NULL placeholder
    pg_conn.rollback()


def test_list_sources_only_legacy_returns_nothing(pg_conn):
    with pg_conn.cursor() as cur:
        exp, scan_id, _ = _seed_experiment_scan(cur)
        tid = _register_trait(cur, "legacy")
        cur.execute(
            "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value) "
            "VALUES (%s, NULL, %s, %s)",
            (scan_id, tid, 1.0),
        )
        assert (
            _list_sources(cur, exp) == []
        )  # no real source at all -> zero rows, no error
    pg_conn.rollback()


def test_list_sources_cross_experiment_isolation(pg_conn):
    with pg_conn.cursor() as cur:
        exp1, _, imgs1 = _seed_experiment_scan(cur)
        src1 = _deliver(cur, imgs1, "e1", run="r1", traits=[_trait("length", 1.0)])
        exp2, _, imgs2 = _seed_experiment_scan(cur)
        _deliver(cur, imgs2, "e2", run="r2", traits=[_trait("length", 2.0)])
        rows = _list_sources(cur, exp1)
        assert {r[0] for r in rows} == {src1}
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Requirement: Bulk read grants match the existing per-trait read surface
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role", _READ_ROLES)
def test_read_roles_can_call_bulk_surface(pg_conn, role):
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "k", run="r", traits=[_trait("length", 1.0)])
        cur.execute(f"SET LOCAL ROLE {role}")
        cur.execute(
            "SELECT count(*) FROM get_experiment_traits(%s, NULL, NULL)", (exp,)
        )
        assert cur.fetchone()[0] is not None
        cur.execute("SELECT count(*) FROM list_experiment_trait_sources(%s)", (exp,))
        assert cur.fetchone()[0] is not None
        cur.execute("RESET ROLE")
    pg_conn.rollback()


def test_authenticated_has_execute_grant(pg_conn):
    """A bare `SET LOCAL ROLE authenticated` has no JWT/auth.uid() context, so assert the
    EXECUTE grant exists rather than exercising a call (mirrors the source-aware precedent's
    `test_authenticated_has_select_grant_on_views`)."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_function_privilege("
            "'authenticated', 'get_experiment_traits(bigint,bigint,text)', 'EXECUTE')"
        )
        assert cur.fetchone()[0] is True
        cur.execute(
            "SELECT has_function_privilege("
            "'authenticated', 'list_experiment_trait_sources(bigint)', 'EXECUTE')"
        )
        assert cur.fetchone()[0] is True
    pg_conn.rollback()


def test_migration_adds_no_write_capability():
    # Purely additive read functions: no policy, no write grant, so this cannot widen write
    # access to any table (a static property of the migration text, not data-dependent).
    sql = MIGRATION.read_text().lower()
    assert "create policy" not in sql
    for verb in ("grant insert", "grant update", "grant delete", "grant all"):
        assert verb not in sql, f"migration must not {verb}"


# --------------------------------------------------------------------------- #
# Requirement: Additive, non-destructive bulk-read migration
# --------------------------------------------------------------------------- #


def test_migration_body_is_idempotent(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION))  # re-apply on already-applied state
        for fn, nargs in (
            ("get_experiment_traits", 3),
            ("list_experiment_trait_sources", 1),
        ):
            cur.execute(
                "SELECT count(*) FROM pg_proc WHERE proname=%s AND pronargs=%s",
                (fn, nargs),
            )
            assert cur.fetchone()[0] == 1
        # pre-existing read objects are untouched by the re-apply
        cur.execute(
            "SELECT count(*) FROM pg_proc WHERE proname='get_scan_traits' AND pronargs=4"
        )
        assert cur.fetchone()[0] == 1
        for view in ("cyl_scan_traits_source", "cyl_scan_traits_latest"):
            cur.execute("SELECT to_regclass(%s)", (f"public.{view}",))
            assert cur.fetchone()[0] is not None
    pg_conn.rollback()


def test_rollback_restores_prior_state(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(ROLLBACK))
        for fn in ("get_experiment_traits", "list_experiment_trait_sources"):
            cur.execute("SELECT count(*) FROM pg_proc WHERE proname=%s", (fn,))
            assert cur.fetchone()[0] == 0
        # pre-existing read objects are untouched by the rollback
        cur.execute(
            "SELECT count(*) FROM pg_proc WHERE proname='get_scan_traits' AND pronargs=4"
        )
        assert cur.fetchone()[0] == 1
        for view in (
            "cyl_scan_traits_source",
            "cyl_scan_traits_latest",
            "cyl_scan_trait_names",
        ):
            cur.execute("SELECT to_regclass(%s)", (f"public.{view}",))
            assert cur.fetchone()[0] is not None
        # round-trip: re-apply the forward migration and confirm the two functions are back
        cur.execute(_sql_body(MIGRATION))
        for fn in ("get_experiment_traits", "list_experiment_trait_sources"):
            cur.execute("SELECT count(*) FROM pg_proc WHERE proname=%s", (fn,))
            assert cur.fetchone()[0] == 1
    pg_conn.rollback()
