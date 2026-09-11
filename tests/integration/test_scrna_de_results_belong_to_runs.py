"""
Integration tests for 20260911002809_scrna_de_results_belong_to_runs.sql.

The five summary counts are gone, a new result has to belong to an analysis,
and a cell type cannot be moved to another dataset.

Each rejection names the constraint it expects, because a row usually breaks
more than one rule and the first to fire wins.

LOCAL ONLY: every test rolls back.
"""

import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
NAME = "scrna_de_results_belong_to_runs"

COUNT_COLUMNS = {"n_genes_tested", "n_significant_fdr", "n_significant_fdr_lfc",
                 "n_up", "n_down"}
COUNT_RULES = {
    "scrna_de_counts_all_or_none", "scrna_de_counts_non_negative",
    "scrna_de_significant_within_tested", "scrna_de_lfc_cut_narrows_fdr_cut",
    "scrna_de_up_plus_down_is_lfc_significant",
    "scrna_de_contrast_rows_carry_counts", "scrna_de_untested_counted_nothing",
}
NEW_RULES = {
    "scrna_de_run_metadata_needs_a_run", "scrna_de_run_rows_name_no_file",
    "scrna_de_new_contrasts_belong_to_a_run", "scrna_de_group_sizes_non_negative",
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
            "contrast": "a_vs_b", "group1": "a", "group2": "b", **cols}


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
# The summary counts
# --------------------------------------------------------------------------- #


def test_the_summary_counts_are_gone(pg_conn):
    with pg_conn.cursor() as cur:
        assert not _columns(cur) & COUNT_COLUMNS


def test_the_rules_that_compared_them_are_gone(pg_conn):
    with pg_conn.cursor() as cur:
        assert not set(_constraints(cur)) & COUNT_RULES


@pytest.mark.parametrize("column", ["n_group1", "n_group2"])
def test_a_group_size_still_cannot_be_negative(pg_conn, column):
    """The dropped counts_non_negative covered the group sizes too."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        row = _current(cur, ds, n_group1=1, n_group2=1)
        row[column] = -1
        _rejects(cur, ds, "scrna_de_group_sizes_non_negative", **row)
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
    "scrna_de_group_sizes_non_negative", "scrna_de_cluster_in_catalogue",
])
def test_the_other_rules_hold_for_every_existing_row(pg_conn, constraint):
    with pg_conn.cursor() as cur:
        assert _constraints(cur)[constraint] is True


# --------------------------------------------------------------------------- #
# A cell type stays in its dataset
# --------------------------------------------------------------------------- #


def test_a_cell_type_cannot_move_to_another_dataset(pg_conn):
    """Its results would follow it through the cascade."""
    with pg_conn.cursor() as cur:
        ds, other = _dataset(cur), _dataset(cur, ("Xylem",))
        _insert(cur, ds, file_path="de/markers_Cortex.json")
        with pytest.raises(psycopg.errors.CheckViolation,
                           match="cannot move to another"):
            cur.execute("UPDATE scrna_clusters SET dataset_id = %s "
                        "WHERE dataset_id = %s", (other, ds))
    pg_conn.rollback()


def test_a_writer_cannot_move_one_either(pg_conn):
    """bloom_writer can update the catalogue, and the cascade ignores its grants."""
    with pg_conn.cursor() as cur:
        ds, other = _dataset(cur), _dataset(cur, ("Xylem",))
        cur.execute("SET LOCAL ROLE bloom_writer")
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("UPDATE scrna_clusters SET dataset_id = %s "
                        "WHERE dataset_id = %s", (other, ds))
    pg_conn.rollback()


def test_renaming_a_cell_type_still_carries_its_results(pg_conn):
    """Changing the identifier is what the cascade is for."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        rid = _insert(cur, ds, file_path="de/markers_Cortex.json")
        cur.execute("UPDATE scrna_clusters SET cluster_id = 'Cortex_v2' "
                    "WHERE dataset_id = %s", (ds,))
        cur.execute("SELECT cluster_id FROM scrna_de WHERE id = %s", (rid,))
        assert cur.fetchone()[0] == "Cortex_v2"
    pg_conn.rollback()


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
    """What they held went with them, so the rule demanding counts on a contrast
    row comes back NOT VALID."""
    with pg_conn.cursor() as cur:
        cur.execute(_script("rollbacks", f"*_{NAME}_rollback.sql"))
        assert COUNT_COLUMNS <= _columns(cur)
        constraints = _constraints(cur)
        assert COUNT_RULES <= set(constraints)
        assert constraints["scrna_de_contrast_rows_carry_counts"] is False
        assert not NEW_RULES & set(constraints)
        cur.execute("SELECT count(*) FROM pg_trigger "
                    "WHERE tgname = 'scrna_clusters_keep_their_dataset'")
        assert cur.fetchone()[0] == 0
    pg_conn.rollback()


def test_the_migration_applies_again_after_its_rollback(pg_conn):
    """Re-runnable, and valid against the rows already on the server."""
    with pg_conn.cursor() as cur:
        cur.execute(_script("rollbacks", f"*_{NAME}_rollback.sql"))
        cur.execute(_script("migrations", f"*_{NAME}.sql"))
        assert not _columns(cur) & COUNT_COLUMNS
        assert NEW_RULES <= set(_constraints(cur))
    pg_conn.rollback()
