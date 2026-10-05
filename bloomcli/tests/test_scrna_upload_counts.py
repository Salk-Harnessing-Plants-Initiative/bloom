"""`hdf5 upload` writing the per-gene counts with the cells, and finishing the dataset last."""

import json

import httpx
from scrna_fixtures import write_h5ad
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
    finished_at = _dataset(env)["ingested_at"]
    client.tables["scrna_counts"].clear()
    client.tables["scrna_genes"].clear()
    client.objects.clear()
    result = _run("upload", "--yes", str(path))
    assert result.exit_code == 0, result.output
    assert "loaded without its counts; they will be added" in result.stderr
    assert "Added the counts dataset" in result.stdout
    assert len(client.tables["scrna_counts"]) == 4 and len(_objects(env)) == 4
    assert _dataset(env)["ingested_at"] == finished_at, "a finished dataset stays as it was"


def test_a_dataset_with_its_counts_is_already_loaded(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "d.h5ad")
    assert _run("upload", "--yes", str(path)).exit_code == 0
    before = len(_writes(env["client"]))
    again = _run("upload", "--yes", str(path))
    assert "is already loaded from this file" in again.stdout
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
