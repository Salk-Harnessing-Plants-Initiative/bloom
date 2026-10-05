"""`bloomctl scrna hdf5 upload` loading the dataset it stores, end to end against fakes.

The file is stored first, then the cells are written; every check that can refuse runs
before the question, and a refusal there sends and writes nothing.
"""

import httpx
from scrna_fake_db import database as FakeClient
from scrna_fixtures import write_h5ad
from test_scrna_cli import _at_a_terminal, _run

from bloomctl.scrna import _object, _writer


def _cells_of(client) -> list[dict]:
    return sorted(client.tables.get("scrna_cells", []), key=lambda r: r["cell_number"])


def _writes(client) -> list:
    return [e for e in client.log if e[0] != "select"]


def _labelled(tmp_path, name="data.h5ad"):
    """Three cells: two genotypes, a transgene label and where each label came from."""
    return write_h5ad(tmp_path / name, obs_columns={
        "cell_type": ["Cortex", "Xylem", "Cortex"], "sample": ["s1", "s2", "s2"],
        "genotype": ["Col-0", "pFACT", "pFACT"], "transgene": ["False", "True", "True"],
        "source": ["shahan", "nuclei", "shahan"], "treat": ["a", "b", "a"],
    })


def test_an_upload_stores_the_file_then_loads_its_cells(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "data.h5ad")
    result = _run("upload", "--yes", str(path))
    assert result.exit_code == 0, result.output
    assert storage.objects, "the file is stored"
    client = env["client"]
    (dataset,) = client.tables["scrna_datasets"]
    assert dataset["name"] == "MYB41" and dataset["species_id"] == 1
    assert dataset["source_checksum"] == _object.fingerprint_of(path)
    assert dataset["n_cells"] == 3 and dataset["ingested_at"]
    assert dataset["metadata"]["cell_type_column"] == "cell_type"
    assert [c["barcode"] for c in _cells_of(client)] == ["cell0", "cell1", "cell2"]
    assert sorted(c["cluster_id"] for c in client.tables["scrna_clusters"]) == [
        "cell0", "cell1", "cell2"]
    assert f"Registered dataset {dataset['id']} ('MYB41'): 3 cells" in result.stdout


def test_the_summary_says_what_the_load_will_write(tmp_path, env, storage):
    result = _run("upload", "--dry-run", str(write_h5ad(tmp_path / "data.h5ad")))
    assert result.exit_code == 0, result.output
    assert "dataset        MYB41 (Arabidopsis) — new, registered by this upload" in result.stderr
    assert "cell types     3: cell0, cell1, cell2" in result.stderr
    assert "samples        cell0 1, cell1 1, cell2 1" in result.stderr


def test_a_dry_run_writes_nothing(tmp_path, env, storage):
    result = _run("upload", "--dry-run", str(write_h5ad(tmp_path / "data.h5ad")))
    assert result.exit_code == 0, result.output
    assert "Nothing was sent or written." in result.output
    assert _writes(env["client"]) == []
    assert storage.requests == []


def test_the_question_names_the_dataset(tmp_path, env, storage, monkeypatch):
    _at_a_terminal(monkeypatch)
    result = _run("upload", str(write_h5ad(tmp_path / "data.h5ad")), input="n\n")
    assert "Upload this file and load its cells into 'MYB41'?" in result.stderr
    assert _writes(env["client"]) == [] and storage.requests == []


def test_the_same_upload_again_is_already_stored_and_already_loaded(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "data.h5ad")
    assert _run("upload", "--yes", str(path)).exit_code == 0
    before = len(_writes(env["client"]))
    again = _run("upload", "--yes", str(path))
    assert again.exit_code == 0, again.output
    assert "already loaded from this file" in again.stderr
    assert "Already uploaded" in again.stdout
    assert "is already loaded from this file: 3 cells" in again.stdout
    assert len(_writes(env["client"])) == before


def test_without_create_an_unknown_name_is_refused_before_sending(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "data.h5ad")
    result = _run("upload", "--yes", "--name", "MYB41", "--species", "Arabidopsis",
                  "--annotation", "cell_type", str(path))
    assert result.exit_code != 0
    assert "Pass --create" in result.output and "Nothing was sent." in result.output
    assert storage.requests == [] and _writes(env["client"]) == []


def test_an_unknown_species_is_refused_naming_the_ones_there_are(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "data.h5ad")
    result = _run("upload", "--yes", "--name", "MYB41", "--species", "Tomato",
                  "--annotation", "cell_type", "--create", str(path))
    assert result.exit_code != 0
    assert "no species named 'Tomato'; Bloom has: Arabidopsis" in result.output
    assert storage.requests == []


def test_the_species_name_ignores_case_and_spaces(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "data.h5ad")
    result = _run("upload", "--yes", "--name", "MYB41", "--species", "  arabidopsis ",
                  "--annotation", "cell_type", "--create", str(path))
    assert result.exit_code == 0, result.output


def test_an_annotation_column_the_file_lacks_is_refused_before_sending(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "data.h5ad")
    result = _run("upload", "--yes", "--name", "MYB41", "--species", "Arabidopsis",
                  "--annotation", "celltype", "--create", str(path))
    assert result.exit_code != 0
    assert "has no obs['celltype']" in result.output and "Nothing was sent." in result.output
    assert storage.requests == []


def test_an_unexpected_cell_count_is_refused_before_sending(tmp_path, env, storage):
    result = _run("upload", "--yes", "--expect-cells", "4", str(write_h5ad(tmp_path / "d.h5ad")))
    assert result.exit_code != 0
    assert "expected 4 cells, the file holds 3" in result.output
    assert storage.requests == []


def test_a_dataset_finished_from_another_file_is_refused_before_sending(tmp_path, env, storage):
    env["client"] = FakeClient([{"id": 7, "name": "MYB41", "species_id": 1,
                                 "source_checksum": "other", "ingested_at": "2026-01-01"}])
    result = _run("upload", "--yes", str(write_h5ad(tmp_path / "data.h5ad")))
    assert result.exit_code != 0
    assert "give it a new name, e.g. --name 'MYB41_v2' --create" in result.output
    assert storage.requests == []


def test_a_load_that_stops_says_the_file_is_stored_and_resumes(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "data.h5ad")
    client = env["client"]
    client.fail(lambda op, table, payload: op == "insert" and table == "scrna_cells",
                httpx.ConnectError("refused"))
    stopped = _run("upload", "--yes", str(path))
    assert stopped.exit_code != 0
    assert "the file is stored, but loading it stopped" in stopped.output
    assert "run the same command again to continue" in stopped.output
    assert client.tables["scrna_datasets"][0].get("ingested_at") is None

    resumed = _run("upload", "--yes", str(path))
    assert resumed.exit_code == 0, resumed.output
    assert "continues a load that stopped" in resumed.stderr
    assert "Resumed dataset" in resumed.stdout
    assert len(_cells_of(client)) == 3 and client.tables["scrna_datasets"][0]["ingested_at"]


def test_a_write_of_unknown_outcome_is_waited_out_before_anything_is_sent(
    tmp_path, env, storage, monkeypatch
):
    path = write_h5ad(tmp_path / "data.h5ad")
    env["client"].fail(lambda op, table, payload: op == "insert" and table == "scrna_cells",
                       httpx.ReadTimeout("slow"), commit=True)
    assert _run("upload", "--yes", str(path)).exit_code != 0
    requests = len(storage.requests)
    again = _run("upload", "--yes", str(path))
    assert again.exit_code != 0
    assert "may still be finishing on the server; wait" in again.output
    assert len(storage.requests) == requests


def test_genotypes_and_labels_are_written_with_the_cells(tmp_path, env, storage):
    path = _labelled(tmp_path)
    result = _run("upload", "--yes", "--genotype-column", "genotype", "--control", "Col-0",
                  "--construct", "pFACT=pFACT:MYB41", "--facet", "transgene",
                  "--source-column", "source", str(path))
    assert result.exit_code == 0, result.output
    assert "genotypes      Col-0 (control), pFACT (pFACT:MYB41)" in result.stderr
    assert "labels         transgene: False 1, True 2" in result.stderr
    assert "label sources  nuclei (1 cell type); shahan (1 cell type)" in result.stderr
    client = env["client"]
    ids = {g["name"]: g["id"] for g in client.tables["scrna_genotypes"]}
    assert [c["genotype_id"] for c in _cells_of(client)] == [ids["Col-0"], ids["pFACT"],
                                                             ids["pFACT"]]
    assert [c["facets"] for c in _cells_of(client)] == [
        {"transgene": "False"}, {"transgene": "True"}, {"transgene": "True"}]
    sources = {c["cluster_id"]: c["source"] for c in client.tables["scrna_clusters"]}
    assert sources == {"Cortex": "shahan", "Xylem": "nuclei"}


def test_labels_are_added_to_a_dataset_already_loaded_from_the_file(tmp_path, env, storage):
    path = _labelled(tmp_path)
    assert _run("upload", "--yes", str(path)).exit_code == 0
    result = _run("upload", "--yes", "--name", "MYB41", "--species", "Arabidopsis",
                  "--annotation", "cell_type", "--add-labels", "--facet", "transgene",
                  str(path))
    assert result.exit_code == 0, result.output
    assert "labels will be added to its cells" in result.stderr
    assert "Added labels to dataset" in result.stdout
    assert [c["facets"]["transgene"] for c in _cells_of(env["client"])] == [
        "False", "True", "True"]


def test_a_control_without_a_genotype_column_is_a_usage_error(tmp_path, env):
    result = _run("upload", "--yes", "--control", "Col-0", str(write_h5ad(tmp_path / "d.h5ad")))
    assert result.exit_code == 2
    assert "needs --genotype-column" in result.output


def test_add_labels_needs_something_to_add(tmp_path, env):
    result = _run("upload", "--yes", "--name", "M", "--species", "Arabidopsis",
                  "--annotation", "cell_type", "--add-labels", str(write_h5ad(tmp_path / "d.h5ad")))
    assert result.exit_code == 2
    assert "--add-labels needs" in result.output


def test_add_labels_cannot_create_a_dataset(tmp_path, env):
    result = _run("upload", "--yes", "--add-labels", "--facet", "sample",
                  str(write_h5ad(tmp_path / "d.h5ad")))
    assert result.exit_code == 2
    assert "cannot --create" in result.output


def test_a_malformed_construct_is_a_usage_error(tmp_path, env):
    result = _run("upload", "--yes", "--construct", "pFACT", str(write_h5ad(tmp_path / "d.h5ad")))
    assert result.exit_code == 2
    assert "GENOTYPE=NAME" in result.output


def test_an_unknown_outcome_record_lives_in_the_staging_folder(tmp_path):
    path = _writer.marker_path(tmp_path / "stage", "MYB41")
    assert path.parent == tmp_path / "stage"
