"""Integration tests for the cyl read-model views (PR #450).

Covers the three views that back `bloomctl cyl datasets get` / `cyl accessions`:
  * cyl_dataset_trait_names        — distinct trait names per dataset
  * cyl_experiment_accessions      — distinct accessions per experiment
  * cyl_accession_sample_counts    — plant count per accession, per species

LOCAL ONLY: the `pg_conn` fixture (see conftest) connects as `supabase_admin`
(BYPASSRLS); every test rolls back. RLS/grants are exercised with `SET LOCAL ROLE`.
Data is seeded by direct INSERT (allowed for supabase_admin).
"""

import itertools

import pytest

psycopg = pytest.importorskip("psycopg")

_uniq = itertools.count(1)

VIEWS = ("cyl_dataset_trait_names", "cyl_experiment_accessions", "cyl_accession_sample_counts")


# --------------------------------------------------------------------------- #
# Seeding helpers
# --------------------------------------------------------------------------- #


def _species(cur, common_name="Test Sp"):
    cur.execute("INSERT INTO species (common_name) VALUES (%s) RETURNING id", (common_name,))
    return cur.fetchone()[0]


def _experiment(cur, *, species_id, deleted=False):
    n = next(_uniq)
    cur.execute(
        "INSERT INTO cyl_experiments (name, species_id) VALUES (%s, %s) RETURNING id",
        (f"exp-{n}", species_id),
    )
    exp_id = cur.fetchone()[0]
    if deleted:
        cur.execute("UPDATE cyl_experiments SET deleted_at = now() WHERE id = %s", (exp_id,))
    return exp_id


def _wave(cur, exp_id):
    cur.execute(
        "INSERT INTO cyl_waves (experiment_id, number) VALUES (%s, 1) RETURNING id", (exp_id,)
    )
    return cur.fetchone()[0]


def _accession(cur, name):
    cur.execute("INSERT INTO accessions (name) VALUES (%s) RETURNING id", (name,))
    return cur.fetchone()[0]


def _plant(cur, wave_id, accession_id):
    cur.execute(
        "INSERT INTO cyl_plants (wave_id, accession_id) VALUES (%s, %s) RETURNING id",
        (wave_id, accession_id),
    )
    return cur.fetchone()[0]


def _scan(cur, plant_id, date="2026-01-01"):
    cur.execute(
        "INSERT INTO cyl_scans (plant_id, date_scanned, plant_age_days) VALUES (%s, %s, 10) RETURNING id",
        (plant_id, date),
    )
    return cur.fetchone()[0]


def _trait(cur, name):
    cur.execute("INSERT INTO cyl_traits (name) VALUES (%s) RETURNING id", (name,))
    return cur.fetchone()[0]


def _dataset(cur, exp_id):
    n = next(_uniq)
    cur.execute(
        "INSERT INTO cyl_datasets (name, experiment_id) VALUES (%s, %s) RETURNING id",
        (f"ds-{n}", exp_id),
    )
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# cyl_dataset_trait_names
# --------------------------------------------------------------------------- #


def test_dataset_trait_names_collapses_over_1000_rows(pg_conn):
    """>1000 cyl_dataset_traits across 3 distinct traits → the view returns exactly 3."""
    with pg_conn.cursor() as cur:
        sp = _species(cur)
        exp = _experiment(cur, species_id=sp)
        wave = _wave(cur, exp)
        acc = _accession(cur, f"acc-{next(_uniq)}")
        plant = _plant(cur, wave, acc)
        scan = _scan(cur, plant)
        t = [_trait(cur, f"vt-{next(_uniq)}") for _ in range(3)]
        # 1002 scan-trait rows for one scan (source_id NULL → unique treats them distinct),
        # cycling the 3 traits so every trait is represented many times.
        cur.execute(
            "INSERT INTO cyl_scan_traits (scan_id, trait_id) "
            "SELECT %s, (ARRAY[%s,%s,%s]::bigint[])[1 + (g %% 3)] "
            "FROM generate_series(1, 1002) g",
            (scan, t[0], t[1], t[2]),
        )
        ds = _dataset(cur, exp)
        cur.execute(
            "INSERT INTO cyl_dataset_traits (dataset_id, trait_id) "
            "SELECT %s, id FROM cyl_scan_traits WHERE scan_id = %s",
            (ds, scan),
        )
        cur.execute(
            "SELECT count(*) FROM cyl_dataset_trait_names WHERE dataset_id = %s", (ds,)
        )
        assert cur.fetchone()[0] == 3
    pg_conn.rollback()


def test_dataset_trait_names_excludes_null_trait_id(pg_conn):
    """A cyl_scan_traits row with NULL trait_id (legacy backfill gap) is excluded."""
    with pg_conn.cursor() as cur:
        sp = _species(cur)
        exp = _experiment(cur, species_id=sp)
        wave = _wave(cur, exp)
        acc = _accession(cur, f"acc-{next(_uniq)}")
        scan = _scan(cur, _plant(cur, wave, acc))
        t1 = _trait(cur, f"vt-{next(_uniq)}")
        cur.execute(
            "INSERT INTO cyl_scan_traits (scan_id, trait_id) VALUES (%s, %s), (%s, NULL) RETURNING id",
            (scan, t1, scan),
        )
        st_ids = [r[0] for r in cur.fetchall()]
        ds = _dataset(cur, exp)
        cur.executemany(
            "INSERT INTO cyl_dataset_traits (dataset_id, trait_id) VALUES (%s, %s)",
            [(ds, sid) for sid in st_ids],
        )
        cur.execute("SELECT count(*) FROM cyl_dataset_trait_names WHERE dataset_id = %s", (ds,))
        assert cur.fetchone()[0] == 1  # only the resolvable trait; NULL trait_id dropped
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# cyl_experiment_accessions / cyl_accession_sample_counts
# --------------------------------------------------------------------------- #


def test_experiment_accessions_distinct(pg_conn):
    """One accession across two plants in one experiment → exactly one row."""
    with pg_conn.cursor() as cur:
        exp = _experiment(cur, species_id=_species(cur))
        wave = _wave(cur, exp)
        acc = _accession(cur, f"acc-{next(_uniq)}")
        _plant(cur, wave, acc)
        _plant(cur, wave, acc)
        cur.execute(
            "SELECT count(*) FROM cyl_experiment_accessions WHERE experiment_id = %s", (exp,)
        )
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_sample_counts_counts_plants_not_scans(pg_conn):
    """1 accession x 2 plants x 3 scans each → plant_count = 2 (not 6)."""
    with pg_conn.cursor() as cur:
        sp = _species(cur, "Countable")
        exp = _experiment(cur, species_id=sp)
        wave = _wave(cur, exp)
        acc = _accession(cur, f"acc-{next(_uniq)}")
        for _ in range(2):
            plant = _plant(cur, wave, acc)
            for d in ("2026-01-01", "2026-01-02", "2026-01-03"):
                _scan(cur, plant, date=d)
        cur.execute(
            "SELECT plant_count FROM cyl_accession_sample_counts WHERE accession_id = %s", (acc,)
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 2
    pg_conn.rollback()


def test_accession_views_exclude_soft_deleted_experiments(pg_conn):
    """Accessions in a soft-deleted experiment appear in neither view."""
    with pg_conn.cursor() as cur:
        exp = _experiment(cur, species_id=_species(cur), deleted=True)
        wave = _wave(cur, exp)
        acc = _accession(cur, f"acc-{next(_uniq)}")
        _plant(cur, wave, acc)
        cur.execute("SELECT count(*) FROM cyl_experiment_accessions WHERE experiment_id = %s", (exp,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM cyl_accession_sample_counts WHERE accession_id = %s", (acc,))
        assert cur.fetchone()[0] == 0
    pg_conn.rollback()


def test_sample_counts_left_joins_species(pg_conn):
    """An experiment with no species still yields a row (species_name NULL)."""
    with pg_conn.cursor() as cur:
        exp = _experiment(cur, species_id=None)
        wave = _wave(cur, exp)
        acc = _accession(cur, f"acc-{next(_uniq)}")
        _plant(cur, wave, acc)
        cur.execute(
            "SELECT species_id, species_name, plant_count "
            "FROM cyl_accession_sample_counts WHERE accession_id = %s",
            (acc,),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] is None and row[1] is None  # species left-joined
        assert row[2] == 1
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Grants + RLS (security_invoker)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("view", VIEWS)
@pytest.mark.parametrize("role", ["authenticated", "bloom_user", "bloom_admin", "bloom_agent"])
def test_view_grants_present(pg_conn, view, role):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (role, view))
        assert cur.fetchone()[0] is True, f"{role} should have SELECT on {view}"
    pg_conn.rollback()


@pytest.mark.parametrize("view", VIEWS)
def test_view_not_granted_to_anon(pg_conn, view):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT has_table_privilege('anon', %s, 'SELECT')", (view,))
        assert cur.fetchone()[0] is False, f"anon must NOT have SELECT on {view}"
    pg_conn.rollback()


def test_dataset_trait_names_readable_as_bloom_user(pg_conn):
    """security_invoker: a bloom_user (RLS-scoped) reads its own seeded rows through the view."""
    with pg_conn.cursor() as cur:
        sp = _species(cur)
        exp = _experiment(cur, species_id=sp)
        wave = _wave(cur, exp)
        scan = _scan(cur, _plant(cur, wave, _accession(cur, f"acc-{next(_uniq)}")))
        t1 = _trait(cur, f"vt-{next(_uniq)}")
        cur.execute(
            "INSERT INTO cyl_scan_traits (scan_id, trait_id) VALUES (%s, %s) RETURNING id",
            (scan, t1),
        )
        st = cur.fetchone()[0]
        ds = _dataset(cur, exp)
        cur.execute("INSERT INTO cyl_dataset_traits (dataset_id, trait_id) VALUES (%s, %s)", (ds, st))
        cur.execute("SET LOCAL ROLE bloom_user")
        cur.execute("SELECT count(*) FROM cyl_dataset_trait_names WHERE dataset_id = %s", (ds,))
        assert cur.fetchone()[0] == 1
        cur.execute("RESET ROLE")
    pg_conn.rollback()
