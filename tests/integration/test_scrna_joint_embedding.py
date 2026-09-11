"""Integration tests for 20260911082453_scrna_joint_embedding.sql.

A joint embedding (a SATURN integration, say) is stored as its datasets, its
labels and its points. What is pinned here is what keeps such a map honest:
a reference dataset holds no cells, a point belongs to a member dataset and
links only to that dataset's own cell, a map is finished only when every point
it counts is stored, readers never see a half-loaded map, and each role can do
exactly what it needs and nothing more.

Each rejection names the constraint it expects. Every test rolls back.
"""

import re
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
NAME = "scrna_joint_embedding"
TABLES = ("scrna_embeddings", "scrna_embedding_members",
          "scrna_embedding_labels", "scrna_embedding_points")
CHECKSUM = "a" * 64


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _tag() -> str:
    return uuid.uuid4().hex[:10]


def _dataset(cur, kind="full") -> int:
    tag = _tag()
    cur.execute(
        "INSERT INTO species (common_name, genus, species) VALUES (%s, %s, %s) "
        "RETURNING id", (f"joint-{tag}", f"Testus-{tag}", f"jointis-{tag}"),
    )
    species_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO scrna_datasets (name, species_id, kind) VALUES (%s, %s, %s) "
        "RETURNING id", (f"joint-{tag}", species_id, kind),
    )
    return cur.fetchone()[0]


def _cells(cur, dataset_id, n, facets=None, genotype_id=None) -> list[int]:
    ids = []
    for i in range(n):
        cur.execute(
            "INSERT INTO scrna_cells (dataset_id, cell_number, barcode, x, y, facets, "
            "genotype_id) VALUES (%s, %s, %s, 0, 0, %s, %s) RETURNING id",
            (dataset_id, i, f"cell-{i}", psycopg.types.json.Jsonb(facets) if facets else None,
             genotype_id),
        )
        ids.append(cur.fetchone()[0])
    return ids


def _embedding(cur, n_points=4, **cols) -> int:
    cols = {"name": f"joint-{_tag()}", "title": "A joint map", "method": "SATURN",
            "source_checksum": CHECKSUM, "n_points": n_points, **cols}
    cur.execute(
        f"INSERT INTO scrna_embeddings ({', '.join(cols)}) "
        f"VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
        list(cols.values()),
    )
    return cur.fetchone()[0]


def _member(cur, embedding_id, dataset_id, role, ordinal, n_points):
    cur.execute(
        "INSERT INTO scrna_embedding_members (embedding_id, dataset_id, role, ordinal, "
        "n_points) VALUES (%s, %s, %s, %s, %s)",
        (embedding_id, dataset_id, role, ordinal, n_points),
    )


def _point(cur, embedding_id, dataset_id, ordinal, barcode=None, cell_id=None,
           x=0.0, y=0.0, facets=None):
    cur.execute(
        "INSERT INTO scrna_embedding_points (embedding_id, ordinal, dataset_id, barcode, "
        "cell_id, x, y, facets) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (embedding_id, ordinal, dataset_id, barcode or f"b-{ordinal}", cell_id, x, y,
         psycopg.types.json.Jsonb(facets) if facets is not None else None),
    )


def _finish(cur, embedding_id):
    cur.execute("UPDATE scrna_embeddings SET ingested_at = now() WHERE id = %s",
                (embedding_id,))


@contextmanager
def _refused(cur, constraint=None, message=None, error=psycopg.errors.IntegrityError):
    """Expect the statement inside to fail, in a savepoint so the test goes on."""
    with pytest.raises(error) as exc:
        with cur.connection.transaction():
            yield
    if constraint is not None:
        assert exc.value.diag.constraint_name == constraint, (
            f"expected {constraint}, got {exc.value.diag.constraint_name}: {exc.value}"
        )
    if message is not None:
        assert re.search(message, str(exc.value)), str(exc.value)


@contextmanager
def _as_role(cur, role):
    cur.execute(f"SET LOCAL ROLE {role}")
    try:
        yield
    finally:
        cur.execute("RESET ROLE")


def _joint_map(cur):
    """A finished embedding: a reference with two points, a query with two cells
    linked, inserted out of order so storage order is not point order."""
    reference = _dataset(cur, kind="reference")
    query = _dataset(cur)
    cur.execute("INSERT INTO scrna_genotypes (dataset_id, name, is_control) "
                "VALUES (%s, 'pFACT', false) RETURNING id", (query,))
    pfact = cur.fetchone()[0]
    cells = _cells(cur, query, 2, facets={"transgene_pos": "True"}, genotype_id=pfact)
    emb = _embedding(cur, n_points=4)
    _member(cur, emb, reference, "reference", 0, 2)
    _member(cur, emb, query, "query", 1, 2)
    _point(cur, emb, query, 2, "q-0", cells[0], 2.0, 20.0, {"shahan_cell_type": "Cortex"})
    _point(cur, emb, reference, 0, "r-0", None, 0.0, 0.0, {"shahan_cell_type": "Xylem"})
    _point(cur, emb, query, 3, "q-1", cells[1], 3.0, 30.0)
    _point(cur, emb, reference, 1, "r-1", None, 1.0, 10.0, {"shahan_cell_type": "Cortex"})
    _finish(cur, emb)
    return {"embedding": emb, "reference": reference, "query": query, "cells": cells}


def _script(folder: str, pattern: str) -> str:
    """A migration or rollback without BEGIN/COMMIT, to run inside the test."""
    matches = sorted((REPO_ROOT / "supabase" / folder).glob(pattern))
    assert matches, f"{pattern} not found"
    return "\n".join(
        line for line in matches[-1].read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


# --------------------------------------------------------------------------- #
# Reference datasets
# --------------------------------------------------------------------------- #


def test_a_dataset_is_full_unless_registered_as_a_reference(pg_conn):
    with pg_conn.cursor() as cur:
        tag = _tag()
        cur.execute("INSERT INTO species (common_name, genus, species) VALUES "
                    "(%s, %s, %s) RETURNING id", (f"k-{tag}", f"K-{tag}", f"k-{tag}"))
        cur.execute("INSERT INTO scrna_datasets (name, species_id) VALUES (%s, %s) "
                    "RETURNING kind", (f"k-{tag}", cur.fetchone()[0]))
        assert cur.fetchone()[0] == "full"
        with _refused(cur, "scrna_datasets_kind_valid"):
            _dataset(cur, kind="atlas")
    pg_conn.rollback()


def test_a_reference_dataset_refuses_cells(pg_conn):
    with pg_conn.cursor() as cur:
        reference = _dataset(cur, kind="reference")
        with _refused(cur, message=r"is a reference and holds no cells"):
            _cells(cur, reference, 1)
    pg_conn.rollback()


def test_a_cell_cannot_be_moved_into_a_reference_dataset(pg_conn):
    with pg_conn.cursor() as cur:
        full, reference = _dataset(cur), _dataset(cur, kind="reference")
        (cell,) = _cells(cur, full, 1)
        with _refused(cur, message=r"is a reference and holds no cells"):
            cur.execute("UPDATE scrna_cells SET dataset_id = %s WHERE id = %s",
                        (reference, cell))
    pg_conn.rollback()


def test_a_dataset_with_cells_cannot_become_a_reference(pg_conn):
    with pg_conn.cursor() as cur:
        full = _dataset(cur)
        _cells(cur, full, 1)
        with _refused(cur, message=r"holds cells, so it cannot be a reference"):
            cur.execute("UPDATE scrna_datasets SET kind = 'reference' WHERE id = %s", (full,))
        # A reference that later gets cells of its own becomes full first.
        reference = _dataset(cur, kind="reference")
        cur.execute("UPDATE scrna_datasets SET kind = 'full' WHERE id = %s", (reference,))
        _cells(cur, reference, 1)
    pg_conn.rollback()


def test_a_dataset_in_an_embedding_cannot_be_soft_deleted(pg_conn):
    with pg_conn.cursor() as cur:
        m = _joint_map(cur)
        with _refused(cur, message=r"is in a joint embedding"):
            cur.execute("UPDATE scrna_datasets SET deleted_at = now() WHERE id = %s",
                        (m["reference"],))
        loose = _dataset(cur)
        cur.execute("UPDATE scrna_datasets SET deleted_at = now() WHERE id = %s", (loose,))
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Members, points and labels
# --------------------------------------------------------------------------- #


def test_a_point_belongs_to_a_member_dataset(pg_conn):
    with pg_conn.cursor() as cur:
        emb = _embedding(cur)
        outsider = _dataset(cur)
        with _refused(cur, "scrna_embedding_points_member_fkey"):
            _point(cur, emb, outsider, 0)
    pg_conn.rollback()


def test_a_member_is_listed_once_and_in_one_place(pg_conn):
    with pg_conn.cursor() as cur:
        emb, a, b = _embedding(cur), _dataset(cur), _dataset(cur)
        _member(cur, emb, a, "reference", 0, 1)
        with _refused(cur, "scrna_embedding_members_pkey"):
            _member(cur, emb, a, "query", 1, 1)
        with _refused(cur, "scrna_embedding_members_ordinal_unique"):
            _member(cur, emb, b, "query", 0, 1)
        with _refused(cur, "scrna_embedding_members_role_valid"):
            _member(cur, emb, b, "anchor", 1, 1)
        with _refused(cur, "scrna_embedding_members_points_positive"):
            _member(cur, emb, b, "query", 1, 0)
    pg_conn.rollback()


def test_a_barcode_and_an_order_are_used_once(pg_conn):
    with pg_conn.cursor() as cur:
        emb, ds = _embedding(cur), _dataset(cur, kind="reference")
        _member(cur, emb, ds, "reference", 0, 2)
        _point(cur, emb, ds, 0, "AAAC")
        with _refused(cur, "scrna_embedding_points_barcode_unique"):
            _point(cur, emb, ds, 1, "AAAC")
        with _refused(cur, "scrna_embedding_points_pkey"):
            _point(cur, emb, ds, 0, "AAAG")
    pg_conn.rollback()


def test_a_point_links_only_to_its_own_datasets_cell_and_only_once(pg_conn):
    with pg_conn.cursor() as cur:
        emb, query, other = _embedding(cur), _dataset(cur), _dataset(cur)
        (mine,) = _cells(cur, query, 1)
        (theirs,) = _cells(cur, other, 1)
        _member(cur, emb, query, "query", 0, 2)
        with _refused(cur, "scrna_embedding_points_cell_fkey"):
            _point(cur, emb, query, 0, "x", theirs)
        _point(cur, emb, query, 0, "x", mine)
        with _refused(cur, "scrna_embedding_points_cell_unique"):
            _point(cur, emb, query, 1, "y", mine)
    pg_conn.rollback()


def test_a_linked_cell_cannot_be_deleted(pg_conn):
    with pg_conn.cursor() as cur:
        m = _joint_map(cur)
        with _refused(cur, "scrna_embedding_points_cell_fkey"):
            cur.execute("DELETE FROM scrna_cells WHERE id = %s", (m["cells"][0],))
    pg_conn.rollback()


@pytest.mark.parametrize("facets", [{"shahan_cell_type": 3}, {"genotype": "pFACT"}, {"x": ""}])
def test_point_labels_are_short_text_and_never_genotype(pg_conn, facets):
    with pg_conn.cursor() as cur:
        emb, ds = _embedding(cur), _dataset(cur, kind="reference")
        _member(cur, emb, ds, "reference", 0, 1)
        with _refused(cur, "scrna_embedding_points_facets_are_flat_text"):
            _point(cur, emb, ds, 0, facets=facets)
    pg_conn.rollback()


@pytest.mark.parametrize("x", [float("nan"), float("inf")])
def test_a_position_is_a_finite_number(pg_conn, x):
    with pg_conn.cursor() as cur:
        emb, ds = _embedding(cur), _dataset(cur, kind="reference")
        _member(cur, emb, ds, "reference", 0, 1)
        with _refused(cur, "scrna_embedding_points_position_finite"):
            _point(cur, emb, ds, 0, x=x)
    pg_conn.rollback()


def test_a_label_is_native_to_a_member_or_to_none(pg_conn):
    with pg_conn.cursor() as cur:
        emb, member, outsider = _embedding(cur), _dataset(cur), _dataset(cur)
        _member(cur, emb, member, "reference", 0, 1)
        cur.execute("INSERT INTO scrna_embedding_labels (embedding_id, key, source_column, "
                    "native_dataset_id) VALUES (%s, 'shahan_cell_type', 'Celltype', %s)",
                    (emb, member))
        cur.execute("INSERT INTO scrna_embedding_labels (embedding_id, key, source_column) "
                    "VALUES (%s, 'nn_label', 'myb41__nn_label_plain')", (emb,))
        with _refused(cur, "scrna_embedding_labels_native_member_fkey"):
            cur.execute("INSERT INTO scrna_embedding_labels (embedding_id, key, "
                        "source_column, native_dataset_id) VALUES (%s, 'other', 'c', %s)",
                        (emb, outsider))
        with _refused(cur, "scrna_embedding_labels_key_shape"):
            cur.execute("INSERT INTO scrna_embedding_labels (embedding_id, key, source_column) "
                        "VALUES (%s, 'genotype', 'sample')", (emb,))
    pg_conn.rollback()


def test_an_embedding_name_is_unique_and_its_checksum_is_sha256(pg_conn):
    with pg_conn.cursor() as cur:
        name = f"joint-{_tag()}"
        _embedding(cur, name=name)
        with _refused(cur, "scrna_embeddings_name_unique"):
            _embedding(cur, name=name)
        with _refused(cur, "scrna_embeddings_checksum_is_sha256"):
            _embedding(cur, source_checksum="not-a-checksum")
    pg_conn.rollback()


def test_deleting_an_embedding_removes_only_the_embedding(pg_conn):
    with pg_conn.cursor() as cur:
        m = _joint_map(cur)
        cur.execute("INSERT INTO scrna_embedding_labels (embedding_id, key, source_column) "
                    "VALUES (%s, 'nn_label', 'c')", (m["embedding"],))
        cur.execute("DELETE FROM scrna_embeddings WHERE id = %s", (m["embedding"],))
        for table in TABLES[1:]:
            cur.execute(f"SELECT count(*) FROM {table} WHERE embedding_id = %s",
                        (m["embedding"],))
            assert cur.fetchone()[0] == 0, table
        cur.execute("SELECT count(*) FROM scrna_datasets WHERE id IN (%s, %s)",
                    (m["reference"], m["query"]))
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT count(*) FROM scrna_cells WHERE id = ANY(%s)", (m["cells"],))
        assert cur.fetchone()[0] == 2
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Finishing an embedding
# --------------------------------------------------------------------------- #


def test_an_embedding_is_finished_only_when_every_point_is_stored(pg_conn):
    with pg_conn.cursor() as cur:
        emb, ds = _embedding(cur, n_points=2), _dataset(cur, kind="reference")
        _member(cur, emb, ds, "reference", 0, 2)
        _point(cur, emb, ds, 0)
        with _refused(cur, message=r"counts 2 points but holds 1"):
            _finish(cur, emb)
        _point(cur, emb, ds, 1)
        _finish(cur, emb)
    pg_conn.rollback()


def test_an_embedding_is_finished_only_when_each_member_holds_its_count(pg_conn):
    with pg_conn.cursor() as cur:
        emb, a, b = _embedding(cur, n_points=2), _dataset(cur, kind="reference"), _dataset(
            cur, kind="reference")
        _member(cur, emb, a, "reference", 0, 1)
        _member(cur, emb, b, "reference", 1, 1)
        _point(cur, emb, a, 0)
        _point(cur, emb, a, 1)
        with _refused(cur, message=r"does not hold the points its member row counts"):
            _finish(cur, emb)
    pg_conn.rollback()


def test_an_embedding_cannot_be_created_already_finished(pg_conn):
    with pg_conn.cursor() as cur:
        with _refused(cur, message=r"counts 4 points but holds 0"):
            _embedding(cur, ingested_at="2026-09-11T00:00:00Z")
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Reading the map
# --------------------------------------------------------------------------- #


def test_the_whole_map_comes_back_in_one_row_in_point_order(pg_conn):
    with pg_conn.cursor() as cur:
        m = _joint_map(cur)
        cur.execute("SELECT * FROM scrna_embedding_arrays(%s)", (m["embedding"],))
        rows = cur.fetchall()
        assert len(rows) == 1
        x, y, member_ordinal, cell_id = rows[0]
        assert x == [0.0, 1.0, 2.0, 3.0]
        assert y == [0.0, 10.0, 20.0, 30.0]
        assert member_ordinal == [0, 0, 1, 1]
        assert cell_id == [None, None, *m["cells"]]
    pg_conn.rollback()


def test_an_unfinished_or_unknown_embedding_reads_as_no_row(pg_conn):
    with pg_conn.cursor() as cur:
        emb, ds = _embedding(cur, n_points=1), _dataset(cur, kind="reference")
        _member(cur, emb, ds, "reference", 0, 1)
        _point(cur, emb, ds, 0)
        for fn in ("scrna_embedding_arrays(%s)", "scrna_embedding_label_codes(%s, 'x')"):
            cur.execute(f"SELECT * FROM {fn}", (emb,))
            assert cur.fetchall() == [], fn
            cur.execute(f"SELECT * FROM {fn}", (-1,))
            assert cur.fetchall() == [], fn
    pg_conn.rollback()


def test_a_label_reads_from_the_point_then_from_its_linked_cell(pg_conn):
    with pg_conn.cursor() as cur:
        m = _joint_map(cur)
        cur.execute("SELECT * FROM scrna_embedding_label_codes(%s, 'shahan_cell_type')",
                    (m["embedding"],))
        levels, codes = cur.fetchone()
        assert levels == ["Cortex", "Xylem"]
        # point order: r-0 Xylem, r-1 Cortex, q-0 Cortex, q-1 no label
        assert codes == [1, 0, 0, -1]

        cur.execute("SELECT * FROM scrna_embedding_label_codes(%s, 'transgene_pos')",
                    (m["embedding"],))
        assert cur.fetchone() == (["True"], [-1, -1, 0, 0])

        cur.execute("SELECT * FROM scrna_embedding_label_codes(%s, 'genotype')",
                    (m["embedding"],))
        assert cur.fetchone() == (["pFACT"], [-1, -1, 0, 0])

        cur.execute("SELECT * FROM scrna_embedding_label_codes(%s, 'nobody_has_this')",
                    (m["embedding"],))
        assert cur.fetchone() == ([], [-1, -1, -1, -1])
    pg_conn.rollback()


def test_a_points_own_label_wins_over_its_cells(pg_conn):
    with pg_conn.cursor() as cur:
        m = _joint_map(cur)
        cur.execute("UPDATE scrna_cells SET facets = '{\"shahan_cell_type\": \"Phloem\"}' "
                    "WHERE id = %s", (m["cells"][0],))
        cur.execute("SELECT codes FROM scrna_embedding_label_codes(%s, 'shahan_cell_type')",
                    (m["embedding"],))
        # q-0 carries Cortex itself; its cell's Phloem is not used
        assert cur.fetchone()[0][2] == 0
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #


def test_row_level_security_is_on(pg_conn):
    with pg_conn.cursor() as cur:
        for table in TABLES:
            cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass",
                        (f"public.{table}",))
            assert cur.fetchone()[0] is True, table


@pytest.mark.parametrize("table", TABLES)
def test_each_role_holds_exactly_what_it_needs(pg_conn, table):
    expected = {
        "bloom_user": {"SELECT"},
        "bloom_agent": {"SELECT"},
        "bloom_writer": {"SELECT", "INSERT"},
        "bloom_admin": {"SELECT", "INSERT", "UPDATE", "DELETE"},
        "anon": set(),
        "authenticated": set(),
    }
    with pg_conn.cursor() as cur:
        for role, held in expected.items():
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur.execute("SELECT has_table_privilege(%s, %s, %s)",
                            (role, f"public.{table}", privilege))
                assert cur.fetchone()[0] is (privilege in held), f"{role} {privilege} {table}"


def test_the_writer_can_only_mark_an_embedding_finished(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'scrna_embeddings'")
        for (column,) in cur.fetchall():
            cur.execute("SELECT has_column_privilege('bloom_writer', "
                        "'public.scrna_embeddings', %s, 'UPDATE')", (column,))
            assert cur.fetchone()[0] is (column == "ingested_at"), column


@pytest.mark.parametrize("fn", ["public.scrna_embedding_arrays(bigint)",
                                "public.scrna_embedding_label_codes(bigint, text)"])
def test_the_read_functions_are_for_signed_in_readers(pg_conn, fn):
    with pg_conn.cursor() as cur:
        for role, allowed in {"bloom_user": True, "bloom_agent": True, "bloom_admin": True,
                              "anon": False, "authenticated": False}.items():
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, fn))
            assert cur.fetchone()[0] is allowed, f"{role} {fn}"


@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent"])
def test_a_signed_in_reader_reads_the_same_map_as_the_owner(pg_conn, role):
    """has_table_privilege checks grants, not policies. Reading through the
    policies is the only way to know a reader is not handed an empty map."""
    with pg_conn.cursor() as cur:
        m = _joint_map(cur)
        cur.execute("SELECT * FROM scrna_embedding_arrays(%s)", (m["embedding"],))
        owner_map = cur.fetchone()
        cur.execute("SELECT * FROM scrna_embedding_label_codes(%s, 'transgene_pos')",
                    (m["embedding"],))
        owner_label = cur.fetchone()
        with _as_role(cur, role):
            cur.execute("SELECT * FROM scrna_embedding_arrays(%s)", (m["embedding"],))
            assert cur.fetchone() == owner_map
            cur.execute("SELECT * FROM scrna_embedding_label_codes(%s, 'transgene_pos')",
                        (m["embedding"],))
            assert cur.fetchone() == owner_label
            cur.execute("SELECT count(*) FROM scrna_embedding_members WHERE embedding_id = %s",
                        (m["embedding"],))
            assert cur.fetchone()[0] == 2
    pg_conn.rollback()


def test_the_writer_loads_an_unfinished_embedding_and_nothing_else(pg_conn):
    with pg_conn.cursor() as cur:
        finished = _joint_map(cur)
        ds = _dataset(cur, kind="reference")
        with _as_role(cur, "bloom_writer"):
            emb = _embedding(cur, n_points=1)
            _member(cur, emb, ds, "reference", 0, 1)
            _point(cur, emb, ds, 0)
            _finish(cur, emb)
            # A finished embedding takes no more points ...
            with _refused(cur, message=r"row-level security",
                          error=psycopg.errors.InsufficientPrivilege):
                _point(cur, finished["embedding"], finished["reference"], 9)
            # ... and is not reopened.
            cur.execute("UPDATE scrna_embeddings SET ingested_at = NULL WHERE id = %s", (emb,))
            assert cur.rowcount == 0
            with _refused(cur, error=psycopg.errors.InsufficientPrivilege):
                cur.execute("UPDATE scrna_embeddings SET title = 't' WHERE id = %s", (emb,))
            with _refused(cur, error=psycopg.errors.InsufficientPrivilege):
                cur.execute("DELETE FROM scrna_embedding_points WHERE embedding_id = %s", (emb,))
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# The migration and its rollback
# --------------------------------------------------------------------------- #


def test_the_migration_runs_again_without_error(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_script("migrations", f"*_{NAME}.sql"))
    pg_conn.rollback()


def test_the_rollback_removes_everything_and_the_migration_restores_it(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_script("rollbacks", f"*_{NAME}_rollback.sql"))
        for table in TABLES:
            cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
            assert cur.fetchone()[0] is None, table
        cur.execute("SELECT count(*) FROM information_schema.columns WHERE "
                    "table_name = 'scrna_datasets' AND column_name = 'kind'")
        assert cur.fetchone()[0] == 0

        # A dataset that exists before the migration comes out of it full.
        tag = _tag()
        cur.execute("INSERT INTO species (common_name, genus, species) VALUES "
                    "(%s, %s, %s) RETURNING id", (f"r-{tag}", f"R-{tag}", f"r-{tag}"))
        cur.execute("INSERT INTO scrna_datasets (name, species_id) VALUES (%s, %s) "
                    "RETURNING id", (f"r-{tag}", cur.fetchone()[0]))
        before = cur.fetchone()[0]

        cur.execute(_script("migrations", f"*_{NAME}.sql"))
        cur.execute("SELECT kind FROM scrna_datasets WHERE id = %s", (before,))
        assert cur.fetchone()[0] == "full"
        for table in TABLES:
            cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
            assert cur.fetchone()[0] is not None, table
    pg_conn.rollback()


def test_the_rollback_refuses_while_an_embedding_or_reference_exists(pg_conn):
    with pg_conn.cursor() as cur:
        _dataset(cur, kind="reference")
        with _refused(cur, message=r"refusing to roll back",
                      error=psycopg.errors.RaiseException):
            cur.execute(_script("rollbacks", f"*_{NAME}_rollback.sql"))
    pg_conn.rollback()
