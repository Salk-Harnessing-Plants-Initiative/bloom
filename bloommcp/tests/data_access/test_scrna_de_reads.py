"""scrna_de_reads: a dataset's cell types, DE runs and comparisons, the cluster panel's
markers, and the DE tab's passing genes."""

from __future__ import annotations

import pytest

from bloom_mcp.data_access import de_choice, gene_reads, scrna_de_reads

from .scrna_de_data import reads


def _ids(rows):
    return [r["cluster_id"] for r in rows]


def _names(rows):
    return [g["gene"] for g in rows]


# Dataset


def test_a_live_dataset_is_found():
    assert scrna_de_reads.live_dataset(reads(), 14)["name"] == "MYB41 roots"


@pytest.mark.parametrize("dataset_id", [15, 99])
def test_a_deleted_or_missing_dataset_is_not(dataset_id):
    assert scrna_de_reads.live_dataset(reads(), dataset_id) is None


# Cell types


def test_cell_types_default_to_the_websites_order_capped_at_10():
    found = scrna_de_reads.cell_types(reads(), 14, [])
    assert _ids(found.chosen) == [f"c{n}" for n in range(12, 2, -1)]
    assert _ids(found.left_out) == ["c2", "c1"]
    assert found.unknown == []


def test_a_cell_type_by_id_or_name_in_any_case_with_spaces():
    found = scrna_de_reads.cell_types(reads(), 14, ["C7", " XYLEM "])
    assert _ids(found.chosen) == ["c7", "c3"]


def test_a_name_on_two_clusters_returns_both():
    found = scrna_de_reads.cell_types(reads(), 14, ["cortex"])
    assert _ids(found.chosen) == ["c4", "c1"]


def test_unknown_names_are_reported_once_and_blanks_dropped():
    found = scrna_de_reads.cell_types(reads(), 14, ["c7", "Stele", "stele", " ", ""])
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


def test_no_complete_runs():
    assert scrna_de_reads.runs(reads(), 15) == []


def test_one_vs_rest_is_tested_only_from_complete_runs_over_most_cells():
    chosen = scrna_de_reads.one_vs_rest(reads(), [2, 1], ["c4", "c1", "c2"])
    # c2: run 3 failed and run 2's comparison is untested.
    assert {k: v["id"] for k, v in chosen.items()} == {"c4": 100, "c1": 103}


def test_an_untested_newer_one_vs_rest_falls_back_to_the_older_tested_one():
    chosen = scrna_de_reads.one_vs_rest(reads(), [2, 1], ["c3"])
    assert chosen["c3"]["id"] == 106


def test_a_one_vs_rest_row_comparing_another_cluster_is_not_chosen():
    assert scrna_de_reads.one_vs_rest(reads(), [2, 1], ["c5"]) == {}


def test_no_runs_or_no_cell_types_choose_nothing():
    assert scrna_de_reads.one_vs_rest(reads(), [], ["c4"]) == {}
    assert scrna_de_reads.genotype(reads(), [], ["c4"], None, whole=True) == []
    assert scrna_de_reads.genotype(reads(), [2, 1], [], None, whole=False) == []


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
    asked = scrna_de_reads.genotype(reads(), [2, 1], [], None, whole=True)
    assert [c["id"] for c in asked] == [204]


def test_comparisons_are_filtered_by_cell_type_in_the_query(monkeypatch):
    # One row per read: a read of every cell type would cut c1's comparison off.
    monkeypatch.setattr(gene_reads, "MAX_ROWS", 1)
    r = reads()
    [comparison] = scrna_de_reads.genotype(r, [1], ["c1"], None, whole=False)
    assert comparison["id"] == 202
    assert "scrna_de" not in r.incomplete


def test_a_cut_comparison_read_is_marked_incomplete(monkeypatch):
    monkeypatch.setattr(gene_reads, "MAX_ROWS", 1)
    r = reads()
    scrna_de_reads.genotype(r, [2, 1], ["c1"], None, whole=False)
    assert "scrna_de" in r.incomplete


# The cluster panel's markers


def test_stored_markers_are_the_panels_cleaned_as_the_panel_cleans_them():
    markers = scrna_de_reads.panel_markers(reads(), 14, ["c4"], [2, 1])
    c4 = markers["c4"]
    assert (c4["from"], c4["n_significant"]) == ("stored", 7)
    # The entry without a gene is dropped; q is the FDR.
    [beta] = c4["top"]
    assert (beta["gene"], beta["fdr"], beta["log2fc"]) == ("BETA", 0.0001, 1.5)


def test_without_stored_markers_the_panel_uses_the_one_vs_rest_comparison():
    # c1's stored top is empty: the panel falls back to comparison 103.
    c1 = scrna_de_reads.panel_markers(reads(), 14, ["c1"], [2, 1])["c1"]
    assert c1["from"] == "comparison"
    # log2FC > 0 (MID at 0.3 counts), FDR < 0.05, ranked by FDR then log2FC.
    assert _names(c1["top"]) == ["ZETA", "MID"]
    assert c1["n_significant"] == 2


def test_no_stored_markers_and_no_comparison_means_no_markers():
    assert scrna_de_reads.panel_markers(reads(), 14, ["c7", "c2"], [2, 1]) == {}


def test_the_panel_fallback_lists_at_most_the_panels_25(monkeypatch):
    monkeypatch.setattr(de_choice, "TOP_MARKERS", 1)
    c1 = scrna_de_reads.panel_markers(reads(), 14, ["c1"], [2, 1])["c1"]
    assert _names(c1["top"]) == ["ZETA"] and c1["n_significant"] == 2


# The DE tab's passing genes


def test_up_genes_rank_by_fdr_then_gene_name_and_count_in_full():
    genes, count = scrna_de_reads.de_genes(reads(), 100, 20)["up"]
    assert _names(genes) == [
        "BETA", "ALPHA", "MID", "ZETA", "INFGENE", "BIGGENE", "EDGE",
    ]  # fmt: skip
    assert count == 7


def test_the_top_cut_at_a_tie_keeps_the_websites_order():
    # Ids order ZETA (id 1) before ALPHA (id 2); the website orders by name.
    genes, count = scrna_de_reads.de_genes(reads(), 100, 2)["up"]
    assert _names(genes) == ["BETA", "ALPHA"]
    assert count == 7


def test_names_compare_ignoring_case_first():
    # Codepoints put ATZ1 first; the website's localeCompare puts atMYB41 first.
    genes, _ = scrna_de_reads.de_genes(reads(), 204, 1)["up"]
    assert _names(genes) == ["atMYB41"]


def test_a_tie_never_brings_in_the_other_direction():
    # ALPHA is down at the same FDR as MID and ZETA, and sorts first by name.
    genes, count = scrna_de_reads.de_genes(reads(), 202, 1)["up"]
    assert _names(genes) == ["MID"] and count == 2


def test_genes_on_the_cuts_do_not_pass():
    # BETA has log2FC exactly 0.5 and LATE an FDR of exactly 0.05.
    found = scrna_de_reads.de_genes(reads(), 202, 50)
    assert _names(found["up"][0]) == ["MID", "ZETA"]
    assert _names(found["down"][0]) == ["ALPHA", "DOWN1"]


def test_down_genes_listed_on_their_own():
    genes, count = scrna_de_reads.de_genes(reads(), 100, 20)["down"]
    assert _names(genes) == ["DOWN1", "DOWN2"] and count == 2


def test_a_nan_fold_change_is_never_up_down_or_counted():
    found = scrna_de_reads.de_genes(reads(), 100, 50)
    (up, n_up), (down, n_down) = found["up"], found["down"]
    assert "NANGENE" not in _names(up) + _names(down)
    assert (n_up, n_down) == (7, 2)


@pytest.mark.parametrize("top", [0, -3])
def test_no_genes_asked_for_lists_none_but_still_counts(top):
    genes, count = scrna_de_reads.de_genes(reads(), 100, top)["up"]
    assert genes == [] and count == 7


def test_top_is_capped_at_max_rows(monkeypatch):
    monkeypatch.setattr(gene_reads, "MAX_ROWS", 3)
    genes, _ = scrna_de_reads.de_genes(reads(), 100, 1000)["up"]
    assert len(genes) == 3


def test_an_unknown_count_is_none_not_zero():
    r = reads()
    r.reader.count = lambda *a, **k: None
    genes, count = scrna_de_reads.de_genes(r, 100, 2)["up"]
    assert len(genes) == 2 and count is None


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
    genes, _ = scrna_de_reads.de_genes(reads(), 100, 20)["up"]
    row = next(g for g in genes if g["gene"] == gene)
    assert row["log2fc"] == log2fc
    assert row["found_in_one_group_only"] is one_group
    assert row["likely_found_in_one_group_only"] is likely


def test_a_negative_infinite_fold_change_is_down_and_one_group():
    genes, _ = scrna_de_reads.de_genes(reads(), 100, 20)["down"]
    row = next(g for g in genes if g["gene"] == "DOWN2")
    assert row["log2fc"] is None and row["found_in_one_group_only"]
