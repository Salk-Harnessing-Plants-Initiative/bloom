"""
Integration tests for the cells loader, `scripts/ingest_scrnaseq.py`, writing through
the site's API as a writer account, the way it runs against staging.

No `.h5ad` is needed: the tests hand `load()` the dict `read_cells` returns. Writes
through the API commit, so each test works under a species of its own, removed with
everything under it afterwards.

The most important check is the order the cells come back in. Per-gene expression is
served as a bare array indexed against `scrna_cell_arrays`, which orders by
`cell_number`; if that order or its base changes, every gene paints onto the wrong
cell and nothing downstream can tell.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ingest_scrnaseq.py"

OPTIONS = {"annotation": "ann", "sample_column": "sample", "umap_key": "X_umap",
           "source_column": None, "expression_units": "log1p normalised counts"}


@pytest.fixture(scope="module")
def ingest():
    spec = importlib.util.spec_from_file_location("ingest_scrnaseq", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ingest_scrnaseq"] = module
    spec.loader.exec_module(module)
    return module


def cells(labels, samples=None) -> dict:
    """Shaped like read_cells returns, with coordinates that show a swap."""
    n = len(labels)
    return {"n_cells": n, "n_genes": 100,
            "x": [float(i) for i in range(n)], "y": [float(i) + 0.5 for i in range(n)],
            "labels": list(labels), "samples": samples or ["Col-0"] * n,
            "levels": sorted(set(labels)), "barcodes": [f"BC{i}" for i in range(n)],
            "sources": {}}


class LoseReply(httpx.BaseTransport):
    """Sends every request, and loses the reply to the chosen one after it committed."""

    def __init__(self, lose):
        self.inner, self.lose = httpx.HTTPTransport(), lose

    def handle_request(self, request):
        response = self.inner.handle_request(request)
        if self.lose(request):
            response.read()
            response.close()
            raise httpx.ReadTimeout("the reply was lost", request=request)
        return response


def losing(table: str, nth: int = 1):
    """A client factory whose nth insert into `table` commits and then times out."""
    seen = []

    def lose(request):
        if request.method == "POST" and request.url.path.endswith(f"/rest/v1/{table}"):
            seen.append(1)
            return len(seen) == nth
        return False

    def create_client(url, key):
        from supabase.lib.client_options import SyncClientOptions

        from supabase import create_client
        return create_client(url, key, options=SyncClientOptions(
            httpx_client=httpx.Client(transport=LoseReply(lose), timeout=120)))
    return create_client


@pytest.fixture
def sign_in(ingest, scrna_accounts, scrna_api, tmp_path):
    """A writer signed in through the API, as the writer or the admin account."""
    api = ingest.ingest_api

    def make(role="writer", create_client=None):
        account = scrna_accounts[role]
        extra = {"create_client": create_client} if create_client else {}
        session = api.sign_in(*scrna_api, account["email"], account["password"], **extra)
        return api.Writer(session, api.Marker(tmp_path / "marker.json", wait_s=0))
    return make


@pytest.fixture
def db(pg_conninfo):
    """A committed connection, to see what the API wrote and to set rows up."""
    import psycopg

    with psycopg.connect(pg_conninfo, autocommit=True) as conn:
        yield conn


def load(ingest, writer, species_id, labels=("A", "B"), *, name="cells",
         checksum="sha-1", options=None, create=True, table=None):
    return ingest.load(writer, name, species_id, table or cells(labels), checksum,
                       options or OPTIONS, create=create)


def dataset_id_of(db, species_id, name="cells") -> int:
    (found,) = db.execute("SELECT id FROM scrna_datasets WHERE species_id = %s "
                          "AND btrim(name) = %s AND deleted_at IS NULL",
                          (species_id, name)).fetchall()
    return found[0]


def unfinished(ingest, sign_in, db, species_id, labels=("A", "B"), *,
               table="scrna_cells", name="cells") -> int:
    """Start a load and lose the reply to one write, leaving the dataset unfinished."""
    with pytest.raises(ingest.IngestError, match="outcome is unknown"):
        load(ingest, sign_in(create_client=losing(table)), species_id, labels, name=name)
    dataset_id = dataset_id_of(db, species_id, name)
    assert db.execute("SELECT ingested_at FROM scrna_datasets WHERE id = %s",
                      (dataset_id,)).fetchone()[0] is None
    return dataset_id


def snapshot(db, dataset_id):
    """What a load left, without ids, names or timestamps."""
    return (
        db.execute("SELECT n_cells, n_genes, expression_units, metadata, "
                   "source_checksum, ingested_at IS NOT NULL FROM scrna_datasets "
                   "WHERE id = %s", (dataset_id,)).fetchone(),
        db.execute("SELECT cluster_id, ordinal, name, color, source FROM scrna_clusters "
                   "WHERE dataset_id = %s ORDER BY ordinal", (dataset_id,)).fetchall(),
        db.execute("SELECT cell_number, barcode, x, y, cluster_id, replicate "
                   "FROM scrna_cells WHERE dataset_id = %s ORDER BY cell_number, id",
                   (dataset_id,)).fetchall(),
    )


# --------------------------------------------------------------------------- #
# A first load
# --------------------------------------------------------------------------- #


def test_a_first_load_registers_the_dataset_and_finishes_it(
    ingest, sign_in, db, scrna_species, scrna_accounts
):
    labels = ("Xylem", "Cortex", "Phellem", "Cortex")
    dataset_id, stored, outcome = load(ingest, sign_in(), scrna_species, labels)
    assert (stored, outcome) == (4, "registered")

    row = db.execute("SELECT source_checksum, metadata, n_cells, n_genes, "
                     "expression_units, ingested_at, created_by FROM scrna_datasets "
                     "WHERE id = %s", (dataset_id,)).fetchone()
    checksum, metadata, n_cells, n_genes, units, ingested_at, created_by = row
    assert checksum == "sha-1"
    assert metadata == {"load_options": OPTIONS, "cell_type_column": "ann"}
    assert (n_cells, n_genes, units) == (4, 100, "log1p normalised counts")
    assert ingested_at is not None
    assert str(created_by) == scrna_accounts["writer"]["id"]

    # Ordinals from zero in sorted label order, and a distinct colour each.
    assert db.execute("SELECT cluster_id, ordinal, name, color FROM scrna_clusters "
                      "WHERE dataset_id = %s ORDER BY ordinal", (dataset_id,)).fetchall() == [
        ("Cortex", 0, "Cortex", ingest.PALETTE[0]),
        ("Phellem", 1, "Phellem", ingest.PALETTE[1]),
        ("Xylem", 2, "Xylem", ingest.PALETTE[2]),
    ]
    assert snapshot(db, dataset_id)[2] == [
        (0, "BC0", 0.0, 0.5, "Xylem", "Col-0"),
        (1, "BC1", 1.0, 1.5, "Cortex", "Col-0"),
        (2, "BC2", 2.0, 2.5, "Phellem", "Col-0"),
        (3, "BC3", 3.0, 3.5, "Cortex", "Col-0"),
    ]


def test_the_view_returns_the_cells_in_file_order(ingest, sign_in, db, scrna_species):
    dataset_id, _, _ = load(ingest, sign_in(), scrna_species,
                            ("Xylem", "Cortex", "Phellem", "Cortex"))
    # Xylem=2, Cortex=0, Phellem=1 by sorted order.
    assert db.execute("SELECT x, y, cluster_ordinal FROM scrna_cell_arrays(%s)",
                      (dataset_id,)).fetchall() == [
        (0.0, 0.5, 2), (1.0, 1.5, 0), (2.0, 2.5, 1), (3.0, 3.5, 0)]


def test_no_cell_is_left_without_a_cell_type(ingest, sign_in, db, scrna_species):
    dataset_id, _, _ = load(ingest, sign_in(), scrna_species, ("A", "B", "A", "C"))
    assert db.execute("SELECT count(*) FROM scrna_cell_arrays(%s) "
                      "WHERE cluster_ordinal = 255", (dataset_id,)).fetchone()[0] == 0


def test_an_admin_account_can_load(ingest, sign_in, scrna_species):
    _, stored, outcome = load(ingest, sign_in("admin"), scrna_species)
    assert (stored, outcome) == (2, "registered")


# --------------------------------------------------------------------------- #
# Resuming
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("create", [True, False])
def test_a_stopped_load_resumes_to_the_same_rows(
    ingest, sign_in, db, scrna_species, monkeypatch, create
):
    monkeypatch.setattr(ingest, "CELL_BATCH", 2)
    labels = ("A", "B", "A", "B", "C")
    whole, _, _ = load(ingest, sign_in(), scrna_species, labels, name="whole")

    with pytest.raises(ingest.IngestError, match="re-run the same command"):
        load(ingest, sign_in(create_client=losing("scrna_cells", 2)), scrna_species,
             labels, name="part")
    part = dataset_id_of(db, scrna_species, "part")
    assert db.execute("SELECT count(*) FROM scrna_cells WHERE dataset_id = %s",
                      (part,)).fetchone()[0] == 4, "the lost batch had committed"

    again, stored, outcome = load(ingest, sign_in(), scrna_species, labels,
                                  name="part", create=create)
    assert (again, stored, outcome) == (part, 5, "resumed")
    assert snapshot(db, part) == snapshot(db, whole)


def test_the_same_file_after_finishing_is_already_loaded(ingest, sign_in, db,
                                                         scrna_species):
    first, _, _ = load(ingest, sign_in(), scrna_species)
    before = snapshot(db, first)
    again, stored, outcome = load(ingest, sign_in(), scrna_species, create=False)
    assert (again, stored, outcome) == (first, 2, "already loaded")
    assert snapshot(db, first) == before


def test_a_padded_name_finds_the_dataset_it_meant(ingest, sign_in, db, scrna_species):
    first, _, _ = load(ingest, sign_in(), scrna_species, name="padded")
    again, _, _ = load(ingest, sign_in(), scrna_species, name="  padded  ", create=False)
    assert again == first
    assert db.execute("SELECT count(*) FROM scrna_datasets WHERE species_id = %s",
                      (scrna_species,)).fetchone()[0] == 1


def test_a_name_stored_with_padding_is_still_found(ingest, sign_in, db, scrna_species):
    from psycopg.types.json import Jsonb

    (existing,) = db.execute(
        "INSERT INTO scrna_datasets (name, species_id, source_checksum, metadata) "
        "VALUES ('legacy padded ', %s, 'sha-1', %s) RETURNING id",
        (scrna_species, Jsonb({"load_options": OPTIONS})),
    ).fetchone()
    again, _, outcome = load(ingest, sign_in(), scrna_species, name="legacy padded",
                             create=False)
    assert (again, outcome) == (existing, "resumed")


def test_the_cell_type_column_is_recorded_without_touching_other_metadata(
    ingest, sign_in, db, scrna_species
):
    """`annotation` is the genome annotation, written by the uploader; the cell-type
    column goes in `metadata`, beside whatever else is there."""
    dataset_id = unfinished(ingest, sign_in, db, scrna_species)
    db.execute("UPDATE scrna_datasets SET annotation = 'Araport11', "
               "metadata = metadata || '{\"strain\": \"Col-0\"}' WHERE id = %s",
               (dataset_id,))
    load(ingest, sign_in(), scrna_species, create=False)
    annotation, metadata = db.execute("SELECT annotation, metadata FROM scrna_datasets "
                                      "WHERE id = %s", (dataset_id,)).fetchone()
    assert annotation == "Araport11"
    assert metadata == {"load_options": OPTIONS, "strain": "Col-0",
                        "cell_type_column": "ann"}


# --------------------------------------------------------------------------- #
# Refusals, and that they leave the dataset as it was
# --------------------------------------------------------------------------- #


def test_a_resume_from_another_file_is_refused(ingest, sign_in, db, scrna_species):
    dataset_id = unfinished(ingest, sign_in, db, scrna_species)
    before = snapshot(db, dataset_id)
    with pytest.raises(ingest.IngestError, match="sha-1.*sha-2"):
        load(ingest, sign_in(), scrna_species, checksum="sha-2")
    assert snapshot(db, dataset_id) == before


def test_a_resume_with_other_options_is_refused(ingest, sign_in, db, scrna_species):
    dataset_id = unfinished(ingest, sign_in, db, scrna_species)
    before = snapshot(db, dataset_id)
    with pytest.raises(ingest.IngestError, match="umap_key was 'X_umap', now 'X_tsne'"):
        load(ingest, sign_in(), scrna_species, options={**OPTIONS, "umap_key": "X_tsne"})
    assert snapshot(db, dataset_id) == before


def test_a_stored_catalogue_that_differs_from_the_file_is_refused(
    ingest, sign_in, db, scrna_species
):
    dataset_id = unfinished(ingest, sign_in, db, scrna_species, table="scrna_clusters")
    with pytest.raises(ingest.IngestError, match="cell types"):
        load(ingest, sign_in(), scrna_species, ("A", "C"))
    assert [c for (c,) in db.execute("SELECT cluster_id FROM scrna_clusters WHERE "
                                     "dataset_id = %s ORDER BY ordinal", (dataset_id,))
            ] == ["A", "B"]


@pytest.mark.parametrize("create", [True, False])
def test_a_dataset_finished_from_another_file_is_refused(
    ingest, sign_in, db, scrna_species, create
):
    first, _, _ = load(ingest, sign_in(), scrna_species)
    before = snapshot(db, first)
    with pytest.raises(ingest.IngestError, match="admin"):
        load(ingest, sign_in(), scrna_species, ("C", "D"), checksum="sha-2",
             create=create)
    assert snapshot(db, first) == before


def test_a_dataset_with_no_checksum_is_refused(ingest, sign_in, db, scrna_species):
    db.execute("INSERT INTO scrna_datasets (name, species_id) VALUES ('cells', %s)",
               (scrna_species,))
    with pytest.raises(ingest.IngestError, match="admin"):
        load(ingest, sign_in(), scrna_species)


def add_later_row(db, table: str, dataset_id: int) -> None:
    if table == "scrna_cluster_stats":
        db.execute("INSERT INTO scrna_cluster_stats (dataset_id, cluster_id, cell_count, "
                   "pct) VALUES (%s, 'A', 1, 50.0)", (dataset_id,))
    elif table == "scrna_cluster_neighbors":
        db.execute("INSERT INTO scrna_cluster_neighbors (dataset_id, cluster_id, "
                   "neighbor_cluster_id, rank, similarity) VALUES (%s, 'A', 'B', 1, 0.5)",
                   (dataset_id,))
    elif table == "scrna_counts":
        (gene_id,) = db.execute("INSERT INTO scrna_genes (dataset_id, gene_number, "
                                "gene_name) VALUES (%s, 0, 'AT1G01010') RETURNING id",
                                (dataset_id,)).fetchone()
        db.execute("INSERT INTO scrna_counts (dataset_id, gene_id, counts_object_path) "
                   "VALUES (%s, %s, 'counts/x/AT1G01010.json')", (dataset_id, gene_id))
    else:
        db.execute("INSERT INTO scrna_de (dataset_id, cluster_id, file_path) "
                   "VALUES (%s, 'A', 'de/A.tsv')", (dataset_id,))


@pytest.mark.parametrize("table,named", [
    ("scrna_cluster_stats", "per-cluster statistics"),
    ("scrna_cluster_neighbors", "neighbour rows"),
    ("scrna_counts", "per-gene expression"),
    ("scrna_de", "differential expression"),
])
def test_later_rows_on_an_unfinished_dataset_are_refused(
    ingest, sign_in, db, scrna_species, table, named
):
    dataset_id = unfinished(ingest, sign_in, db, scrna_species)
    add_later_row(db, table, dataset_id)
    with pytest.raises(ingest.IngestError, match=named):
        load(ingest, sign_in(), scrna_species)
    assert db.execute(f"SELECT count(*) FROM {table} WHERE dataset_id = %s",
                      (dataset_id,)).fetchone()[0] == 1, "the rows must survive"


def test_repeated_cell_numbers_stop_it_before_finishing(ingest, sign_in, db,
                                                        scrna_species):
    dataset_id = unfinished(ingest, sign_in, db, scrna_species)
    db.execute("INSERT INTO scrna_cells (dataset_id, cell_number, barcode, x, y, "
               "cluster_id, replicate) VALUES (%s, 0, 'BC0', 0, 0.5, 'A', 'Col-0')",
               (dataset_id,))
    with pytest.raises(ingest.IngestError, match="repeated"):
        load(ingest, sign_in(), scrna_species)
    assert db.execute("SELECT ingested_at FROM scrna_datasets WHERE id = %s",
                      (dataset_id,)).fetchone()[0] is None


def test_an_unknown_name_without_create_is_refused(ingest, sign_in, db, scrna_species):
    with pytest.raises(ingest.IngestError, match="--create"):
        load(ingest, sign_in(), scrna_species, name="brand new", create=False)
    assert db.execute("SELECT count(*) FROM scrna_datasets WHERE species_id = %s",
                      (scrna_species,)).fetchone()[0] == 0


def test_a_name_the_counts_path_cannot_hold_is_refused(ingest, sign_in, db,
                                                       scrna_species):
    with pytest.raises(ingest.IngestError, match="storage path"):
        load(ingest, sign_in(), scrna_species, name="a/b")
    assert db.execute("SELECT count(*) FROM scrna_datasets WHERE species_id = %s",
                      (scrna_species,)).fetchone()[0] == 0


def test_a_blank_name_is_refused(ingest, sign_in, scrna_species):
    with pytest.raises(ingest.IngestError, match="blank"):
        load(ingest, sign_in(), scrna_species, name="   ")


def test_two_datasets_with_one_name_are_refused(ingest, sign_in, db, scrna_species):
    for _ in range(2):
        db.execute("INSERT INTO scrna_datasets (name, species_id) VALUES ('dup', %s)",
                   (scrna_species,))
    with pytest.raises(ingest.IngestError, match="cannot tell which"):
        load(ingest, sign_in(), scrna_species, name="dup")


def test_a_soft_deleted_dataset_is_not_resurrected(ingest, sign_in, db, scrna_species):
    (buried,) = db.execute("INSERT INTO scrna_datasets (name, species_id, deleted_at) "
                           "VALUES ('gone', %s, now()) RETURNING id",
                           (scrna_species,)).fetchone()
    fresh, _, outcome = load(ingest, sign_in(), scrna_species, name="gone")
    assert outcome == "registered" and fresh != buried
    assert db.execute("SELECT n_cells FROM scrna_datasets WHERE id = %s",
                      (buried,)).fetchone()[0] is None
