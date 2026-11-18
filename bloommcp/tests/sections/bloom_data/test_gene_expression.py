"""gene_expression: per-cell-type numbers for genes in one scRNA dataset, with the plot's link."""

from __future__ import annotations

import copy
import json

import pytest

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access.fake_file_reader import FakeFileReader
from bloom_mcp.data_access.fake_table_reader import FakeTableReader
from bloom_mcp.data_access.table_ports import Function, Param
from bloom_mcp.sections.bloom_data import gene_expression as ge
from bloom_mcp.tools import _ports

SITE = "https://bloom.example"
# Cell 0-1 cortex, 2-3 xylem, 4 in a cluster with no catalog row.
CELLS = [
    {
        "x": 0.0,
        "y": 0.0,
        "cluster_ordinal": 0,
        "genotype": "Col-0",
        "facets": {"transgene_pos": "False"},
    },
    {
        "x": 1.0,
        "y": 0.0,
        "cluster_ordinal": 0,
        "genotype": "pFACT",
        "facets": {"transgene_pos": "True"},
    },
    {"x": 0.0, "y": 1.0, "cluster_ordinal": 1, "genotype": "pFACT", "facets": None},
    {"x": 1.0, "y": 1.0, "cluster_ordinal": 1, "genotype": "Col-0", "facets": None},
    {"x": 2.0, "y": 2.0, "cluster_ordinal": 255, "genotype": None, "facets": None},
]
TABLES = {
    "scrna_datasets": [
        {
            "id": 3,
            "name": "MYB41 root",
            "species_id": 16,
            "kind": "full",
            "deleted_at": None,
        },
        {
            "id": 4,
            "name": "Atlas",
            "species_id": 16,
            "kind": "reference",
            "deleted_at": None,
        },
        {
            "id": 5,
            "name": "Gone",
            "species_id": 16,
            "kind": "full",
            "deleted_at": "2026-01-01",
        },
    ],
    "scrna_clusters": [
        {
            "id": 1,
            "dataset_id": 3,
            "cluster_id": "C",
            "name": "Cortex",
            "source": None,
            "ordinal": 0,
        },
        {
            "id": 2,
            "dataset_id": 3,
            "cluster_id": "X",
            "name": None,
            "source": None,
            "ordinal": 1,
        },
    ],
    "scrna_genes": [
        {"id": 10, "dataset_id": 3, "gene_name": "AT4G28110"},
        {"id": 11, "dataset_id": 3, "gene_name": "AT1G01010"},
        {"id": 12, "dataset_id": 3, "gene_name": "AT2G02020"},
        {"id": 13, "dataset_id": 3, "gene_name": "AT3G03030"},
        {"id": 14, "dataset_id": 3, "gene_name": "Glyma.01G000100"},
        # Listed by symbol: PDF1.2 is its own gene, not PDF1.
        {"id": 15, "dataset_id": 3, "gene_name": "PDF1"},
        {"id": 16, "dataset_id": 3, "gene_name": "PDF1.2"},
        {"id": 17, "dataset_id": 3, "gene_name": "AT5G05050"},
        # Another dataset's gene, never read for dataset 3.
        {"id": 20, "dataset_id": 4, "gene_name": "AT9G09090"},
    ],
    "scrna_counts": [
        {
            "id": 1,
            "dataset_id": 3,
            "gene_id": 10,
            "counts_object_path": "counts/MYB41_root_3_/AT4G28110.json",
        },
        {
            "id": 2,
            "dataset_id": 3,
            "gene_id": 11,
            "counts_object_path": "counts/MYB41 root/AT1G01010.json",
        },
        {"id": 3, "dataset_id": 3, "gene_id": 12, "counts_object_path": None},
        {
            "id": 4,
            "dataset_id": 3,
            "gene_id": 13,
            "counts_object_path": "counts/MYB41_root_3_/AT3G03030.json",
        },
        {
            "id": 5,
            "dataset_id": 3,
            "gene_id": 17,
            "counts_object_path": "counts/MYB41_root_3_/AT5G05050.json",
        },
        # Another dataset's file for gene 12, which dataset 3 stores none for.
        {
            "id": 6,
            "dataset_id": 4,
            "gene_id": 12,
            "counts_object_path": "counts/Atlas_4_/AT2G02020.json",
        },
    ],
    "genes": [
        {
            "gene_id": "AT4G28110.Araport11.447",
            "symbol": "MYB41",
            "standard_name": None,
            "short_description": None,
            "ortho_group": None,
            "reference_id": None,
        },
        # One symbol on two genes.
        {
            "gene_id": "AT2G02020.Araport11.1",
            "symbol": "PAIR",
            "standard_name": None,
            "short_description": None,
            "ortho_group": None,
            "reference_id": None,
        },
        {
            "gene_id": "AT3G03030.Araport11.1",
            "symbol": "PAIR",
            "standard_name": None,
            "short_description": None,
            "ortho_group": None,
            "reference_id": None,
        },
    ],
}
FILES = {
    ("scrna", "counts/MYB41_root_3_/AT4G28110.json"): b'{"1": 4.0, "2": 2.0, "3": 1.0}',
    # An older, list-shaped file under the legacy path shape.
    ("scrna", "counts/MYB41 root/AT1G01010.json"): b'[{"0": 1.0}, {"4": 3.0}]',
    # Written against other cells.
    ("scrna", "counts/MYB41_root_3_/AT3G03030.json"): b'{"9": 1.0}',
    # Values that need rounding.
    ("scrna", "counts/MYB41_root_3_/AT5G05050.json"): b'{"0": 1, "1": 0.123456789}',
    ("scrna", "counts/Atlas_4_/AT2G02020.json"): b'{"0": 1.0}',
}
CELL_ARRAYS = Function("scrna_cell_arrays", (Param("ds_id", "bigint", True),))


@pytest.fixture
def bloom(monkeypatch):
    monkeypatch.setenv("SITE_URL", SITE)
    reader = FakeTableReader(copy.deepcopy(TABLES))
    reader.function_rows = {"scrna_cell_arrays": (CELL_ARRAYS, copy.deepcopy(CELLS))}
    files = FakeFileReader(FILES)
    previous = (_ports.table_reader(), _ports.file_reader())
    _ports.configure(table_reader=reader, file_reader=files)
    yield reader, files
    _ports.configure(table_reader=previous[0], file_reader=previous[1])


def _run(genes, **kw):
    return ge.gene_expression({"dataset_id": 3, "genes": genes, **kw})


def _rows(result, gene):
    [found] = [g for g in result.genes if g.gene_name == gene]
    return [(r.cell_type, r.part, r.cells, r.expressing, r.mean) for r in found.groups]


def test_each_cell_type_gets_the_websites_numbers_with_zeros_counted(bloom):
    result = _run(["AT4G28110"])
    assert _rows(result, "AT4G28110") == [
        ("Cortex", None, 2, 1, 2.0),
        ("X", None, 2, 2, 1.5),
        ("No cell type", None, 1, 0, 0.0),
    ]
    assert result.cells == 5 and result.returned == 1


def test_a_symbol_finds_the_datasets_agi_name(bloom):
    [gene] = _run(["myb41"]).genes
    assert (gene.requested, gene.gene_name, gene.note) == ("myb41", "AT4G28110", None)


def test_an_older_list_file_on_the_legacy_path_is_read(bloom):
    assert _rows(_run(["AT1G01010"]), "AT1G01010") == [
        ("Cortex", None, 2, 1, 0.5),
        ("X", None, 2, 0, 0.0),
        ("No cell type", None, 1, 1, 3.0),
    ]


def test_split_by_genotype_and_transgene(bloom):
    by_genotype = _run(["AT4G28110"], split="genotype")
    assert [(r[0], r[1]) for r in _rows(by_genotype, "AT4G28110")] == [
        ("Cortex", "Col-0"),
        ("Cortex", "pFACT"),
        ("X", "Col-0"),
        ("X", "pFACT"),
        ("No cell type", "Not recorded"),
    ]
    by_status = _run(["AT4G28110"], split="transgene")
    assert [(r[0], r[1]) for r in _rows(by_status, "AT4G28110")][:2] == [
        ("Cortex", "Transgene+"),
        ("Cortex", "Transgene−"),
    ]
    assert by_status.splits_offered == {"genotype": True, "transgene": True}


def test_genes_without_numbers_say_why_and_the_others_still_return(bloom):
    result = _run(["AT4G28110", "AT2G02020", "AT3G03030", "NOPE1"])
    notes = {g.requested: g.note for g in result.genes}
    assert notes["AT4G28110"] is None
    assert notes["AT2G02020"] == "This dataset stores no expression for this gene."
    assert "doesn't match this dataset" in notes["AT3G03030"]
    assert notes["NOPE1"] == "Not a gene in this dataset."
    assert result.returned == 1


def test_the_link_opens_the_plot_on_the_genes_with_numbers(bloom):
    result = _run(["AT4G28110", "AT1G01010", "AT2G02020"])
    assert result.page_url == (
        f"{SITE}/app/expression/16/3?tab=genes&gene=AT4G28110,AT1G01010"
    )


def test_no_link_without_a_site(bloom, monkeypatch):
    monkeypatch.delenv("SITE_URL")
    assert _run(["AT4G28110"]).page_url is None


def test_counts_are_read_only_from_the_stored_path(bloom):
    _, files = bloom
    _run(["AT4G28110"])
    assert ("download", "scrna", "counts/MYB41_root_3_/AT4G28110.json") in files.calls


@pytest.mark.parametrize(
    "dataset_id,match",
    [(4, "reference atlas"), (5, "No scRNA dataset"), (99, "No scRNA dataset")],
)
def test_unusable_datasets_are_refused(bloom, dataset_id, match):
    with pytest.raises(BloomMCPError, match=match):
        ge.gene_expression({"dataset_id": dataset_id, "genes": ["AT4G28110"]})


def test_a_dataset_over_the_cell_cap_is_refused(bloom, monkeypatch):
    monkeypatch.setattr(ge, "MAX_CELLS", 4)
    with pytest.raises(BloomMCPError, match="more than this tool reads"):
        _run(["AT4G28110"])


@pytest.mark.parametrize(
    "params",
    [
        {"dataset_id": 3, "genes": []},
        {"dataset_id": 3, "genes": ["a", "b", "c", "d", "e", "f"]},
        {"dataset_id": True, "genes": ["a"]},
        {"dataset_id": 3, "genes": ["a"], "split": "sample"},
        {"dataset_id": 3, "genes": ["  "]},
    ],
)
def test_bad_requests_are_refused(bloom, params):
    with pytest.raises(BloomMCPError):
        ge.gene_expression(params)


def test_a_name_in_another_case_still_finds_the_datasets_gene(bloom):
    [gene] = _run(["glyma.01g000100"]).genes
    assert gene.gene_name == "Glyma.01G000100"
    assert gene.note == "This dataset stores no expression for this gene."


def test_a_symbol_is_never_trimmed_to_another_gene(bloom):
    [gene] = _run(["PDF1.2"]).genes
    assert gene.gene_name == "PDF1.2"


def test_an_agi_transcript_finds_the_datasets_gene(bloom):
    [gene] = _run(["AT4G28110.1"]).genes
    assert gene.gene_name == "AT4G28110"
    assert gene.groups


def test_at_most_max_genes_are_read_in_all_and_the_rest_are_named(bloom, monkeypatch):
    monkeypatch.setattr(ge, "MAX_GENES", 2)
    result = _run(["AT4G28110", "PAIR"])
    assert [(g.requested, g.gene_name) for g in result.genes] == [
        ("AT4G28110", "AT4G28110"),
        ("PAIR", "AT2G02020"),
    ]
    [note] = result.notes
    assert "not read: AT3G03030." in note


def test_no_note_when_every_match_is_read(bloom):
    assert _run(["PAIR"]).notes == []


def _size(result):
    return len(json.dumps(result.model_dump(), default=str))


def test_genes_are_left_out_from_the_end_to_fit_one_reply(bloom, monkeypatch):
    full = _run(["AT4G28110", "AT1G01010"], split="genotype")
    monkeypatch.setattr(ge, "MAX_REPLY_CHARS", _size(full) - 1)
    first, last = _run(["AT4G28110", "AT1G01010"], split="genotype").genes
    assert first.groups == full.genes[0].groups
    assert last.groups == []
    assert last.note == (
        "Left out to fit one reply: ask for this gene in its own call or without split."
    )


def test_every_gene_is_left_out_when_none_fits(bloom, monkeypatch):
    monkeypatch.setattr(ge, "MAX_REPLY_CHARS", 10)
    result = _run(["AT4G28110", "AT1G01010"])
    assert result.returned == 0
    assert all(g.note.startswith("Left out to fit one reply") for g in result.genes)
    assert all(not g.note.endswith("without split.") for g in result.genes)


def test_every_statistic_matches_the_websites(bloom):
    [gene] = _run(["AT4G28110"]).genes
    cortex, xylem, _ = (r.model_dump() for r in gene.groups)
    # Cortex holds 0 and 4; X holds 2 and 1.
    assert cortex == {
        "cell_type": "Cortex",
        "part": None,
        "cells": 2,
        "expressing": 1,
        "share": 0.5,
        "mean": 2.0,
        "min": 0.0,
        "q1": 1.0,
        "median": 2.0,
        "q3": 3.0,
        "max": 4.0,
    }
    assert (xylem["share"], xylem["min"], xylem["q1"], xylem["median"]) == (
        1.0,
        1.0,
        1.25,
        1.5,
    )
    assert (xylem["q3"], xylem["max"]) == (1.75, 2.0)


def test_numbers_are_rounded_to_four_places(bloom):
    [cortex, *_] = _run(["AT5G05050"]).genes[0].groups
    assert (cortex.mean, cortex.min, cortex.q1) == (0.5617, 0.1235, 0.3426)


@pytest.mark.parametrize("name", ["AT9G09090", "at9g09090"])
def test_another_datasets_gene_is_not_found(bloom, name):
    [gene] = _run([name]).genes
    assert (gene.gene_name, gene.note) == (None, "Not a gene in this dataset.")


def test_another_datasets_file_is_never_read(bloom):
    _, files = bloom
    [gene] = _run(["AT2G02020"]).genes
    assert gene.note == "This dataset stores no expression for this gene."
    assert not any("Atlas_4_" in call[2] for call in files.calls)
