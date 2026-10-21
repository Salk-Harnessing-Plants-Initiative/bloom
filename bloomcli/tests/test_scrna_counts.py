"""Writing each gene's counts: a row per gene, its object, then a row recording the object.

Against an in-memory client and real files: genes numbered by position, every object written
before its row, a stopped write resumed without a second row, and the refusals.
"""

from __future__ import annotations

import json

import httpx
import pytest
from scrna_fake_db import UPLOAD_TRIES, FakeClient
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
    client.fail(lambda op, table, payload: op == "upload" and payload.endswith("AT1G00003.json"),
                httpx.ConnectError("refused"), times=UPLOAD_TRIES)
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
    client.fail(lambda op, table, payload: op == "upload", httpx.ReadTimeout("slow"), commit=True,
                times=UPLOAD_TRIES)
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


# --- storage replies the storage library cannot read ---------------------------------------


def _storage_client(status, body, ctype="application/json"):
    """A client whose storage is the real storage3 client, answering every request so."""
    from storage3 import SyncStorageClient

    def reply(_request):
        return httpx.Response(status, content=body, headers={"content-type": ctype})

    storage = SyncStorageClient("http://s.test/storage/v1/", {"Authorization": "Bearer t"},
                                http_client=httpx.Client(transport=httpx.MockTransport(reply)))
    return type("Client", (), {"storage": storage})()


@pytest.mark.parametrize("status,body,ctype", [
    (502, b"<html>Bad Gateway</html>", "text/html"),
    (504, b'{"message": "The upstream server is timing out"}', "application/json"),
], ids=["gateway-html-502", "gateway-504"])
def test_an_unreadable_storage_reply_is_an_unknown_outcome_not_a_traceback(
    tmp_path, status, body, ctype
):
    marker = Marker(tmp_path / "m.json", wait_s=300)
    w = Writer(_storage_client(status, body, ctype), lambda: None, marker)
    with pytest.raises(LoadError) as exc:
        _counts._upload(w, "counts/MYB41_7_/G.json", b"{}")
    assert "the storage server answered with an error page instead of a reply" in str(exc.value)
    assert "outcome is unknown" in str(exc.value)
    assert marker.path.exists(), "the next run waits it out"


def test_an_expired_login_named_in_storages_message_signs_in_again(tmp_path):
    expired = _storage_client(
        403, b'{"statusCode": "403", "error": "Unauthorized", "message": "jwt expired"}')
    fresh = _storage_client(200, b'{"Key": "scrna/counts/MYB41_7_/G.json"}')
    renewed = []
    w = Writer(expired, lambda: renewed.append(1) or fresh, Marker(tmp_path / "m.json"))
    _counts._upload(w, "counts/MYB41_7_/G.json", b"{}")
    assert renewed == [1]


@pytest.mark.parametrize("name", ["a/../../x", "..", "a/b", "."])
def test_a_dataset_name_that_cannot_be_a_path_stops_before_any_object(tmp_path, name):
    with pytest.raises(LoadError, match="cannot be part of a storage path; an admin has to"):
        _counts.object_path(name, 7, "G")


@pytest.mark.parametrize("name,folder", [("a\\b", "ab"), ("a%2Fb", "a2Fb")])
def test_characters_the_cli_drops_leave_a_plain_folder(name, folder):
    assert _counts.object_path(name, 7, "G") == f"counts/{folder}_7_/G.json"


# --- several uploads at once ---------------------------------------------------


def _timed_uploads(monkeypatch, delay=0.02):
    """Wraps each upload to note how many run together; returns that tally."""
    import threading
    import time

    tally = {"now": 0, "most": 0}
    lock = threading.Lock()
    upload = _counts._upload

    def timed(writer, path, payload):
        with lock:
            tally["now"] += 1
            tally["most"] = max(tally["most"], tally["now"])
        try:
            time.sleep(delay)
            return upload(writer, path, payload)
        finally:
            with lock:
                tally["now"] -= 1

    monkeypatch.setattr(_counts, "_upload", timed)
    return tally


def test_uploads_run_together_but_never_more_than_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(_counts, "UPLOAD_WORKERS", 2)
    tally = _timed_uploads(monkeypatch)
    client = FakeClient()
    assert write(client, tmp_path) == 5
    assert tally["most"] == 2
    assert len(client.tables["scrna_counts"]) == 5 and len(objects(client)) == 5


def test_the_limit_leaves_most_of_storages_database_pool_free():
    # Storage's pool holds 20 connections, shared with everything the website serves.
    assert 1 < _counts.UPLOAD_WORKERS <= 8


def test_a_failed_upload_stops_new_uploads_and_records_no_row_for_it(tmp_path, monkeypatch):
    monkeypatch.setattr(_counts, "UPLOAD_WORKERS", 1)
    monkeypatch.setattr(_counts, "READ_AHEAD", 1)
    client = FakeClient()
    client.fail(lambda op, table, payload: op == "upload" and payload.endswith("AT1G00001.json"),
                httpx.ConnectError("refused"), times=UPLOAD_TRIES)
    with pytest.raises(LoadError, match="run the same command again"):
        write(client, tmp_path)
    sent = [e[2].rsplit("/", 1)[1] for e in client.log if e[0] == "upload"]
    assert sent == ["AT1G00000.json"], "no gene after the refused one is sent"
    assert all(not r["counts_object_path"].endswith("AT1G00001.json")
               for r in client.tables["scrna_counts"])


def test_an_interrupt_during_the_uploads_is_waited_out_by_the_next_run(tmp_path, monkeypatch):
    def interrupted(writer, path, payload):
        raise KeyboardInterrupt

    monkeypatch.setattr(_counts, "_upload", interrupted)
    marker = Marker(tmp_path / "m.json", wait_s=300)
    client = FakeClient()
    with pytest.raises(KeyboardInterrupt):
        _counts.write(Writer(client, lambda: client, marker), DATASET, "MYB41",
                      write_h5ad(tmp_path / "f.h5ad"), _genes.read_names(tmp_path / "f.h5ad"))
    assert marker.path.exists(), "an upload in flight may still land"
    assert client.tables["scrna_counts"] == []


def test_a_failed_upload_still_records_every_gene_that_was_stored(tmp_path, monkeypatch):
    monkeypatch.setattr(_counts, "UPLOAD_WORKERS", 4)
    monkeypatch.setattr(_counts, "RECORD_BATCH", 500)
    client = FakeClient()
    client.fail(lambda op, table, payload: op == "upload" and payload.endswith("AT1G00002.json"),
                httpx.ConnectError("refused"), times=UPLOAD_TRIES)
    with pytest.raises(LoadError, match="run the same command again"):
        write(client, tmp_path)
    recorded = sorted(r["counts_object_path"].rsplit("/", 1)[1] for r in client.tables["scrna_counts"])
    stored = sorted(path.rsplit("/", 1)[1] for path in objects(client))
    assert "AT1G00002.json" not in recorded
    assert recorded == stored, "every stored gene is recorded, so the rerun redoes only the failed one"
