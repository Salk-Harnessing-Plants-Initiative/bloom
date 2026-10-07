"""`scrna hdf5 upload --run-id`: the RNA-seq pipeline records which run a dataset is loaded for,
so the run can be linked to it, and a run never continues a dataset another run, or a person,
started. Without the option, nothing changes."""

from __future__ import annotations

import pytest
from scrna_fake_db import FakeClient
from scrna_fixtures import write_h5ad
from test_scrna_cli import _run

from bloomctl.scrna import _load
from bloomctl.scrna._writer import LoadError, Marker, Writer

OPTIONS = {"annotation": "ann", "sample_column": "sample", "umap_key": "X_umap",
           "source_column": None, "expression_units": "log1p normalised counts"}
CELLS = {"n_cells": 2, "n_genes": 100, "x": [0.0, 1.0], "y": [0.5, 1.5], "labels": ["A", "B"],
         "samples": ["Col-0", "Col-0"], "levels": ["A", "B"], "barcodes": ["BC0", "BC1"],
         "sources": {}}


def _writer(client, tmp_path):
    return Writer(client, lambda: client, Marker(tmp_path / "m.json", wait_s=0))


def _load_for(client, tmp_path, run_id):
    return _load.load(_writer(client, tmp_path), "Root atlas", 1, CELLS, "sha-1", OPTIONS,
                      create=True, run_id=run_id)


def _plan_for(client, tmp_path, run_id):
    return _load.plan(_writer(client, tmp_path), "Root atlas", 1, CELLS, "sha-1", OPTIONS,
                      create=True, run_id=run_id)


def _started(client, **metadata):
    """An unfinished dataset from the same file, as a stopped load leaves it."""
    client.tables.setdefault("scrna_datasets", []).append({
        "id": 7, "name": "Root atlas", "species_id": 1, "deleted_at": None,
        "source_checksum": "sha-1", "ingested_at": None, "n_cells": None,
        "metadata": {"load_options": OPTIONS, **metadata}})


def _writes(client):
    return [e for e in client.log if e[0] != "select"]


def test_a_new_dataset_records_the_run_it_is_loaded_for(tmp_path):
    client = FakeClient()
    _load_for(client, tmp_path, run_id=42)
    (dataset,) = client.tables["scrna_datasets"]
    assert dataset["metadata"]["rnaseq_run_id"] == 42
    assert dataset["ingested_at"], "the finish kept the record"


def test_without_a_run_nothing_is_recorded(tmp_path):
    client = FakeClient()
    _load_for(client, tmp_path, run_id=None)
    (dataset,) = client.tables["scrna_datasets"]
    assert "rnaseq_run_id" not in dataset["metadata"]


def test_a_run_continues_its_own_dataset(tmp_path):
    client = FakeClient()
    _started(client, rnaseq_run_id=42)
    assert _plan_for(client, tmp_path, run_id=42) == _load.Plan("resume", 7)
    assert _load_for(client, tmp_path, run_id=42)[2] == "resumed"


@pytest.mark.parametrize("recorded, words", [(41, "for run 41"), (None, "outside a run")])
def test_a_run_refuses_a_dataset_it_did_not_start(tmp_path, recorded, words):
    client = FakeClient()
    _started(client, **({} if recorded is None else {"rnaseq_run_id": recorded}))
    for attempt in (_plan_for, _load_for):
        with pytest.raises(LoadError, match=f"{words}, not run 42") as refused:
            attempt(client, tmp_path, run_id=42)
        assert "Root atlas_v2" in str(refused.value), "the message names a free name"
    assert _writes(client) == []


def test_a_load_without_a_run_continues_any_dataset_as_before(tmp_path):
    client = FakeClient()
    _started(client, rnaseq_run_id=42)
    assert _load_for(client, tmp_path, run_id=None)[2] == "resumed"


def test_the_option_reaches_the_dataset(tmp_path, env, storage):
    result = _run("upload", "--yes", "--run-id", "42", str(write_h5ad(tmp_path / "d.h5ad")))
    assert result.exit_code == 0, result.output
    (dataset,) = env["client"].tables["scrna_datasets"]
    assert dataset["metadata"]["rnaseq_run_id"] == 42


@pytest.mark.parametrize("value", ["0", "-1", "seven"])
def test_a_run_id_must_be_a_positive_whole_number(tmp_path, env, value):
    result = _run("upload", "--yes", "--run-id", value, str(write_h5ad(tmp_path / "d.h5ad")))
    assert result.exit_code == 2


def test_a_run_id_is_not_for_adding_labels(tmp_path, env):
    result = _run("upload", "--name", "MYB41", "--species", "Arabidopsis", "--annotation",
                  "cell_type", "--yes", "--add-labels", "--facet", "sample", "--run-id", "42",
                  str(write_h5ad(tmp_path / "d.h5ad")))
    assert result.exit_code == 2
    assert "--run-id is for loading a run's dataset" in result.output
