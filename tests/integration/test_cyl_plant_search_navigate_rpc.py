"""cyl_plant_search_navigate RPC — server-side search-bar navigation.

Answers "exactly one destination, or not?" in SQL (DISTINCT ... LIMIT 2) so the
client never decides navigation from a capped row sample deduped in JS (#528).

LOCAL ONLY: the `pg_conn` fixture (see conftest) connects as `supabase_admin`
(BYPASSRLS); every test rolls back. Seed names/barcodes are uniquified so the
tests pass whether or not the DB carries seed data.
"""

import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
_TS = "20260724000300_add_cyl_plant_search_navigate_rpc"
MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_TS}.sql"
SIG = "cyl_plant_search_navigate(text)"


def _tok() -> str:
    return uuid.uuid4().hex[:10]


def _seed(cur, *, species, exp, waves=1, accession=None, barcodes=()):
    """Seed species -> experiment -> N waves -> plants. Returns the ids."""
    cur.execute("INSERT INTO species (common_name) VALUES (%s) RETURNING id", (species,))
    species_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_experiments (name, species_id) VALUES (%s, %s) RETURNING id",
        (exp, species_id),
    )
    exp_id = cur.fetchone()[0]
    accession_id = None
    if accession is not None:
        cur.execute("INSERT INTO accessions (name) VALUES (%s) RETURNING id", (accession,))
        accession_id = cur.fetchone()[0]
    wave_ids = []
    for n in range(1, waves + 1):
        cur.execute(
            "INSERT INTO cyl_waves (experiment_id, number) VALUES (%s, %s) RETURNING id",
            (exp_id, n),
        )
        wave_ids.append(cur.fetchone()[0])
    for i, qr in enumerate(barcodes):
        cur.execute(
            "INSERT INTO cyl_plants (wave_id, accession_id, qr_code) VALUES (%s, %s, %s)",
            (wave_ids[i % len(wave_ids)], accession_id, qr),
        )
    return {
        "species_id": species_id,
        "exp_id": exp_id,
        "accession_id": accession_id,
        "wave_ids": wave_ids,
    }


def _nav(cur, text):
    cur.execute("SELECT cyl_plant_search_navigate(%s)", (text,))
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def test_exact_species_name_resolves_to_species(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        ids = _seed(cur, species=f"Sp-{t}", exp=f"E-{t}")
        assert _nav(cur, f"Sp-{t}") == {"target": "species", "species_id": ids["species_id"]}
    pg_conn.rollback()


def test_species_match_is_case_insensitive(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        ids = _seed(cur, species=f"Soybean-{t}", exp=f"E-{t}")
        assert _nav(cur, f"soybean-{t}".upper())["species_id"] == ids["species_id"]
    pg_conn.rollback()


def test_species_wins_over_a_matching_barcode(pg_conn):
    # Priority order: species, then barcode, then accession.
    t = _tok()
    term = f"Amb-{t}"
    with pg_conn.cursor() as cur:
        ids = _seed(cur, species=term, exp=f"E-{t}", accession=f"A-{t}", barcodes=[term])
        assert _nav(cur, term) == {"target": "species", "species_id": ids["species_id"]}
    pg_conn.rollback()


def test_barcode_in_one_wave_resolves_to_that_plant(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        ids = _seed(cur, species=f"Sp-{t}", exp=f"E-{t}", accession=f"A-{t}", barcodes=[f"Q-{t}"])
        assert _nav(cur, f"Q-{t}") == {
            "target": "plant",
            "species_id": ids["species_id"],
            "experiment_id": ids["exp_id"],
            "wave_id": ids["wave_ids"][0],
            "accession_id": ids["accession_id"],
        }
    pg_conn.rollback()


def test_barcode_across_two_waves_is_ambiguous(pg_conn):
    # The bug this RPC exists to fix: qr_code is unique per wave only, so the
    # same barcode in two waves is two destinations and must not auto-navigate.
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed(
            cur,
            species=f"Sp-{t}",
            exp=f"E-{t}",
            waves=2,
            accession=f"A-{t}",
            barcodes=[f"Q-{t}", f"Q-{t}"],
        )
        assert _nav(cur, f"Q-{t}") == {"target": "none"}
    pg_conn.rollback()


def test_accession_in_one_wave_resolves_to_that_plant(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        ids = _seed(cur, species=f"Sp-{t}", exp=f"E-{t}", accession=f"A-{t}", barcodes=[f"Q-{t}"])
        assert _nav(cur, f"A-{t}")["target"] == "plant"
        assert _nav(cur, f"A-{t}")["accession_id"] == ids["accession_id"]
    pg_conn.rollback()


def test_accession_spanning_two_waves_is_ambiguous(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed(
            cur,
            species=f"Sp-{t}",
            exp=f"E-{t}",
            waves=2,
            accession=f"A-{t}",
            barcodes=[f"Q1-{t}", f"Q2-{t}"],
        )
        assert _nav(cur, f"A-{t}") == {"target": "none"}
    pg_conn.rollback()


def test_resolution_is_exact_beyond_any_row_cap(pg_conn):
    # 600 plants share one accession in one wave -> one distinct destination.
    # The old client capped its sample; DISTINCT ... LIMIT 2 does not care how
    # many rows match, only how many distinct destinations there are.
    t = _tok()
    with pg_conn.cursor() as cur:
        ids = _seed(cur, species=f"Sp-{t}", exp=f"E-{t}", accession=f"A-{t}")
        cur.execute(
            "INSERT INTO cyl_plants (wave_id, accession_id, qr_code) "
            "SELECT %s, %s, 'Q-' || g || '-' || %s FROM generate_series(1, 600) g",
            (ids["wave_ids"][0], ids["accession_id"], t),
        )
        assert _nav(cur, f"A-{t}") == {
            "target": "plant",
            "species_id": ids["species_id"],
            "experiment_id": ids["exp_id"],
            "wave_id": ids["wave_ids"][0],
            "accession_id": ids["accession_id"],
        }
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Non-matches and exclusions
# --------------------------------------------------------------------------- #


def test_unknown_term_resolves_to_none(pg_conn):
    with pg_conn.cursor() as cur:
        assert _nav(cur, f"no-such-thing-{_tok()}") == {"target": "none"}
    pg_conn.rollback()


@pytest.mark.parametrize("term", ["", "   ", None])
def test_blank_input_resolves_to_none(pg_conn, term):
    with pg_conn.cursor() as cur:
        assert _nav(cur, term) == {"target": "none"}
    pg_conn.rollback()


@pytest.mark.parametrize("term", ["%", "_", "%%"])
def test_wildcards_are_literal_not_patterns(pg_conn, term):
    # Equality, not ILIKE — so a term of '%' can never match everything and
    # auto-navigate somewhere arbitrary.
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed(cur, species=f"Sp-{t}", exp=f"E-{t}", accession=f"A-{t}", barcodes=[f"Q-{t}"])
        assert _nav(cur, term) == {"target": "none"}
    pg_conn.rollback()


def test_soft_deleted_species_is_not_navigable(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO species (common_name, deleted_at) VALUES (%s, now())", (f"Sp-{t}",)
        )
        assert _nav(cur, f"Sp-{t}") == {"target": "none"}
    pg_conn.rollback()


def test_soft_deleted_experiment_barcode_is_not_navigable(pg_conn):
    # The barcode is absent from cyl_plant_search, so there is no destination.
    t = _tok()
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO species (common_name) VALUES (%s) RETURNING id", (f"Sp-{t}",))
        species_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO cyl_experiments (name, species_id, deleted_at) "
            "VALUES (%s, %s, now()) RETURNING id",
            (f"E-{t}", species_id),
        )
        exp_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO cyl_waves (experiment_id, number) VALUES (%s, 1) RETURNING id", (exp_id,)
        )
        wave_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO cyl_plants (wave_id, qr_code) VALUES (%s, %s)", (wave_id, f"Q-{t}")
        )
        assert _nav(cur, f"Q-{t}") == {"target": "none"}
    pg_conn.rollback()


def test_plant_without_an_accession_yields_no_destination(pg_conn):
    # fieldHrefs needs every id; a plant with no accession has no page to go to.
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed(cur, species=f"Sp-{t}", exp=f"E-{t}", accession=None, barcodes=[f"Q-{t}"])
        assert _nav(cur, f"Q-{t}") == {"target": "none"}
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Security posture
# --------------------------------------------------------------------------- #


def test_function_is_security_invoker_with_pinned_search_path(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT prosecdef, proconfig FROM pg_proc WHERE proname = 'cyl_plant_search_navigate'"
        )
        prosecdef, proconfig = cur.fetchone()
        assert prosecdef is False, "must be SECURITY INVOKER so caller RLS applies"
        assert proconfig and any(c.startswith("search_path=") for c in proconfig)
    pg_conn.rollback()


def test_execute_not_granted_to_public_or_anon(pg_conn):
    with pg_conn.cursor() as cur:
        for role in ("public", "anon"):
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, SIG))
            assert cur.fetchone()[0] is False, f"{role} must not execute the navigate RPC"
    pg_conn.rollback()


@pytest.mark.parametrize("role", ["authenticated", "bloom_user", "bloom_admin", "bloom_agent"])
def test_execute_granted_to_bloom_roles(pg_conn, role):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, SIG))
        assert cur.fetchone()[0] is True
    pg_conn.rollback()


def test_migration_body_is_idempotent(pg_conn):
    # CREATE OR REPLACE + REVOKE/GRANT: re-applying must not error.
    sql = MIGRATION.read_text()
    assert re.search(r"CREATE OR REPLACE FUNCTION", sql)
    with pg_conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(sql)
    pg_conn.rollback()
