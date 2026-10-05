"""The write path of `scrna hdf5 upload`'s load, against an in-memory client: registering a
dataset, resuming one, the checks that refuse, finishing, and adding labels."""

from __future__ import annotations

import httpx
import pytest
from scrna_fake_db import FakeClient

from bloomctl.scrna import _cells, _load
from bloomctl.scrna._writer import LoadError, Marker, Writer

OPTIONS = {"annotation": "ann", "sample_column": "sample", "umap_key": "X_umap",
           "source_column": None, "expression_units": "log1p normalised counts"}


def cells(labels, samples=None) -> dict:
    """Shaped like read_cells returns, with coordinates that show a swap."""
    n = len(labels)
    return {"n_cells": n, "n_genes": 100,
            "x": [float(i) for i in range(n)], "y": [float(i) + 0.5 for i in range(n)],
            "labels": list(labels), "samples": samples or ["Col-0"] * n,
            "levels": sorted(set(labels)), "barcodes": [f"BC{i}" for i in range(n)],
            "sources": {}}


def writer(client, tmp_path):
    return Writer(client, lambda: client, Marker(tmp_path / "m.json", wait_s=0))


def load(client, tmp_path, name="MYB41", labels=("A", "B", "A"),
         checksum="sha-1", options=None, create=True):
    return _load.load(writer(client, tmp_path), name, 1, cells(labels),
                       checksum, options or OPTIONS, create=create)


def dataset(client, **cols) -> dict:
    row = {"id": 7, "name": "MYB41", "species_id": 1, "deleted_at": None,
           "source_checksum": "sha-1", "ingested_at": None,
           "metadata": {"load_options": OPTIONS}, "n_cells": None, **cols}
    client.tables.setdefault("scrna_datasets", []).append(row)
    return row


def stored(client) -> dict:
    """Every table's rows, without ids, so two loads can be compared."""
    drop = {"id", "dataset_id", "ingested_at"}
    return {t: sorted(sorted((k, repr(v)) for k, v in r.items() if k not in drop)
                      for r in client.tables.get(t, []))
            for t in ("scrna_datasets", "scrna_clusters", "scrna_cells")}


def writes(client, since=0):
    return [e for e in client.log[since:] if e[0] != "select"]


# --------------------------------------------------------------------------- #
# A first load
# --------------------------------------------------------------------------- #


def test_a_first_load_registers_the_dataset_and_finishes_it_last(tmp_path):
    client = FakeClient()
    dataset_id, n, outcome = load(client, tmp_path,
                                  labels=("Xylem", "Cortex", "Phellem", "Cortex"))
    assert (n, outcome) == (4, "registered")
    (ds,) = client.tables["scrna_datasets"]
    assert ds["id"] == dataset_id and ds["source_checksum"] == "sha-1"
    assert ds["metadata"] == {"load_options": OPTIONS, "cell_type_column": "ann",
                              "expected_cells": 4}
    assert (ds["n_cells"], ds["n_genes"], ds["expression_units"]) == \
        (4, 100, "log1p normalised counts")
    assert ds["ingested_at"]
    assert writes(client)[-1][:2] == ("update", "scrna_datasets")

    catalogue = sorted((r["ordinal"], r["cluster_id"], r["name"], r["color"])
                       for r in client.tables["scrna_clusters"])
    assert catalogue == [(0, "Cortex", "Cortex", _cells.PALETTE[0]),
                         (1, "Phellem", "Phellem", _cells.PALETTE[1]),
                         (2, "Xylem", "Xylem", _cells.PALETTE[2])]
    rows = sorted((r["cell_number"], r["barcode"], r["x"], r["y"], r["cluster_id"],
                   r["replicate"]) for r in client.tables["scrna_cells"])
    assert rows == [(0, "BC0", 0.0, 0.5, "Xylem", "Col-0"),
                    (1, "BC1", 1.0, 1.5, "Cortex", "Col-0"),
                    (2, "BC2", 2.0, 2.5, "Phellem", "Col-0"),
                    (3, "BC3", 3.0, 3.5, "Cortex", "Col-0")]


def test_every_write_goes_out_with_retries_off(tmp_path):
    client = FakeClient()
    load(client, tmp_path)
    assert writes(client) and all(e[3] is False for e in writes(client))


def test_an_unknown_name_without_create_is_refused(tmp_path):
    client = FakeClient()
    with pytest.raises(LoadError, match="--create"):
        load(client, tmp_path, create=False)
    assert writes(client) == []


def test_create_refuses_a_name_the_counts_path_cannot_hold(tmp_path):
    client = FakeClient()
    with pytest.raises(LoadError, match="storage path"):
        load(client, tmp_path, name="MYB41/../other")
    assert writes(client) == []


def test_a_blank_name_is_refused(tmp_path):
    with pytest.raises(LoadError, match="blank"):
        load(FakeClient(), tmp_path, name="   ")


def test_columns_of_different_lengths_are_refused_before_writing(tmp_path):
    """zip() would drop the tail of the longer columns without a word."""
    client = FakeClient()
    table = cells(["A", "B", "C"])
    table["labels"] = ["A", "B"]
    with pytest.raises(LoadError, match="labels holds 2 values for 3 cells"):
        _load.load(writer(client, tmp_path), "MYB41", 1, table, "sha-1",
                    OPTIONS, create=True)
    assert writes(client) == []


def test_two_live_datasets_with_the_name_are_refused(tmp_path):
    client = FakeClient()
    dataset(client, id=7)
    dataset(client, id=9)
    with pytest.raises(LoadError, match="cannot tell which"):
        load(client, tmp_path, create=False)
    assert writes(client) == []


def test_a_soft_deleted_dataset_is_not_resurrected(tmp_path):
    client = FakeClient()
    buried = dataset(client, deleted_at="2026-01-01")
    dataset_id, _, outcome = load(client, tmp_path)
    assert outcome == "registered" and dataset_id != buried["id"]
    assert buried["ingested_at"] is None


# --------------------------------------------------------------------------- #
# Resuming
# --------------------------------------------------------------------------- #


def fail_on_cells_batch(client, number):
    """Time out on the given cells insert, after it committed on the server."""
    seen = []
    def when(op, table, payload):
        if op == "insert" and table == "scrna_cells":
            seen.append(1)
            return len(seen) == number
        return False
    client.fail(when, httpx.ReadTimeout("timed out"), commit=True)


def test_an_interrupted_load_resumes_to_the_same_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(_load, "CELL_BATCH", 2)
    labels = ("A", "B", "A", "B", "C")
    whole = FakeClient()
    load(whole, tmp_path, labels=labels)

    client = FakeClient()
    fail_on_cells_batch(client, 2)
    with pytest.raises(LoadError, match="run the same command again"):
        load(client, tmp_path, labels=labels)
    assert client.tables["scrna_datasets"][0].get("ingested_at") is None

    _, n, outcome = load(client, tmp_path, labels=labels, create=False)
    assert (n, outcome) == (5, "resumed")
    assert stored(client) == stored(whole)


def test_a_resume_accepts_create(tmp_path):
    client = FakeClient()
    dataset(client)
    _, _, outcome = load(client, tmp_path, create=True)
    assert outcome == "resumed"


def test_a_padded_name_finds_its_dataset(tmp_path):
    client = FakeClient()
    dataset(client, name="MYB41 ")
    dataset_id, _, _ = load(client, tmp_path, name="  MYB41", create=False)
    assert dataset_id == 7 and len(client.tables["scrna_datasets"]) == 1


def test_the_same_file_after_finishing_is_already_loaded(tmp_path):
    client = FakeClient()
    first, _, _ = load(client, tmp_path)
    before = len(client.log)
    again, _, outcome = load(client, tmp_path, create=False)
    assert (again, outcome) == (first, "already loaded")
    assert writes(client, before) == []


def test_a_dataset_finished_from_another_file_is_refused(tmp_path):
    client = FakeClient()
    load(client, tmp_path, checksum="sha-1")
    before = len(client.log)
    with pytest.raises(LoadError, match="give it a new name"):
        load(client, tmp_path, checksum="sha-2", create=False)
    assert writes(client, before) == []


@pytest.mark.parametrize("ingested_at", [None, "2026-01-01T00:00:00Z"])
def test_a_dataset_with_no_checksum_is_refused(tmp_path, ingested_at):
    client = FakeClient()
    dataset(client, source_checksum=None, ingested_at=ingested_at, metadata=None)
    with pytest.raises(LoadError, match="give it a new name"):
        load(client, tmp_path, create=False)
    assert writes(client) == []


def test_a_resume_from_a_different_file_is_refused_naming_both(tmp_path):
    client = FakeClient()
    dataset(client, source_checksum="sha-1")
    with pytest.raises(LoadError, match=r"sha-1.*sha-2|sha-2.*sha-1"):
        load(client, tmp_path, checksum="sha-2", create=False)
    assert writes(client) == []


def test_a_resume_with_different_options_is_refused_naming_the_option(tmp_path):
    client = FakeClient()
    dataset(client)
    changed = {**OPTIONS, "sample_column": "replicate"}
    with pytest.raises(LoadError, match=r"--sample-column was sample, now replicate"):
        load(client, tmp_path, options=changed, create=False)
    assert writes(client) == []


def test_a_stored_catalogue_that_differs_from_the_file_is_refused(tmp_path):
    client = FakeClient({"scrna_clusters": [
        {"dataset_id": 7, "cluster_id": "A", "ordinal": 0},
        {"dataset_id": 7, "cluster_id": "Z", "ordinal": 1}]})
    dataset(client)
    with pytest.raises(LoadError, match="cell types"):
        load(client, tmp_path, create=False)
    assert writes(client) == []


@pytest.mark.parametrize("table,named", [
    ("scrna_cluster_stats", "statistic"), ("scrna_cluster_neighbors", "neighbour"),
    ("scrna_de", "differential expression"),
])
def test_results_on_the_dataset_stop_the_cells_loader(tmp_path, table, named):
    client = FakeClient({table: [{"id": 1, "dataset_id": 7}]})
    dataset(client)
    with pytest.raises(LoadError, match=named):
        load(client, tmp_path, create=False)
    assert writes(client) == []


def test_repeated_cells_stop_it_before_finishing(tmp_path):
    client = FakeClient({
        "scrna_clusters": [{"dataset_id": 7, "cluster_id": "A", "ordinal": 0},
                           {"dataset_id": 7, "cluster_id": "B", "ordinal": 1}],
        "scrna_cells": [{"dataset_id": 7, "cell_number": 0},
                        {"dataset_id": 7, "cell_number": 0}]})
    ds = dataset(client)
    with pytest.raises(LoadError, match="repeated"):
        load(client, tmp_path, create=False)
    assert ds["ingested_at"] is None


# --------------------------------------------------------------------------- #
# Genotypes and labels
# --------------------------------------------------------------------------- #

LABEL_OPTIONS = {**OPTIONS, "genotype_column": "sample", "control": "Col-0",
                 "constructs": {"pFACT": "pFACT:MYB41"}, "facets": ["transgene_pos"],
                 "source_column": "nn_source"}


def labelled(labels=("A", "B", "A", "B")):
    table = cells(labels, samples=["Col-0", "pFACT", "pFACT", "Col-0"][:len(labels)])
    table["genotypes"] = list(table["samples"])
    table["facets"] = [{"transgene_pos": v} for v in ("False", "True", "True", "False")][:len(labels)]
    table["sources"] = {"A": "shahan", "B": "nuclei"}
    return table


def test_a_first_load_writes_genotypes_and_labels_with_the_cells(tmp_path):
    client = FakeClient()
    dataset_id, _, _ = _load.load(writer(client, tmp_path), "MYB41", 1, labelled(),
                                   "sha-1", LABEL_OPTIONS, create=True)
    genotypes = {g["name"]: g for g in client.tables["scrna_genotypes"]}
    assert {n: (g["is_control"], g["construct"]) for n, g in genotypes.items()} == {
        "Col-0": (True, None), "pFACT": (False, "pFACT:MYB41")}
    assert all(g["dataset_id"] == dataset_id for g in genotypes.values())
    rows = sorted(client.tables["scrna_cells"], key=lambda r: r["cell_number"])
    assert [r["genotype_id"] for r in rows] == [genotypes[n]["id"] for n in
                                               ("Col-0", "pFACT", "pFACT", "Col-0")]
    assert [r["facets"] for r in rows] == [{"transgene_pos": v} for v in
                                           ("False", "True", "True", "False")]


def finished_without_labels(client, tmp_path):
    """A dataset loaded before labels existed: cells, catalogue, no genotypes."""
    table = labelled()
    plain = {k: v for k, v in table.items() if k not in ("genotypes", "facets")}
    plain["sources"] = {}
    dataset_id, _, _ = _load.load(writer(client, tmp_path), "MYB41", 1, plain,
                                   "sha-1", OPTIONS, create=True)
    return dataset_id, table


def test_labels_are_added_to_a_finished_dataset_from_the_same_file(tmp_path):
    client = FakeClient()
    dataset_id, table = finished_without_labels(client, tmp_path)
    before = len(client.tables["scrna_cells"])

    got_id, added = _load.add_labels(writer(client, tmp_path), "MYB41", 1, table,
                                      "sha-1", LABEL_OPTIONS)
    assert got_id == dataset_id
    assert added == {"genotypes": 2, "cells": 4, "sources": 2}
    assert len(client.tables["scrna_cells"]) == before, "no cell is inserted"
    sources = {c["cluster_id"]: c["source"] for c in client.tables["scrna_clusters"]}
    assert sources == {"A": "shahan", "B": "nuclei"}
    ids = {g["name"]: g["id"] for g in client.tables["scrna_genotypes"]}
    rows = sorted(client.tables["scrna_cells"], key=lambda r: r["cell_number"])
    assert [r["genotype_id"] for r in rows] == [ids[n] for n in ("Col-0", "pFACT", "pFACT", "Col-0")]
    assert [r["facets"]["transgene_pos"] for r in rows] == ["False", "True", "True", "False"]
    (ds,) = client.tables["scrna_datasets"]
    assert ds["metadata"]["load_options"]["facets"] == ["transgene_pos"]
    assert ds["metadata"]["load_options"]["control"] == "Col-0"
    assert ds["metadata"]["cell_type_column"] == "ann", "other metadata is kept"


def test_adding_labels_twice_changes_nothing_more(tmp_path):
    client = FakeClient()
    _, table = finished_without_labels(client, tmp_path)
    _load.add_labels(writer(client, tmp_path), "MYB41", 1, table, "sha-1", LABEL_OPTIONS)
    snapshot = stored(client), sorted(map(repr, client.tables["scrna_genotypes"]))
    _load.add_labels(writer(client, tmp_path), "MYB41", 1, table, "sha-1", LABEL_OPTIONS)
    assert (stored(client), sorted(map(repr, client.tables["scrna_genotypes"]))) == snapshot


def test_label_updates_go_out_with_retries_off_and_in_groups(tmp_path):
    client = FakeClient()
    _, table = finished_without_labels(client, tmp_path)
    since = len(client.log)
    _load.add_labels(writer(client, tmp_path), "MYB41", 1, table, "sha-1", LABEL_OPTIONS)
    cell_updates = [e for e in writes(client, since) if e[:2] == ("update", "scrna_cells")]
    # two genotypes x two transgene values, all four cells in two groups here
    assert 1 <= len(cell_updates) <= 4
    assert all(e[3] is False for e in writes(client, since))


def test_labels_are_refused_for_an_unfinished_dataset(tmp_path):
    client = FakeClient()
    dataset(client)
    with pytest.raises(LoadError, match="not finished"):
        _load.add_labels(writer(client, tmp_path), "MYB41", 1, labelled(), "sha-1",
                          LABEL_OPTIONS)
    assert writes(client) == []


def test_labels_are_refused_from_another_file(tmp_path):
    client = FakeClient()
    finished_without_labels(client, tmp_path)
    since = len(client.log)
    with pytest.raises(LoadError, match="sha-1.*sha-2"):
        _load.add_labels(writer(client, tmp_path), "MYB41", 1, labelled(), "sha-2",
                          LABEL_OPTIONS)
    assert writes(client, since) == []


def test_labels_are_refused_when_the_stored_cells_differ_from_the_file(tmp_path):
    client = FakeClient()
    _, table = finished_without_labels(client, tmp_path)
    table["barcodes"] = list(reversed(table["barcodes"]))
    since = len(client.log)
    with pytest.raises(LoadError, match="does not hold these cells in this order"):
        _load.add_labels(writer(client, tmp_path), "MYB41", 1, table, "sha-1",
                          LABEL_OPTIONS)
    assert writes(client, since) == []


def test_a_stored_genotype_that_disagrees_is_refused(tmp_path):
    client = FakeClient()
    dataset_id, table = finished_without_labels(client, tmp_path)
    client.tables["scrna_genotypes"] = [{"id": 1, "dataset_id": dataset_id, "name": "pFACT",
                                         "is_control": True, "construct": None}]
    since = len(client.log)
    with pytest.raises(LoadError, match="pFACT"):
        _load.add_labels(writer(client, tmp_path), "MYB41", 1, table, "sha-1",
                          LABEL_OPTIONS)
    assert writes(client, since) == []


# --------------------------------------------------------------------------- #
# Planning: the same decisions as the load, made before the question, writing nothing
# --------------------------------------------------------------------------- #


def plan(client, tmp_path, name="MYB41", table=None, checksum="sha-1", options=None,
         create=True, add_labels=False):
    return _load.plan(writer(client, tmp_path), name, 1, table or cells(("A", "B", "A")),
                      checksum, options or OPTIONS, create=create, add_labels=add_labels)


def test_a_new_name_with_create_plans_a_registration(tmp_path):
    client = FakeClient()
    assert plan(client, tmp_path) == _load.Plan("register", None)
    assert writes(client) == []


def test_a_new_name_without_create_is_refused_at_planning(tmp_path):
    client = FakeClient()
    with pytest.raises(LoadError, match="--create"):
        plan(client, tmp_path, create=False)
    assert writes(client) == []


def test_a_name_the_storage_path_cannot_hold_is_refused_at_planning(tmp_path):
    with pytest.raises(LoadError, match="storage path"):
        plan(FakeClient(), tmp_path, name="a/b")


def test_an_unfinished_dataset_from_this_file_plans_a_resume(tmp_path):
    client = FakeClient()
    dataset(client)
    assert plan(client, tmp_path, create=False) == _load.Plan("resume", 7)
    assert writes(client) == []


def test_a_finished_dataset_from_this_file_plans_nothing_to_load(tmp_path):
    client = FakeClient()
    dataset(client, ingested_at="2026-01-01T00:00:00Z")
    assert plan(client, tmp_path, create=False) == _load.Plan("already loaded", 7)


def test_a_finished_dataset_from_another_file_is_refused_at_planning(tmp_path):
    client = FakeClient()
    dataset(client, ingested_at="2026-01-01T00:00:00Z")
    with pytest.raises(LoadError, match="give it a new name"):
        plan(client, tmp_path, checksum="sha-2", create=False)


def test_results_on_an_unfinished_dataset_are_refused_at_planning(tmp_path):
    client = FakeClient({"scrna_de": [{"id": 1, "dataset_id": 7}]})
    dataset(client)
    with pytest.raises(LoadError, match="differential expression"):
        plan(client, tmp_path, create=False)


def test_adding_labels_plans_against_the_finished_dataset(tmp_path):
    client = FakeClient()
    dataset_id, table = finished_without_labels(client, tmp_path)
    since = len(client.log)
    got = plan(client, tmp_path, table=table, options=LABEL_OPTIONS, add_labels=True)
    assert got == _load.Plan("add labels", dataset_id)
    assert writes(client, since) == []


def test_adding_labels_to_a_dataset_that_is_not_there_is_refused_at_planning(tmp_path):
    with pytest.raises(LoadError, match="load its cells first"):
        plan(FakeClient(), tmp_path, table=labelled(), options=LABEL_OPTIONS, add_labels=True)


def test_a_stored_genotype_that_disagrees_is_refused_at_planning(tmp_path):
    client = FakeClient()
    dataset_id, table = finished_without_labels(client, tmp_path)
    client.tables["scrna_genotypes"] = [{"id": 1, "dataset_id": dataset_id, "name": "pFACT",
                                         "is_control": True, "construct": None}]
    with pytest.raises(LoadError, match="pFACT"):
        plan(client, tmp_path, table=table, options=LABEL_OPTIONS, add_labels=True)


# --------------------------------------------------------------------------- #
# Guards that keep a dataset whole
# --------------------------------------------------------------------------- #


def test_labels_reach_every_cell_across_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(_load, "LABEL_BATCH", 1)
    client = FakeClient()
    _, table = finished_without_labels(client, tmp_path)
    _load.add_labels(writer(client, tmp_path), "MYB41", 1, table, "sha-1", LABEL_OPTIONS)
    rows = sorted(client.tables["scrna_cells"], key=lambda r: r["cell_number"])
    assert [r["facets"]["transgene_pos"] for r in rows] == ["False", "True", "True", "False"]
    assert all(r.get("genotype_id") for r in rows)


def test_a_dataset_missing_a_cell_is_not_finished(tmp_path, monkeypatch):
    real = _load._insert_cells

    def drop_last(w, dataset_id, table, missing, genotype_ids=None, on_progress=None):
        real(w, dataset_id, table, missing[:-1], genotype_ids, on_progress)

    monkeypatch.setattr(_load, "_insert_cells", drop_last)
    client = FakeClient()
    with pytest.raises(LoadError, match="1 of 3 cells missing"):
        load(client, tmp_path)
    assert client.tables["scrna_datasets"][0].get("ingested_at") is None


def test_labels_are_refused_when_the_stored_cell_types_differ(tmp_path):
    client = FakeClient()
    _, table = finished_without_labels(client, tmp_path)
    for row in client.tables["scrna_clusters"]:
        row["ordinal"] += 5
    since = len(client.log)
    with pytest.raises(LoadError, match="stored cell types differ"):
        _load.add_labels(writer(client, tmp_path), "MYB41", 1, table, "sha-1", LABEL_OPTIONS)
    assert writes(client, since) == []


def test_adding_labels_records_only_the_options_given(tmp_path):
    client = FakeClient()
    _, table = finished_without_labels(client, tmp_path)
    _load.add_labels(writer(client, tmp_path), "MYB41", 1, table, "sha-1", LABEL_OPTIONS)
    only_sources = {**OPTIONS, "source_column": "nn_source"}
    sources_table = {**table, "genotypes": None, "facets": None}
    _load.add_labels(writer(client, tmp_path), "MYB41", 1, sources_table, "sha-1", only_sources)
    recorded = client.tables["scrna_datasets"][0]["metadata"]["load_options"]
    assert recorded["genotype_column"] == "sample" and recorded["control"] == "Col-0"
    assert recorded["facets"] == ["transgene_pos"]
    assert recorded["annotation"] == "ann"


def test_a_resume_with_repeated_cells_is_refused_at_planning(tmp_path):
    client = FakeClient({"scrna_cells": [{"dataset_id": 7, "cell_number": 0},
                                         {"dataset_id": 7, "cell_number": 0}]})
    dataset(client)
    with pytest.raises(LoadError, match="repeated"):
        plan(client, tmp_path, create=False)


# --------------------------------------------------------------------------- #
# A loaded dataset's name is taken
# --------------------------------------------------------------------------- #


def test_a_name_differing_only_in_case_asks_for_the_exact_name_or_a_new_one(tmp_path):
    client = FakeClient()
    dataset(client, ingested_at="2026-01-01T00:00:00Z")
    with pytest.raises(LoadError) as exc:
        plan(client, tmp_path, name="myb41", create=True)
    assert "a dataset named 'MYB41' already exists (id 7)" in str(exc.value)
    assert "use --name 'MYB41' exactly" in str(exc.value)
    assert "--name 'myb41_v2' --create" in str(exc.value)
    assert writes(client) == []


def test_the_exact_name_with_spaces_around_it_is_still_the_same_dataset(tmp_path):
    client = FakeClient()
    dataset(client, ingested_at="2026-01-01T00:00:00Z")
    assert plan(client, tmp_path, name="  MYB41 ", create=True) == _load.Plan("already loaded", 7)


def test_another_file_under_a_loaded_name_is_offered_a_versioned_name(tmp_path):
    client = FakeClient()
    dataset(client, ingested_at="2026-01-01T00:00:00Z")
    with pytest.raises(LoadError, match=r"--name 'MYB41_v2' --create"):
        plan(client, tmp_path, checksum="sha-2", create=True)
    assert writes(client) == []


@pytest.mark.parametrize("name,suggested", [
    ("MYB41", "MYB41_v2"), ("MYB41_v2", "MYB41_v3"), ("MYB41_v10", "MYB41_v11"),
    ("run_v", "run_v_v2"),
])
def test_the_suggested_name_counts_up_a_version(name, suggested):
    from bloomctl.scrna._checks import next_name

    assert next_name(name) == suggested
