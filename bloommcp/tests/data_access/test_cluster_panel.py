"""cluster_panel: a cell type's markers as the website's cluster panel lists them."""

from __future__ import annotations

import pytest

from bloom_mcp.data_access import cluster_panel

from .scrna_de_data import reads

# Parsing stored markers as parseMarkers does


def test_stored_markers_parse_with_q_as_the_fdr():
    parsed = cluster_panel.parse_markers(
        {"top": [{"gene": "BETA", "log2fc": 1.5, "q": 0.0001, "pct_1": 0.8,
                  "pct_2": 0.1, "symbol": "B1", "source": "wilcoxon"}],
         "n_significant": 7}
    )  # fmt: skip
    assert parsed == {
        "top": [{"gene": "BETA", "symbol": "B1", "log2fc": 1.5, "fdr": 0.0001,
                 "pct_1": 0.8, "pct_2": 0.1, "source": "wilcoxon"}],
        "n_significant": 7,
    }  # fmt: skip


def test_missing_values_take_the_websites_defaults():
    [bare] = cluster_panel.parse_markers({"top": [{"gene": "BARE"}]})["top"]
    assert (bare["log2fc"], bare["fdr"], bare["pct_1"], bare["pct_2"]) == (0, 1, 0, 0)
    assert bare["symbol"] is None and bare["source"] is None


def test_values_of_the_wrong_type_take_the_defaults_too():
    # JS's typeof rejects a boolean as a number and a non-string as text.
    [entry] = cluster_panel.parse_markers(
        {"top": [{"gene": "HUGE", "log2fc": True, "q": False, "pct_1": True,
                  "symbol": 5, "source": ["x"]}]}
    )["top"]  # fmt: skip
    assert (entry["log2fc"], entry["fdr"], entry["pct_1"]) == (0, 1, 0)
    assert entry["symbol"] is None and entry["source"] is None


@pytest.mark.parametrize(
    ("given", "count"), [(12, 12), (12.0, 12), (True, 0), ("7", 0), (None, 0)]
)
def test_any_number_is_a_count(given, count):
    parsed = cluster_panel.parse_markers({"top": [], "n_significant": given})
    assert parsed["n_significant"] == count


def test_entries_without_a_gene_are_dropped():
    parsed = cluster_panel.parse_markers({"top": [{"log2fc": 9.0}, "x", {"gene": 3}]})
    assert parsed["top"] == []


@pytest.mark.parametrize("raw", [None, [], "x", {"top": {}}, {"n_significant": 3}])
def test_markers_that_are_not_a_list_parse_to_none(raw):
    assert cluster_panel.parse_markers(raw) is None


# Which cell types have stored markers


def test_only_a_non_empty_stored_list_counts():
    # c4 has a list; c7's markers are null and c1's list is empty.
    found = cluster_panel.stored(reads(), 14, ["c4", "c7", "c1", "c2"])
    assert list(found) == ["c4"]


# The panel's fallback: the top of the one-vs-rest comparison


def _ids(rows):
    return [r["gene_id"] for r in rows]


def test_the_fallback_ranks_by_fdr_then_log2fc():
    # atMYB41 (13) has the lowest FDR but the smallest log2FC; ZETA (1) and MID (3) tie
    # on FDR, so the larger log2FC comes first.
    rows, more = cluster_panel.fallback(reads(), 103)
    assert _ids(rows) == [13, 1, 3] and not more


def test_the_fallback_cuts_are_strict():
    # WEAK has log2FC 0 and LATE an FDR of exactly 0.05.
    rows, _ = cluster_panel.fallback(reads(), 103)
    assert not {11, 12} & set(_ids(rows))


def test_the_fallback_breaks_ties_by_row_id():
    r = reads()
    r.reader.tables["scrna_de_genes"] += [
        {"id": 99, "de_id": 103, "gene_id": 14, "log2fc": 2.0, "fdr": 0.01},
        {"id": 98, "de_id": 103, "gene_id": 15, "log2fc": 2.0, "fdr": 0.01},
    ]
    rows, _ = cluster_panel.fallback(r, 103)
    assert _ids(rows)[1:4] == [1, 15, 14]


def test_the_fallback_lists_at_most_the_panels_25(monkeypatch):
    monkeypatch.setattr(cluster_panel, "TOP_MARKERS", 1)
    rows, more = cluster_panel.fallback(reads(), 103)
    assert _ids(rows) == [13] and more
