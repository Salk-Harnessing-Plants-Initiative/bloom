"""Writing each gene's counts: a row per gene, its object, then a row recording the object.

Against an in-memory client and real files: genes numbered by position, every object written
before its row, a stopped write resumed without a second row, and the refusals.
"""

from __future__ import annotations

import json

import httpx
import pytest
from scrna_fake_db import FakeClient
from test_scrna_genes import MATRIX, expected, write_h5ad

from bloomctl.scrna import _counts, _genes
from bloomctl.scrna._writer import LoadError, Marker, Writer

DATASET = 7


def writer(client, tmp_path):
    return Writer(client, lambda: client, Marker(tmp_path / "m.json", wait_s=0))


def write(client, tmp_path, path=None, name="MYB41 transgene", progress=None):
    path = path or write_h5ad(tmp_path / "f.h5ad")
    return _counts.write(writer(client, tmp_path), DATASET, name, path,
                         _genes.read_names(path), on_progress=progress)


def objects(client) -> dict[str, dict]:
    return {path: json.loads(data) for (_bucket, path), (data, _opts) in client.objects.items()}


def test_every_gene_is_numbered_by_position_and_its_object_recorded(tmp_path):
    client = FakeClient()
    assert write(client, tmp_path) == 5
    genes = sorted((r["gene_number"], r["gene_name"]) for r in client.tables["scrna_genes"])
    assert genes == [(i, f"AT1G{i:05d}") for i in range(5)]
    ids = {r["gene_name"]: r["id"] for r in client.tables["scrna_genes"]}
    rows = {r["gene_id"]: r["counts_object_path"] for r in client.tables["scrna_counts"]}
    assert rows == {ids[f"AT1G{i:05d}"]: f"counts/MYB41_transgene_7_/AT1G{i:05d}.json"
                    for i in range(5)}
    stored = objects(client)
    for i in range(5):
        assert stored[f"counts/MYB41_transgene_7_/AT1G{i:05d}.json"] == expected()[i]


def test_objects_are_stored_as_json_and_replace_a_stopped_attempt(tmp_path):
    client = FakeClient()
    write(client, tmp_path)
    options = {opts["content-type"] for (_data, opts) in client.objects.values()}
    upserts = {opts["upsert"] for (_data, opts) in client.objects.values()}
    assert (options, upserts) == ({"application/json"}, {"true"})


def test_no_row_records_an_object_not_yet_stored(tmp_path):
    client = FakeClient()
    write(client, tmp_path)
    uploads, recorded = [], []
    for entry in client.log:
        if entry[0] == "upload":
            uploads.append(entry[2])
        if entry[:2] == ("insert", "scrna_counts"):
            recorded.append(len(uploads))
    assert recorded and all(n > 0 for n in recorded)
    assert recorded[-1] == 5


def test_every_write_goes_out_with_retries_off(tmp_path):
    client = FakeClient()
    write(client, tmp_path)
    assert all(e[3] is False for e in client.log if e[0] == "insert")


def test_the_path_uses_the_trimmed_name_cleaned_as_the_cli_cleans_it(tmp_path):
    client = FakeClient()
    write(client, tmp_path, name="  MYB41  transgene ")
    assert all(p.startswith("counts/MYB41_transgene_7_/") for p in objects(client))


@pytest.mark.parametrize("name,cleaned", [
    ("MYB41 transgene", "MYB41_transgene"), ("a.json", "a"), ('x"y', "xy"), ("a  b", "a_b"),
])
def test_the_dataset_name_is_cleaned_as_the_cli_cleans_it(name, cleaned):
    assert _counts.clean_dataset_name(name) == cleaned


def test_two_datasets_with_one_name_write_to_separate_paths():
    assert _counts.object_path("MYB41", 7, "G") != _counts.object_path("MYB41", 8, "G")


def test_a_stopped_write_resumes_to_the_same_rows_and_objects(tmp_path, monkeypatch):
    monkeypatch.setattr(_counts, "RECORD_BATCH", 2)
    whole = FakeClient()
    write(whole, tmp_path)
    client = FakeClient()
    seen = []
    client.fail(lambda op, table, payload: op == "upload" and seen.append(1) is None
                and len(seen) == 4, httpx.ConnectError("refused"))
    with pytest.raises(LoadError, match="run the same command again"):
        write(client, tmp_path)
    assert 0 < len(client.tables["scrna_counts"]) < 5
    write(client, tmp_path)
    def rows(c):
        return sorted(r["counts_object_path"] for r in c.tables["scrna_counts"])
    assert rows(client) == rows(whole)
    assert objects(client) == objects(whole)


def test_a_stop_after_an_upload_writes_no_second_row(tmp_path):
    client = FakeClient()
    client.fail(lambda op, table, payload: op == "upload", httpx.ReadTimeout("slow"), commit=True)
    with pytest.raises(LoadError, match="outcome is unknown"):
        write(client, tmp_path)
    write(client, tmp_path)
    gene_ids = [r["gene_id"] for r in client.tables["scrna_counts"]]
    assert len(gene_ids) == len(set(gene_ids)) == 5


def test_a_complete_write_run_again_writes_nothing(tmp_path):
    client = FakeClient()
    write(client, tmp_path)
    before = len(client.log)
    assert write(client, tmp_path) == 0
    assert [e for e in client.log[before:] if e[0] != "select"] == []


def test_progress_counts_each_gene_written(tmp_path):
    seen = []
    write(FakeClient(), tmp_path, progress=lambda d, t: seen.append((d, t)))
    assert seen == [(i, 5) for i in range(1, 6)]


def test_how_many_genes_are_missing_counts(tmp_path):
    client = FakeClient()
    path = write_h5ad(tmp_path / "f.h5ad")
    names = _genes.read_names(path)
    assert _counts.missing(writer(client, tmp_path), DATASET, names) == 5
    write(client, tmp_path, path)
    assert _counts.missing(writer(client, tmp_path), DATASET, names) == 0


@pytest.mark.parametrize("rows,named", [
    ([{"gene_number": 0, "gene_name": "AT1G00000"}, {"gene_number": 0, "gene_name": "AT1G00000"}],
     "registered more than once"),
    ([{"gene_number": 0, "gene_name": "AT9G99999"}], "this file does not hold"),
    ([{"gene_number": 3, "gene_name": "AT1G00000"}], "is gene 3 in dataset 7 and 0 in this file"),
])
def test_genes_registered_from_another_file_are_refused(tmp_path, rows, named):
    client = FakeClient({"scrna_genes": [{"id": i + 1, "dataset_id": DATASET, **r}
                                         for i, r in enumerate(rows)]})
    with pytest.raises(LoadError, match=named):
        write(client, tmp_path)
    assert client.objects == {}


def test_a_gene_recorded_twice_is_refused(tmp_path):
    client = FakeClient()
    write(client, tmp_path)
    client.tables["scrna_counts"].append(dict(client.tables["scrna_counts"][0], id=999))
    with pytest.raises(LoadError, match="more than once"):
        write(client, tmp_path)


def test_a_value_that_is_not_finite_stops_before_its_object(tmp_path):
    matrix = MATRIX.copy()
    matrix[2, 3] = float("nan")
    client = FakeClient()
    with pytest.raises(LoadError, match="AT1G00003 holds a value that is not finite"):
        write(client, tmp_path, write_h5ad(tmp_path / "nan.h5ad", matrix=matrix))
    assert "counts/MYB41_transgene_7_/AT1G00003.json" not in objects(client)
