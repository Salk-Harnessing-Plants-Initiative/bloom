"""
Integration tests for the counts loader, `scripts/ingest_scrnaseq_counts.py`, writing
through the site's API as a writer account: genes and counts rows through the REST
API, objects through the Storage API.

Each test first loads cells with the cells loader, so the dataset is finished the way
it is on staging. Writes commit, so each test works under a species of its own,
removed with everything under it afterwards.
"""

from __future__ import annotations

import json
import uuid

import numpy as np
import pytest

from tests.integration.scrna_loaders import load_script, losing, signer

BARCODES = ["AAA-1", "CCC-1", "GGG-1"]
# Not alphabetical: ordering by barcode differs from ordering by cell_number.
SCRAMBLED = ["TTT-9", "AAA-1", "GGG-4"]
NAMES = [f"AT1G{i:03d}" for i in range(5)]
OPTIONS = {"annotation": "ann", "sample_column": "sample", "umap_key": "X_umap",
           "source_column": None, "expression_units": "log1p normalised counts"}


@pytest.fixture(scope="module")
def cells_loader():
    return load_script("ingest_scrnaseq")


@pytest.fixture(scope="module")
def counts(cells_loader):
    return load_script("ingest_scrnaseq_counts")


@pytest.fixture
def sign_in(counts, scrna_accounts, scrna_api, tmp_path):
    return signer(counts.ingest_api, scrna_accounts, scrna_api, tmp_path / "marker.json")


@pytest.fixture
def db(pg_conninfo):
    import psycopg

    with psycopg.connect(pg_conninfo, autocommit=True) as conn:
        yield conn


@pytest.fixture
def name():
    """Objects are stored under the dataset name, so each test uses its own."""
    return f"expr-{uuid.uuid4().hex[:8]}"


def cells_table(barcodes) -> dict:
    n = len(barcodes)
    labels = ["A" if i % 2 else "B" for i in range(n)]
    return {"n_cells": n, "n_genes": len(NAMES), "x": [float(i) for i in range(n)],
            "y": [float(i) + 0.5 for i in range(n)], "labels": labels,
            "samples": ["Col-0"] * n, "levels": sorted(set(labels)),
            "barcodes": list(barcodes), "sources": {}}


def finished(cells_loader, sign_in, species_id, name, barcodes=BARCODES) -> int:
    dataset_id, _, _ = cells_loader.load(sign_in(), name, species_id,
                                         cells_table(barcodes), "sha-1", OPTIONS,
                                         create=True)
    return dataset_id


def genes(names, barcodes=BARCODES) -> dict:
    """What read_genes returns, without a file, with one zero per gene."""
    n = len(barcodes)
    return {"n_cells": n, "barcodes": list(barcodes), "names": list(names),
            "by_gene": np.array([[0.0 if c == g else float(g + 1) * (c + 1)
                                  for g in range(len(names))] for c in range(n)],
                                dtype="float32"),
            "expectations": {}}


def load(counts, writer, species_id, name, names=NAMES, barcodes=BARCODES):
    return counts.load(writer, name, species_id, genes(names, barcodes))


def stored(db, dataset_id) -> tuple:
    """Genes by number, and each recorded gene's object file name."""
    return (
        db.execute("SELECT gene_number, gene_name FROM scrna_genes WHERE dataset_id = %s "
                   "ORDER BY gene_number", (dataset_id,)).fetchall(),
        db.execute("SELECT g.gene_name, split_part(c.counts_object_path, '/', 3) "
                   "FROM scrna_counts c JOIN scrna_genes g ON g.id = c.gene_id "
                   "WHERE c.dataset_id = %s ORDER BY g.gene_number", (dataset_id,)).fetchall(),
    )


def download(writer, path) -> dict:
    return json.loads(writer.session.client.storage.from_("scrna").download(path))


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def test_a_first_load_writes_genes_rows_and_objects_as_the_writer(
    counts, cells_loader, sign_in, db, scrna_species, name
):
    dataset_id = finished(cells_loader, sign_in, scrna_species, name)
    writer = sign_in()
    assert load(counts, writer, scrna_species, name) == (dataset_id, 5, 0)

    genes_rows, counts_rows = stored(db, dataset_id)
    assert genes_rows == list(enumerate(NAMES)), "genes are numbered by file position"
    assert counts_rows == [(g, f"{g}.json") for g in NAMES]
    table = genes(NAMES)
    for column, gene in enumerate(NAMES):
        (path,) = db.execute("SELECT c.counts_object_path FROM scrna_counts c JOIN "
                             "scrna_genes g ON g.id = c.gene_id WHERE g.gene_name = %s "
                             "AND c.dataset_id = %s", (gene, dataset_id)).fetchone()
        assert path == f"counts/{name}_{dataset_id}_/{gene}.json"
        assert download(writer, path) == counts.gene_counts(table["by_gene"], column)


def test_a_stopped_load_resumes_to_the_same_rows(
    counts, cells_loader, sign_in, db, scrna_species, name, monkeypatch
):
    monkeypatch.setattr(counts, "RECORD_BATCH", 2)
    whole = finished(cells_loader, sign_in, scrna_species, f"{name}-whole")
    load(counts, sign_in(), scrna_species, f"{name}-whole")

    part = finished(cells_loader, sign_in, scrna_species, name)
    with pytest.raises(counts.IngestError, match="re-run the same command"):
        load(counts, sign_in(create_client=losing("scrna_counts", 2)), scrna_species, name)
    assert len(stored(db, part)[1]) == 4, "the lost batch had committed"

    assert load(counts, sign_in(), scrna_species, name) == (part, 1, 4)
    assert stored(db, part) == stored(db, whole)


def test_a_stop_during_an_upload_resumes_without_a_second_row(
    counts, cells_loader, sign_in, db, scrna_species, name
):
    dataset_id = finished(cells_loader, sign_in, scrna_species, name)
    with pytest.raises(counts.IngestError, match="outcome is unknown"):
        load(counts, sign_in(create_client=losing(upload=True, nth=3)), scrna_species, name)
    assert stored(db, dataset_id)[1] == [], "no row for an upload whose reply was lost"

    assert load(counts, sign_in(), scrna_species, name) == (dataset_id, 5, 0)
    assert [g for g, _ in stored(db, dataset_id)[1]] == NAMES


def test_a_complete_load_is_already_loaded(counts, cells_loader, sign_in, db,
                                           scrna_species, name):
    dataset_id = finished(cells_loader, sign_in, scrna_species, name)
    load(counts, sign_in(), scrna_species, name)
    before = stored(db, dataset_id)
    assert load(counts, sign_in(), scrna_species, name) == (dataset_id, 0, 5)
    assert stored(db, dataset_id) == before


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


def test_an_unfinished_dataset_is_refused(counts, cells_loader, sign_in, db,
                                          scrna_species, name):
    with pytest.raises(counts.IngestError, match="outcome is unknown"):
        cells_loader.load(sign_in(create_client=losing("scrna_cells")), name,
                          scrna_species, cells_table(BARCODES), "sha-1", OPTIONS,
                          create=True)
    with pytest.raises(counts.IngestError, match="not finished"):
        load(counts, sign_in(), scrna_species, name)
    assert db.execute("SELECT count(*) FROM scrna_genes g JOIN scrna_datasets d ON "
                      "d.id = g.dataset_id WHERE d.species_id = %s",
                      (scrna_species,)).fetchone()[0] == 0


@pytest.mark.parametrize("file", [["AAA-1", "TTT-9", "GGG-1"], list(reversed(BARCODES))])
def test_a_file_whose_cells_differ_is_refused(counts, cells_loader, sign_in, db,
                                              scrna_species, name, file):
    dataset_id = finished(cells_loader, sign_in, scrna_species, name)
    with pytest.raises(counts.IngestError, match="does not hold these cells in this order"):
        load(counts, sign_in(), scrna_species, name, barcodes=file)
    assert stored(db, dataset_id) == ([], [])


def test_cells_are_read_in_cell_number_order(counts, sign_in, db, scrna_species, name):
    """Rows come back in whatever order they were written; cell_number is the only
    thing that orders them."""
    (dataset_id,) = db.execute(
        "INSERT INTO scrna_datasets (name, species_id, n_cells, source_checksum, "
        "ingested_at) VALUES (%s, %s, 3, 'sha-1', now()) RETURNING id",
        (name, scrna_species)).fetchone()
    for number, barcode in reversed(list(enumerate(SCRAMBLED))):
        db.execute("INSERT INTO scrna_cells (dataset_id, cell_number, barcode) "
                   "VALUES (%s, %s, %s)", (dataset_id, number, barcode))
    assert load(counts, sign_in(), scrna_species, name, barcodes=SCRAMBLED)[0] == dataset_id


def test_a_gene_registered_twice_is_refused_by_name(counts, cells_loader, sign_in, db,
                                                    scrna_species, name):
    dataset_id = finished(cells_loader, sign_in, scrna_species, name)
    for _ in range(2):
        db.execute("INSERT INTO scrna_genes (dataset_id, gene_number, gene_name) "
                   "VALUES (%s, 1, %s)", (dataset_id, NAMES[1]))
    with pytest.raises(counts.IngestError, match=f"{NAMES[1]}.*more than once"):
        load(counts, sign_in(), scrna_species, name)


def test_a_file_that_disagrees_with_the_registered_genes_is_refused(
    counts, cells_loader, sign_in, scrna_species, name
):
    finished(cells_loader, sign_in, scrna_species, name)
    load(counts, sign_in(), scrna_species, name, names=NAMES[:2])
    with pytest.raises(counts.IngestError, match="do not describe the same"):
        load(counts, sign_in(), scrna_species, name, names=NAMES[:1])
