"""cyl_plant_search_query RPC — server-side advanced plant search.

One call returns the capped page, the true total (independent of the cap), and
which pasted barcodes do not exist. AND across fields, OR within a field.

LOCAL ONLY: the `pg_conn` fixture (see conftest) connects as `supabase_admin`
(BYPASSRLS); every test rolls back. Seed names/barcodes are uniquified so the
tests pass whether or not the DB carries seed data. Grants are exercised with
`has_function_privilege`.
"""

import json
import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
_TS = "20260724000200_add_cyl_plant_search_rpc"
MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_TS}.sql"
SIG = "cyl_plant_search_query(text[], bigint[], bigint[], bigint[], integer)"


def _tok() -> str:
    return uuid.uuid4().hex[:10]


def _seed_plant(cur, *, qr, accession="__auto__", species="Sp", exp="Exp", deleted=False):
    """Seed one plant through the join chain; return its (species_id, exp_id, accession_id).

    accession defaults to a unique name (accessions.name is UNIQUE); pass None for
    a plant with no accession.
    """
    if accession == "__auto__":
        accession = f"Acc-{_tok()}"
    cur.execute("INSERT INTO species (common_name) VALUES (%s) RETURNING id", (species,))
    species_id = cur.fetchone()[0]
    deleted_at = "now()" if deleted else "NULL"
    cur.execute(
        f"INSERT INTO cyl_experiments (name, species_id, deleted_at) "
        f"VALUES (%s, %s, {deleted_at}) RETURNING id",
        (exp, species_id),
    )
    exp_id = cur.fetchone()[0]
    cur.execute("INSERT INTO cyl_waves (experiment_id, number) VALUES (%s, 1) RETURNING id", (exp_id,))
    wave_id = cur.fetchone()[0]
    accession_id = None
    if accession is not None:
        cur.execute("INSERT INTO accessions (name) VALUES (%s) RETURNING id", (accession,))
        accession_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_plants (wave_id, accession_id, qr_code) VALUES (%s, %s, %s) RETURNING id",
        (wave_id, accession_id, qr),
    )
    return {"species_id": species_id, "exp_id": exp_id, "accession_id": accession_id}


def _call(cur, *, barcodes=None, accession_ids=None, species_ids=None, experiment_ids=None, limit=500):
    cur.execute(
        "SELECT cyl_plant_search_query("
        "p_barcodes := %s::text[], p_accession_ids := %s::bigint[], "
        "p_species_ids := %s::bigint[], p_experiment_ids := %s::bigint[], p_limit := %s)",
        (barcodes or [], accession_ids or [], species_ids or [], experiment_ids or [], limit),
    )
    res = cur.fetchone()[0]
    return json.loads(res) if isinstance(res, str) else res


# --------------------------------------------------------------------------- #
# Matching, total, not-found
# --------------------------------------------------------------------------- #


def test_barcode_search_returns_rows_and_total(pg_conn):
    t = _tok()
    a, b = f"Q-a-{t}", f"Q-b-{t}"
    with pg_conn.cursor() as cur:
        _seed_plant(cur, qr=a, species=f"Sp-a-{t}", exp=f"E-a-{t}")
        _seed_plant(cur, qr=b, species=f"Sp-b-{t}", exp=f"E-b-{t}")
        res = _call(cur, barcodes=[a, b])
        assert res["total"] == 2
        assert {r["qr_code"] for r in res["rows"]} == {a, b}
        assert res["not_found"] == []
    pg_conn.rollback()


def test_nonexistent_barcode_reported_not_found(pg_conn):
    t = _tok()
    real, bogus = f"Q-{t}", f"NOPE-{t}"
    with pg_conn.cursor() as cur:
        _seed_plant(cur, qr=real, species=f"Sp-{t}", exp=f"E-{t}")
        res = _call(cur, barcodes=[real, bogus])
        assert res["total"] == 1
        assert res["not_found"] == [bogus]
    pg_conn.rollback()


def test_not_found_excludes_barcode_filtered_out_by_another_field(pg_conn):
    # Bug 3: a real barcode excluded by an ANDed species filter is NOT "not found".
    t = _tok()
    qr = f"Q-{t}"
    with pg_conn.cursor() as cur:
        ids = _seed_plant(cur, qr=qr, species=f"SpReal-{t}", exp=f"E-{t}")
        other_species = ids["species_id"] + 10_000_000  # an id that isn't this plant's species
        res = _call(cur, barcodes=[qr], species_ids=[other_species])
        assert res["total"] == 0          # excluded by the filter
        assert res["rows"] == []
        assert res["not_found"] == []      # but it exists, so NOT reported missing
    pg_conn.rollback()


def test_total_reflects_full_count_beyond_limit(pg_conn):
    # Bug 2: the true total is returned even when the page is capped below it.
    t = _tok()
    with pg_conn.cursor() as cur:
        ids = _seed_plant(cur, qr=f"Q-0-{t}", species=f"Sp-{t}", exp=f"E-{t}")
        for i in (1, 2):  # two more plants (own wave) on the same experiment
            cur.execute(
                "INSERT INTO cyl_waves (experiment_id, number) VALUES (%s, %s) RETURNING id",
                (ids["exp_id"], i + 1),
            )
            wave_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO cyl_plants (wave_id, qr_code) VALUES (%s, %s)", (wave_id, f"Q-{i}-{t}")
            )
        res = _call(cur, experiment_ids=[ids["exp_id"]], limit=1)
        assert res["total"] == 3
        assert len(res["rows"]) == 1
    pg_conn.rollback()


def test_limit_zero_returns_no_rows_but_true_total(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        ids = _seed_plant(cur, qr=f"Q-{t}", species=f"Sp-{t}", exp=f"E-{t}")
        res = _call(cur, experiment_ids=[ids["exp_id"]], limit=0)
        assert res["rows"] == []
        assert res["total"] == 1
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Server-side clamps. The RPC is callable directly through PostgREST by any
# holder of a bloom-role JWT, so neither the page size nor the filter sizes can
# be trusted from the caller.
# --------------------------------------------------------------------------- #


def _seed_many(cur, exp_id, token, n):
    """Add n extra plants on exp_id's existing wave (qr_code is unique per wave)."""
    cur.execute(
        "INSERT INTO cyl_plants (wave_id, qr_code) "
        "SELECT w.id, 'Q-' || g || '-' || %s FROM cyl_waves w, generate_series(1, %s) g "
        "WHERE w.experiment_id = %s",
        (token, n, exp_id),
    )


def test_limit_above_ceiling_is_clamped(pg_conn):
    # A direct caller asking for everything still gets one capped page, while
    # `total` keeps reporting the true size.
    t = _tok()
    with pg_conn.cursor() as cur:
        ids = _seed_plant(cur, qr=f"Q-0-{t}", species=f"Sp-{t}", exp=f"E-{t}")
        _seed_many(cur, ids["exp_id"], t, 600)
        res = _call(cur, experiment_ids=[ids["exp_id"]], limit=99999)
        assert res["total"] == 601
        assert len(res["rows"]) == 500
    pg_conn.rollback()


def test_default_limit_is_the_ceiling(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        ids = _seed_plant(cur, qr=f"Q-0-{t}", species=f"Sp-{t}", exp=f"E-{t}")
        _seed_many(cur, ids["exp_id"], t, 600)
        cur.execute(
            "SELECT cyl_plant_search_query(p_experiment_ids := %s::bigint[])", ([ids["exp_id"]],)
        )
        res = cur.fetchone()[0]
        res = json.loads(res) if isinstance(res, str) else res
        assert len(res["rows"]) == 500
    pg_conn.rollback()


def test_negative_limit_returns_no_rows_but_true_total(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        ids = _seed_plant(cur, qr=f"Q-{t}", species=f"Sp-{t}", exp=f"E-{t}")
        res = _call(cur, experiment_ids=[ids["exp_id"]], limit=-5)
        assert res["rows"] == []
        assert res["total"] == 1
    pg_conn.rollback()


def test_oversized_filter_raises_rather_than_truncating(pg_conn):
    # Silently dropping barcodes past the cap would report them as "not found" —
    # the exact false negative this RPC exists to prevent — so it must raise.
    with pg_conn.cursor() as cur:
        with pytest.raises(Exception, match="filter list too large"):
            _call(cur, barcodes=[f"B-{i}" for i in range(5001)])
    pg_conn.rollback()


def test_filter_at_the_cap_is_accepted(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_plant(cur, qr=f"Q-{t}", species=f"Sp-{t}", exp=f"E-{t}")
        res = _call(cur, barcodes=[f"Q-{t}"] + [f"B-{i}" for i in range(4999)])
        assert res["total"] == 1
        assert len(res["not_found"]) == 4999
    pg_conn.rollback()


def test_null_array_is_treated_as_unfiltered(pg_conn):
    # A NULL from a direct caller would make every filter test NULL and return
    # nothing; it must behave like an empty array instead.
    t = _tok()
    with pg_conn.cursor() as cur:
        ids = _seed_plant(cur, qr=f"Q-{t}", species=f"Sp-{t}", exp=f"E-{t}")
        cur.execute(
            "SELECT cyl_plant_search_query("
            "p_barcodes := NULL::text[], p_experiment_ids := %s::bigint[])",
            ([ids["exp_id"]],),
        )
        res = cur.fetchone()[0]
        res = json.loads(res) if isinstance(res, str) else res
        assert res["total"] == 1
        assert res["not_found"] == []
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# AND across fields / OR within a field
# --------------------------------------------------------------------------- #


def test_and_across_fields_narrows(pg_conn):
    t = _tok()
    keep, drop = f"Q-keep-{t}", f"Q-drop-{t}"
    with pg_conn.cursor() as cur:
        k = _seed_plant(cur, qr=keep, species=f"Sp-{t}", exp=f"E1-{t}")
        _seed_plant(cur, qr=drop, species=f"Sp2-{t}", exp=f"E2-{t}")
        # barcode list of both, AND species = keep's species -> only keep
        res = _call(cur, barcodes=[keep, drop], species_ids=[k["species_id"]])
        assert {r["qr_code"] for r in res["rows"]} == {keep}
        assert res["not_found"] == []  # drop exists, just filtered out
    pg_conn.rollback()


def test_or_within_experiment_field(pg_conn):
    t = _tok()
    a, b = f"Q-a-{t}", f"Q-b-{t}"
    with pg_conn.cursor() as cur:
        ia = _seed_plant(cur, qr=a, species=f"SpA-{t}", exp=f"EA-{t}")
        ib = _seed_plant(cur, qr=b, species=f"SpB-{t}", exp=f"EB-{t}")
        res = _call(cur, experiment_ids=[ia["exp_id"], ib["exp_id"]])
        assert {r["qr_code"] for r in res["rows"]} == {a, b}
    pg_conn.rollback()


def test_duplicate_barcode_deduped_in_not_found(pg_conn):
    t = _tok()
    bogus = f"NOPE-{t}"
    with pg_conn.cursor() as cur:
        res = _call(cur, barcodes=[bogus, bogus, bogus])
        assert res["not_found"] == [bogus]
    pg_conn.rollback()


def test_soft_deleted_experiment_barcode_is_not_found(pg_conn):
    # A plant on a soft-deleted experiment is absent from the view, so its
    # barcode reads as "not found" (does not exist in the searchable data).
    t = _tok()
    qr = f"Q-del-{t}"
    with pg_conn.cursor() as cur:
        _seed_plant(cur, qr=qr, species=f"Sp-{t}", exp=f"E-{t}", deleted=True)
        res = _call(cur, barcodes=[qr])
        assert res["total"] == 0
        assert res["not_found"] == [qr]
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Security posture: SECURITY INVOKER, search_path, grants
# --------------------------------------------------------------------------- #


def test_function_is_security_invoker_with_pinned_search_path(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT prosecdef, proconfig FROM pg_proc WHERE proname='cyl_plant_search_query'"
        )
        secdef, proconfig = cur.fetchone()
        assert secdef is False, "must be SECURITY INVOKER so RLS applies as the caller"
        assert any(c.startswith("search_path=") for c in (proconfig or []))
    pg_conn.rollback()


def test_execute_not_granted_to_public_or_anon(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT has_function_privilege('public', %s, 'EXECUTE')", (SIG,))
        assert cur.fetchone()[0] is False, "PUBLIC must not execute the RPC"
        cur.execute("SELECT has_function_privilege('anon', %s, 'EXECUTE')", (SIG,))
        assert cur.fetchone()[0] is False, "anon must not execute the RPC"
    pg_conn.rollback()


@pytest.mark.parametrize("role", ["authenticated", "bloom_user", "bloom_admin", "bloom_agent"])
def test_execute_granted_to_bloom_roles(pg_conn, role):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, SIG))
        assert cur.fetchone()[0] is True, f"{role} should hold EXECUTE on the RPC"
    pg_conn.rollback()


def test_bloom_user_can_call_and_gets_envelope(pg_conn):
    # End-to-end through the grant + security_invoker view as the read role.
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_plant(cur, qr=f"Q-{t}", species=f"Sp-{t}", exp=f"E-{t}")
        cur.execute("SET LOCAL ROLE bloom_user")
        res = _call(cur, barcodes=[f"Q-{t}"])
        assert set(res) == {"total", "rows", "not_found"}
        cur.execute("RESET ROLE")
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Migration idempotency
# --------------------------------------------------------------------------- #


def _sql_body(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def test_migration_body_is_idempotent(pg_conn):
    # CREATE OR REPLACE FUNCTION / REVOKE / GRANT re-applies cleanly.
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION))
        cur.execute("SELECT 1 FROM pg_proc WHERE proname='cyl_plant_search_query'")
        assert cur.fetchone() is not None
    pg_conn.rollback()
