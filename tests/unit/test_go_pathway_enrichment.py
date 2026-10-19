"""Tests for argo/scrna/analysis: GO and KEGG pathway over-representation of a dataset's
differential expression, and the enrichment file. No scanpy needed."""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "argo/scrna/analysis"))

from bloom_scrna_analysis import enrichment  # noqa: E402
from bloom_scrna_analysis.annotation import Annotation  # noqa: E402
from bloom_scrna_analysis.steps import StepError  # noqa: E402

PARAMS = enrichment.Params(top_n=300, fdr_max=0.05, lfc_min=0.25, min_term=5, max_term=500,
                           min_list=3)


def de(*rows):
    """DE rows: (gene, log2fc, fdr)."""
    return [{"gene": g, "log2fc": lfc, "fdr": fdr} for g, lfc, fdr in rows]


# --------------------------------------------------------------------------- #
# Gene lists
# --------------------------------------------------------------------------- #


def test_lists_take_significant_genes_past_the_fold_change_by_direction():
    lists = enrichment.gene_lists(
        de(("A", 2.0, 0.01), ("B", -1.0, 0.01), ("C", 0.1, 0.01), ("D", 3.0, 0.2),
           ("E", None, 0.01)),
        PARAMS,
    )
    assert lists == {"up": ["A"], "down": ["B"]}


def test_lists_keep_the_strongest_genes_first_up_to_the_limit():
    rows = de(*[(f"G{i}", 1.0 + i, 0.01) for i in range(5)])
    params = enrichment.Params(**{**PARAMS.__dict__, "top_n": 2})
    assert enrichment.gene_lists(rows, params)["up"] == ["G4", "G3"]


def test_equal_fold_changes_are_ordered_by_gene_so_runs_repeat():
    rows = de(("B", 1.0, 0.01), ("A", 1.0, 0.01), ("C", 1.0, 0.01))
    params = enrichment.Params(**{**PARAMS.__dict__, "top_n": 2})
    assert enrichment.gene_lists(rows, params)["up"] == ["A", "B"]


# --------------------------------------------------------------------------- #
# The statistics
# --------------------------------------------------------------------------- #


def test_the_p_value_is_the_hypergeometric_upper_tail():
    # 3 of 4 list genes in a 5-gene term, background 20: (C(5,3)C(15,1) + C(5,4)) / C(20,4).
    assert enrichment.p_value(k=3, background=20, term=5, listed=4) == pytest.approx(155 / 4845)


def test_benjamini_hochberg_matches_rs_p_adjust():
    # p.adjust(c(0.01, 0.04, 0.03, 0.005), "BH") in R.
    assert enrichment.bh([0.01, 0.04, 0.03, 0.005]) == pytest.approx([0.02, 0.04, 0.04, 0.02])
    assert enrichment.bh([]) == []


# --------------------------------------------------------------------------- #
# A run over an annotation
# --------------------------------------------------------------------------- #

BACKGROUND = [f"G{i:02d}" for i in range(20)]
# The root process on every gene; response to light and its parent on G00..G05; a function on
# G10..G15; pathway map00010 on G00..G05 and map00020 on G06..G15, so 16 genes have a pathway.
TERMS = {
    "GO:0008150": {"kind": "GO_BP", "name": "biological_process", "parents": []},
    "GO:0009416": {"kind": "GO_BP", "name": "response to light stimulus",
                   "parents": ["GO:0050896"]},
    "GO:0050896": {"kind": "GO_BP", "name": "response to stimulus", "parents": ["GO:0008150"]},
    "GO:0003674": {"kind": "GO_MF", "name": "molecular_function", "parents": []},
    "map00010": {"kind": "KEGG", "name": "Glycolysis / Gluconeogenesis"},
    "map00020": {"kind": "KEGG", "name": "Citrate cycle (TCA cycle)"},
}


def genes():
    out = {}
    for i, g in enumerate(BACKGROUND):
        terms = ["GO:0008150"]
        if i < 6:
            terms += ["GO:0009416", "GO:0050896", "map00010"]
        elif i < 16:
            terms += ["map00020"]
        if 10 <= i < 16:
            terms += ["GO:0003674"]
        out[g] = {"name": None, "description": None, "terms": terms}
    return out


ANNOTATION = Annotation(terms=TERMS, genes=genes())
COMPARISON = {"comparison": "0_vs_rest",
              "genes": de(*[(f"G{i:02d}", 2.0, 0.001) for i in range(5)], ("G19", -2.0, 0.001))}


def run(**kw):
    return enrichment.run(ANNOTATION, dataset_genes=BACKGROUND + ["UNANNOTATED"],
                          comparisons=[COMPARISON], params=kw.get("params", PARAMS))


def test_an_over_represented_term_is_reported_with_its_counts_and_genes():
    result = run()
    light = [r for r in result.results if r["term"] == "GO:0009416"]
    assert len(light) == 1
    row = light[0]
    assert (row["comparison"], row["direction"], row["kind"]) == ("0_vs_rest", "up", "GO_BP")
    assert row["name"] == "response to light stimulus"
    assert (row["list_genes_in_term"], row["list_size"], row["term_size"],
            row["background_size"]) == (5, 5, 6, 20)
    assert row["genes"] == ["G00", "G01", "G02", "G03", "G04"]
    assert row["p"] == pytest.approx(enrichment.p_value(k=5, background=20, term=6, listed=5))
    assert row["fdr"] >= row["p"]


def test_terms_covering_the_same_genes_keep_only_the_most_specific():
    terms = {r["term"] for r in run().results}
    assert "GO:0050896" not in terms, "same genes as its child, response to light"


def test_each_kind_is_tested_against_its_own_background():
    kegg = [r for r in run().results if r["kind"] == "KEGG"]
    assert [r["term"] for r in kegg] == ["map00010"]
    assert kegg[0]["background_size"] == 16, "only genes with a pathway"


def test_terms_outside_the_size_limits_are_not_tested():
    small = enrichment.Params(**{**PARAMS.__dict__, "max_term": 5})
    assert "GO:0009416" not in {r["term"] for r in run(params=small).results}, "6 genes > 5"
    large = enrichment.Params(**{**PARAMS.__dict__, "min_term": 7})
    assert "GO:0009416" not in {r["term"] for r in run(params=large).results}, "6 genes < 7"


def test_a_list_with_too_few_genes_in_a_term_is_not_tested():
    assert not [r for r in run().results if r["direction"] == "down"], "one down gene < 3"


def test_only_significant_terms_are_kept():
    result = run()
    assert result.results and all(r["fdr"] < PARAMS.fdr_max for r in result.results)


def test_the_gene_counts_are_the_dataset_and_its_annotated_genes():
    result = run()
    assert (result.background_genes, result.annotated_genes) == (21, 20)


def test_a_comparison_with_genes_outside_the_dataset_is_refused():
    bad = {"comparison": "1_vs_rest", "genes": de(("ELSEWHERE", 2.0, 0.001))}
    with pytest.raises(StepError, match="ELSEWHERE"):
        enrichment.run(ANNOTATION, dataset_genes=BACKGROUND, comparisons=[bad], params=PARAMS)


def test_a_dataset_sharing_no_genes_with_the_annotation_is_refused():
    with pytest.raises(StepError, match="no gene"):
        enrichment.run(ANNOTATION, dataset_genes=["X1", "X2"], comparisons=[], params=PARAMS)


# --------------------------------------------------------------------------- #
# The file
# --------------------------------------------------------------------------- #


def test_the_file_records_what_was_tested_and_how(tmp_path):
    path = tmp_path / "run.json.gz"
    enrichment.write(path, run(), dataset_id=25, de_run_id=7,
                     annotation="sbicolor.btx623.hpiv3.v1/eggnog-b1", params=PARAMS)
    with gzip.open(path, "rt") as fh:
        doc = json.load(fh)
    assert doc["format"] == enrichment.FORMAT == "bloom-enrichment/1"
    assert (doc["dataset_id"], doc["de_run_id"]) == (25, 7)
    assert doc["annotation"] == "sbicolor.btx623.hpiv3.v1/eggnog-b1"
    assert doc["params"] == {"top_n": 300, "fdr_max": 0.05, "lfc_min": 0.25, "min_term": 5,
                             "max_term": 500, "min_list": 3}
    assert (doc["background_genes"], doc["annotated_genes"]) == (21, 20)
    assert doc["results"] == run().results


def test_results_are_ordered_by_comparison_direction_kind_then_fdr():
    rows = run().results
    keys = [(r["comparison"], r["direction"], r["kind"], r["fdr"], r["term"]) for r in rows]
    assert keys == sorted(keys)
