"""Integration tests for 20260911002809_scrna_de_results_belong_to_runs.sql.

Each rejection names the constraint it expects. Every test rolls back.
"""

import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
NAME = "scrna_de_results_belong_to_runs"

COUNT_COLUMNS = {"n_significant_fdr", "n_significant_fdr_lfc", "n_up", "n_down"}
COUNT_RULES = {
    "scrna_de_counts_all_or_none", "scrna_de_counts_non_negative",
    "scrna_de_significant_within_tested", "scrna_de_lfc_cut_narrows_fdr_cut",
    "scrna_de_up_plus_down_is_lfc_significant",
    "scrna_de_contrast_rows_carry_counts",
}
NEW_RULES = {
    "scrna_de_run_metadata_needs_a_run", "scrna_de_run_rows_name_no_file",
    "scrna_de_new_contrasts_belong_to_a_run", "scrna_de_sizes_non_negative",
    "scrna_de_run_rows_count_their_genes", "scrna_de_tested_means_genes_tested",
}
# What describes an analysis on a result row.
RUN_COLUMNS = {"group_kind": "genotype", "method": "m", "params_hash": "h",
               "tested": True}


def _dataset(cur, cell_types=("Cortex",)) -> int:
    tag = uuid.uuid4().hex[:10]
    cur.execute(
        "INSERT INTO species (common_name, genus, species) VALUES (%s, %s, %s) "
        "RETURNING id", (f"de-runs-{tag}", f"Testus-{tag}", f"runsis-{tag}"),
    )
    species_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO scrna_datasets (name, species_id) VALUES (%s, %s) "
        "RETURNING id", (f"de-runs-{tag}", species_id),
    )
    dataset_id = cur.fetchone()[0]
    cur.executemany(
        "INSERT INTO scrna_clusters (dataset_id, cluster_id, ordinal, name, color) "
        "VALUES (%s, %s, %s, %s, '#000000')",
        [(dataset_id, c, i, c) for i, c in enumerate(cell_types)],
    )
    return dataset_id


def _run(cur, dataset_id) -> int:
    cur.execute(
        "INSERT INTO scrna_de_runs (dataset_id, source, status, method, "
        "params_hash, completed_at) "
        "VALUES (%s, 'batch', 'complete', 'm', %s, now()) RETURNING id",
        (dataset_id, uuid.uuid4().hex),
    )
    return cur.fetchone()[0]


def _insert(cur, dataset_id, **cols) -> int:
    cols.setdefault("cluster_id", "Cortex")
    names = ["dataset_id", *cols]
    cur.execute(
        f"INSERT INTO scrna_de ({', '.join(names)}) "
        f"VALUES ({', '.join(['%s'] * len(names))}) RETURNING id",
        [dataset_id, *cols.values()],
    )
    return cur.fetchone()[0]


def _current(cur, dataset_id, **cols) -> dict:
    """A result as one is written now: under an analysis, naming no file."""
    return {"run_id": _run(cur, dataset_id), **RUN_COLUMNS,
            "n_genes_tested": 100, "contrast": "a_vs_b", "group1": "a",
            "group2": "b", **cols}


def _rejects(cur, dataset_id, constraint, **cols):
    with pytest.raises(psycopg.errors.IntegrityError) as exc:
        _insert(cur, dataset_id, **cols)
    assert exc.value.diag.constraint_name == constraint, (
        f"expected {constraint}, got {exc.value.diag.constraint_name}"
    )


def _script(folder: str, pattern: str) -> str:
    """A migration or rollback without BEGIN/COMMIT, to run inside the test."""
    matches = sorted((REPO_ROOT / "supabase" / folder).glob(pattern))
    assert matches, f"{pattern} not found"
    return "\n".join(
        line for line in matches[-1].read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def _constraints(cur) -> dict[str, bool]:
    cur.execute("SELECT conname, convalidated FROM pg_constraint "
                "WHERE conrelid = 'public.scrna_de'::regclass")
    return dict(cur.fetchall())


def _columns(cur) -> set[str]:
    cur.execute("SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'scrna_de'")
    return {r[0] for r in cur.fetchall()}


# --------------------------------------------------------------------------- #
# The threshold counts, and the one that stays
# --------------------------------------------------------------------------- #


def test_the_threshold_counts_are_gone_and_genes_tested_stays(pg_conn):
    with pg_conn.cursor() as cur:
        columns = _columns(cur)
        assert not columns & COUNT_COLUMNS
        assert "n_genes_tested" in columns


def test_the_rules_that_compared_them_are_gone(pg_conn):
    with pg_conn.cursor() as cur:
        assert not set(_constraints(cur)) & COUNT_RULES


@pytest.mark.parametrize("column", ["n_group1", "n_group2", "n_genes_tested"])
def test_a_size_still_cannot_be_negative(pg_conn, column):
    """The dropped counts_non_negative covered these too."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        row = _current(cur, ds, n_group1=1, n_group2=1)
        row[column] = -1
        _rejects(cur, ds, "scrna_de_sizes_non_negative", **row)
    pg_conn.rollback()


def test_a_row_under_an_analysis_says_how_many_genes_it_tested(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _rejects(cur, ds, "scrna_de_run_rows_count_their_genes",
                 **_current(cur, ds, n_genes_tested=None))
    pg_conn.rollback()


def test_a_tested_comparison_tested_some_genes(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _rejects(cur, ds, "scrna_de_tested_means_genes_tested",
                 **_current(cur, ds, n_genes_tested=0))
    pg_conn.rollback()


def test_an_untested_comparison_with_no_genes_is_accepted(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _insert(cur, ds, **_current(cur, ds, tested=False, n_genes_tested=0))
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# A result belongs to an analysis
# --------------------------------------------------------------------------- #


def test_a_current_result_is_accepted(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _insert(cur, ds, **_current(cur, ds, n_group1=3, n_group2=4))
    pg_conn.rollback()


def test_a_one_vs_rest_row_still_needs_no_analysis(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _insert(cur, ds, file_path="de/markers_Cortex.json")
    pg_conn.rollback()


@pytest.mark.parametrize("column", sorted(RUN_COLUMNS))
def test_analysis_columns_need_an_analysis(pg_conn, column):
    """A row that predates runs carries none of what describes one."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _rejects(cur, ds, "scrna_de_run_metadata_needs_a_run",
                 file_path="de/x.json", **{column: RUN_COLUMNS[column]})
    pg_conn.rollback()


def test_a_result_under_an_analysis_names_no_file(pg_conn):
    """Its genes are rows; a file as well would be a second copy to disagree."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _rejects(cur, ds, "scrna_de_run_rows_name_no_file",
                 **_current(cur, ds, file_path="de/x.json"))
    pg_conn.rollback()


def test_a_new_contrast_has_to_belong_to_an_analysis(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _rejects(cur, ds, "scrna_de_new_contrasts_belong_to_a_run",
                 file_path="de/x.json", contrast="a_vs_b", group1="a",
                 group2="b")
    pg_conn.rollback()


def test_contrasts_loaded_before_runs_are_left_as_they_are(pg_conn):
    """NOT VALID: the rule binds rows written from now on."""
    with pg_conn.cursor() as cur:
        assert _constraints(cur)["scrna_de_new_contrasts_belong_to_a_run"] is False


@pytest.mark.parametrize("constraint", [
    "scrna_de_run_metadata_needs_a_run", "scrna_de_run_rows_name_no_file",
    "scrna_de_sizes_non_negative", "scrna_de_run_rows_count_their_genes",
    "scrna_de_tested_means_genes_tested", "scrna_de_cluster_in_catalogue",
])
def test_the_other_rules_hold_for_every_existing_row(pg_conn, constraint):
    with pg_conn.cursor() as cur:
        assert _constraints(cur)[constraint] is True


# --------------------------------------------------------------------------- #
# Grants on the tables 20260911000000 created
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("table", ["scrna_de_runs", "scrna_de_genes"])
def test_each_role_holds_what_it_needs_on_the_new_tables(pg_conn, table):
    expected = {
        ("bloom_user", "SELECT"): True, ("bloom_agent", "SELECT"): True,
        ("bloom_agent", "INSERT"): False, ("bloom_writer", "INSERT"): True,
        ("bloom_writer", "UPDATE"): False, ("bloom_admin", "UPDATE"): True,
        ("bloom_admin", "DELETE"): True,
    }
    with pg_conn.cursor() as cur:
        for (role, privilege), allowed in expected.items():
            cur.execute("SELECT has_table_privilege(%s, %s, %s)",
                        (role, f"public.{table}", privilege))
            assert cur.fetchone()[0] is allowed, f"{role} {privilege} {table}"


# --------------------------------------------------------------------------- #
# The rollback
# --------------------------------------------------------------------------- #


def test_the_rollback_brings_the_count_columns_back_empty(pg_conn):
    """The four columns return empty; the rules needing all five return NOT VALID."""
    with pg_conn.cursor() as cur:
        cur.execute(_script("rollbacks", f"*_{NAME}_rollback.sql"))
        assert COUNT_COLUMNS <= _columns(cur)
        constraints = _constraints(cur)
        assert COUNT_RULES <= set(constraints)
        assert constraints["scrna_de_contrast_rows_carry_counts"] is False
        assert constraints["scrna_de_counts_all_or_none"] is False
        assert "n_genes_tested" in _columns(cur)
        assert not NEW_RULES & set(constraints)
    pg_conn.rollback()


def test_the_migration_applies_again_after_its_rollback(pg_conn):
    """The migration re-applies after its rollback."""
    with pg_conn.cursor() as cur:
        cur.execute(_script("rollbacks", f"*_{NAME}_rollback.sql"))
        cur.execute(_script("migrations", f"*_{NAME}.sql"))
        assert not _columns(cur) & COUNT_COLUMNS
        assert NEW_RULES <= set(_constraints(cur))
    pg_conn.rollback()
