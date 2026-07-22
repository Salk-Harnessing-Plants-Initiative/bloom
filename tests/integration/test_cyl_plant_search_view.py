"""cyl_plant_search view — flattened, searchable plant rows (barcode/accession/species).

LOCAL ONLY: the `pg_conn` fixture (see conftest) connects as `supabase_admin`
(BYPASSRLS); every test rolls back. Grants are exercised with `has_table_privilege`.
"""


def _seed_plant(cur, *, qr, accession="Acc-1", species="Rice", exp="Exp-1", deleted=False):
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
    with pg_conn.cursor() as cur:
        _seed_plant(cur, qr="Q-100", accession="Williams82", species="Soybean", exp="GDM Screen")
        cur.execute(
            "SELECT qr_code, accession_name, species_name, experiment_name "
            "FROM cyl_plant_search WHERE qr_code = %s",
            ("Q-100",),
        )
        row = cur.fetchone()
        assert row == ("Q-100", "Williams82", "Soybean", "GDM Screen")
    pg_conn.rollback()


def test_plant_search_matches_accession_and_species(pg_conn):
    # the view is what backs the multi-field search: accession + species are queryable columns
    with pg_conn.cursor() as cur:
        _seed_plant(cur, qr="Q-1", accession="Bay-0", species="Canola")
        cur.execute(
            "SELECT count(*) FROM cyl_plant_search "
            "WHERE accession_name ILIKE %s OR species_name ILIKE %s",
            ("%bay%", "%canola%"),
        )
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_plant_search_excludes_soft_deleted_experiments(pg_conn):
    with pg_conn.cursor() as cur:
        _seed_plant(cur, qr="Q-live")
        _seed_plant(cur, qr="Q-deleted", deleted=True)
        cur.execute("SELECT qr_code FROM cyl_plant_search WHERE qr_code IN ('Q-live', 'Q-deleted')")
        got = {r[0] for r in cur.fetchall()}
        assert got == {"Q-live"}  # the deleted-experiment plant is excluded
    pg_conn.rollback()


def test_plant_search_keeps_plant_without_accession(pg_conn):
    # a plant with no accession must still be findable by barcode (accession_name NULL)
    with pg_conn.cursor() as cur:
        _seed_plant(cur, qr="Q-noacc", accession=None)
        cur.execute(
            "SELECT qr_code, accession_name FROM cyl_plant_search WHERE qr_code = %s", ("Q-noacc",)
        )
        assert cur.fetchone() == ("Q-noacc", None)
    pg_conn.rollback()


def test_plant_search_not_granted_to_anon(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT has_table_privilege('anon', 'public.cyl_plant_search', 'SELECT')")
        assert cur.fetchone()[0] is False, "anon must NOT have SELECT on cyl_plant_search"
    pg_conn.rollback()
