"""Per-cell-type expression statistics, as the website's Genes by cell type view computes them
(web/components/expression-lib/gene-stats.ts: groupCells, groupStats, partOf)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from bloom_mcp.data_access import expression_counts as ec
from bloom_mcp.data_access import expression_stats as es
from bloom_mcp.data_access.fake_file_reader import FakeFileReader

_WEB_LIB = Path(__file__).resolve().parents[3] / "web" / "components" / "expression-lib"

TYPES = [es.CellType(0, "Cortex"), es.CellType(1, "Xylem")]
CELLS = [
    {"cluster_ordinal": 0, "genotype": "Col-0", "facets": {"transgene_pos": "False"}},
    {"cluster_ordinal": 0, "genotype": "pFACT", "facets": {"transgene_pos": "True"}},
    {"cluster_ordinal": 1, "genotype": "pFACT", "facets": None},
    {"cluster_ordinal": 1, "genotype": None, "facets": None},
    {"cluster_ordinal": 255, "genotype": "Col-0", "facets": None},
]


def _ts_string(file: str, name: str) -> str:
    text = (_WEB_LIB / file).read_text()
    return re.search(rf'export const {name} = "([^"]*)"', text).group(1)


def test_names_match_the_website():
    assert es.NO_CELL_TYPE == _ts_string("gene-stats.ts", "NO_CELL_TYPE")
    assert es.NOT_RECORDED == _ts_string("gene-stats.ts", "NOT_RECORDED")
    assert es.TRANSGENE_FACET == _ts_string("transgene.ts", "TRANSGENE_FACET")
    assert es.TRANSGENE_POSITIVE == _ts_string("transgene.ts", "TRANSGENE_POSITIVE")


def test_cell_types_follow_the_map_order_and_names():
    clusters = [
        {"ordinal": 1, "cluster_id": "7", "name": None},
        {"ordinal": 0, "cluster_id": "3", "name": "Cortex"},
    ]
    assert es.cell_types(clusters) == [es.CellType(0, "Cortex"), es.CellType(1, "7")]


def test_cells_group_by_cell_type_with_unknown_clusters_last():
    groups = es.group_cells(CELLS, TYPES, "none")
    assert [(g.cell_type, g.part, g.cells) for g in groups] == [
        ("Cortex", None, [0, 1]),
        ("Xylem", None, [2, 3]),
        (es.NO_CELL_TYPE, None, [4]),
    ]


def test_a_cell_type_with_no_cells_is_left_out():
    groups = es.group_cells(CELLS[:2], TYPES, "none")
    assert [g.cell_type for g in groups] == ["Cortex"]


def test_genotype_split_puts_not_recorded_last():
    groups = es.group_cells(CELLS, TYPES, "genotype")
    assert [(g.cell_type, g.part) for g in groups] == [
        ("Cortex", "Col-0"),
        ("Cortex", "pFACT"),
        ("Xylem", "pFACT"),
        ("Xylem", es.NOT_RECORDED),
        (es.NO_CELL_TYPE, "Col-0"),
    ]


def test_transgene_split_names_each_status():
    groups = es.group_cells(CELLS, TYPES, "transgene")
    assert [(g.cell_type, g.part) for g in groups] == [
        ("Cortex", "Transgene+"),
        ("Cortex", "Transgene−"),
        ("Xylem", es.NOT_RECORDED),
        (es.NO_CELL_TYPE, es.NOT_RECORDED),
    ]


def test_splits_offered_are_those_some_cell_records():
    assert es.splits_offered(CELLS) == {"genotype": True, "transgene": True}
    assert es.splits_offered([{"cluster_ordinal": 0}]) == {
        "genotype": False,
        "transgene": False,
    }


def test_group_stats_count_every_cell_with_zeros():
    values = [0.0, 3.0, 1.0, 2.0, 0.0]
    stats = es.group_stats(values, [0, 1, 2, 3, 4])
    assert stats == es.GroupStats(
        cells=5,
        expressing=3,
        share=0.6,
        mean=1.2,
        min=0.0,
        q1=0.0,
        median=1.0,
        q3=2.0,
        max=3.0,
    )


def test_quartiles_interpolate_as_the_website_does():
    stats = es.group_stats([1.0, 2.0, 3.0, 4.0], [0, 1, 2, 3])
    assert (stats.q1, stats.median, stats.q3) == (1.75, 2.5, 3.25)


def test_a_group_with_no_cells_has_no_stats():
    assert es.group_stats([1.0], []) is None


def test_the_shared_fixture_gives_the_websites_numbers():
    """bloommcp's reader, grouping and stats on the fixture the genome browser's gene panel checks
    (web cluster-expression.ts); the row labels here only line rows up with the fixture.
    """
    fixture = json.loads(
        (_WEB_LIB / "fixtures" / "cluster-expression.json").read_text()
    )
    by_ordinal = {c["ordinal"]: c for c in fixture["clusters"]}
    ordinals = fixture["cell_ordinals"]
    groups = es.group_cells(
        [{"cluster_ordinal": o} for o in ordinals],
        es.cell_types(fixture["clusters"]),
        "none",
    )
    for shape, body in fixture["count_files"].items():
        path = f"counts/fixture/{shape}.json"
        files = FakeFileReader({(ec.BUCKET, path): body.encode()})
        values = ec.gene_values(files, path, fixture["cell_count"])
        rows = []
        for group in groups:
            stats = es.group_stats(values, group.cells)
            cluster = by_ordinal.get(ordinals[group.cells[0]]) or {}
            rows.append(
                {
                    "ordinal": cluster.get("ordinal"),
                    "cluster_id": cluster.get("cluster_id"),
                    "name": cluster.get("name") or None,
                    "cells": stats.cells,
                    "expressing": stats.expressing,
                    "share": stats.share,
                    "mean": pytest.approx(stats.mean, abs=1e-6),
                }
            )
        assert rows == fixture["expected"], shape
