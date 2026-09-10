"""
Integration tests for migration 20260909090000_scrna_cells_genotype_and_labels.

The migration adds `scrna_genotypes`, `scrna_cells.genotype_id`,
`scrna_cells.facets` and `scrna_clusters.source`, and redefines
`scrna_cell_arrays` to return the new columns.

Nothing writes these columns yet, so what is worth pinning is the shape they
enforce: the facets check, which is the only logic in the migration and the
thing a later loader will lean on; the refusal to delete a genotype that still
has cells; and the order `scrna_cell_arrays` returns, since everything the
explorer fetches is paired to cells by position.

LOCAL ONLY: `pg_conn` connects to 127.0.0.1 on POSTGRES_HOST_PORT and mutates
nothing -- every test rolls back. It connects as `supabase_admin`, which is
BYPASSRLS, so policy checks read the catalog rather than claiming to prove
enforcement.

Runs in CI's `compose-health-check` job after migrations are applied.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def species(cur) -> int:
    tag = uuid.uuid4().hex[:10]
    cur.execute(
        "INSERT INTO species (common_name, genus, species) VALUES (%s, %s, %s) "
        "RETURNING id",
        (f"geno-{tag}", f"Genotypus-{tag}", f"testis-{tag}"),
    )
    return cur.fetchone()[0]


def dataset(cur, species_id: int) -> int:
    cur.execute(
        "INSERT INTO scrna_datasets (name, species_id) VALUES (%s, %s) RETURNING id",
        (f"geno-{uuid.uuid4().hex[:8]}", species_id),
    )
    return cur.fetchone()[0]


def genotype(cur, dataset_id: int, name: str = "Col-0", **kwargs) -> int:
    columns = {"dataset_id": dataset_id, "name": name, **kwargs}
    cur.execute(
        f"INSERT INTO scrna_genotypes ({', '.join(columns)}) "
        f"VALUES ({', '.join(['%s'] * len(columns))}) RETURNING id",
        tuple(columns.values()),
    )
    return cur.fetchone()[0]


def cluster(cur, dataset_id: int, cluster_id: str = "Cortex", ordinal: int = 0,
            source: str | None = None) -> None:
    cur.execute(
        "INSERT INTO scrna_clusters (dataset_id, cluster_id, ordinal, name, color, source) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (dataset_id, cluster_id, ordinal, cluster_id, "#4E79A7", source),
    )


def cell(cur, dataset_id: int, number: int, cluster_id: str = "Cortex",
         genotype_id: int | None = None, facets=None) -> None:
    cur.execute(
        "INSERT INTO scrna_cells "
        "(dataset_id, cell_number, barcode, x, y, cluster_id, genotype_id, facets) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (dataset_id, number, f"BC{number}", float(number), float(number) + 0.5,
         cluster_id, genotype_id, facets),
    )


# --------------------------------------------------------------------------- #
# The columns exist and hold what they should
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("table, column", [
    ("scrna_cells", "genotype_id"),
    ("scrna_cells", "facets"),
    ("scrna_clusters", "source"),
])
def test_the_new_columns_exist(pg_conn, table, column):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
            (table, column),
        )
        assert cur.fetchone()[0] == 1


def test_the_genotypes_table_exists_with_its_columns(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'scrna_genotypes'"
        )
        assert {r[0] for r in cur.fetchall()} == {
            "id", "dataset_id", "name", "accession_id", "is_control",
            "construct", "notes", "created_at",
        }


def test_a_genotype_records_what_it_is(pg_conn):
    """The point of the table: metadata once, not repeated on every cell."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        gid = genotype(cur, ds, "pFACT", is_control=False,
                       construct="pMYB41::FACT", notes="promoter line")
        cur.execute(
            "SELECT name, is_control, construct, notes FROM scrna_genotypes "
            "WHERE id = %s", (gid,),
        )
        assert cur.fetchone() == ("pFACT", False, "pMYB41::FACT", "promoter line")
    pg_conn.rollback()


def test_is_control_defaults_to_false_rather_than_guessing(pg_conn):
    """A dataset whose wild type is not called Col-0 would be guessed wrong, so
    nothing infers it from the name."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        gid = genotype(cur, ds, "Col-0")
        cur.execute("SELECT is_control FROM scrna_genotypes WHERE id = %s", (gid,))
        assert cur.fetchone()[0] is False
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# What a genotype refuses
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("blank", ["", "   ", "\t", " "],
                         ids=["empty", "spaces", "tab", "nbsp"])
def test_a_blank_genotype_name_is_refused(pg_conn, blank):
    """NULL is the only way to say nothing. The non-breaking space is in here
    because btrim() with one argument strips ordinary spaces alone."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            genotype(cur, ds, blank)
        assert "name_not_blank" in str(exc.value)
    pg_conn.rollback()


def test_a_blank_construct_is_refused_but_none_is_allowed(pg_conn):
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        genotype(cur, ds, "Col-0", construct=None)      # a line with no construct
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            genotype(cur, ds, "pFACT", construct="   ")
        assert "construct_not_blank" in str(exc.value)
    pg_conn.rollback()


def test_one_row_per_genotype_per_dataset(pg_conn):
    """So a re-ingest lands on the row it made last time rather than a second
    copy, and a cell's genotype cannot be ambiguous."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        genotype(cur, ds, "Col-0")
        with pytest.raises(psycopg.errors.UniqueViolation):
            genotype(cur, ds, "Col-0")
    pg_conn.rollback()


def test_the_same_genotype_name_may_appear_in_different_datasets(pg_conn):
    """Col-0 is in nearly every experiment; uniqueness is per dataset."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        first, second = dataset(cur, sid), dataset(cur, sid)
        genotype(cur, first, "Col-0")
        genotype(cur, second, "Col-0")
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Deleting a genotype that still has cells
# --------------------------------------------------------------------------- #


def test_a_genotype_with_cells_cannot_be_deleted(pg_conn):
    """RESTRICT, not CASCADE: deleting a genotype must not silently take 8,683
    cells with it. Whoever wants it gone deals with the cells first."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)
        gid = genotype(cur, ds, "Col-0")
        cell(cur, ds, 0, genotype_id=gid)

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            cur.execute("DELETE FROM scrna_genotypes WHERE id = %s", (gid,))
    pg_conn.rollback()


def test_a_genotype_with_no_cells_can_be_deleted(pg_conn):
    """The accept case, so the rule above is not satisfied by refusing every
    delete."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        gid = genotype(cur, ds, "unused")
        cur.execute("DELETE FROM scrna_genotypes WHERE id = %s", (gid,))
        cur.execute("SELECT count(*) FROM scrna_genotypes WHERE id = %s", (gid,))
        assert cur.fetchone()[0] == 0
    pg_conn.rollback()


def test_a_cell_may_have_no_genotype(pg_conn):
    """Every cell already in the platform has none, and nothing backfills
    them."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)
        cell(cur, ds, 0, genotype_id=None)
    pg_conn.rollback()


def test_a_cell_cannot_point_at_a_genotype_that_does_not_exist(pg_conn):
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            cell(cur, ds, 0, genotype_id=999999999)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# What facets may hold
# --------------------------------------------------------------------------- #
#
# The check is the only logic in this migration, and the loader that fills the
# column will lean on it. An object of short strings is the one shape the
# explorer can draw as toggles.


@pytest.mark.parametrize("value", [
    '{"transgene_pos": "true"}',
    '{"transgene_pos": "true", "nn_source": "shahan"}',
    '{}',
    None,
], ids=["one", "several", "empty", "null"])
def test_facets_accepts_an_object_of_strings(pg_conn, value):
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)
        cell(cur, ds, 0, facets=value)
    pg_conn.rollback()


@pytest.mark.parametrize("value", [
    '["a", "b"]',
    '42',
    '"a string"',
    'true',
    '{"nested": {"a": "b"}}',
    '{"a": ["b"]}',
    '{"a": 1}',
    '{"a": null}',
], ids=["array", "number", "string", "bool", "nested-object",
        "nested-array", "number-value", "null-value"])
def test_facets_refuses_anything_the_explorer_cannot_draw(pg_conn, value):
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            cell(cur, ds, 0, facets=value)
        assert "facets_are_flat_text" in str(exc.value)
    pg_conn.rollback()


@pytest.mark.parametrize("value", ['{"": "true"}', '{"   ": "true"}',
                                   '{"a": ""}', '{"a": "   "}'],
                         ids=["blank-key", "spaces-key", "blank-value",
                              "spaces-value"])
def test_facets_refuses_a_blank_label_or_value(pg_conn, value):
    """A blank would reach the browser as a toggle with no name, or a name with
    nothing behind it."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)
        with pytest.raises(psycopg.errors.CheckViolation):
            cell(cur, ds, 0, facets=value)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# The cell query
# --------------------------------------------------------------------------- #


def test_the_cell_query_returns_the_new_columns(pg_conn):
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)
        gid = genotype(cur, ds, "pFACT")
        cell(cur, ds, 0, genotype_id=gid, facets='{"transgene_pos": "true"}')

        cur.execute("SELECT x, y, cluster_ordinal, genotype, replicate, facets "
                    "FROM scrna_cell_arrays(%s)", (ds,))
        x, y, ordinal, geno, _replicate, facets = cur.fetchone()
        assert (x, y, ordinal, geno) == (0.0, 0.5, 0, "pFACT")
        assert facets == {"transgene_pos": "true"}
    pg_conn.rollback()


def test_the_cell_query_still_returns_cells_in_file_order(pg_conn):
    """Everything the explorer fetches is paired to cells by position, so this
    order is what keeps a genotype attached to the cell it belongs to."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)
        for n in (2, 0, 3, 1):          # inserted out of order on purpose
            cell(cur, ds, n)
        cur.execute("SELECT x FROM scrna_cell_arrays(%s)", (ds,))
        assert [r[0] for r in cur.fetchall()] == [0.0, 1.0, 2.0, 3.0]
    pg_conn.rollback()


def test_a_cell_with_no_genotype_comes_back_as_null(pg_conn):
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)
        cell(cur, ds, 0, genotype_id=None)
        cur.execute("SELECT genotype, facets FROM scrna_cell_arrays(%s)", (ds,))
        assert cur.fetchone() == (None, None)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Cell type source
# --------------------------------------------------------------------------- #


def test_a_cell_type_records_where_its_label_came_from(pg_conn):
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds, "Cortex", 0, source="nuclei")
        cluster(cur, ds, "Pericycle", 1, source="shahan (73%), nuclei (27%)")
        cur.execute("SELECT cluster_id, source FROM scrna_clusters "
                    "WHERE dataset_id = %s ORDER BY ordinal", (ds,))
        assert cur.fetchall() == [
            ("Cortex", "nuclei"),
            ("Pericycle", "shahan (73%), nuclei (27%)"),
        ]
    pg_conn.rollback()


def test_a_cell_type_may_have_no_source(pg_conn):
    """Every cell type already in the platform has none."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds, "Cortex", 0, source=None)
        cur.execute("SELECT source FROM scrna_clusters WHERE dataset_id = %s", (ds,))
        assert cur.fetchone()[0] is None
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Who may read the genotypes
# --------------------------------------------------------------------------- #


def test_row_level_security_is_on(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT relrowsecurity FROM pg_class "
                    "WHERE oid = 'public.scrna_genotypes'::regclass")
        assert cur.fetchone()[0] is True


@pytest.mark.parametrize("policy, role", [
    ("Anon users can select scrna_genotypes", "anon"),
    ("Authenticated users can select scrna_genotypes", "authenticated"),
    ("admin_all_scrna_genotypes", "bloom_admin"),
    ("user_read_scrna_genotypes", "bloom_user"),
    ("agent_read_scrna_genotypes", "bloom_agent"),
])
def test_each_read_policy_exists_for_its_role(pg_conn, policy, role):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT roles FROM pg_policies WHERE schemaname = 'public' "
                    "AND tablename = 'scrna_genotypes' AND policyname = %s",
                    (policy,))
        row = cur.fetchone()
        assert row is not None, f"{policy} is missing"
        assert role in row[0]


@pytest.mark.parametrize("policy, cmd", [
    ("Anon users can select scrna_genotypes", "SELECT"),
    ("Authenticated users can select scrna_genotypes", "SELECT"),
    ("user_read_scrna_genotypes", "SELECT"),
    ("agent_read_scrna_genotypes", "SELECT"),
    ("admin_all_scrna_genotypes", "ALL"),
])
def test_no_read_policy_grants_more_than_reading(pg_conn, policy, cmd):
    """A copied policy block is how a table quietly gains a write it was never
    meant to have."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT cmd FROM pg_policies WHERE schemaname = 'public' "
                    "AND tablename = 'scrna_genotypes' AND policyname = %s",
                    (policy,))
        assert cur.fetchone()[0] == cmd


# --------------------------------------------------------------------------- #
# Re-running the migration
# --------------------------------------------------------------------------- #


def _migration_body() -> str:
    matches = sorted((REPO_ROOT / "supabase" / "migrations")
                     .glob("*_scrna_cells_genotype_and_labels.sql"))
    assert matches, "migration not found"
    return "\n".join(
        line for line in matches[-1].read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def test_running_it_again_changes_nothing(pg_conn):
    """It is written with IF NOT EXISTS and DROP IF EXISTS throughout. Nothing
    proved that, and a migration that cannot be re-run is one that cannot be
    recovered by re-running."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds, "Cortex", 0, source="nuclei")
        gid = genotype(cur, ds, "Col-0")
        cell(cur, ds, 0, genotype_id=gid, facets='{"transgene_pos": "true"}')

        cur.execute(_migration_body())

        cur.execute("SELECT genotype, facets FROM scrna_cell_arrays(%s)", (ds,))
        assert cur.fetchone() == ("Col-0", {"transgene_pos": "true"})
        cur.execute("SELECT source FROM scrna_clusters WHERE dataset_id = %s", (ds,))
        assert cur.fetchone()[0] == "nuclei"
    pg_conn.rollback()


def test_a_cell_cannot_take_a_genotype_from_another_dataset(pg_conn):
    """Col-0 exists in most Arabidopsis experiments, so an ingest resolving a
    genotype name against the wrong dataset is a live mistake. The reference
    carries dataset_id for the same reason the cluster reference on this table
    does."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        mine, other = dataset(cur, sid), dataset(cur, sid)
        cluster(cur, mine)
        elsewhere = genotype(cur, other, "Col-0")

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            cell(cur, mine, 0, genotype_id=elsewhere)
    pg_conn.rollback()


def test_a_cell_takes_a_genotype_from_its_own_dataset(pg_conn):
    """The accept case, so the rule above is not satisfied by refusing every
    genotype."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)
        gid = genotype(cur, ds, "Col-0")
        cell(cur, ds, 0, genotype_id=gid)

        cur.execute("SELECT genotype FROM scrna_cell_arrays(%s)", (ds,))
        assert cur.fetchone()[0] == "Col-0"
    pg_conn.rollback()


@pytest.mark.parametrize("cmd", ["SELECT", "INSERT", "UPDATE"])
def test_the_ingest_role_can_write_genotypes(pg_conn, cmd):
    """bloom_writer is the ingest role. Without these the loader could point
    cells at genotypes it is not allowed to create, which is no loader at all.
    Every other scrna_* table grants it the same three."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM pg_policies WHERE schemaname = 'public' "
            "AND tablename = 'scrna_genotypes' AND cmd = %s "
            "AND 'bloom_writer' = ANY(roles)",
            (cmd,),
        )
        assert cur.fetchone()[0] == 1, f"bloom_writer has no {cmd} policy"


def test_the_ingest_role_gets_no_more_than_its_siblings(pg_conn):
    """A copied policy block is how a table quietly gains a delete."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT tablename, array_agg(DISTINCT cmd ORDER BY cmd) "
            "FROM pg_policies WHERE schemaname = 'public' "
            "AND tablename IN ('scrna_genotypes', 'scrna_clusters') "
            "AND 'bloom_writer' = ANY(roles) GROUP BY tablename"
        )
        by_table = dict(cur.fetchall())
        assert by_table["scrna_genotypes"] == by_table["scrna_clusters"]
