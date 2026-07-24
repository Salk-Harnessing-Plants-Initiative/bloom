"""cyl_plant_search view — flattened, searchable plant rows (barcode/accession/species).

LOCAL ONLY: the `pg_conn` fixture (see conftest) connects as `supabase_admin`
(BYPASSRLS); every test rolls back. Grants are exercised with `has_table_privilege`.
Seed names are uniquified so the tests pass whether or not the DB carries seed data.
"""

import uuid

import pytest


def _tok() -> str:
    return uuid.uuid4().hex[:10]


def _seed_plant(cur, *, qr, accession="Acc", species="Sp", exp="Exp", deleted=False):
    cur.execute("INSERT INTO species (common_name) VALUES (%s) RETURNING id", (species,))
    species_id = cur.fetchone()[0]
    if deleted:
        cur.execute(
            "INSERT INTO cyl_experiments (name, species_id, deleted_at) "
            "VALUES (%s, %s, now()) RETURNING id",
            (exp, species_id),
        )
    else:
        cur.execute(
            "INSERT INTO cyl_experiments (name, species_id) VALUES (%s, %s) RETURNING id",
            (exp, species_id),
        )
    exp_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_waves (experiment_id, number) VALUES (%s, 1) RETURNING id", (exp_id,)
    )
    wave_id = cur.fetchone()[0]
    accession_id = None
    if accession is not None:
        cur.execute("INSERT INTO accessions (name) VALUES (%s) RETURNING id", (accession,))
        accession_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_plants (wave_id, accession_id, qr_code) VALUES (%s, %s, %s) RETURNING id",
        (wave_id, accession_id, qr),
    )
    return cur.fetchone()[0]


def test_plant_search_flattens_fields(pg_conn):
    t = _tok()
    qr, acc, sp, exp = f"Q-{t}", f"Williams82-{t}", f"Soybean-{t}", f"GDM-{t}"
    with pg_conn.cursor() as cur:
        _seed_plant(cur, qr=qr, accession=acc, species=sp, exp=exp)
        cur.execute(
            "SELECT qr_code, accession_name, species_name, experiment_name "
            "FROM cyl_plant_search WHERE qr_code = %s",
            (qr,),
        )
        assert cur.fetchone() == (qr, acc, sp, exp)
    pg_conn.rollback()


def test_plant_search_matches_accession_and_species(pg_conn):
    # the view backs the multi-field search: accession + species are queryable columns
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_plant(cur, qr=f"Q-{t}", accession=f"Bay0-{t}", species=f"Canola-{t}")
        cur.execute(
            "SELECT count(*) FROM cyl_plant_search "
            "WHERE accession_name ILIKE %s OR species_name ILIKE %s",
            (f"%Bay0-{t}%", f"%Canola-{t}%"),
        )
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_plant_search_excludes_soft_deleted_experiments(pg_conn):
    t = _tok()
    live, deleted = f"Q-live-{t}", f"Q-del-{t}"
    with pg_conn.cursor() as cur:
        _seed_plant(cur, qr=live, accession=f"Acc-live-{t}", species=f"Sp-live-{t}", exp=f"Exp-live-{t}")
        _seed_plant(
            cur, qr=deleted, accession=f"Acc-del-{t}", species=f"Sp-del-{t}",
            exp=f"Exp-del-{t}", deleted=True,
        )
        cur.execute("SELECT qr_code FROM cyl_plant_search WHERE qr_code IN (%s, %s)", (live, deleted))
        got = {r[0] for r in cur.fetchall()}
        assert got == {live}  # the deleted-experiment plant is excluded
    pg_conn.rollback()


def test_plant_search_keeps_plant_without_accession(pg_conn):
    # a plant with no accession must still be findable by barcode (accession_name NULL)
    t = _tok()
    qr = f"Q-noacc-{t}"
    with pg_conn.cursor() as cur:
        _seed_plant(cur, qr=qr, accession=None, species=f"Sp-{t}", exp=f"Exp-{t}")
        cur.execute("SELECT qr_code, accession_name FROM cyl_plant_search WHERE qr_code = %s", (qr,))
        assert cur.fetchone() == (qr, None)
    pg_conn.rollback()


def test_plant_search_not_granted_to_anon(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT has_table_privilege('anon', 'public.cyl_plant_search', 'SELECT')")
        assert cur.fetchone()[0] is False, "anon must NOT have SELECT on cyl_plant_search"
    pg_conn.rollback()


@pytest.mark.parametrize("role", ["authenticated", "bloom_user", "bloom_admin", "bloom_agent"])
def test_plant_search_granted_to_bloom_roles(pg_conn, role):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT has_table_privilege(%s, 'public.cyl_plant_search', 'SELECT')", (role,))
        assert cur.fetchone()[0] is True, f"{role} must have SELECT on cyl_plant_search"
    pg_conn.rollback()
