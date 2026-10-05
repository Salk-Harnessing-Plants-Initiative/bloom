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
