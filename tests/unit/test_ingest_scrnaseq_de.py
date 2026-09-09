"""
Unit tests for `scripts/ingest_scrnaseq_de.py`.

The differential expression panel shows the summary counts and opens the file
the row points at. Nothing in the browser can tell whether the two agree, so
these check that the script recomputes every count from the results and refuses
the row when they disagree — and that the rows it writes are shaped the way the
panel reads them.

The database and storage writes are covered by
tests/integration/test_scrna_ingest_de.py, which has both.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ingest_scrnaseq_de.py"

SUMMARY_HEADER = (
    "celltype\tcontrast\tgroup1\tgroup2\tn_group1\tn_group2\ttested\t"
    "n_genes_tested\tn_FDR_0.05\tn_FDR_0.05_abs_log2FC_0.5\tn_up\tn_down"
)
RESULT_HEADER = (
    "celltype\tcontrast\tgroup1\tgroup2\tn_group1\tn_group2\tgene\t"
    "wilcoxon_score\tlog2FC\tpvalue\tFDR\tpct_nz_group\tpct_expr_group1\t"
    "pct_expr_group2\tsig_FDR_0.05\tsig_FDR_0.05_abs_log2FC_0.5"
)


@pytest.fixture(scope="module")
def de():
    spec = importlib.util.spec_from_file_location("ingest_scrnaseq_de", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ingest_scrnaseq_de"] = module
    spec.loader.exec_module(module)
    return module


def summary_row(celltype="Cortex", contrast="pFACT_vs_Col-0", group1="pFACT",
                group2="Col-0", n1=10, n2=20, tested=True, tested_genes=3,
                fdr=2, fdr_lfc=2, up=1, down=1) -> str:
    return (f"{celltype}\t{contrast}\t{group1}\t{group2}\t{n1}\t{n2}\t"
            f"{tested}\t{tested_genes}\t{fdr}\t{fdr_lfc}\t{up}.0\t{down}.0")


def result_row(gene, celltype="Cortex", contrast="pFACT_vs_Col-0", log2fc=1.0,
               pvalue=0.001, fdr=0.01, pct1=0.5, pct2=0.25,
               sig_fdr=True, sig_lfc=True) -> str:
    return (f"{celltype}\t{contrast}\tpFACT\tCol-0\t10\t20\t{gene}\t1.0\t"
            f"{log2fc}\t{pvalue}\t{fdr}\t1.0\t{pct1}\t{pct2}\t{sig_fdr}\t"
            f"{sig_lfc}")


def write(path: Path, header: str, rows: list[str]) -> Path:
    path.write_text("\n".join([header, *rows]) + "\n")
    return path


def files(tmp_path, summary_rows: list[str], result_rows: list[str]):
    return (write(tmp_path / "summary.tsv", SUMMARY_HEADER, summary_rows),
            write(tmp_path / "results.tsv", RESULT_HEADER, result_rows))


DEFAULT_RESULTS = [
    result_row("AT1G00001.Araport11.447", log2fc=1.5),
    result_row("AT1G00002.Araport11.447", log2fc=-1.5),
    result_row("AT1G00003.Araport11.447", log2fc=0.1, sig_fdr=False,
               sig_lfc=False),
]


# --------------------------------------------------------------------------- #
# Reading the summary
# --------------------------------------------------------------------------- #


def test_a_tested_comparison_carries_its_counts(de, tmp_path):
    s, _ = files(tmp_path, [summary_row()], [])
    entry = de.read_summary(s)[0]
    assert entry["celltype"] == "Cortex"
    assert entry["group1"] == "pFACT" and entry["group2"] == "Col-0"
    assert entry["n_group1"] == 10 and entry["n_group2"] == 20
    assert entry["counts"] == {
        "n_genes_tested": 3, "n_significant_fdr": 2,
        "n_significant_fdr_lfc": 2, "n_up": 1, "n_down": 1,
    }


def test_a_skipped_comparison_keeps_its_group_sizes_and_no_counts(de, tmp_path):
    """The sizes are what explain why it was skipped, so the panel can say so."""
    s, _ = files(tmp_path, [summary_row(tested=False)], [])
    entry = de.read_summary(s)[0]
    assert entry["tested"] is False
    assert entry["counts"] is None
    assert (entry["n_group1"], entry["n_group2"]) == (10, 20)


def test_the_same_comparison_twice_is_refused(de, tmp_path):
    s, _ = files(tmp_path, [summary_row(), summary_row()], [])
    with pytest.raises(de.IngestError, match="names the same comparison twice"):
        de.read_summary(s)


def test_a_group_compared_against_itself_is_refused(de, tmp_path):
    s, _ = files(tmp_path, [summary_row(group1="pFACT", group2="pFACT")], [])
    with pytest.raises(de.IngestError, match="against itself"):
        de.read_summary(s)


def test_a_missing_column_names_what_is_missing(de, tmp_path):
    path = write(tmp_path / "short.tsv", "celltype\tcontrast", ["Cortex\tx"])
    with pytest.raises(de.IngestError, match="has no column"):
        de.read_summary(path)


def test_an_empty_file_is_refused(de, tmp_path):
    path = write(tmp_path / "empty.tsv", SUMMARY_HEADER, [])
    with pytest.raises(de.IngestError, match="holds no rows"):
        de.read_summary(path)


def test_a_missing_file_is_refused(de, tmp_path):
    with pytest.raises(de.IngestError, match="no such file"):
        de.read_summary(tmp_path / "absent.tsv")


def test_a_flag_that_is_not_a_boolean_is_refused(de, tmp_path):
    """Anything unrecognised would otherwise read as false and quietly change
    every count that follows from it."""
    s, _ = files(tmp_path, [summary_row().replace("\tTrue\t", "\tyes\t")], [])
    with pytest.raises(de.IngestError, match="expected True or False"):
        de.read_summary(s)


# --------------------------------------------------------------------------- #
# Reading the results
# --------------------------------------------------------------------------- #


def test_rows_are_shaped_the_way_the_panel_reads_them(de, tmp_path):
    _, r = files(tmp_path, [], [result_row("AT1G00001.Araport11.447",
                                           log2fc=2.0, pvalue=0.004,
                                           fdr=0.04, pct1=0.6, pct2=0.3)])
    row = de.read_results(r)["groups"][("Cortex", "pFACT_vs_Col-0")][0]
    assert row["p_val"] == 0.004
    assert row["avg_log2FC"] == 2.0
    assert row["pct.1"] == 0.6 and row["pct.2"] == 0.3
    assert row["p_val_adj"] == 0.04
    assert row["_row"] == "AT1G00001"


def test_the_annotation_release_is_stripped_from_gene_names(de, tmp_path):
    """The expression matrix names the same gene without it, so leaving it on
    means nothing in this table can be looked up anywhere else."""
    _, r = files(tmp_path, [], [result_row("AT1G00001.Araport11.447")])
    read = de.read_results(r)
    row = read["groups"][("Cortex", "pFACT_vs_Col-0")][0]
    assert row["gene"] == "AT1G00001"
    assert read["suffixed"] == 1


def test_a_gene_with_no_release_suffix_is_left_alone(de, tmp_path):
    """The transgene is named the same way in both files and must not be
    trimmed into something that does not exist."""
    _, r = files(tmp_path, [], [result_row("AT4G28110.Fusion")])
    read = de.read_results(r)
    assert read["groups"][("Cortex", "pFACT_vs_Col-0")][0]["gene"] == \
        "AT4G28110.Fusion"
    assert read["suffixed"] == 0


def test_two_rows_for_one_gene_after_stripping_are_refused(de, tmp_path):
    _, r = files(tmp_path, [], [result_row("AT1G00001.Araport11.447"),
                                result_row("AT1G00001.Araport11.448")])
    with pytest.raises(de.IngestError, match="names the same gene twice"):
        de.read_results(r)


def test_only_the_panel_s_columns_are_written(de, tmp_path):
    """The working flags are recomputed from, not written; a reader of the file
    should not be able to disagree with the row that points at it."""
    _, r = files(tmp_path, [], DEFAULT_RESULTS)
    rows = de.read_results(r)["groups"][("Cortex", "pFACT_vs_Col-0")]
    written = json.loads(de.as_json(rows))
    assert list(written[0]) == ["gene", "p_val", "avg_log2FC", "pct.1",
                                "pct.2", "p_val_adj", "_row"]
    assert not any(k.startswith("_fdr") for k in written[0])


# --------------------------------------------------------------------------- #
# Recomputing the counts, which is the point of the whole step
# --------------------------------------------------------------------------- #


def test_the_counts_are_worked_out_from_the_rows(de, tmp_path):
    _, r = files(tmp_path, [], DEFAULT_RESULTS)
    rows = de.read_results(r)["groups"][("Cortex", "pFACT_vs_Col-0")]
    assert de.recount(rows) == {
        "n_genes_tested": 3, "n_significant_fdr": 2,
        "n_significant_fdr_lfc": 2, "n_up": 1, "n_down": 1,
    }


def test_a_summary_that_matches_the_results_is_accepted(de, tmp_path):
    s, r = files(tmp_path, [summary_row()], DEFAULT_RESULTS)
    de.reconcile(de.read_summary(s), de.read_results(r)["groups"])


@pytest.mark.parametrize("field,value", [
    ("tested_genes", 4), ("fdr", 3), ("fdr_lfc", 1), ("up", 2), ("down", 0),
])
def test_every_count_is_checked_not_just_the_first(de, tmp_path, field, value):
    """Each of the five is compared, so a summary wrong in any one of them is
    refused rather than written and displayed."""
    s, r = files(tmp_path, [summary_row(**{field: value})], DEFAULT_RESULTS)
    with pytest.raises(de.IngestError, match="the summary says"):
        de.reconcile(de.read_summary(s), de.read_results(r)["groups"])


def test_a_comparison_marked_tested_with_no_results_is_refused(de, tmp_path):
    s, r = files(tmp_path, [summary_row()],
                 [result_row("AT1G00001.Araport11.447", celltype="Xylem")])
    with pytest.raises(de.IngestError, match="marked tested but the results"):
        de.reconcile(de.read_summary(s), de.read_results(r)["groups"])


def test_a_comparison_marked_skipped_with_results_is_refused(de, tmp_path):
    s, r = files(tmp_path, [summary_row(tested=False)], DEFAULT_RESULTS)
    with pytest.raises(de.IngestError, match="marked untested but the results"):
        de.reconcile(de.read_summary(s), de.read_results(r)["groups"])


def test_results_for_a_comparison_the_summary_never_mentions_are_refused(de,
                                                                        tmp_path):
    s, r = files(tmp_path, [summary_row()],
                 DEFAULT_RESULTS + [result_row("AT2G00001.Araport11.447",
                                               celltype="Xylem")])
    with pytest.raises(de.IngestError, match="no summary row"):
        de.reconcile(de.read_summary(s), de.read_results(r)["groups"])


def test_a_gene_with_no_fold_change_counts_as_down(de, tmp_path):
    """Up is above zero and everything else is down, which is what makes up plus
    down equal the number significant."""
    _, r = files(tmp_path, [], [result_row("AT1G00001.Araport11.447", log2fc=0.0)])
    counted = de.recount(de.read_results(r)["groups"][("Cortex", "pFACT_vs_Col-0")])
    assert (counted["n_up"], counted["n_down"]) == (0, 1)
    assert counted["n_up"] + counted["n_down"] == counted["n_significant_fdr_lfc"]


# --------------------------------------------------------------------------- #
# Where each comparison's file goes
# --------------------------------------------------------------------------- #


def test_only_tested_comparisons_get_a_file(de, tmp_path):
    s, _ = files(tmp_path, [summary_row(), summary_row(celltype="Xylem",
                                                       tested=False)], [])
    paths = de.check_paths(de.read_summary(s), "MYB41 transgene")
    assert set(paths) == {("Cortex", "pFACT_vs_Col-0")}


def test_a_cell_type_with_punctuation_still_makes_one_path(de, tmp_path):
    s, _ = files(tmp_path, [summary_row(celltype="Cortex (elongation/maturation)")],
                 [])
    paths = de.check_paths(de.read_summary(s), "MYB41 transgene")
    assert paths[("Cortex (elongation/maturation)", "pFACT_vs_Col-0")] == \
        "de/MYB41_transgene/Cortex_elongation_maturation___pFACT_vs_Col-0.json"


def test_two_cell_types_that_would_share_a_file_are_refused(de, tmp_path):
    """Punctuation is flattened to make a path, so two names that differ only in
    punctuation collide -- and the second written would replace the first."""
    s, _ = files(tmp_path, [summary_row(celltype="Cortex maturation"),
                            summary_row(celltype="Cortex/maturation")], [])
    with pytest.raises(de.IngestError, match="would share one object"):
        de.check_paths(de.read_summary(s), "d")


def test_the_real_cell_types_do_not_collide(de, tmp_path):
    """Two of them are 'Cortex (maturation)' and 'Cortex maturation'."""
    rows = [summary_row(celltype=t) for t in
            ("Cortex", "Cortex (maturation)", "Cortex maturation",
             "Cortex (elongation/maturation)", "Cortex/Atrichoblast (maturation)")]
    s, _ = files(tmp_path, rows, [])
    assert len(de.check_paths(de.read_summary(s), "d")) == 5


# --------------------------------------------------------------------------- #
# The command line
# --------------------------------------------------------------------------- #


def test_a_dry_run_needs_no_credentials_and_writes_nothing(de, tmp_path,
                                                           monkeypatch, capsys):
    for name in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        monkeypatch.delenv(name, raising=False)
    s, r = files(tmp_path, [summary_row(), summary_row(celltype="Xylem",
                                                       tested=False)],
                 DEFAULT_RESULTS)
    code = de.main(["--results", str(r), "--summary", str(s),
                    "--dataset-name", "d", "--species-id", "1", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "2 comparisons, 1 tested, 1 skipped" in out
    assert "1 objects would be written" in out


def test_a_dry_run_still_refuses_a_summary_that_disagrees(de, tmp_path, capsys):
    s, r = files(tmp_path, [summary_row(up=2)], DEFAULT_RESULTS)
    code = de.main(["--results", str(r), "--summary", str(s),
                    "--dataset-name", "d", "--species-id", "1", "--dry-run"])
    assert code == 1
    assert "the summary says n_up is 2" in capsys.readouterr().err


def test_writing_needs_all_three_credentials(de, tmp_path, monkeypatch, capsys):
    s, r = files(tmp_path, [summary_row()], DEFAULT_RESULTS)
    for name in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", "set")
    code = de.main(["--results", str(r), "--summary", str(s),
                    "--dataset-name", "d", "--species-id", "1"])
    assert code == 1
    assert "are all" in capsys.readouterr().err
