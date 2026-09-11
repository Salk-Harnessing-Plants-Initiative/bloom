"""
Unit tests for the write path of `scripts/ingest_scrnaseq_counts.py`, against an
in-memory client: finding the finished dataset, registering genes, writing each
object before its row, resuming, and the refusals. The same flows run through the
real API in tests/integration/test_scrna_ingest_counts.py.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import numpy as np
import pytest

from tests.unit.fake_supabase import FakeClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ingest_scrnaseq_counts.py"

BARCODES = ["AAA-1", "CCC-1", "GGG-1"]
# Not alphabetical: ordering by barcode differs from ordering by cell_number.
SCRAMBLED = ["TTT-9", "AAA-1", "GGG-4"]


@pytest.fixture(scope="module")
def counts():
    spec = importlib.util.spec_from_file_location("ingest_scrnaseq_counts", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ingest_scrnaseq_counts"] = module
    spec.loader.exec_module(module)
    return module


def genes(names, barcodes=BARCODES) -> dict:
    """What read_genes returns, without a file. One zero per gene, so a dense
    object would not compare equal to a sparse one."""
    n = len(barcodes)
    return {"n_cells": n, "barcodes": list(barcodes), "names": list(names),
            "by_gene": np.array([[0.0 if c == g else float(g + 1) * (c + 1)
                                  for g in range(len(names))] for c in range(n)],
                                dtype="float32"),
            "expectations": {}}


def client_with(barcodes=BARCODES, *, ingested=True, n_cells=None, gene_rows=(),
                cell_ids=None) -> FakeClient:
    ds = {"id": 7, "name": "MYB41", "species_id": 1, "deleted_at": None,
          "source_checksum": "sha", "metadata": {},
          "ingested_at": "2026-09-11T00:00:00+00:00" if ingested else None,
          "n_cells": len(barcodes) if n_cells is None else n_cells}
    ids = cell_ids or [100 + i for i in range(len(barcodes))]
    cells = [{"id": ids[i], "dataset_id": 7, "cell_number": i, "barcode": b}
             for i, b in enumerate(barcodes)]
    return FakeClient({"scrna_datasets": [ds], "scrna_cells": cells,
                       "scrna_genes": [{"dataset_id": 7, **r} for r in gene_rows]})


def writer(counts, client, tmp_path, renew_to=None):
    api = counts.ingest_api
    session = api.Session(lambda: (renew_to or client, "bloom_writer", "u1"),
                          client, "bloom_writer", "u1")
    return api.Writer(session, api.Marker(tmp_path / "m.json", wait_s=0))


def load(counts, client, tmp_path, names=("G0", "G1", "G2"), name="MYB41",
         barcodes=BARCODES, renew_to=None):
    return counts.load(writer(counts, client, tmp_path, renew_to), name, 1,
                       genes(names, barcodes))


def snapshot(client) -> tuple:
    """Genes, counts rows by gene name, and objects: what two loads compare on."""
    names = {g["id"]: g["gene_name"] for g in client.tables.get("scrna_genes", [])}
    return (sorted((g["gene_number"], g["gene_name"]) for g in client.tables["scrna_genes"]),
            sorted((names[c["gene_id"]], c["counts_object_path"])
                   for c in client.tables.get("scrna_counts", [])),
            sorted((path, body) for (_, path), (body, _) in client.objects.items()))


def writes(client, since=0):
    return [e for e in client.log[since:] if e[0] != "select"]


def failing_nth(client, op, table, nth, error, *, commit=False):
    seen = []
    def when(o, t, payload):
        if o == op and t == table:
            seen.append(1)
            return len(seen) == nth
        return False
    client.fail(when, error, commit=commit)


# --------------------------------------------------------------------------- #
# A first load
# --------------------------------------------------------------------------- #


def test_a_first_load_numbers_genes_by_position_and_records_every_object(counts, tmp_path):
    client = client_with()
    dataset_id, written, skipped = load(counts, client, tmp_path)
    assert (dataset_id, written, skipped) == (7, 3, 0)
    table = genes(["G0", "G1", "G2"])
    genes_rows, counts_rows, _ = snapshot(client)
    assert genes_rows == [(0, "G0"), (1, "G1"), (2, "G2")]
    assert counts_rows == [(g, f"counts/MYB41/{g}.json") for g in ("G0", "G1", "G2")]
    for i, g in enumerate(("G0", "G1", "G2")):
        body, options = client.objects[("scrna", f"counts/MYB41/{g}.json")]
        assert json.loads(body) == counts.gene_counts(table["by_gene"], i)
        assert options == {"content-type": "application/json", "upsert": "true"}


def test_every_insert_goes_out_with_retries_off(counts, tmp_path):
    client = client_with()
    load(counts, client, tmp_path)
    inserts = [e for e in client.log if e[0] == "insert"]
    assert inserts and all(e[3] is False for e in inserts)


def test_no_row_is_written_before_its_object(counts, tmp_path, monkeypatch):
    """A row with no object is a gene that breaks in the browser; an object with no
    row is only uploaded again by the next run."""
    monkeypatch.setattr(counts, "RECORD_BATCH", 1)
    client = client_with()
    failing_nth(client, "upload", "scrna", 3, httpx.ConnectError("gone"))
    with pytest.raises(counts.IngestError):
        load(counts, client, tmp_path)
    recorded = {c["counts_object_path"] for c in client.tables["scrna_counts"]}
    assert recorded == {path for (_, path) in client.objects} == {
        "counts/MYB41/G0.json", "counts/MYB41/G1.json"}


def test_the_object_path_uses_the_trimmed_name(counts, tmp_path):
    client = client_with()
    load(counts, client, tmp_path, name="  MYB41 ")
    assert {path for (_, path) in client.objects} == {
        f"counts/MYB41/{g}.json" for g in ("G0", "G1", "G2")}


# --------------------------------------------------------------------------- #
# Resuming
# --------------------------------------------------------------------------- #


NAMES = [f"G{i}" for i in range(5)]


def test_a_stopped_load_resumes_to_the_same_rows_and_objects(counts, tmp_path, monkeypatch):
    monkeypatch.setattr(counts, "RECORD_BATCH", 2)
    whole = client_with()
    load(counts, whole, tmp_path, NAMES)

    client = client_with()
    failing_nth(client, "insert", "scrna_counts", 2, httpx.ReadTimeout("lost"), commit=True)
    with pytest.raises(counts.IngestError, match="re-run the same command"):
        load(counts, client, tmp_path, NAMES)
    assert len(client.tables["scrna_counts"]) == 4

    _, written, skipped = load(counts, client, tmp_path, NAMES)
    assert (written, skipped) == (1, 4)
    assert snapshot(client) == snapshot(whole)


def test_a_stop_during_an_upload_resumes_without_a_second_row(counts, tmp_path):
    client = client_with()
    failing_nth(client, "upload", "scrna", 3, httpx.ReadTimeout("lost"))
    with pytest.raises(counts.IngestError, match="outcome is unknown"):
        load(counts, client, tmp_path, NAMES)
    load(counts, client, tmp_path, NAMES)
    recorded = [c["counts_object_path"] for c in client.tables["scrna_counts"]]
    assert sorted(recorded) == sorted(f"counts/MYB41/{g}.json" for g in NAMES)


def test_a_complete_load_is_already_loaded(counts, tmp_path):
    client = client_with()
    load(counts, client, tmp_path)
    before = len(client.log)
    assert load(counts, client, tmp_path) == (7, 0, 3)
    assert writes(client, before) == []


def test_a_renewed_session_uploads_through_the_new_client(counts, tmp_path):
    """Each request takes its storage handle from the current client, so a
    session renewed mid-load does not keep uploading with the expired token."""
    from storage3.exceptions import StorageApiError

    first = client_with()
    second = FakeClient()
    second.tables, second.objects = first.tables, first.objects
    failing_nth(first, "upload", "scrna", 1, StorageApiError("jwt expired", "InvalidJWT", 400))
    load(counts, first, tmp_path, renew_to=second)
    assert [e for e in first.log if e[0] == "upload"] == []
    assert len([e for e in second.log if e[0] == "upload"]) == 3


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


def test_an_unfinished_dataset_is_refused(counts, tmp_path):
    client = client_with(ingested=False)
    with pytest.raises(counts.IngestError, match="not finished"):
        load(counts, client, tmp_path)
    assert writes(client) == []


def test_an_unknown_dataset_says_to_load_the_cells_first(counts, tmp_path):
    with pytest.raises(counts.IngestError, match="Load its cells first"):
        load(counts, client_with(), tmp_path, name="other")


@pytest.mark.parametrize("stored,file,named", [
    (BARCODES, ["AAA-1", "TTT-9", "GGG-1"], "does not hold these cells in this order"),
    (BARCODES, list(reversed(BARCODES)), "does not hold these cells in this order"),
    (BARCODES[:2], BARCODES, "holds 2 cells and this file"),
])
def test_cells_that_differ_from_the_file_are_refused(counts, tmp_path, stored, file, named):
    client = client_with(stored)
    with pytest.raises(counts.IngestError, match=named):
        load(counts, client, tmp_path, barcodes=file)
    assert writes(client) == []


def test_a_recorded_cell_count_that_disagrees_is_refused(counts, tmp_path):
    with pytest.raises(counts.IngestError, match="records 99 cells but holds 3"):
        load(counts, client_with(n_cells=99), tmp_path)


def test_cells_are_compared_in_cell_number_order(counts, tmp_path):
    """Rows come back in whatever order they were written; cell_number is the
    only thing that orders them."""
    client = client_with(SCRAMBLED, cell_ids=[300, 200, 100])
    assert load(counts, client, tmp_path, barcodes=SCRAMBLED)[0] == 7


def test_a_gene_registered_twice_is_refused_by_name(counts, tmp_path):
    client = client_with(gene_rows=[{"id": 1, "gene_number": 1, "gene_name": "G1"},
                                    {"id": 2, "gene_number": 1, "gene_name": "G1"}])
    with pytest.raises(counts.IngestError, match="G1.*more than once"):
        load(counts, client, tmp_path)
    assert writes(client) == []


def test_genes_registered_from_another_file_are_refused(counts, tmp_path):
    client = client_with(gene_rows=[{"id": 1, "gene_number": 0, "gene_name": "OTHER"}])
    with pytest.raises(counts.IngestError, match="do not describe the same"):
        load(counts, client, tmp_path)
    assert writes(client) == []


def test_a_gene_registered_at_another_position_is_refused(counts, tmp_path):
    client = client_with(gene_rows=[{"id": 1, "gene_number": 0, "gene_name": "G1"}])
    with pytest.raises(counts.IngestError, match="G1 is gene 0 .* 1 in this file"):
        load(counts, client, tmp_path)
    assert writes(client) == []
