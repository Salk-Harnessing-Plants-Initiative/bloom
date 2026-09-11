"""
Unit tests for `scripts/ingest_scrnaseq_de.py`.

Reading, checking and fingerprinting the two export files, and the command line.
The write path is in test_ingest_scrnaseq_de_load.py and, through the real API, in
tests/integration/test_scrna_ingest_de.py.
"""

from __future__ import annotations

import importlib.util
import math
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
KEY = ("Cortex", "pFACT_vs_Col-0")


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


def argv(summary: Path, results: Path, *extra: str) -> list[str]:
    return ["--results", str(results), "--summary", str(summary),
            "--dataset-name", "d", "--species-id", "1",
            "--method", "seurat-wilcoxon", *extra]


def one_result(de, tmp_path, **kwargs) -> dict:
    _, r = files(tmp_path, [], [result_row("AT1G00001", **kwargs)])
    return de.read_results(r)["groups"][KEY][0]


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
    assert (entry["celltype"], entry["group1"], entry["group2"]) == \
        ("Cortex", "pFACT", "Col-0")
    assert (entry["n_group1"], entry["n_group2"]) == (10, 20)
    assert entry["counts"] == {
        "n_genes_tested": 3, "n_significant_fdr": 2,
        "n_significant_fdr_lfc": 2, "n_up": 1, "n_down": 1,
    }


def test_a_skipped_comparison_keeps_its_group_sizes_and_no_counts(de, tmp_path):
    """The sizes are what explain why it was skipped."""
    s, _ = files(tmp_path, [summary_row(tested=False)], [])
    entry = de.read_summary(s)[0]
    assert entry["tested"] is False and entry["counts"] is None
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
    """Anything unrecognised would otherwise read as false."""
    s, _ = files(tmp_path, [summary_row().replace("\tTrue\t", "\tyes\t")], [])
    with pytest.raises(de.IngestError, match="expected True or False"):
        de.read_summary(s)


# --------------------------------------------------------------------------- #
# Reading the results
# --------------------------------------------------------------------------- #


def test_a_row_carries_what_the_gene_table_stores(de, tmp_path):
    row = one_result(de, tmp_path, log2fc=2.0, pvalue=0.004, fdr=0.04,
                     pct1=0.6, pct2=0.3)
    assert {k: row[k] for k in ("gene", "log2fc", "pvalue", "fdr", "pct_1",
                                "pct_2")} == {
        "gene": "AT1G00001", "log2fc": 2.0, "pvalue": 0.004, "fdr": 0.04,
        "pct_1": 0.6, "pct_2": 0.3,
    }


def test_the_annotation_release_is_stripped_from_gene_names(de, tmp_path):
    _, r = files(tmp_path, [], [result_row("AT1G00001.Araport11.447")])
    read = de.read_results(r)
    assert read["groups"][KEY][0]["gene"] == "AT1G00001"
    assert read["suffixed"] == 1


def test_a_gene_with_no_release_suffix_is_left_alone(de, tmp_path):
    """The transgene is named the same way in both files."""
    _, r = files(tmp_path, [], [result_row("AT4G28110.Fusion")])
    read = de.read_results(r)
    assert read["groups"][KEY][0]["gene"] == "AT4G28110.Fusion"
    assert read["suffixed"] == 0


def test_two_rows_for_one_gene_after_stripping_are_refused(de, tmp_path):
    _, r = files(tmp_path, [], [result_row("AT1G00001.Araport11.447"),
                                result_row("AT1G00001.Araport11.448")])
    with pytest.raises(de.IngestError, match="names the same gene twice"):
        de.read_results(r)


@pytest.mark.parametrize("value", ["NaN", "nan", "NA"])
def test_a_fold_change_that_could_not_be_computed_is_stored_as_none(
        de, tmp_path, value):
    """The database refuses NaN; no fold change is what it means."""
    assert one_result(de, tmp_path, log2fc=value)["log2fc"] is None


@pytest.mark.parametrize("value,expected", [("Inf", math.inf),
                                            ("-Inf", -math.inf)])
def test_an_infinite_fold_change_is_kept(de, tmp_path, value, expected):
    """A gene absent from one group has an unbounded ratio: a measurement."""
    assert one_result(de, tmp_path, log2fc=value)["log2fc"] == expected


@pytest.mark.parametrize("field,value,message", [
    ("pvalue", "NaN", "not a finite number"),
    ("fdr", "Inf", "not a finite number"),
    ("pvalue", "NA", "which is not a number"),
    ("log2fc", "up", "which is not a number"),
    ("pvalue", "1.5", "outside 0 to 1"),
    ("pct1", "45.0", "looks like a percentage"),
    ("pct2", "-0.1", "outside 0 to 1"),
])
def test_a_value_the_gene_table_cannot_hold_is_refused(de, tmp_path, field,
                                                       value, message):
    with pytest.raises(de.IngestError, match=message):
        one_result(de, tmp_path, **{field: value})


@pytest.mark.parametrize("field,value,column,stored", [
    ("pct1", "1.0000000000000002", "pct_1", 1.0),
    ("pct2", "-1e-17", "pct_2", 0.0),
    ("fdr", "1.0000000000000002", "fdr", 1.0),
])
def test_rounding_just_past_0_or_1_is_read_as_the_bound(de, tmp_path, field, value,
                                                      column, stored):
    """The real export writes 1.0000000000000002 for a gene in every cell: float
    noise, not a percentage."""
    assert one_result(de, tmp_path, **{field: value})[column] == stored


def test_a_value_past_the_rounding_margin_is_still_refused(de, tmp_path):
    with pytest.raises(de.IngestError, match="outside 0 to 1"):
        one_result(de, tmp_path, pct1="1.000001")


def test_an_fdr_below_its_own_p_value_is_refused(de, tmp_path):
    with pytest.raises(de.IngestError, match="below its own p-value"):
        one_result(de, tmp_path, pvalue=0.05, fdr=0.01)


@pytest.mark.parametrize("pvalue,fdr", [(0.0, 0.0), (1.0, 1.0), (0.2, 0.2)])
def test_legitimate_edge_values_are_accepted(de, tmp_path, pvalue, fdr):
    """A p-value that underflowed to 0, an FDR capped at 1, and equality."""
    row = one_result(de, tmp_path, pvalue=pvalue, fdr=fdr)
    assert (row["pvalue"], row["fdr"]) == (pvalue, fdr)


def test_a_refused_value_names_the_gene_and_the_column(de, tmp_path):
    """One bad cell in hundreds of thousands of rows is only actionable if the
    refusal says which one."""
    _, r = files(tmp_path, [], [result_row("AT1G00001"),
                                result_row("AT1G00002", celltype="Xylem",
                                           pvalue="NaN")])
    with pytest.raises(de.IngestError,
                       match=r"Xylem / pFACT_vs_Col-0, gene AT1G00002: pvalue"):
        de.read_results(r)


def test_a_truncated_last_row_is_refused(de, tmp_path):
    """DictReader fills a short row's missing columns with None."""
    r = tmp_path / "results.tsv"
    r.write_text(RESULT_HEADER + "\n" + result_row("AT1G00001") + "\n"
                 + "Cortex\tpFACT_vs_Col-0\tpFACT\tCol-0\t10\t20\tAT1G00002\n")
    with pytest.raises(de.IngestError, match="is not a number"):
        de.read_results(r)


# --------------------------------------------------------------------------- #
# Checking the summary against the results
# --------------------------------------------------------------------------- #


def test_the_counts_are_worked_out_from_the_rows(de, tmp_path):
    _, r = files(tmp_path, [], DEFAULT_RESULTS)
    assert de.recount(de.read_results(r)["groups"][KEY]) == {
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
    s, r = files(tmp_path, [summary_row(**{field: value})], DEFAULT_RESULTS)
    with pytest.raises(de.IngestError, match="the summary says"):
        de.reconcile(de.read_summary(s), de.read_results(r)["groups"])


def test_a_comparison_marked_tested_with_no_results_is_refused(de, tmp_path):
    s, r = files(tmp_path, [summary_row()],
                 [result_row("AT1G00001", celltype="Xylem")])
    with pytest.raises(de.IngestError, match="marked tested but the results"):
        de.reconcile(de.read_summary(s), de.read_results(r)["groups"])


def test_a_comparison_marked_skipped_with_results_is_refused(de, tmp_path):
    s, r = files(tmp_path, [summary_row(tested=False)], DEFAULT_RESULTS)
    with pytest.raises(de.IngestError, match="marked untested but the results"):
        de.reconcile(de.read_summary(s), de.read_results(r)["groups"])


def test_results_for_a_comparison_the_summary_never_mentions_are_refused(
        de, tmp_path):
    s, r = files(tmp_path, [summary_row()],
                 DEFAULT_RESULTS + [result_row("AT2G00001", celltype="Xylem")])
    with pytest.raises(de.IngestError, match="no summary row"):
        de.reconcile(de.read_summary(s), de.read_results(r)["groups"])


@pytest.mark.parametrize("log2fc", [0.0, "NaN"])
def test_a_significant_gene_without_a_positive_fold_change_counts_as_down(
        de, tmp_path, log2fc):
    """Up is above zero and everything else is down, so up plus down is the
    number significant."""
    counted = de.recount([one_result(de, tmp_path, log2fc=log2fc)])
    assert (counted["n_up"], counted["n_down"]) == (0, 1)


# --------------------------------------------------------------------------- #
# Identifying what was loaded
# --------------------------------------------------------------------------- #


def test_the_same_files_give_the_same_fingerprint_wherever_they_are(de, tmp_path):
    s, r = files(tmp_path, [summary_row()], DEFAULT_RESULTS)
    moved = tmp_path / "moved"
    moved.mkdir()
    s2, r2 = moved / "a.tsv", moved / "b.tsv"
    s2.write_bytes(s.read_bytes())
    r2.write_bytes(r.read_bytes())
    assert de.fingerprint(s, r) == de.fingerprint(s2, r2)


def test_a_changed_results_file_changes_the_fingerprint(de, tmp_path):
    s, r = files(tmp_path, [summary_row()], DEFAULT_RESULTS)
    params, before = de.fingerprint(s, r)
    assert set(params) == {"summary_sha256", "results_sha256"}
    r.write_text(r.read_text().replace("0.001", "0.002", 1))
    assert de.fingerprint(s, r)[1] != before


# --------------------------------------------------------------------------- #
# The command line
# --------------------------------------------------------------------------- #


def test_a_dry_run_needs_no_account_and_says_what_it_would_write(
        de, tmp_path, monkeypatch, capsys):
    def refuse(*a, **k):
        raise AssertionError("a dry run must not reach the site")
    monkeypatch.setattr(de.ingest_api, "resolve_api", refuse)
    monkeypatch.setattr(de.ingest_api, "sign_in", refuse)
    monkeypatch.delenv("BLOOM_PASSWORD", raising=False)
    s, r = files(tmp_path, [summary_row(), summary_row(celltype="Xylem",
                                                       tested=False)],
                 DEFAULT_RESULTS)
    assert de.main(argv(s, r, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "2 comparisons: 1 tested, 1 skipped" in out
    assert "3 gene results, 0 with no fold change" in out
    assert "would write one analysis of 2 comparisons and 3 gene rows" in out
    assert "dry run — nothing written" in out


def test_a_dry_run_still_refuses_a_summary_that_disagrees(de, tmp_path, capsys):
    s, r = files(tmp_path, [summary_row(up=2)], DEFAULT_RESULTS)
    assert de.main(argv(s, r, "--dry-run")) == 1
    assert "the summary says n_up is 2" in capsys.readouterr().err


def test_the_method_has_to_be_named(de, tmp_path):
    """It is recorded on the analysis, so it cannot be left to a default."""
    s, r = files(tmp_path, [summary_row()], DEFAULT_RESULTS)
    with pytest.raises(SystemExit) as exc:
        de.main(argv(s, r)[:-2])
    assert exc.value.code == 2


def _signed_in(de, monkeypatch):
    api = de.ingest_api
    monkeypatch.setattr(api, "resolve_api", lambda server, url, key, **_: ("http://x/api", "k"))
    monkeypatch.setattr(api, "sign_in", lambda url, key, email, password, **_: api.Session(
        lambda: (None, "bloom_writer", "u1"), None, "bloom_writer", "u1"))
    monkeypatch.setenv("BLOOM_PASSWORD", "pw")
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_writing_needs_an_email(de, tmp_path, monkeypatch, capsys):
    _signed_in(de, monkeypatch)
    s, r = files(tmp_path, [summary_row()], DEFAULT_RESULTS)
    assert de.main(argv(s, r, "--server", "https://x")) == 1
    assert "--email" in capsys.readouterr().err


@pytest.mark.parametrize("result,said", [
    ((5, 1, 3, "loaded"), "wrote analysis 5: 1 comparisons and 3 gene rows"),
    ((5, 0, 2, "resumed"), "resumed analysis 5: 0 comparisons and 2 gene rows"),
    ((5, 0, 0, "already loaded"), "already loaded for dataset d as analysis 5"),
])
def test_main_hands_load_the_analysis_and_says_what_happened(
        de, tmp_path, monkeypatch, capsys, result, said):
    _signed_in(de, monkeypatch)
    seen = {}

    def recorder(writer, name, species_id, method, params, params_hash, summary,
                 groups):
        seen.update(name=name, method=method, params=params, hash=params_hash,
                    comparisons=len(summary))
        return result
    monkeypatch.setattr(de, "load", recorder)
    s, r = files(tmp_path, [summary_row()], DEFAULT_RESULTS)
    code = de.main(argv(s, r, "--server", "https://x", "--email", "me@salk.edu"))
    assert code == 0 and said in capsys.readouterr().out
    params, params_hash = de.fingerprint(s, r)
    assert seen == {"name": "d", "method": "seurat-wilcoxon", "params": params,
                    "hash": params_hash, "comparisons": 1}


def test_the_wait_is_checked_before_signing_in(de, tmp_path, monkeypatch, capsys):
    _signed_in(de, monkeypatch)
    def no_sign_in(*a, **k):
        raise AssertionError("signed in during the wait")
    monkeypatch.setattr(de.ingest_api, "sign_in", no_sign_in)
    s, r = files(tmp_path, [summary_row()], DEFAULT_RESULTS)
    de.ingest_api.Marker(de.ingest_api.marker_path(r, "d")).record("insert gene rows")
    assert de.main(argv(s, r, "--server", "https://x", "--email", "me@salk.edu")) == 1
    assert "seconds" in capsys.readouterr().err


def _notes(tmp_path, text: str) -> Path:
    path = tmp_path / "notes.json"
    path.write_text(text)
    return path


def test_notes_are_stored_with_the_analysis_without_changing_its_fingerprint(
        de, tmp_path, monkeypatch, capsys):
    """The fingerprint is what a re-run finds the analysis by, so it stays the
    files' own."""
    _signed_in(de, monkeypatch)
    seen = {}

    def recorder(writer, name, species_id, method, params, params_hash, summary,
                 groups):
        seen.update(params=params, hash=params_hash)
        return (5, 1, 3, "loaded")
    monkeypatch.setattr(de, "load", recorder)
    s, r = files(tmp_path, [summary_row()], DEFAULT_RESULTS)
    notes = _notes(tmp_path, '{"depth_matching": "lowest 20% by UMI dropped"}')
    assert de.main(argv(s, r, "--server", "https://x", "--email", "me@salk.edu",
                        "--notes", str(notes))) == 0
    params, params_hash = de.fingerprint(s, r)
    assert seen == {"params": {**params, "notes": {"depth_matching": "lowest 20% by UMI dropped"}},
                    "hash": params_hash}


@pytest.mark.parametrize("text", ["[1, 2]", "not json", '"a string"'])
def test_notes_that_are_not_a_json_object_are_refused_before_anything_else(
        de, tmp_path, capsys, text):
    s, r = files(tmp_path, [summary_row()], DEFAULT_RESULTS)
    assert de.main(argv(s, r, "--notes", str(_notes(tmp_path, text)), "--dry-run")) == 1
    assert "notes.json" in capsys.readouterr().err


def test_a_missing_notes_file_is_refused(de, tmp_path, capsys):
    s, r = files(tmp_path, [summary_row()], DEFAULT_RESULTS)
    assert de.main(argv(s, r, "--notes", str(tmp_path / "absent.json"), "--dry-run")) == 1
    assert "no such file" in capsys.readouterr().err


def test_a_dry_run_names_the_notes_it_would_record(de, tmp_path, capsys):
    s, r = files(tmp_path, [summary_row()], DEFAULT_RESULTS)
    notes = _notes(tmp_path, '{"b": 1, "a": 2}')
    assert de.main(argv(s, r, "--notes", str(notes), "--dry-run")) == 0
    assert "notes recorded with the analysis: a, b" in capsys.readouterr().out
