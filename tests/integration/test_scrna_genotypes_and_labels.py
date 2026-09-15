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

import json
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
         genotype_id: int | None = None, facets=None,
         x: float | None = None) -> None:
    """`x` defaults to the cell number, which reads well in most assertions.
    Pass it explicitly where a test needs the two to disagree -- otherwise
    ordering by x and ordering by cell_number are the same thing, and a test of
    one cannot tell it from the other."""
    at = float(number) if x is None else x
    cur.execute(
        "INSERT INTO scrna_cells "
        "(dataset_id, cell_number, barcode, x, y, cluster_id, genotype_id, facets) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (dataset_id, number, f"BC{number}", at, at + 0.5,
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
    order is what keeps a genotype attached to the cell it belongs to.

    The coordinates run opposite to the cell numbers here on purpose. With x
    ascending alongside cell_number -- the obvious fixture -- ordering by either
    gives the same answer, and this test cannot tell `ORDER BY cell_number` from
    `ORDER BY x`."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)
        for n in (2, 0, 3, 1):                     # inserted out of order
            cell(cur, ds, n, x=float(100 - n))     # and x runs the other way
        cur.execute("SELECT x FROM scrna_cell_arrays(%s)", (ds,))
        assert [r[0] for r in cur.fetchall()] == [100.0, 99.0, 98.0, 97.0], (
            "cells must come back numbered 0,1,2,3 -- which here means x "
            "descending, so ordering by x would give the reverse"
        )
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


# --------------------------------------------------------------------------- #
# Bounds
# --------------------------------------------------------------------------- #


def test_a_vertical_tab_is_not_a_name(pg_conn):
    """Postgres reads \\v in an E-string as the letter v, not a vertical tab, so
    a set written that way refuses a genotype called "v" and accepts one made
    of an invisible character. Spelled \\u000b here."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        with pytest.raises(psycopg.errors.CheckViolation):
            genotype(cur, ds, "\v")
    pg_conn.rollback()


def test_a_genotype_may_be_called_v(pg_conn):
    """The other half of the same mistake: a one-letter name is a name."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        genotype(cur, ds, "v")
    pg_conn.rollback()


@pytest.mark.parametrize("field, size", [("name", 101), ("construct", 201),
                                         ("notes", 2001)])
def test_an_oversized_genotype_field_is_refused(pg_conn, field, size):
    """`name` is in the unique index, where an oversized value fails with a
    btree row-size error a reader cannot act on. `notes` is served to every
    viewer of the dataset."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        kwargs = {field: "x" * size} if field != "name" else {}
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            genotype(cur, ds, "x" * size if field == "name" else "Col-0", **kwargs)
        assert "lengths" in str(exc.value)
    pg_conn.rollback()


def test_facets_refuses_a_label_or_value_too_long_to_draw(pg_conn):
    """Every cell carries this to every viewer, so an unbounded object here is
    an unbounded page load, and a 200-character toggle is not a toggle."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)
        for facets in ('{"%s": "true"}' % ("k" * 65),
                       '{"k": "%s"}' % ("v" * 201)):
            # A refusal aborts its transaction, so each attempt gets its own
            # savepoint or the second cannot run.
            with pytest.raises(psycopg.errors.CheckViolation):
                with pg_conn.transaction():
                    cell(cur, ds, 0, facets=facets)
    pg_conn.rollback()


def test_facets_refuses_more_labels_than_a_sidebar_can_hold(pg_conn):
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)
        too_many = "{" + ", ".join(f'"k{i}": "v"' for i in range(33)) + "}"
        with pytest.raises(psycopg.errors.CheckViolation):
            with pg_conn.transaction():
                cell(cur, ds, 0, facets=too_many)

        enough = "{" + ", ".join(f'"k{i}": "v"' for i in range(32)) + "}"
        cell(cur, ds, 1, facets=enough)
    pg_conn.rollback()


def test_the_facets_check_is_not_left_to_the_public_default(pg_conn):
    """A CHECK runs with the writer's privileges. On the PUBLIC default, a later
    blanket REVOKE would turn every write to scrna_cells into a permission error
    naming a function."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_function_privilege('public', "
            "'public.scrna_facets_are_flat_text(jsonb)', 'EXECUTE')"
        )
        assert cur.fetchone()[0] is False, "still relying on the PUBLIC default"
        for role in ("authenticated", "bloom_writer", "bloom_admin"):
            cur.execute(
                "SELECT has_function_privilege(%s, "
                "'public.scrna_facets_are_flat_text(jsonb)', 'EXECUTE')", (role,)
            )
            assert cur.fetchone()[0] is True, f"{role} cannot write to scrna_cells"


def test_the_unique_constraint_covers_lookups_by_dataset(pg_conn):
    """So a separate index on dataset_id would only cost a write per insert.
    The sibling migration removed exactly that shape of index."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_indexes WHERE schemaname = 'public' "
                    "AND indexname = 'idx_scrna_genotypes_dataset'")
        assert cur.fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# Who can actually read and write, as opposed to which policies exist
# --------------------------------------------------------------------------- #
#
# A policy row proves a rule was written, not that the role can reach the table.
# The migration issues no GRANT of its own and relies on Supabase's default
# privileges, so the grants are worth asserting rather than assuming.


@pytest.mark.parametrize("role", ["anon", "authenticated", "bloom_user",
                                  "bloom_agent", "bloom_admin"])
def test_each_role_can_actually_read_the_table(pg_conn, role):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT has_table_privilege(%s, 'public.scrna_genotypes', "
                    "'SELECT')", (role,))
        assert cur.fetchone()[0] is True, f"{role} cannot read scrna_genotypes"


def test_the_cell_query_is_executable_by_everyone_who_needs_it(pg_conn):
    """The migration DROPs and recreates this function, which discards its
    grants. The explorer's only cell fetch goes through it as anon."""
    with pg_conn.cursor() as cur:
        for role in ("anon", "authenticated", "bloom_user", "bloom_agent",
                     "bloom_admin"):
            cur.execute(
                "SELECT has_function_privilege(%s, "
                "'public.scrna_cell_arrays(bigint)', 'EXECUTE')", (role,)
            )
            assert cur.fetchone()[0] is True, f"{role} lost EXECUTE on the RPC"

        # Without this the test cannot fail: CREATE FUNCTION grants EXECUTE to
        # PUBLIC, so deleting the GRANT above leaves every role still holding it.
        cur.execute(
            "SELECT has_function_privilege('public', "
            "'public.scrna_cell_arrays(bigint)', 'EXECUTE')"
        )
        assert cur.fetchone()[0] is False, "the RPC is executable by PUBLIC"


def test_the_policy_set_is_exactly_what_the_migration_declares(pg_conn):
    """Every rule, what it permits, and who it permits it to.

    The roles matter as much as the verb: anon already holds the table
    privileges from Supabase's defaults, so these rules are the only thing
    standing between an anonymous visitor and a write. Adding anon to the
    writer's insert rule is a one-word edit that changes exactly that, and a
    policyname-to-command assertion cannot see it.

    Permissive against restrictive belongs here for the same reason. A
    restrictive rule grants nothing on its own -- it only narrows what the
    permissive ones already allow -- so flipping the admin rule to RESTRICTIVE
    leaves bloom_admin with no rule that permits anything, locked out of the
    table, with every other assertion in this file still passing."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT policyname, cmd, permissive, roles FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = 'scrna_genotypes'"
        )
        got = {name: (cmd, permissive, sorted(roles))
               for name, cmd, permissive, roles in cur.fetchall()}
        assert got == {
            "Anon users can select scrna_genotypes":
                ("SELECT", "PERMISSIVE", ["anon"]),
            "Authenticated users can select scrna_genotypes":
                ("SELECT", "PERMISSIVE", ["authenticated"]),
            "admin_all_scrna_genotypes": ("ALL", "PERMISSIVE", ["bloom_admin"]),
            "user_read_scrna_genotypes":
                ("SELECT", "PERMISSIVE", ["bloom_user"]),
            "agent_read_scrna_genotypes":
                ("SELECT", "PERMISSIVE", ["bloom_agent"]),
            "writer_select_scrna_genotypes":
                ("SELECT", "PERMISSIVE", ["bloom_writer"]),
            "writer_insert_scrna_genotypes":
                ("INSERT", "PERMISSIVE", ["bloom_writer"]),
            "writer_update_scrna_genotypes":
                ("UPDATE", "PERMISSIVE", ["bloom_writer"]),
        }


def test_no_policy_lets_a_reader_write(pg_conn):
    """The predicate as well as the roles. A read rule flipped to USING (false)
    makes every dataset invisible to anonymous visitors -- the point of the
    table -- and nothing else here would notice."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT policyname, qual, with_check FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = 'scrna_genotypes' "
            "AND cmd = 'SELECT'"
        )
        for name, qual, with_check in cur.fetchall():
            assert qual == "true", f"{name} reads with {qual!r}, not true"
            assert with_check is None, f"{name} is a read rule with a write check"


@pytest.mark.parametrize("blank", ["\u00a0", "\t", "\u000b"],
                         ids=["nbsp", "tab", "vtab"])
@pytest.mark.parametrize("where", ["key", "value"])
def test_facets_refuses_an_invisible_label_or_value(pg_conn, blank, where):
    """The genotype name is covered for these characters; the facets check has
    the same set and was covered only for ordinary spaces."""
    facets = (json.dumps({blank: "true"}) if where == "key"
              else json.dumps({"a": blank}))
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)
        with pytest.raises(psycopg.errors.CheckViolation):
            cell(cur, ds, 0, facets=facets)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# The rollback
# --------------------------------------------------------------------------- #
#
# Nothing runs this file automatically, so the only time it runs is by hand
# against a database someone has filled -- which is the worst moment to find a
# syntax error in it.


def _rollback_body() -> str:
    """The rollback without its BEGIN/COMMIT, so it runs inside the fixture's
    uncommitted transaction and leaves the schema untouched."""
    matches = sorted((REPO_ROOT / "supabase" / "rollbacks")
                     .glob("*_scrna_cells_genotype_and_labels_rollback.sql"))
    assert matches, "rollback script not found"
    return "\n".join(
        line for line in matches[-1].read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def _clear(cur) -> None:
    """The state the rollback's guard demands: nothing it would destroy."""
    cur.execute("UPDATE public.scrna_cells SET facets = NULL, genotype_id = NULL")
    cur.execute("UPDATE public.scrna_clusters SET source = NULL")
    cur.execute("DELETE FROM public.scrna_genotypes")


def test_the_rollback_undoes_all_three_columns(pg_conn):
    """It added a table and three columns. The header says it drops what it
    added, and one column was being left behind."""
    with pg_conn.cursor() as cur:
        _clear(cur)
        cur.execute(_rollback_body())

        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'public' AND ("
            "  (table_name = 'scrna_cells' AND column_name IN ('genotype_id','facets'))"
            "  OR (table_name = 'scrna_clusters' AND column_name = 'source'))"
        )
        assert cur.fetchone()[0] == 0, "a column the migration added survives"

        cur.execute("SELECT to_regclass('public.scrna_genotypes') IS NULL")
        assert cur.fetchone()[0] is True
    pg_conn.rollback()


def test_the_rollback_puts_the_cell_query_back(pg_conn):
    """Leaving the function missing would take the map down, not just the new
    columns."""
    with pg_conn.cursor() as cur:
        _clear(cur)
        cur.execute(_rollback_body())
        cur.execute("SELECT count(*) FROM scrna_cell_arrays(%s)", (1,))
        cur.execute(
            "SELECT p.proargnames FROM pg_proc p "
            "WHERE p.oid = 'public.scrna_cell_arrays(bigint)'::regprocedure"
        )
        assert cur.fetchone()[0] == ["ds_id", "x", "y", "cluster_ordinal"]
    pg_conn.rollback()


def test_the_rollback_keeps_the_cells(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.scrna_cells")
        before = cur.fetchone()[0]
        _clear(cur)
        cur.execute(_rollback_body())
        cur.execute("SELECT count(*) FROM public.scrna_cells")
        assert cur.fetchone()[0] == before
    pg_conn.rollback()


@pytest.mark.parametrize("what", ["genotype", "facets", "source"])
def test_the_rollback_refuses_rather_than_destroying(pg_conn, what):
    """Each of the three things it would drop has to stop it, not just the two
    the guard originally counted."""
    with pg_conn.cursor() as cur:
        _clear(cur)
        ds = dataset(cur, species(cur))
        cluster(cur, ds, "Cortex", 0,
                source="nuclei" if what == "source" else None)
        if what == "genotype":
            genotype(cur, ds, "Col-0")
        elif what == "facets":
            cell(cur, ds, 0, facets='{"transgene_pos": "true"}')

        with pytest.raises(psycopg.errors.RaiseException) as exc:
            cur.execute(_rollback_body())
        message = str(exc.value)
        assert "Refusing to roll back" in message
        # Whichever of the three stopped it, the operator needs all three
        # statements: clearing only the one named leaves the next run refusing
        # for a different reason, with the same message.
        for statement in ("SET facets = NULL, genotype_id = NULL",
                          "SET source = NULL",
                          "DELETE FROM public.scrna_genotypes"):
            assert statement in message, f"{what}: no mention of {statement!r}"
    pg_conn.rollback()


def test_facets_is_capped_as_a_whole_and_not_only_per_field(pg_conn):
    """Every part can sit inside its own limit while the object is far past a
    page's worth: 32 labels of 64 characters with 200-character values is 8.7k
    characters, and no per-field limit can see the total."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)

        big = json.dumps({f"k{i:02d}" * 8: "v" * 200 for i in range(32)})
        assert all(len(k) <= 64 for k in json.loads(big)), "keys within limit"
        assert all(len(v) <= 200 for v in json.loads(big).values()), "values too"
        assert len(big) > 1024, "but the whole is past the cap"
        with pytest.raises(psycopg.errors.CheckViolation):
            with pg_conn.transaction():
                cell(cur, ds, 0, facets=big)
    pg_conn.rollback()


def _facets_of(cur, characters: int) -> str:
    """A facets object whose stored rendering is exactly `characters` long,
    every part inside its own limit, so only the total can refuse it."""
    built = {f"k{i:02d}": "v" * 200 for i in range(4)}
    for pad in range(1, 201):
        built["k04"] = "v" * pad
        cur.execute("SELECT length(%s::jsonb::text)", (json.dumps(built),))
        if cur.fetchone()[0] == characters:
            return json.dumps(built)
    raise AssertionError(f"no flat object renders to {characters} characters")


def test_the_cap_is_where_the_migration_says_it_is(pg_conn):
    """Without both sides of the boundary the suite only pins the cap to
    somewhere above 400 and below 7424 -- a four-fold regression, or <= turning
    into <, would ship green."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)

        cell(cur, ds, 0, facets=_facets_of(cur, 1024))
        with pytest.raises(psycopg.errors.CheckViolation):
            with pg_conn.transaction():
                cell(cur, ds, 1, facets=_facets_of(cur, 1025))
    pg_conn.rollback()


def test_a_replicate_too_long_for_a_page_is_refused(pg_conn):
    """The cell query returns this to anonymous visitors and nothing else
    bounds it, so one write would decide the size of every load of the dataset.
    Real values are sample names: Col-0, pFACT, pHORST."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)
        cell(cur, ds, 0)

        with pytest.raises(psycopg.errors.CheckViolation):
            with pg_conn.transaction():
                cur.execute(
                    "UPDATE scrna_cells SET replicate = %s WHERE dataset_id = %s",
                    ("x" * 101, ds),
                )
        cur.execute(
            "UPDATE scrna_cells SET replicate = %s WHERE dataset_id = %s",
            ("x" * 100, ds),
        )
    pg_conn.rollback()


def test_the_labels_a_dataset_would_really_carry_are_accepted(pg_conn):
    """The cap has to clear the real cases or it is just an outage. A cell
    carries about 48 bytes today; all ten of the first dataset's annotation
    columns together would be 401."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds)
        cell(cur, ds, 0, facets=json.dumps(
            {"transgene_pos": "true", "nn_source": "shahan"}))
        cell(cur, ds, 1, facets=json.dumps(
            {f"annotation_{i}": "some_cell_type_label" for i in range(10)}))
    pg_conn.rollback()


def test_a_cell_whose_cell_type_is_missing_still_comes_back(pg_conn):
    """LEFT JOIN, not JOIN. A cell with no matching cluster row must still be
    returned -- as the orphan ordinal 255, which the map draws grey.

    Dropping it instead would shift every cell after it by one, and everything
    the explorer fetches is paired to cells by position, so each would show its
    neighbour's expression, genotype and colour. Nothing would error.
    `cluster_id` is nullable and its foreign key is NOT VALID, so an unmatched
    cell is reachable."""
    with pg_conn.cursor() as cur:
        ds = dataset(cur, species(cur))
        cluster(cur, ds, "Cortex", 0)
        cell(cur, ds, 0, cluster_id="Cortex")
        cur.execute(
            "INSERT INTO scrna_cells "
            "(dataset_id, cell_number, barcode, x, y, cluster_id) "
            "VALUES (%s, 1, 'BC1', 1.0, 1.5, NULL)", (ds,),
        )
        cell(cur, ds, 2, cluster_id="Cortex")

        cur.execute("SELECT cluster_ordinal FROM scrna_cell_arrays(%s)", (ds,))
        assert [r[0] for r in cur.fetchall()] == [0, 255, 0], (
            "the unmatched cell must hold its place, not vanish"
        )
    pg_conn.rollback()
