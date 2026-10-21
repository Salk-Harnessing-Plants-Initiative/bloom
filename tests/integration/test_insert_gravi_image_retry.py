"""insert_gravi_image lets a failed GraviScan upload be retried (bloom#1125).

The RPC creates the gravi_scans row; the caller then uploads the file and writes gravi_images.
If that second step fails, the retry must get the scan id back so it can upload the file again.
Only a scan that already has its gravi_images row counts as uploaded (NULL).

LOCAL ONLY: `pg_conn` connects as supabase_admin (BYPASSRLS) against the database CI has
migrated, and every test rolls back. Seed names carry a token so the DB's own data can't clash.
"""

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as cursor:
        yield cursor
    pg_conn.rollback()


@pytest.fixture
def species(cur):
    tok = uuid.uuid4().hex[:10]
    name = f"Sp{tok}"
    # (genus, species) is UNIQUE, so the names are fixture-only.
    cur.execute(
        "INSERT INTO species (common_name, genus, species) VALUES (%s, %s, %s)",
        (name, f"Zgravi{tok}", "retry"),
    )
    return name


def _insert_gravi_image(cur, species, plate="PLATE-1"):
    """Call the RPC the way bloom-js does.

    It passes a system name so repeated calls share one experiment: without one, #250 creates
    a new experiment per call and the duplicate check never matches.
    """
    tok = species[2:]
    cur.execute(
        """
        SELECT public.insert_gravi_image(
            species_common_name := %(species)s, experiment_name := %(experiment)s,
            scanner_name := %(scanner)s, phenotyper_name := 'Test',
            phenotyper_email := %(pheno)s, scientist_name := 'Test',
            scientist_email := %(sci)s, plate_barcode_ := %(plate)s,
            capture_date_ := '2026-10-19T10:00:00Z', grid_mode_ := '2grid',
            plate_index_ := '00', resolution_ := 1200, format_ := 'tiff',
            system_name_ := %(system)s)
        """,
        {
            "species": species,
            "experiment": f"exp-{tok}",
            "scanner": f"scanner-{tok}",
            "pheno": f"pheno-{tok}@example.org",
            "sci": f"sci-{tok}@example.org",
            "plate": plate,
            "system": f"rig-{tok}",
        },
    )
    return cur.fetchone()[0]


def _store_image(cur, scan_id):
    cur.execute(
        "INSERT INTO gravi_images (scan_id, object_path) VALUES (%s, %s)",
        (scan_id, f"gravi-images/{scan_id}.tiff"),
    )


def test_a_new_scan_returns_its_id(cur, species):
    assert _insert_gravi_image(cur, species) is not None


def test_a_scan_whose_file_never_arrived_returns_the_same_id(cur, species):
    # Another plate's image is stored, so the check must look at this scan's image, not any.
    _store_image(cur, _insert_gravi_image(cur, species, plate="PLATE-2"))
    first = _insert_gravi_image(cur, species, plate="PLATE-1")

    # PLATE-1's file upload failed: no gravi_images row. The retry gets the scan back.
    assert _insert_gravi_image(cur, species, plate="PLATE-1") == first
    cur.execute(
        "SELECT count(*) FROM gravi_scans WHERE experiment_id = "
        "(SELECT experiment_id FROM gravi_scans WHERE id = %s) AND plate_id = 'PLATE-1'",
        (first,),
    )
    assert cur.fetchone()[0] == 1


def test_a_scan_with_its_image_returns_null(cur, species):
    first = _insert_gravi_image(cur, species)
    _store_image(cur, first)
    assert _insert_gravi_image(cur, species) is None


def test_another_plate_is_a_different_scan(cur, species):
    first = _insert_gravi_image(cur, species, plate="PLATE-1")
    second = _insert_gravi_image(cur, species, plate="PLATE-2")
    assert second not in (None, first)
