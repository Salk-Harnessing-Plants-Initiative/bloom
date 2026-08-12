"""gravi_scans_extended — the joined plate-scan read surface for `bloomctl plate download`.

Exercises the actual SQL, not a mocked client. The interesting cases are the nullable joins:
species_id, scanner_id, session_id and metadata_id are all nullable on the gravi side, so an
inner join would silently drop scans that have images. Each of those gets a test.

LOCAL ONLY: the `pg_conn` fixture (see conftest) connects as `supabase_admin` (BYPASSRLS);
every test rolls back. Seed names are uniquified with a token so the assertions hold whether
or not the DB carries seed data. Mirrors `test_cyl_read_model_views.py`.
"""

import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
MIGRATION = (
    REPO_ROOT
    / "supabase"
    / "migrations"
    / "20260811000000_create_gravi_scans_extended_view.sql"
)

# Every column the CLI reads off the view. Named here so a dropped column fails loudly
# rather than showing up as an empty CSV field.
EXPECTED_COLUMNS = {
    "scan_id",
    "plate_id",
    "capture_date",
    "uploaded_at",
    "cycle_number",
    "grid_mode",
    "plate_index",
    "resolution",
    "format",
    "wave_number",
    "transplant_date",
    "custom_note",
    "scanner_id",
    "scanner_name",
    "phenotyper_id",
    "session_id",
    "scan_mode",
    "experiment_id",
    "experiment_name",
    "system_name",
    "species_id",
    "species_name",
    "species_genus",
    "species_species",
    "metadata_id",
    "accession_id",
    "accession_name",
}


def _tok() -> str:
    return uuid.uuid4().hex[:10]


def _seed_experiment(cur, *, with_species=True, system_name=None):
    """Seed a gravi_experiment (optionally with a species); return (experiment_id, name)."""
    species_id = None
    if with_species:
        cur.execute(
            "INSERT INTO species (common_name, genus, species) VALUES (%s, %s, %s) RETURNING id",
            (f"Sp{_tok()}", "Arabidopsis", "thaliana"),
        )
        species_id = cur.fetchone()[0]
    name = f"gravi-exp-{_tok()}"
    cur.execute(
        "INSERT INTO gravi_experiments (name, species_id, system_name) "
        "VALUES (%s, %s, %s) RETURNING id",
        (name, species_id, system_name),
    )
    return cur.fetchone()[0], name


def _seed_scan(cur, experiment_id, *, scanner=True, session=True, metadata=True, **overrides):
    """Seed one gravi_scan, attaching the optional relations the flags ask for."""
    scanner_id = session_id = metadata_id = None
    if scanner:
        cur.execute(
            "INSERT INTO gravi_scanners (name) VALUES (%s) RETURNING id", (f"GRAV-{_tok()}",)
        )
        scanner_id = cur.fetchone()[0]
    if session:
        cur.execute(
            "INSERT INTO gravi_scan_sessions (experiment_id, scan_mode) "
            "VALUES (%s, 'continuous') RETURNING id",
            (experiment_id,),
        )
        session_id = cur.fetchone()[0]
    plate_id = overrides.get("plate_id", f"PLATE-{_tok()}")
    if metadata:
        cur.execute(
            "INSERT INTO gravi_scan_metadata_accession "
            "(plate_id, accession_name, wave_number) VALUES (%s, %s, %s) RETURNING id",
            (plate_id, f"acc-{_tok()}", 3),
        )
        metadata_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO gravi_scans "
        "(experiment_id, scanner_id, session_id, metadata_id, plate_id, grid_mode, "
        " plate_index, resolution, wave_number, cycle_number) "
        "VALUES (%s, %s, %s, %s, %s, '2x2', 'A1', 1200, 3, %s) RETURNING id",
        (
            experiment_id,
            scanner_id,
            session_id,
            metadata_id,
            plate_id,
            overrides.get("cycle_number", 0),
        ),
    )
    return cur.fetchone()[0]


def _row(cur, scan_id):
    """The view's row for one scan as a dict, or None."""
    cur.execute("SELECT * FROM gravi_scans_extended WHERE scan_id = %s", (scan_id,))
    found = cur.fetchone()
    if found is None:
        return None
    return dict(zip([c.name for c in cur.description], found))


# --------------------------------------------------------------------------- #
# Shape
# --------------------------------------------------------------------------- #


def test_view_exposes_every_column_the_cli_reads(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='gravi_scans_extended'"
        )
        present = {r[0] for r in cur.fetchall()}
        assert EXPECTED_COLUMNS <= present, f"missing: {sorted(EXPECTED_COLUMNS - present)}"
    pg_conn.rollback()


def test_view_is_security_invoker(pg_conn):
    # Without this the view runs as its owner and bypasses RLS on the gravi tables.
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT reloptions FROM pg_class WHERE relname = 'gravi_scans_extended'",
        )
        options = cur.fetchone()[0] or []
        assert "security_invoker=true" in [o.replace(" ", "") for o in options]
    pg_conn.rollback()


def test_one_row_per_scan_with_metadata_attached(pg_conn):
    with pg_conn.cursor() as cur:
        experiment_id, name = _seed_experiment(cur, system_name="GRAV-01")
        scan_id = _seed_scan(cur, experiment_id)

        cur.execute(
            "SELECT count(*) FROM gravi_scans_extended WHERE scan_id = %s", (scan_id,)
        )
        assert cur.fetchone()[0] == 1

        row = _row(cur, scan_id)
        assert row["experiment_name"] == name
        assert row["system_name"] == "GRAV-01"
        assert row["species_name"] is not None
        assert row["species_genus"] == "Arabidopsis"
        assert row["scanner_name"] is not None
        assert row["scan_mode"] == "continuous"
        assert row["accession_name"] is not None
        assert row["grid_mode"] == "2x2"
        assert row["plate_index"] == "A1"
        assert row["resolution"] == 1200
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Nullable joins — the whole reason this isn't a copy of cyl_scans_extended
# --------------------------------------------------------------------------- #


def test_scan_without_scanner_session_or_metadata_is_still_returned(pg_conn):
    with pg_conn.cursor() as cur:
        experiment_id, _ = _seed_experiment(cur)
        scan_id = _seed_scan(cur, experiment_id, scanner=False, session=False, metadata=False)

        row = _row(cur, scan_id)
        assert row is not None, "an inner join would have dropped this scan"
        assert row["scanner_id"] is None and row["scanner_name"] is None
        assert row["session_id"] is None and row["scan_mode"] is None
        assert row["metadata_id"] is None
        assert row["accession_id"] is None and row["accession_name"] is None
    pg_conn.rollback()


def test_experiment_without_species_is_still_returned(pg_conn):
    with pg_conn.cursor() as cur:
        experiment_id, _ = _seed_experiment(cur, with_species=False)
        scan_id = _seed_scan(cur, experiment_id)

        row = _row(cur, scan_id)
        assert row is not None, "an inner join on species would have dropped this scan"
        assert row["species_id"] is None
        assert row["species_name"] is None
    pg_conn.rollback()


def test_every_scan_in_an_experiment_appears(pg_conn):
    # The count the CLI relies on: the view must not lose rows to any of its joins.
    with pg_conn.cursor() as cur:
        experiment_id, _ = _seed_experiment(cur)
        _seed_scan(cur, experiment_id, plate_id=f"P1-{_tok()}")
        _seed_scan(cur, experiment_id, plate_id=f"P2-{_tok()}", scanner=False)
        _seed_scan(cur, experiment_id, plate_id=f"P3-{_tok()}", metadata=False, session=False)

        cur.execute(
            "SELECT count(*) FROM gravi_scans_extended WHERE experiment_id = %s",
            (experiment_id,),
        )
        assert cur.fetchone()[0] == 3
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Grants
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent", "bloom_admin"])
def test_select_granted_to_bloom_roles(pg_conn, role):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_table_privilege(%s, 'public.gravi_scans_extended', 'SELECT')", (role,)
        )
        assert cur.fetchone()[0] is True, f"{role} should be able to read the view"
    pg_conn.rollback()


def test_bloom_user_can_read_through_the_view(pg_conn):
    # End-to-end through the grant + security_invoker as the read role.
    with pg_conn.cursor() as cur:
        experiment_id, _ = _seed_experiment(cur)
        _seed_scan(cur, experiment_id)
        cur.execute("SET LOCAL ROLE bloom_user")
        cur.execute(
            "SELECT count(*) FROM gravi_scans_extended WHERE experiment_id = %s",
            (experiment_id,),
        )
        assert cur.fetchone()[0] == 1
        cur.execute("RESET ROLE")
    pg_conn.rollback()


def test_anon_cannot_read_the_view(pg_conn):
    # Supabase's default privileges grant SELECT on every new public object to anon; the
    # migration revokes it. Without the revoke this passes for the wrong reason (RLS returns
    # zero rows), so assert on the grant itself.
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_table_privilege('anon', 'public.gravi_scans_extended', 'SELECT')"
        )
        assert cur.fetchone()[0] is False, "anon must not read plate scans"
    pg_conn.rollback()


def test_anon_sees_no_rows_even_if_the_grant_came_back(pg_conn):
    # Defence in depth: security_invoker means RLS is evaluated as anon, and no gravi table
    # has an anon policy. Re-granting SELECT must still yield nothing.
    with pg_conn.cursor() as cur:
        experiment_id, _ = _seed_experiment(cur)
        _seed_scan(cur, experiment_id)
        cur.execute("GRANT SELECT ON public.gravi_scans_extended TO anon")
        cur.execute("SET LOCAL ROLE anon")
        cur.execute(
            "SELECT count(*) FROM gravi_scans_extended WHERE experiment_id = %s",
            (experiment_id,),
        )
        assert cur.fetchone()[0] == 0
        cur.execute("RESET ROLE")
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Migration idempotency
# --------------------------------------------------------------------------- #


def test_migration_body_is_idempotent(pg_conn):
    # Apply the migration body twice in this transaction: DROP VIEW IF EXISTS + CREATE VIEW +
    # GRANT must all survive a re-apply regardless of whether CI has already pushed it.
    body = MIGRATION.read_text()
    with pg_conn.cursor() as cur:
        cur.execute(body)
        cur.execute(body)
        cur.execute("SELECT 1 FROM pg_views WHERE viewname = 'gravi_scans_extended'")
        assert cur.fetchone() is not None
    pg_conn.rollback()
