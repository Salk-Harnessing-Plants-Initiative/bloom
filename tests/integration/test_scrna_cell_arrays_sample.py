"""
Integration tests for the sample column on `scrna_cell_arrays`.

The explorer pairs everything it fetches with cells by position — there are no
cell identifiers in what comes back — so the sample has to arrive from the same
query as the position, in the same order. A second query returning samples in
its own order could label every cell wrongly and look completely normal.

LOCAL ONLY: every test rolls back.
"""

from __future__ import annotations

import uuid

import pytest


def species(cur) -> int:
    tag = uuid.uuid4().hex[:10]
    cur.execute(
        "INSERT INTO species (common_name, genus, species) VALUES (%s, %s, %s) "
        "RETURNING id",
        (f"arrays-{tag}", f"Arrayus-{tag}", f"testis-{tag}"),
    )
    return cur.fetchone()[0]


def dataset(cur, species_id: int) -> int:
    cur.execute(
        "INSERT INTO scrna_datasets (name, species_id) VALUES (%s, %s) "
        "RETURNING id", (f"arrays-{uuid.uuid4().hex[:8]}", species_id),
    )
    return cur.fetchone()[0]


def seed(cur, dataset_id: int, cells: list[tuple[str, str]]) -> None:
    """cells: (cluster_id, replicate) in cell order."""
    for ordinal, cluster in enumerate(sorted({c for c, _ in cells})):
        cur.execute(
            "INSERT INTO scrna_clusters (dataset_id, cluster_id, ordinal, name, "
            "color) VALUES (%s, %s, %s, %s, %s)",
            (dataset_id, cluster, ordinal, cluster, "#4E79A7"),
        )
    cur.executemany(
        "INSERT INTO scrna_cells (dataset_id, cell_number, barcode, x, y, "
        "cluster_id, replicate) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        [
            (dataset_id, i, f"BC{i}", float(i), float(i) + 0.5, cluster, sample)
            for i, (cluster, sample) in enumerate(cells)
        ],
    )


def test_each_cell_comes_back_with_its_own_sample(pg_conn):
    """The pairing is the point: cell 1 is the pFACT one, and it has to be the
    second row back, with pFACT on it."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid)
        seed(cur, did, [("A", "Col-0"), ("B", "pFACT"), ("A", "pHORST")])

        cur.execute("SELECT x, y, cluster_ordinal, replicate "
                    "FROM scrna_cell_arrays(%s)", (did,))
        assert cur.fetchall() == [
            (0.0, 0.5, 0, "Col-0"),
            (1.0, 1.5, 1, "pFACT"),
            (2.0, 2.5, 0, "pHORST"),
        ]
    pg_conn.rollback()


def test_the_rows_stay_in_cell_order(pg_conn):
    """Insertion order is not row order, and the explorer indexes by position."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid)
        cur.execute(
            "INSERT INTO scrna_clusters (dataset_id, cluster_id, ordinal, name, "
            "color) VALUES (%s, 'A', 0, 'A', '#4E79A7')", (did,),
        )
        for number, sample in ((2, "third"), (0, "first"), (1, "second")):
            cur.execute(
                "INSERT INTO scrna_cells (dataset_id, cell_number, barcode, x, "
                "y, cluster_id, replicate) VALUES (%s, %s, %s, %s, %s, 'A', %s)",
                (did, number, f"BC{number}", float(number), 0.0, sample),
            )
        cur.execute("SELECT replicate FROM scrna_cell_arrays(%s)", (did,))
        assert [r[0] for r in cur.fetchall()] == ["first", "second", "third"]
    pg_conn.rollback()


def test_a_dataset_that_records_no_sample_returns_nulls(pg_conn):
    """Older datasets have no sample on their cells. They come back as null, and
    the view shows no toggles rather than an empty control."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid)
        cur.execute(
            "INSERT INTO scrna_clusters (dataset_id, cluster_id, ordinal, name, "
            "color) VALUES (%s, 'A', 0, 'A', '#4E79A7')", (did,),
        )
        cur.execute(
            "INSERT INTO scrna_cells (dataset_id, cell_number, barcode, x, y, "
            "cluster_id) VALUES (%s, 0, 'BC0', 1.0, 2.0, 'A')", (did,),
        )
        cur.execute("SELECT replicate FROM scrna_cell_arrays(%s)", (did,))
        assert cur.fetchall() == [(None,)]
    pg_conn.rollback()


def test_an_orphan_cell_still_reports_its_sample(pg_conn):
    """The sentinel ordinal and the sample are independent of each other."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid)
        cur.execute(
            "INSERT INTO scrna_clusters (dataset_id, cluster_id, ordinal, name, "
            "color) VALUES (%s, 'A', 0, 'A', '#4E79A7')", (did,),
        )
        cur.execute(
            "INSERT INTO scrna_cells (dataset_id, cell_number, barcode, x, y, "
            "cluster_id, replicate) VALUES (%s, 0, 'BC0', 1.0, 2.0, 'A', 'Col-0')",
            (did,),
        )
        cur.execute("SELECT cluster_ordinal, replicate "
                    "FROM scrna_cell_arrays(%s)", (did,))
        assert cur.fetchall() == [(0, "Col-0")]
    pg_conn.rollback()


def test_one_dataset_s_cells_are_not_returned_for_another(pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        mine, theirs = dataset(cur, sid), dataset(cur, sid)
        seed(cur, mine, [("A", "Col-0")])
        seed(cur, theirs, [("A", "pFACT")])
        cur.execute("SELECT replicate FROM scrna_cell_arrays(%s)", (mine,))
        assert cur.fetchall() == [("Col-0",)]
    pg_conn.rollback()
