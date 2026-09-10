"""
Integration tests for the database side of `scripts/ingest_scrnaseq_counts.py`.

These drive the real write path against a real database. Storage is a stand-in
that records what it was asked to write, because what matters here is which
objects are written, under what names, and in what order relative to the rows
that point at them.

The load this script performs is deliberately not one transaction: it commits as
it goes so an interrupted run keeps its progress. That makes the ordering of
object and row the thing worth pinning — the object is written first, so a crash
leaves an object nothing points at rather than a row pointing at nothing.

LOCAL ONLY: every test rolls back. The script commits as it goes, so the tests
hand it a connection whose `commit` only counts the calls — the rows stay visible
to this connection, which is all `already_written` needs, and nothing survives
the rollback. One test asserts the commits really are attempted.
"""

from __future__ import annotations

import json
import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ingest_scrnaseq_counts.py"


@pytest.fixture(scope="module")
def counts():
    spec = importlib.util.spec_from_file_location("ingest_scrnaseq_counts", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ingest_scrnaseq_counts"] = module
    spec.loader.exec_module(module)
    return module


class CountingConn:
    """The real connection, with `commit` counted instead of performed.

    The script has to commit as it goes to be resumable, and these tests have to
    leave the database as they found it. Uncommitted rows are visible to the
    connection that wrote them, so everything under test behaves identically.
    """

    def __init__(self, conn):
        self._conn = conn
        self.commits = 0

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        self.commits += 1


class FakeStorage:
    """Records uploads. `fail_after` makes it stop like a dropped connection."""

    def __init__(self, fail_after: int | None = None):
        self.written: list[str] = []
        self.payloads: dict[str, bytes] = {}
        self.fail_after = fail_after

    def upload(self, path: str, file: bytes, file_options: dict) -> None:
        if self.fail_after is not None and len(self.written) >= self.fail_after:
            raise RuntimeError("storage went away")
        self.written.append(path)
        self.payloads[path] = file


def species(cur) -> int:
    tag = uuid.uuid4().hex[:10]
    cur.execute(
        "INSERT INTO species (common_name, genus, species) VALUES (%s, %s, %s) "
        "RETURNING id",
        (f"counts-{tag}", f"Countus-{tag}", f"testis-{tag}"),
    )
    return cur.fetchone()[0]


def dataset(cur, species_id: int, name: str, n_cells: int = 3) -> int:
    cur.execute(
        "INSERT INTO scrna_datasets (name, species_id, n_cells) "
        "VALUES (%s, %s, %s) RETURNING id",
        (name, species_id, n_cells),
    )
    return cur.fetchone()[0]


def cells(cur, dataset_id: int, barcodes: list[str]) -> None:
    """The cells `open_dataset` compares against, in the order it reads them."""
    for number, barcode in enumerate(barcodes):
        cur.execute(
            "INSERT INTO scrna_cells (dataset_id, cell_number, barcode) "
            "VALUES (%s, %s, %s)",
            (dataset_id, number, barcode),
        )


BARCODES = ["AAA-1", "CCC-1", "GGG-1"]

# Deliberately not in alphabetical order: sorting these by barcode gives a
# different sequence than sorting by cell_number.
SCRAMBLED = ["TTT-9", "AAA-1", "GGG-4"]


def genes(names: list[str], n_cells: int = 3) -> dict:
    """What `read_genes` returns, without needing a file."""
    import numpy as np
    return {
        "n_cells": n_cells,
        "names": names,
        "by_gene": np.array(
            # A zero on the diagonal: without it every entry is non-zero and a
            # dense object would compare equal to a sparse one.
            [[0.0 if c == g else float(g + 1) * (c + 1)
              for g in range(len(names))]
             for c in range(n_cells)],
            dtype="float32",
        ),
        "expectations": {},
    }


# --------------------------------------------------------------------------- #
# Finding the dataset the counts belong to
# --------------------------------------------------------------------------- #


def test_the_dataset_must_already_hold_cells_from_this_file(counts, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid, "match")
        cells(cur, did, BARCODES)
    assert counts.open_dataset(pg_conn, "match", sid, BARCODES) == did
    pg_conn.rollback()


def test_the_guard_reads_cells_in_cell_number_order_not_insertion_order(
    counts, pg_conn
):
    """Rows arrive in whatever order they were written. `cell_number` is the
    only thing that orders them, so a dataset whose rows were inserted out of
    order must still verify -- and would not if the query stopped sorting."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid, "unsorted")
        # inserted back-to-front, and the barcodes are not alphabetical either,
        # so physical order and barcode order both disagree with cell_number
        for number, barcode in reversed(list(enumerate(SCRAMBLED))):
            cur.execute(
                "INSERT INTO scrna_cells (dataset_id, cell_number, barcode) "
                "VALUES (%s, %s, %s)",
                (did, number, barcode),
            )
    assert counts.open_dataset(pg_conn, "unsorted", sid, SCRAMBLED) == did
    pg_conn.rollback()


def test_a_dataset_loaded_from_another_file_is_refused(counts, pg_conn):
    """The counts are paired to the cells by position, so a file holding
    different cells would pair every gene with the wrong cell."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid, "drift")
        cells(cur, did, BARCODES)
    with pytest.raises(counts.IngestError,
                       match="does not hold these cells in this order"):
        counts.open_dataset(pg_conn, "drift", sid, ["AAA-1", "TTT-9", "GGG-1"])
    pg_conn.rollback()


def test_the_same_cells_in_a_different_order_are_refused(counts, pg_conn):
    """Position is the only thing pairing a gene to a cell, so a re-export that
    reordered the same cells would silently mispaint every one of them."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid, "shuffled")
        cells(cur, did, BARCODES)
    with pytest.raises(counts.IngestError,
                       match="does not hold these cells in this order"):
        counts.open_dataset(pg_conn, "shuffled", sid, list(reversed(BARCODES)))
    pg_conn.rollback()


def test_a_cell_count_that_disagrees_is_refused(counts, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid, "shortcells", n_cells=2)
        cells(cur, did, BARCODES[:2])
    with pytest.raises(counts.IngestError, match="holds 2 cells and this file"):
        counts.open_dataset(pg_conn, "shortcells", sid, BARCODES)
    pg_conn.rollback()


def test_a_dataset_whose_recorded_count_disagrees_with_its_cells_is_refused(
    counts, pg_conn
):
    """`n_cells` and the rows can drift apart. The rows are what the counts are
    paired against, so a disagreement means one of the two is not what it says."""
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid, "miscounted", n_cells=99)
        cells(cur, did, BARCODES)
    with pytest.raises(counts.IngestError, match="records 99 cells but holds 3"):
        counts.open_dataset(pg_conn, "miscounted", sid, BARCODES)
    pg_conn.rollback()


def test_an_unloaded_dataset_says_to_load_the_cells_first(counts, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
    with pytest.raises(counts.IngestError, match="Load its cells first"):
        counts.open_dataset(pg_conn, "absent", sid, BARCODES)
    pg_conn.rollback()


def test_a_soft_deleted_dataset_is_not_written_to(counts, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid, "gone")
        cur.execute("UPDATE scrna_datasets SET deleted_at = now() WHERE id = %s",
                    (did,))
    with pytest.raises(counts.IngestError, match="Load its cells first"):
        counts.open_dataset(pg_conn, "gone", sid, BARCODES)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Registering the genes
# --------------------------------------------------------------------------- #


def test_genes_are_numbered_by_their_position_in_the_file(counts, pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid, "numbered")
        counts.register_genes(pg_conn, did, ["AT1G001", "AT1G002", "AT1G003"])
        cur.execute(
            "SELECT gene_name, gene_number FROM scrna_genes WHERE dataset_id = %s "
            "ORDER BY gene_number", (did,),
        )
        assert cur.fetchall() == [("AT1G001", 0), ("AT1G002", 1), ("AT1G003", 2)]
    pg_conn.rollback()


def test_registering_twice_adds_nothing_and_keeps_the_ids(counts, pg_conn):
    """A re-run has to land on the rows the first run made, or every object
    already written would be pointed at by nothing."""
    names = ["AT1G001", "AT1G002"]
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid, "again")
        first = counts.register_genes(pg_conn, did, names)
        second = counts.register_genes(pg_conn, did, names)
        assert first == second
        cur.execute("SELECT count(*) FROM scrna_genes WHERE dataset_id = %s", (did,))
        assert cur.fetchone()[0] == 2
    pg_conn.rollback()


def test_a_file_that_disagrees_with_the_registered_genes_is_refused(counts,
                                                                    pg_conn):
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid, "mismatch")
        counts.register_genes(pg_conn, did, ["AT1G001", "AT1G002"])
        with pytest.raises(counts.IngestError, match="do not describe the same"):
            counts.register_genes(pg_conn, did, ["AT1G001"])
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Writing, resuming, and the order of object and row
# --------------------------------------------------------------------------- #


def test_a_first_run_writes_every_gene_and_records_each_one(counts, pg_conn):
    names = ["AT1G001", "AT1G002", "AT1G003"]
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid, "first")
    ids = counts.register_genes(pg_conn, did, names)
    store, conn = FakeStorage(), CountingConn(pg_conn)

    written, skipped = counts.write_counts(
        conn, store, did, "first", genes(names), ids, set()
    )
    assert conn.commits >= 1, "an interrupted run must keep the genes it finished"
    assert (written, skipped) == (3, 0)
    assert store.written == [f"counts/first/{g}.json" for g in names]

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT g.gene_name, c.counts_object_path FROM scrna_counts c "
            "JOIN scrna_genes g ON g.id = c.gene_id WHERE c.dataset_id = %s "
            "ORDER BY g.gene_number", (did,),
        )
        assert cur.fetchall() == [(g, f"counts/first/{g}.json") for g in names]
    pg_conn.rollback()


def test_each_object_holds_that_gene_s_values_for_every_cell(counts, pg_conn):
    """One float per cell, in cell order — the array carries nothing else, so a
    wrong length or a wrong order cannot be noticed downstream."""
    import numpy as np
    names = ["AT1G001", "AT1G002"]
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid, "values")
    ids = counts.register_genes(pg_conn, did, names)
    store, table = FakeStorage(), genes(names, n_cells=3)

    counts.write_counts(CountingConn(pg_conn), store, did, "values", table,
                        ids, set())

    for column, gene in enumerate(names):
        # Sparse: cell index -> value, zeros omitted. The reader fills the rest
        # with zero, so an absent index and a stored 0.0 mean the same thing.
        got = json.loads(store.payloads[f"counts/values/{gene}.json"])
        expected = {
            str(cell): float(value)
            for cell, value in enumerate(table["by_gene"][:, column])
            if value != 0
        }
        assert got == expected
    pg_conn.rollback()


def test_a_second_run_skips_what_is_already_recorded(counts, pg_conn):
    names = ["AT1G001", "AT1G002", "AT1G003"]
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid, "resume")
    ids = counts.register_genes(pg_conn, did, names)
    counts.write_counts(CountingConn(pg_conn), FakeStorage(), did, "resume",
                        genes(names), ids, set())

    store = FakeStorage()
    written, skipped = counts.write_counts(
        CountingConn(pg_conn), store, did, "resume", genes(names), ids,
        counts.already_written(pg_conn, did),
    )
    assert (written, skipped) == (0, 3)
    assert store.written == []
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM scrna_counts WHERE dataset_id = %s",
                    (did,))
        assert cur.fetchone()[0] == 3, "the second run must not duplicate rows"
    pg_conn.rollback()


def test_an_interrupted_run_resumes_where_it_stopped(counts, pg_conn):
    """The point of committing as it goes: the genes already done stay done."""
    names = [f"AT1G{i:03d}" for i in range(6)]
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid, "broken")
    ids = counts.register_genes(pg_conn, did, names)

    with pytest.raises(RuntimeError, match="storage went away"):
        counts.write_counts(CountingConn(pg_conn), FakeStorage(fail_after=4),
                            did, "broken", genes(names), ids, set())

    done = counts.already_written(pg_conn, did)
    store = FakeStorage()
    written, skipped = counts.write_counts(
        CountingConn(pg_conn), store, did, "broken", genes(names), ids, done
    )
    assert written + skipped == 6
    assert written == 6 - len(done)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM scrna_counts WHERE dataset_id = %s",
                    (did,))
        assert cur.fetchone()[0] == 6
    pg_conn.rollback()


def test_no_gene_is_recorded_before_its_object_exists(counts, pg_conn,
                                                     monkeypatch):
    """Recording as it goes is what makes a re-run cheap, but a row must never
    get ahead of the object it points at: that gene would then be offered by the
    search and fail when anyone selected it. Recorded one at a time here, so a
    row written before its own upload is visible."""
    monkeypatch.setattr(counts, "RECORD_BATCH", 1)
    names = [f"AT1G{i:03d}" for i in range(4)]
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid, "ahead")
    ids = counts.register_genes(pg_conn, did, names)
    store = FakeStorage(fail_after=2)

    with pytest.raises(RuntimeError):
        counts.write_counts(CountingConn(pg_conn), store, did, "ahead",
                            genes(names), ids, set())

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT c.counts_object_path FROM scrna_counts c "
            "WHERE c.dataset_id = %s", (did,),
        )
        recorded = {row[0] for row in cur.fetchall()}
    assert recorded == set(store.written), (
        f"recorded {recorded - set(store.written)} with no object behind it"
    )
    pg_conn.rollback()


def test_nothing_is_recorded_for_an_object_that_was_never_written(counts,
                                                                  pg_conn):
    """A row pointing at a missing object is a gene that breaks in the browser.
    An object with no row is merely rewritten next run, so the object goes
    first — and a storage failure must leave no row for it."""
    names = [f"AT1G{i:03d}" for i in range(3)]
    with pg_conn.cursor() as cur:
        sid = species(cur)
        did = dataset(cur, sid, "orderly")
    ids = counts.register_genes(pg_conn, did, names)

    with pytest.raises(RuntimeError):
        counts.write_counts(CountingConn(pg_conn), FakeStorage(fail_after=0),
                            did, "orderly", genes(names), ids, set())

    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM scrna_counts WHERE dataset_id = %s",
                    (did,))
        assert cur.fetchone()[0] == 0
    pg_conn.rollback()
