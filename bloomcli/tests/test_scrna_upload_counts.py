"""`hdf5 upload` writing the per-gene counts with the cells, and finishing the dataset last."""

import json

import httpx
import pytest
from scrna_fixtures import X, write_h5ad
from test_scrna_cli import _run
from test_scrna_upload_load import _writes

from bloomctl.scrna import _object


def _dataset(env) -> dict:
    (row,) = env["client"].tables["scrna_datasets"]
    return row


def _objects(env) -> dict[str, dict]:
    return {path: json.loads(data) for (_b, path), (data, _o) in env["client"].objects.items()}


def test_an_upload_writes_every_genes_counts_and_finishes_last(tmp_path, env, storage):
    result = _run("upload", "--yes", str(write_h5ad(tmp_path / "d.h5ad")))
    assert result.exit_code == 0, result.output
    assert "gene counts    4 genes, one object each under scrna/counts/MYB41_<new id>_/" in (
        result.stderr)
    dataset = _dataset(env)
    prefix = f"counts/MYB41_{dataset['id']}_/"
    assert sorted(_objects(env)) == [f"{prefix}gene{i}.json" for i in range(4)]
    assert _objects(env)[f"{prefix}gene1.json"] == {"0": 1.5}
    assert len(env["client"].tables["scrna_counts"]) == 4
    assert dataset["ingested_at"] and dataset["n_genes"] == 4
    writes = _writes(env["client"])
    finish = max(i for i, e in enumerate(writes) if e[:2] == ("update", "scrna_datasets"))
    last_counts = max(i for i, e in enumerate(writes) if e[:2] == ("insert", "scrna_counts"))
    assert last_counts < finish, "the dataset is finished only after every gene's counts"


def test_a_load_stopped_during_the_counts_stays_unfinished_and_resumes(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "d.h5ad")
    env["client"].fail(lambda op, table, payload: op == "upload", httpx.ConnectError("refused"))
    stopped = _run("upload", "--yes", str(path))
    assert stopped.exit_code != 0
    assert "the file is stored, but loading it stopped" in stopped.output
    assert _dataset(env).get("ingested_at") is None
    resumed = _run("upload", "--yes", str(path))
    assert resumed.exit_code == 0, resumed.output
    assert "continues a load that stopped" in resumed.stderr
    assert _dataset(env)["ingested_at"] and len(env["client"].tables["scrna_counts"]) == 4


def test_a_dataset_finished_without_its_counts_gets_them(tmp_path, env, storage):
    """A dataset loaded before counts were part of the upload is finished with cells only."""
    path = write_h5ad(tmp_path / "d.h5ad")
    assert _run("upload", "--yes", str(path)).exit_code == 0
    client = env["client"]
    finished_at = _dataset(env)["ingested_at"] = "2026-01-01T00:00:00+00:00"
    client.tables["scrna_counts"].clear()
    client.tables["scrna_genes"].clear()
    client.objects.clear()
    result = _run("upload", "--yes", str(path))
    assert result.exit_code == 0, result.output
    assert "loaded without its counts; they will be added" in result.stderr
    assert f"4 genes, one object each under scrna/counts/MYB41_{_dataset(env)['id']}_/" in (
        result.stderr)
    assert "Added the counts of 4 genes to dataset" in result.stdout
    assert len(client.tables["scrna_counts"]) == 4 and len(_objects(env)) == 4
    dataset = _dataset(env)
    assert dataset["ingested_at"] and dataset["ingested_at"] != finished_at
    assert "counts_pending" not in dataset["metadata"]


def test_a_dataset_with_its_counts_is_already_loaded(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "d.h5ad")
    assert _run("upload", "--yes", str(path)).exit_code == 0
    before = len(_writes(env["client"]))
    again = _run("upload", "--yes", str(path))
    assert "is already loaded from this file" in again.stdout
    assert "gene counts" not in again.stderr
    assert len(_writes(env["client"])) == before


def test_an_expected_count_is_checked_and_shown_before_the_question(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "d.h5ad")
    result = _run("upload", "--dry-run", "--expect-nonzero", "gene3=2", str(path))
    assert result.exit_code == 0, result.output
    assert "checked        gene3 non-zero in 2 cells" in result.stderr


def test_an_expected_count_that_fails_is_refused_before_sending(tmp_path, env, storage):
    result = _run("upload", "--yes", "--expect-nonzero", "gene3=232",
                  str(write_h5ad(tmp_path / "d.h5ad")))
    assert result.exit_code != 0
    assert "gene3 is non-zero in 2 cells, expected 232. Nothing was sent." in result.output
    assert storage.requests == [] and _writes(env["client"]) == []


def test_a_malformed_expected_count_is_a_usage_error(tmp_path, env):
    result = _run("upload", "--yes", "--expect-nonzero", "gene3",
                  str(write_h5ad(tmp_path / "d.h5ad")))
    assert result.exit_code == 2
    assert "GENE=COUNT" in result.output


def test_a_gene_name_that_cannot_be_a_path_is_refused_before_sending(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "d.h5ad", var_ids=["g/1", "g2", "g3", "g4"])
    result = _run("upload", "--yes", str(path))
    assert result.exit_code != 0
    assert "cannot be part of an object path" in result.output
    assert storage.requests == []


def test_the_counts_follow_the_stored_file(tmp_path, env, storage):
    """The objects hold the values of the very file whose fingerprint the dataset records."""
    path = write_h5ad(tmp_path / "d.h5ad")
    assert _run("upload", "--yes", str(path)).exit_code == 0
    assert _dataset(env)["source_checksum"] == _object.fingerprint_of(path)


# --- a file that changes while it is being uploaded ------------------------------------------


def _rewrite(path, factor):
    """Re-save the file with every expression value scaled, as a re-export would."""
    import os

    import h5py

    with h5py.File(path, "r+") as f:
        f["X/data"][...] = f["X/data"][...] * factor
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def test_a_file_changed_after_it_is_stored_stops_before_any_counts(tmp_path, env, storage,
                                                                   monkeypatch):
    from bloomctl.scrna import _send

    path = write_h5ad(tmp_path / "d.h5ad")
    real = _send.send_through_expiry

    def send_then_change(*args, **kwargs):
        real(*args, **kwargs)
        _rewrite(path, 100)

    monkeypatch.setattr(_send, "send_through_expiry", send_then_change)
    result = _run("upload", "--yes", str(path))
    assert result.exit_code != 0
    assert "d.h5ad changed while it was being uploaded" in result.output
    assert "Put the original file back" in result.output
    assert _objects(env) == {} and env["client"].tables.get("scrna_counts", []) == []
    assert _dataset(env).get("ingested_at") is None


def test_a_file_changed_during_the_counts_stops_and_keeps_only_the_originals_values(
    tmp_path, env, storage, monkeypatch
):
    from bloomctl.scrna import _genes

    monkeypatch.setattr(_genes, "BLOCK_VALUES", 1)
    path = write_h5ad(tmp_path / "d.h5ad")
    original = {f"gene{j}": v for j, v in _genes.gene_values(path, _genes.read_names(path))}
    blocks = []
    real_read = _genes._read_block

    def change_after_two_blocks(*args):
        # The loader holds the file open, so the re-save is simulated by its new time.
        import os

        blocks.append(1)
        if len(blocks) == 3:
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        return real_read(*args)

    monkeypatch.setattr(_genes, "_read_block", change_after_two_blocks)
    result = _run("upload", "--yes", str(path))
    assert result.exit_code != 0
    assert "changed while it was being uploaded" in result.output
    stored = {p.rsplit("/", 1)[1][:-5]: v for p, v in _objects(env).items()}
    assert stored == {g: original[g] for g in ("gene0", "gene1")}, "nothing read after the change"
    assert _dataset(env).get("ingested_at") is None


def test_the_original_file_put_back_finishes_the_load(tmp_path, env, storage, monkeypatch):
    import shutil

    from bloomctl.scrna import _send

    path = write_h5ad(tmp_path / "d.h5ad")
    kept = tmp_path / "original.h5ad"
    shutil.copy2(path, kept)
    real = _send.send_through_expiry
    changed = []

    def send_then_change_once(*args, **kwargs):
        real(*args, **kwargs)
        if not changed:
            changed.append(1)
            _rewrite(path, 100)

    monkeypatch.setattr(_send, "send_through_expiry", send_then_change_once)
    assert _run("upload", "--yes", str(path)).exit_code != 0
    shutil.copy2(kept, path)
    resumed = _run("upload", "--yes", str(path))
    assert resumed.exit_code == 0, resumed.output
    assert _dataset(env)["ingested_at"] and len(env["client"].tables["scrna_counts"]) == 4


def test_a_storage_error_page_during_the_counts_gives_a_plain_message(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "d.h5ad")
    env["client"].fail(lambda op, table, payload: op == "upload",
                       ValueError("Expecting value: line 1 column 1 (char 0)"))
    result = _run("upload", "--yes", str(path))
    assert result.exit_code != 0
    assert "the file is stored, but loading it stopped" in result.output
    assert "the storage server answered with an error page instead of a reply" in result.output
    assert "Traceback" not in result.output
    again = _run("upload", "--yes", str(path))
    assert "may still be finishing on the server; wait" in again.output


def test_a_file_that_cannot_be_read_during_the_counts_gives_a_plain_message(
    tmp_path, env, storage, monkeypatch
):
    from bloomctl.scrna import _genes

    def truncated(*_args):
        raise OSError("Unable to read data (truncated file)")

    monkeypatch.setattr(_genes, "_read_block", truncated)
    result = _run("upload", "--yes", str(write_h5ad(tmp_path / "d.h5ad")))
    assert result.exit_code != 0
    assert "loading it stopped: d.h5ad could not be read: Unable to read data" in result.output
    assert not isinstance(result.exception, OSError)


def test_a_resume_typed_in_other_capitals_is_refused_before_sending(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "d.h5ad")
    env["client"].fail(lambda op, table, payload: op == "upload", httpx.ConnectError("refused"))
    assert _run("upload", "--yes", str(path)).exit_code != 0
    requests = len(storage.requests)
    result = _run("upload", "--yes", "--name", "myb41", "--species", "Arabidopsis",
                  "--annotation", "cell_type", str(path))
    assert result.exit_code != 0
    assert "differs from it only in capital letters" in result.output
    assert "Nothing was sent." in result.output
    assert len(storage.requests) == requests
    folders = {p.split("/")[1] for p in _objects(env)}
    assert len(folders) <= 1, "a dataset's objects stay in one folder"


# --- resuming the counts, and adding them to a finished dataset ------------------------------


def _expected_objects(dataset_id) -> dict[str, dict]:
    return {f"counts/MYB41_{dataset_id}_/gene{j}.json":
            {str(i): X[i, j] for i in range(X.shape[0]) if X[i, j]} for j in range(X.shape[1])}


def _counts_sent(client, since=0) -> list[str]:
    return [e[2].rsplit("/", 1)[1] for e in client.log[since:]
            if e[0] == "upload" and e[2].startswith("counts/")]


def _fail_on_gene(client, gene):
    client.fail(lambda op, table, payload: op == "upload" and payload.endswith(f"/{gene}.json"),
                httpx.ConnectError("refused"))


def test_a_load_stopped_after_some_genes_are_recorded_resumes_with_the_rest(
    tmp_path, env, storage, monkeypatch
):
    from bloomctl.scrna import _counts

    monkeypatch.setattr(_counts, "RECORD_BATCH", 1)
    path = write_h5ad(tmp_path / "d.h5ad")
    client = env["client"]
    _fail_on_gene(client, "gene2")
    assert _run("upload", "--yes", str(path)).exit_code != 0
    assert len(client.tables["scrna_counts"]) == 2, "gene0 and gene1 recorded before the stop"
    before = len(client.log)
    resumed = _run("upload", "--yes", str(path))
    assert resumed.exit_code == 0, resumed.output
    assert "continues a load that stopped" in resumed.stderr
    assert _counts_sent(client, before) == ["gene2.json", "gene3.json"]
    gene_ids = [r["gene_id"] for r in client.tables["scrna_counts"]]
    assert len(gene_ids) == len(set(gene_ids)) == 4
    assert len(client.tables["scrna_genes"]) == 4
    assert _objects(env) == _expected_objects(_dataset(env)["id"])
    assert _dataset(env)["ingested_at"]


def _finished_without_counts(tmp_path, env):
    path = write_h5ad(tmp_path / "d.h5ad")
    assert _run("upload", "--yes", str(path)).exit_code == 0
    client = env["client"]
    client.tables["scrna_counts"].clear()
    client.tables["scrna_genes"].clear()
    client.objects.clear()
    return path, client


def test_adding_counts_shows_the_dataset_unfinished_until_they_are_all_there(
    tmp_path, env, storage, monkeypatch
):
    from bloomctl.scrna import _counts

    monkeypatch.setattr(_counts, "RECORD_BATCH", 1)
    path, client = _finished_without_counts(tmp_path, env)
    _fail_on_gene(client, "gene2")
    stopped = _run("upload", "--yes", str(path))
    assert stopped.exit_code != 0
    dataset = _dataset(env)
    assert dataset["ingested_at"] is None and dataset["source_checksum"], "shown as incomplete"
    assert dataset["metadata"]["counts_pending"] is True
    before = len(client.log)
    again = _run("upload", "--yes", str(path))
    assert again.exit_code == 0, again.output
    assert "loaded without its counts; they will be added" in again.stderr
    assert "Added the counts of 2 genes to dataset" in again.stdout
    assert _counts_sent(client, before) == ["gene2.json", "gene3.json"]
    dataset = _dataset(env)
    assert dataset["ingested_at"] and "counts_pending" not in dataset["metadata"]
    assert dataset["metadata"]["normalization"], "the rest of the metadata is kept"
    assert _objects(env) == _expected_objects(dataset["id"])


def test_adding_counts_stopped_before_the_finish_is_finished_by_the_next_run(
    tmp_path, env, storage
):
    path, client = _finished_without_counts(tmp_path, env)
    client.fail(lambda op, table, payload: op == "update" and table == "scrna_datasets"
                and payload.get("ingested_at"), httpx.ConnectError("refused"))
    assert _run("upload", "--yes", str(path)).exit_code != 0
    assert len(client.tables["scrna_counts"]) == 4 and _dataset(env)["ingested_at"] is None
    before = len(client.log)
    again = _run("upload", "--yes", str(path))
    assert again.exit_code == 0, again.output
    assert "gene counts    all stored; the dataset will be finished" in again.stderr
    assert "every gene's counts are stored" in again.stdout
    assert _counts_sent(client, before) == []
    assert _dataset(env)["ingested_at"] and "counts_pending" not in _dataset(env)["metadata"]


def _swap_barcodes(client):
    first, second = sorted(client.tables["scrna_cells"], key=lambda r: r["cell_number"])[:2]
    first["barcode"], second["barcode"] = second["barcode"], first["barcode"]


def _rename_barcode(client):
    min(client.tables["scrna_cells"], key=lambda r: r["cell_number"])["barcode"] = "other"


def _drop_last_cell(client):
    last = max(client.tables["scrna_cells"], key=lambda r: r["cell_number"])
    client.tables["scrna_cells"].remove(last)


def _reorder_types(client):
    for row in client.tables["scrna_clusters"]:
        row["ordinal"] = -row["ordinal"] - 1


@pytest.mark.parametrize("change,refusal", [
    (_swap_barcodes, "does not hold these cells in this order: they first differ at cell 0"),
    (_rename_barcode, "they first differ at cell 0"),
    (_drop_last_cell, "they first differ at cell 2 (2 stored, 3 in the file)"),
    (_reorder_types, "stored cell types differ from the file's"),
], ids=["swapped", "renamed", "missing", "cell-types"])
def test_counts_are_added_only_to_the_cells_of_this_file(
    tmp_path, env, storage, change, refusal
):
    """Each object is keyed by the cell's position, so the stored cells must be this file's."""
    path, client = _finished_without_counts(tmp_path, env)
    change(client)
    requests = len(storage.requests)
    result = _run("upload", "--yes", str(path))
    assert result.exit_code != 0
    assert refusal in result.output and "Nothing was sent." in result.output
    assert len(storage.requests) == requests and _objects(env) == {}
    assert client.tables["scrna_counts"] == [] and _dataset(env)["ingested_at"]


def test_the_cells_are_compared_in_cell_number_order(tmp_path, env, storage):
    """Row ids need not follow the cells' positions; compared by cell_number, they match."""
    path, client = _finished_without_counts(tmp_path, env)
    cells = client.tables["scrna_cells"]
    for row, new_id in zip(cells, sorted((r["id"] for r in cells), reverse=True)):
        row["id"] = new_id
    result = _run("upload", "--yes", str(path))
    assert result.exit_code == 0, result.output
    assert _objects(env) == _expected_objects(_dataset(env)["id"])
