"""scrna_de_reads: a dataset's cell types, DE runs, comparisons and passing genes, read as
Bloom's DE tab reads them."""

from __future__ import annotations

import pytest
from .scrna_de_data import reads

from bloom_mcp.data_access import gene_reads, scrna_de_reads


def _ids(rows):
    return [r["cluster_id"] for r in rows]


# Cell types


def test_cell_types_default_to_the_websites_order_capped_at_10():
    found = scrna_de_reads.cell_types(reads(), 14, [])
    assert _ids(found.chosen) == [f"c{n}" for n in range(12, 2, -1)]
    assert _ids(found.left_out) == ["c2", "c1"]
    assert found.unknown == []


def test_a_cell_type_by_id_or_by_name_in_any_case():
    found = scrna_de_reads.cell_types(reads(), 14, ["c7", "XYLEM"])
    assert _ids(found.chosen) == ["c7", "c3"]


def test_a_name_on_two_clusters_returns_both():
    found = scrna_de_reads.cell_types(reads(), 14, ["cortex"])
    assert _ids(found.chosen) == ["c4", "c1"]


def test_an_unknown_name_is_reported_and_the_others_still_return():
    found = scrna_de_reads.cell_types(reads(), 14, ["c7", "Stele"])
    assert _ids(found.chosen) == ["c7"]
    assert found.unknown == ["Stele"]


def test_named_cell_types_are_capped_at_10_too():
    every = [f"c{n}" for n in range(1, 13)]
    found = scrna_de_reads.cell_types(reads(), 14, every)
    assert len(found.chosen) == 10 and _ids(found.left_out) == ["c2", "c1"]


# Runs and comparisons


def test_complete_runs_newest_first_with_their_method():
    runs = scrna_de_reads.runs(reads(), 14)
    assert [(r["id"], r["method"]) for r in runs] == [
        (2, "pseudobulk-edger"),
        (1, "wilcoxon"),
    ]


def test_one_vs_rest_is_tested_only_from_complete_runs_over_most_cells():
    chosen = scrna_de_reads.one_vs_rest(reads(), [2, 1], ["c4", "c1", "c2"])
    # c2: run 3 failed and run 2's comparison is untested.
    assert {k: v["id"] for k, v in chosen.items()} == {"c4": 100, "c1": 103}


def test_a_newer_untested_genotype_comparison_hides_an_older_tested_one():
    found = scrna_de_reads.genotype(reads(), [2, 1], ["c4"], None, whole=False)
    [comparison] = found
    assert comparison["id"] == 201
    assert not scrna_de_reads.is_tested(comparison)


def test_no_genes_tested_counts_as_untested():
    found = scrna_de_reads.genotype(reads(), [2, 1], ["c1"], None, whole=False)
    by_id = {c["id"]: c for c in found}
    assert set(by_id) == {202, 203}
    assert scrna_de_reads.is_tested(by_id[202])
    assert not scrna_de_reads.is_tested(by_id[203])


def test_a_contrast_keeps_only_its_comparisons():
    found = scrna_de_reads.genotype(
        reads(), [2, 1], ["c4", "c1"], "myb41_vs_Col-0", whole=False
    )
    assert sorted(c["id"] for c in found) == [201, 202]


def test_whole_dataset_comparisons_only_when_asked():
    found = scrna_de_reads.genotype(reads(), [2, 1], ["c1"], None, whole=True)
    assert 204 in {c["id"] for c in found}


def test_comparisons_are_filtered_by_cell_type_in_the_query(monkeypatch):
    # One row per read: a read of every cell type would cut c1's comparison off.
    monkeypatch.setattr(gene_reads, "MAX_ROWS", 1)
    r = reads()
    [comparison] = scrna_de_reads.genotype(r, [1], ["c1"], None, whole=False)
    assert comparison["id"] == 202
    assert "scrna_de" not in r.incomplete


# Passing genes


def _names(rows):
    return [g["gene"] for g in rows]


def test_up_genes_rank_by_fdr_then_gene_name_and_count_in_full():
    genes, count = scrna_de_reads.passing(reads(), 100, "up", 20)
    assert _names(genes) == [
        "BETA", "ALPHA", "MID", "ZETA", "INFGENE", "BIGGENE", "EDGE",
    ]  # fmt: skip
    assert count == 7


def test_the_top_cut_at_a_tie_keeps_the_websites_order():
    # Ids order ZETA (id 1) before ALPHA (id 2); the website orders by name.
    genes, count = scrna_de_reads.passing(reads(), 100, "up", 2)
    assert _names(genes) == ["BETA", "ALPHA"]
    assert count == 7


def test_down_genes_listed_on_their_own():
    genes, count = scrna_de_reads.passing(reads(), 100, "down", 20)
    assert _names(genes) == ["DOWN1", "DOWN2"] and count == 2


def test_a_nan_fold_change_is_never_up_down_or_counted():
    up, n_up = scrna_de_reads.passing(reads(), 100, "up", 50)
    down, n_down = scrna_de_reads.passing(reads(), 100, "down", 50)
    assert "NANGENE" not in _names(up) + _names(down)
    assert (n_up, n_down) == (7, 2)


@pytest.mark.parametrize(
    ("gene", "log2fc", "one_group", "likely"),
    [
        ("INFGENE", None, True, False),
        ("BIGGENE", 25.0, False, True),
        ("EDGE", 20.0, False, False),
        ("BETA", 1.5, False, False),
    ],
)
def test_large_fold_changes(gene, log2fc, one_group, likely):
    genes, _ = scrna_de_reads.passing(reads(), 100, "up", 20)
    row = next(g for g in genes if g["gene"] == gene)
    assert row["log2fc"] == log2fc
    assert row["found_in_one_group_only"] is one_group
    assert row["likely_found_in_one_group_only"] is likely


def test_a_negative_infinite_fold_change_is_down_and_one_group():
    genes, _ = scrna_de_reads.passing(reads(), 100, "down", 20)
    row = next(g for g in genes if g["gene"] == "DOWN2")
    assert row["log2fc"] is None and row["found_in_one_group_only"]


def test_the_cuts_are_the_gene_lookups_own():
    assert scrna_de_reads.DE_FDR_CUT is gene_reads.DE_FDR_CUT
    assert scrna_de_reads.DE_LOG2FC_CUT is gene_reads.DE_LOG2FC_CUT
    assert scrna_de_reads.OFF_SCALE_LOG2FC is gene_reads.OFF_SCALE_LOG2FC


# Stored markers


def test_stored_markers_as_stored_and_none_left_out():
    markers = scrna_de_reads.stored_markers(reads(), 14, ["c4", "c7", "c1"])
    assert set(markers) == {"c4"}
    assert markers["c4"]["n_significant"] == 7
    assert markers["c4"]["top"][0]["gene"] == "BETA"
