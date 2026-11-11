"""gene_lookup: scRNA datasets, markers and genotype comparisons, picked as Bloom's
expression pages pick them."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from gene_lookup_data import (
    TABLES,
    _de,
    _hit,
    _set_hit,
    lookup,
)

from bloom_mcp.data_access import gene_expression, gene_reads
from bloom_mcp.data_access.fake_table_reader import FakeTableReader
from bloom_mcp.sections.bloom_data.gene_lookup import gene_lookup


def test_only_live_datasets_that_measured_the_gene_are_listed(genes_db):
    assert [d.dataset_id for d in lookup("MYB41").expression] == [14]


def test_markers_use_the_newest_run_with_the_comparison_over_most_cells(genes_db):
    [dataset] = lookup("MYB41").expression
    markers = {m.cluster_id: m for m in dataset.markers}
    assert set(markers) == {"c3", "c4"}
    # c3 has no comparison in the newest run, so run 1's; c4 the larger of run 2's two.
    assert markers["c3"].log2fc == 2.0
    assert markers["c4"].log2fc == 1.5
    assert (markers["c3"].cell_type, markers["c3"].label_source) == (
        "Endodermis",
        "nuclei",
    )
    assert (markers["c4"].pct_in_cluster, markers["c4"].pct_elsewhere) == (0.8, 0.1)


def test_a_gene_down_in_a_cluster_is_not_its_marker(genes_db):
    [dataset] = lookup("MYB41").expression
    assert "c5" not in {m.cluster_id for m in dataset.markers}


def test_genotype_comparisons_come_from_the_newest_run_that_has_them(genes_db):
    [dataset] = lookup("MYB41").expression
    [comparison] = dataset.genotype_comparisons
    assert (comparison.group1, comparison.group2, comparison.direction) == (
        "pFACT",
        "Col-0",
        "up",
    )
    assert not comparison.absent_in_one_group


def test_a_construct_naming_the_gene_flags_the_dataset(genes_db):
    [dataset] = lookup("MYB41").expression
    assert dataset.constructs == ["pFACT::MYB41"]


def test_a_gene_missing_from_the_gene_table_still_shows_its_expression(genes_db):
    result = lookup("AT1G01010")
    assert result.genes == []
    assert [d.dataset_id for d in result.expression] == [16]
    assert result.expression[0].kind == "reference"
    assert any("gene table" in n for n in result.notes)


def test_an_infinite_fold_change_is_read_as_absent_from_one_group(genes_db):
    # PostgREST sends Postgres's Infinity as text; c4's marker is de 11, the genotype de 14.
    _set_hit(genes_db, 11, log2fc="Infinity")
    _set_hit(genes_db, 14, log2fc="-Infinity")
    [dataset] = lookup("MYB41").expression
    marker = next(m for m in dataset.markers if m.cluster_id == "c4")
    assert marker.log2fc is None and marker.absent_in_one_group
    [comparison] = dataset.genotype_comparisons
    assert comparison.log2fc is None and comparison.absent_in_one_group
    assert comparison.direction == "down"


def test_a_nan_result_passes_no_cut(genes_db):
    _set_hit(genes_db, 10, fdr="NaN")
    _set_hit(genes_db, 14, log2fc="NaN")
    [dataset] = lookup("MYB41").expression
    assert "c3" not in {m.cluster_id for m in dataset.markers}
    assert dataset.genotype_comparisons == []


def test_a_genotype_comparison_names_its_cluster_and_cell_type(genes_db):
    genes_db.tables["scrna_de"].append(
        _de(18, 2, "genotype", "pFACT", "Col-0", 40, 40, cluster="c3")
    )
    genes_db.tables["scrna_de_genes"].append(_hit(18, -1.0, 0.01))
    [dataset] = lookup("MYB41").expression
    by_cluster = {c.cluster_id: c for c in dataset.genotype_comparisons}
    assert set(by_cluster) == {None, "c3"}
    assert (by_cluster["c3"].cell_type, by_cluster["c3"].direction) == (
        "Endodermis",
        "down",
    )
    assert by_cluster[None].cell_type is None


def test_a_cut_comparison_list_keeps_the_newest_run(genes_db, monkeypatch):
    # Two rows per read: oldest-first would keep run 1's c4 (3.0) and genotype rows only.
    genes_db.tables["scrna_de"].append(
        _de(20, 1, "genotype", "old", "Col-0", 40, 40, cluster="c5")
    )
    monkeypatch.setattr(gene_reads, "MAX_ROWS", 2)
    result = lookup("MYB41")
    [dataset] = result.expression
    assert {m.cluster_id: m.log2fc for m in dataset.markers}["c4"] == 1.5
    whole = [c for c in dataset.genotype_comparisons if c.cluster_id is None]
    assert [c.group1 for c in whole] == ["pFACT"]
    assert any("incomplete" in n and "scrna_de" in n for n in result.notes)


def test_a_genotype_comparison_past_the_fdr_cut_is_left_out(genes_db):
    _set_hit(genes_db, 14, fdr=0.06)
    [dataset] = lookup("MYB41").expression
    assert dataset.genotype_comparisons == []


def test_two_genes_in_one_dataset_keep_their_own_results():
    reads = gene_reads.Reads(
        FakeTableReader(
            {t: [] for t in TABLES}
            | {
                "scrna_datasets": [{"id": 14, "name": "d", "species_id": 1, "kind": "full", "deleted_at": None}],
                "scrna_genes": [
                    {"id": 1, "dataset_id": 14, "gene_name": "AT5G44420"},
                    {"id": 2, "dataset_id": 14, "gene_name": "AT2G42840"},
                ],
            }
        )
    )  # fmt: skip
    found = gene_expression.datasets(reads, ["AT5G44420", "AT2G42840"], [])
    assert {(d["gene_name"], tuple(d["gene_row_ids"])) for d in found} == {
        ("AT5G44420", (1,)),
        ("AT2G42840", (2,)),
    }


def _panel(markers):
    return {m.cluster_id: m.on_cluster_panel for m in markers}


def test_a_saved_top_list_says_whether_the_panel_shows_the_gene(genes_db):
    # c3 and c4 are MYB41's marker clusters; both have a saved top list.
    genes_db.tables["scrna_cluster_stats"] = [
        {"dataset_id": 14, "cluster_id": "c3", "markers": {"top": [{"gene": "at4g28110"}]}},
        {"dataset_id": 14, "cluster_id": "c4", "markers": {"top": [{"gene": "AT1G99999"}]}},
    ]  # fmt: skip
    [dataset] = lookup("MYB41").expression
    assert _panel(dataset.markers) == {"c3": True, "c4": False}


def test_without_a_saved_list_the_panel_is_the_top_of_the_analysis(genes_db):
    # c4's chosen comparison is de 11; push MYB41 out of its top 25 there.
    genes_db.tables["scrna_de_genes"] += [
        {"de_id": 11, "gene_id": 1000 + i, "log2fc": 2.0, "fdr": 1e-6}
        for i in range(gene_expression.TOP_MARKERS)
    ]
    [dataset] = lookup("MYB41").expression
    assert _panel(dataset.markers) == {"c3": True, "c4": False}


def test_the_description_says_the_panel_lists_only_the_top_markers():
    doc = " ".join((gene_lookup.__doc__ or "").split()).lower()
    assert "top 25" in doc and "differential expression" in doc


def test_genotype_comparisons_combine_runs_newest_first(genes_db):
    # Run 1 also compared genotypes within c4; run 2 has only the whole-dataset one.
    genes_db.tables["scrna_de"].append(
        _de(19, 1, "genotype", "pFACT", "Col-0", 40, 40, cluster="c4")
    )
    genes_db.tables["scrna_de_genes"].append(_hit(19, 2.0, 0.001))
    [dataset] = lookup("MYB41").expression
    assert {(c.cluster_id, c.group1) for c in dataset.genotype_comparisons} == {
        (None, "pFACT"),
        ("c4", "pFACT"),
    }


_WEB = Path(__file__).resolve().parents[4] / "web" / "components"


@pytest.mark.skipif(not _WEB.is_dir(), reason="the web app isn't checked out here")
def test_the_cuts_match_the_websites():
    def const(path: str, name: str) -> float:
        match = re.search(rf"const {name} = ([\d.]+);", (_WEB / path).read_text())
        assert match, name
        return float(match.group(1))

    assert gene_reads.MARKER_FDR_CUT == const(
        "expression-lib/cluster-markers.ts", "MARKER_FDR_CUT"
    )
    assert gene_reads.DE_FDR_CUT == const(
        "expression-lib/de-types.ts", "DEFAULT_FDR_CUT"
    )
    assert gene_reads.DE_LOG2FC_CUT == const(
        "expression-lib/de-types.ts", "DEFAULT_LOG2FC_CUT"
    )
    assert gene_reads.OFF_SCALE_LOG2FC == const(
        "expression-differential-analysis.tsx", "OFF_SCALE_LOG2FC"
    )
    assert gene_expression.TOP_MARKERS == const(
        "expression-lib/cluster-markers.ts", "TOP_MARKERS"
    )
