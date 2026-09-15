"""
Integration tests for the common differential expression tables
(migration 20260911000000_scrna_de_common_results.sql).

`scrna_de` used to hold one answer per question, with the genes behind it in a
storage object. It now records which analysis produced a row, so a dataset can
carry several, and the per-gene outcome lives in `scrna_de_genes` where it can
be ranked, joined and counted.

The rows that predate all of this -- one-vs-rest markers naming a file and no
run -- have to keep working exactly as they are, so several tests below exist
only to prove the migration did not disturb them.

Each rejection test names the constraint it expects, because a row usually
breaks more than one rule and the first to fire wins. Without that a test can
pass while the rule it is named for does nothing.

LOCAL ONLY: the `pg_conn` fixture connects to 127.0.0.1 on POSTGRES_HOST_PORT and
mutates nothing -- every test rolls back. It connects as `supabase_admin`, which
is BYPASSRLS, so policy checks read the catalog rather than claiming to prove
enforcement.

Runs in CI's `compose-health-check` job after migrations are applied
(`uv run --extra test pytest tests/integration/ -v`).
"""

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

def _dataset(cur, cell_types=("Cortex",)) -> int:
    tag = uuid.uuid4().hex[:10]
    cur.execute(
        "INSERT INTO species (common_name, genus, species) "
        "VALUES (%s, %s, %s) RETURNING id",
        (f"de-common-{tag}", f"Testus-{tag}", f"communis-{tag}"),
    )
    species_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO scrna_datasets (name, species_id) VALUES (%s, %s) RETURNING id",
        (f"de-common-{tag}", species_id),
    )
    dataset_id = cur.fetchone()[0]
    cur.executemany(
        "INSERT INTO scrna_clusters (dataset_id, cluster_id, ordinal, name, color) "
        "VALUES (%s, %s, %s, %s, '#000000')",
        [(dataset_id, c, i, c) for i, c in enumerate(cell_types)],
    )
    return dataset_id


def _run(cur, dataset_id, source="batch", params_hash=None) -> int:
    cur.execute(
        "INSERT INTO scrna_de_runs (dataset_id, source, status, method, "
        "params_hash, completed_at) "
        "VALUES (%s, %s, 'complete', 'external', %s, now()) RETURNING id",
        (dataset_id, source, params_hash or uuid.uuid4().hex[:12]),
    )
    return cur.fetchone()[0]


def _gene(cur, dataset_id, name=None) -> int:
    cur.execute(
        "INSERT INTO scrna_genes (dataset_id, gene_number, gene_name) "
        "VALUES (%s, %s, %s) RETURNING id",
        (dataset_id, 0, name or f"AT1G{uuid.uuid4().hex[:6]}"),
    )
    return cur.fetchone()[0]


def _result(cur, dataset_id, run_id, **cols):
    """One scrna_de row belonging to a run. Unspecified columns take defaults."""
    row = {
        "cluster_id": "Cortex", "contrast": "pFACT_vs_Col-0",
        "group1": "pFACT", "group2": "Col-0", "n_group1": 10, "n_group2": 20,
        "group_kind": "genotype", "method": "external", "params_hash": "h",
        "tested": True, "n_genes_tested": 3 if cols.get("tested", True) else 0,
        **cols,
    }
    names = ["dataset_id", "run_id", *row]
    values = [dataset_id, run_id, *row.values()]
    cur.execute(
        f"INSERT INTO scrna_de ({', '.join(names)}) "
        f"VALUES ({', '.join(['%s'] * len(names))}) RETURNING id",
        values,
    )
    return cur.fetchone()[0]


def _rejects(cur, constraint, fn, *args, **kwargs):
    with pytest.raises(psycopg.errors.IntegrityError) as exc:
        fn(*args, **kwargs)
    assert exc.value.diag.constraint_name == constraint, (
        f"expected {constraint}, got {exc.value.diag.constraint_name}"
    )


# --------------------------------------------------------------------------- #
# Both units of comparison fit one table
# --------------------------------------------------------------------------- #


def test_a_genotype_contrast_within_a_cell_type(pg_conn):
    """What the batch export produces: two genotypes compared inside one cell
    type."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _result(cur, ds, _run(cur, ds))
    pg_conn.rollback()


def test_a_cluster_pair_across_the_whole_dataset(pg_conn):
    """What the browser asks for: two cell types compared against each other,
    with no cell-type scope of its own."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur, ("Cortex", "Xylem"))
        _result(cur, ds, _run(cur, ds, source="ondemand"),
                cluster_id=None, contrast="Cortex_vs_Xylem",
                group1="Cortex", group2="Xylem", group_kind="cluster")
    pg_conn.rollback()


def test_both_are_returned_by_one_query(pg_conn):
    """The point of a common table: a reader does not have to know which route
    produced a result."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur, ("Cortex", "Xylem"))
        _result(cur, ds, _run(cur, ds))
        _result(cur, ds, _run(cur, ds, source="ondemand"),
                cluster_id=None, contrast="Cortex_vs_Xylem",
                group1="Cortex", group2="Xylem", group_kind="cluster")
        cur.execute("SELECT count(*) FROM scrna_de WHERE dataset_id = %s", (ds,))
        assert cur.fetchone()[0] == 2
    pg_conn.rollback()


def test_a_group_kind_the_schema_does_not_know_is_rejected(pg_conn):
    """A reader joins on the strength of this column, so an unknown value would
    leave it unable to resolve either side."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _rejects(cur, "scrna_de_group_kind_known",
                 _result, cur, ds, _run(cur, ds), group_kind="genotypes")
    pg_conn.rollback()


def test_a_run_row_missing_what_it_must_say_is_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _rejects(cur, "scrna_de_run_rows_are_complete",
                 _result, cur, ds, _run(cur, ds), group_kind=None)
    pg_conn.rollback()


def test_a_cell_type_the_catalogue_does_not_have_is_rejected(pg_conn):
    """This was a Python check in the loader. It is the database's now, which is
    what every other table naming a cell type already does."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _rejects(cur, "scrna_de_cluster_in_catalogue",
                 _result, cur, ds, _run(cur, ds), cluster_id="Phellem")
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# A dataset may hold more than one analysis
# --------------------------------------------------------------------------- #


def test_two_analyses_of_one_question_coexist(pg_conn):
    """A corrected export stands beside the previous answer instead of replacing
    it. This is the whole reason the run is in the key."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _result(cur, ds, _run(cur, ds))
        _result(cur, ds, _run(cur, ds))
        cur.execute("SELECT count(*) FROM scrna_de WHERE dataset_id = %s", (ds,))
        assert cur.fetchone()[0] == 2
    pg_conn.rollback()


def test_one_analysis_cannot_answer_the_same_question_twice(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        run = _run(cur, ds)
        _result(cur, ds, run)
        _rejects(cur, "scrna_de_comparison_uniqueness", _result, cur, ds, run)
    pg_conn.rollback()


def test_a_batch_upload_never_reaches_argo(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        with pytest.raises(psycopg.errors.IntegrityError) as exc:
            cur.execute(
                "INSERT INTO scrna_de_runs (dataset_id, source, status, method, "
                "params_hash, argo_workflow_name, completed_at) "
                "VALUES (%s, 'batch', 'complete', 'external', 'h', 'wf-1', now())",
                (ds,),
            )
        assert exc.value.diag.constraint_name == "scrna_de_runs_batch_does_not_dispatch"
    pg_conn.rollback()


def test_a_failed_run_says_why(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        with pytest.raises(psycopg.errors.IntegrityError) as exc:
            cur.execute(
                "INSERT INTO scrna_de_runs (dataset_id, source, status, method, "
                "params_hash, completed_at) "
                "VALUES (%s, 'ondemand', 'failed', 'wilcoxon', 'h', now())",
                (ds,),
            )
        assert exc.value.diag.constraint_name == "scrna_de_runs_failed_says_why"
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# The genes are rows
# --------------------------------------------------------------------------- #


def _gene_row(cur, dataset_id, de_id, gene_id, **cols):
    row = {"log2fc": 1.5, "pvalue": 0.001, "fdr": 0.01,
           "pct_1": 0.5, "pct_2": 0.25, **cols}
    names = ["de_id", "dataset_id", "gene_id", *row]
    values = [de_id, dataset_id, gene_id, *row.values()]
    cur.execute(
        f"INSERT INTO scrna_de_genes ({', '.join(names)}) "
        f"VALUES ({', '.join(['%s'] * len(names))})",
        values,
    )


def test_a_gene_of_another_dataset_is_rejected(pg_conn):
    """Without the composite key, a result could name a gene the dataset does
    not have and the values would look perfectly ordinary."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        other = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        _rejects(cur, "scrna_de_genes_gene_belongs_to_dataset",
                 _gene_row, cur, ds, de_id, _gene(cur, other))
    pg_conn.rollback()


def test_one_gene_appears_once_in_a_comparison(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        gene = _gene(cur, ds)
        _gene_row(cur, ds, de_id, gene)
        _rejects(cur, "scrna_de_genes_one_row_per_gene",
                 _gene_row, cur, ds, de_id, gene)
    pg_conn.rollback()


def test_a_p_value_outside_zero_to_one_is_rejected(pg_conn):
    """fdr is raised to match, so the ordering rule cannot be what rejects this
    and the range rule is left as the only candidate."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        _rejects(cur, "scrna_de_genes_probabilities_are_probabilities",
                 _gene_row, cur, ds, de_id, _gene(cur, ds), pvalue=5.0, fdr=5.0)
    pg_conn.rollback()


def test_correction_cannot_lower_a_p_value(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        _rejects(cur, "scrna_de_genes_fdr_is_not_below_pvalue",
                 _gene_row, cur, ds, de_id, _gene(cur, ds),
                 pvalue=0.5, fdr=0.01)
    pg_conn.rollback()


def test_removing_an_analysis_takes_its_genes_with_it(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        _gene_row(cur, ds, de_id, _gene(cur, ds))
        cur.execute("DELETE FROM scrna_de WHERE id = %s", (de_id,))
        cur.execute("SELECT count(*) FROM scrna_de_genes WHERE de_id = %s", (de_id,))
        assert cur.fetchone()[0] == 0
    pg_conn.rollback()


def test_the_strongest_genes_can_be_ranked(pg_conn):
    """Ranking is the thing a stored object could not do without being
    downloaded whole."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        for fdr in (0.5, 0.001, 0.05):
            _gene_row(cur, ds, de_id, _gene(cur, ds), fdr=fdr, pvalue=0.0001)
        cur.execute(
            "SELECT fdr FROM scrna_de_genes WHERE de_id = %s "
            "ORDER BY fdr LIMIT 2", (de_id,),
        )
        assert [r[0] for r in cur.fetchall()] == [0.001, 0.05]
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# The rows that predate all of this
# --------------------------------------------------------------------------- #


def test_a_one_vs_rest_row_is_still_legal(pg_conn):
    """No run, no kind, no method -- a file and a cluster, as it always was."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        cur.execute(
            "INSERT INTO scrna_de (dataset_id, cluster_id, file_path) "
            "VALUES (%s, 'Cortex', 'de/Cortex.json')", (ds,),
        )
    pg_conn.rollback()


def test_a_row_with_neither_a_file_nor_an_analysis_is_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        with pytest.raises(psycopg.errors.IntegrityError) as exc:
            cur.execute(
                "INSERT INTO scrna_de (dataset_id, cluster_id) VALUES (%s, 'Cortex')",
                (ds,),
            )
        assert exc.value.diag.constraint_name == "scrna_de_result_is_somewhere"
    pg_conn.rollback()



def test_a_comparison_that_never_ran_counts_nothing(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _rejects(cur, "scrna_de_untested_counted_nothing",
                 _result, cur, ds, _run(cur, ds), tested=False, n_genes_tested=3)
    pg_conn.rollback()


def test_a_comparison_that_never_ran_is_a_row(pg_conn):
    """It carries the group sizes, which are what explain why it was skipped."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _result(cur, ds, _run(cur, ds), tested=False, file_path=None,
                n_group1=0, n_group2=7)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# The rollback
# --------------------------------------------------------------------------- #


def _rollback_body() -> str:
    """The rollback script without its BEGIN/COMMIT wrapper, so it runs inside
    the fixture's uncommitted transaction and leaves the schema untouched."""
    import re
    from pathlib import Path

    path = (Path(__file__).parent.parent.parent / "supabase" / "rollbacks"
            / "20260911000000_scrna_de_common_results_rollback.sql")
    assert path.exists(), "rollback script not found"
    return "\n".join(
        line for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def _later_rollback_body() -> str:
    """The rollback for the migration layered on top of this one."""
    import re
    from pathlib import Path

    matches = sorted((Path(__file__).parent.parent.parent / "supabase" / "rollbacks")
                     .glob("*_scrna_de_results_belong_to_runs_rollback.sql"))
    assert matches, "later rollback script not found"
    return "\n".join(
        line for line in matches[-1].read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def test_rollback_refuses_while_an_analysis_exists(pg_conn):
    """Dropping scrna_de_genes discards every per-gene result, and unlike the
    objects it replaced there is no second copy anywhere."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _result(cur, ds, _run(cur, ds))
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            cur.execute(_rollback_body())
        assert "refusing to roll back" in str(exc.value)
    pg_conn.rollback()


def test_rollback_refuses_while_gene_rows_exist(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        _gene_row(cur, ds, de_id, _gene(cur, ds))
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(_rollback_body())
    pg_conn.rollback()


def test_rollback_runs_when_only_pre_run_rows_exist(pg_conn):
    """With nothing belonging to an analysis there is nothing to lose, so it
    goes -- and the one-vs-rest row comes through it unchanged."""
    with pg_conn.cursor() as cur:
        cur.execute("SAVEPOINT before_rollback")
        # Unwind the later migration first.
        cur.execute(_later_rollback_body())
        cur.execute(_rollback_body())
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'scrna_de' AND column_name = 'run_id'"
        )
        assert cur.fetchone()[0] == 0, "run_id should be gone"
        cur.execute("ROLLBACK TO SAVEPOINT before_rollback")
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# A submitted result is not edited
# --------------------------------------------------------------------------- #


def test_a_writer_cannot_rewrite_a_submitted_result(pg_conn):
    """The arithmetic rules refuse an incoherent edit, not an untrue one: the
    contrast label and the file a row points at can both be changed with every
    count left consistent. Correcting a result is loading it again."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        cur.execute("SET LOCAL ROLE bloom_writer")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "UPDATE scrna_de SET contrast = 'something_else' WHERE id = %s",
                (de_id,),
            )
    # Refused outright rather than silently matching no rows, because the grant
    # is gone as well as the policy.
    pg_conn.rollback()


def test_a_writer_may_still_read(pg_conn):
    """Only the editing goes; reading what was loaded stays."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        cur.execute("SET LOCAL ROLE bloom_writer")
        cur.execute("SELECT count(*) FROM scrna_de WHERE id = %s", (de_id,))
        assert cur.fetchone()[0] == 1
        cur.execute("RESET ROLE")
    pg_conn.rollback()


# May update or delete a result: bloom_admin, the migration roles, pg_write_all_data.
MAY_CHANGE_RESULTS = {"bloom_admin", "postgres", "supabase_admin", "pg_write_all_data"}


def test_only_admin_and_migration_roles_can_change_a_result(pg_conn):
    """Across every role, column-level grants included."""
    with pg_conn.cursor() as cur:
        for table in ("scrna_de", "scrna_de_runs", "scrna_de_genes"):
            cur.execute(
                "SELECT array_agg(rolname) FROM pg_roles r "
                "WHERE has_any_column_privilege(r.oid, %s::regclass, 'UPDATE') "
                "OR has_table_privilege(r.oid, %s::regclass, 'DELETE')",
                (f"public.{table}", f"public.{table}"),
            )
            assert set(cur.fetchone()[0]) == MAY_CHANGE_RESULTS, table
        cur.execute("SELECT count(*) FROM pg_auth_members "
                    "WHERE roleid = 'pg_write_all_data'::regrole")
        assert cur.fetchone()[0] == 0, "a role was given pg_write_all_data"


def test_bloom_admin_keeps_update_for_maintenance(pg_conn):
    """Something has to be able to repair a genuine mistake -- deliberately, by a
    developer, rather than as a side effect of a request."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.role_table_grants "
            "WHERE table_name = 'scrna_de' AND privilege_type = 'UPDATE' "
            "AND grantee = 'bloom_admin'"
        )
        assert cur.fetchone()[0] == 1


# --------------------------------------------------------------------------- #
# A result names its cell type by the catalogue's identifier
# --------------------------------------------------------------------------- #


def test_changing_a_cell_type_s_identifier_carries_its_results_with_it(pg_conn):
    """cluster_id is the stable identifier, and relabelling is a change to
    scrna_clusters.name, which no result references. If the identifier itself is
    ever changed, the results follow it rather than being left naming a cell type
    that no longer exists."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        cur.execute(
            "UPDATE scrna_clusters SET cluster_id = 'Cortex (mature)' "
            "WHERE dataset_id = %s AND cluster_id = 'Cortex'", (ds,)
        )
        cur.execute("SELECT cluster_id FROM scrna_de WHERE id = %s", (de_id,))
        assert cur.fetchone()[0] == "Cortex (mature)"
    pg_conn.rollback()


def test_a_whole_dataset_comparison_names_no_cell_type_at_all(pg_conn):
    """Scoped to no cell type, so it names none."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur, ("Cortex", "Xylem"))
        _result(cur, ds, _run(cur, ds, source="ondemand"),
                cluster_id=None, contrast="Cortex_vs_Xylem",
                group1="Cortex", group2="Xylem", group_kind="cluster")
    pg_conn.rollback()


def test_a_result_cannot_name_another_dataset_s_cell_type(pg_conn):
    """The composite key: without dataset_id in the reference, a result could name
    a cell type that exists only in a different experiment."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _dataset(cur, ("Phloem",))
        _rejects(cur, "scrna_de_cluster_in_catalogue",
                 _result, cur, ds, _run(cur, ds), cluster_id="Phloem")
    pg_conn.rollback()


def test_a_cell_type_with_results_still_cannot_be_deleted(pg_conn):
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _result(cur, ds, _run(cur, ds))
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as exc:
            cur.execute("DELETE FROM scrna_clusters WHERE dataset_id = %s", (ds,))
        assert exc.value.diag.constraint_name == "scrna_de_cluster_in_catalogue"
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Nothing points across a dataset
# --------------------------------------------------------------------------- #


def test_a_result_cannot_be_tagged_with_another_dataset_s_run(pg_conn):
    """Provenance is the one link where a mix-up is unrecoverable: the row would
    report a method and parameters from an entirely different experiment, and
    every value in it would look ordinary."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        other = _dataset(cur)
        _rejects(cur, "scrna_de_run_in_same_dataset",
                 _result, cur, ds, _run(cur, other))
    pg_conn.rollback()


def test_a_gene_row_cannot_hang_off_another_dataset_s_result(pg_conn):
    """The half the single-column reference left open: keep dataset_id and
    gene_id consistent with each other, point de_id at another dataset's result,
    and both old constraints were satisfied."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        other = _dataset(cur)
        de_in_other = _result(cur, other, _run(cur, other))
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as exc:
            _gene_row(cur, ds, de_in_other, _gene(cur, ds))
        assert exc.value.diag.constraint_name == "scrna_de_genes_result_in_same_dataset"
    pg_conn.rollback()


def test_deleting_a_result_still_takes_its_genes(pg_conn):
    """The cascade has to survive moving onto the composite reference."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        _gene_row(cur, ds, de_id, _gene(cur, ds))
        cur.execute("DELETE FROM scrna_de WHERE id = %s", (de_id,))
        cur.execute("SELECT count(*) FROM scrna_de_genes WHERE de_id = %s", (de_id,))
        assert cur.fetchone()[0] == 0
    pg_conn.rollback()


def test_up_and_down_are_a_query_not_a_column(pg_conn):
    """Which genes count as up depends on the cuts the question is asked with, so
    the schema stores the fold change and the reader derives the rest."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'scrna_de_genes' AND column_name = 'direction'"
        )
        assert cur.fetchone()[0] == 0

        ds = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        for lfc in (2.0, 0.6, -2.0):
            _gene_row(cur, ds, de_id, _gene(cur, ds), log2fc=lfc, fdr=0.01)
        # the same rows answer two different questions
        for cut, expected in ((1.0, 1), (0.5, 2)):
            cur.execute(
                "SELECT count(*) FROM scrna_de_genes "
                "WHERE de_id = %s AND fdr <= 0.05 AND log2fc >= %s", (de_id, cut)
            )
            assert cur.fetchone()[0] == expected
    pg_conn.rollback()


def test_a_fold_change_that_could_not_be_computed_is_null_not_nan(pg_conn):
    """NaN is refused; a fold change that could not be computed is NULL."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        _rejects(cur, "scrna_de_genes_fold_change_is_a_number_or_nothing",
                 _gene_row, cur, ds, de_id, _gene(cur, ds), log2fc=float("nan"))
    pg_conn.rollback()


def test_a_gene_with_no_computable_fold_change_is_still_a_row(pg_conn):
    """It was tested; the fold change is what could not be computed."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        _gene_row(cur, ds, de_id, _gene(cur, ds), log2fc=None)
        cur.execute(
            "SELECT log2fc IS NULL FROM scrna_de_genes WHERE de_id = %s", (de_id,)
        )
        assert cur.fetchone()[0] is True
    pg_conn.rollback()


def test_an_unbounded_fold_change_is_kept(pg_conn):
    """A gene absent from one side really does have an unbounded ratio. That is a
    measurement, not a missing one."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        _gene_row(cur, ds, de_id, _gene(cur, ds), log2fc=float("inf"))
    pg_conn.rollback()


def test_rollback_refuses_while_a_run_exists_even_with_no_results(pg_conn):
    """A failed run produces no results and no gene rows by design, so counting
    only those two waves it through -- and its error_message is the whole record
    of what went wrong."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        cur.execute(
            "INSERT INTO scrna_de_runs (dataset_id, source, status, method, "
            "params_hash, error_message, completed_at) VALUES "
            "(%s,'ondemand','failed','wilcoxon','h','argo pod OOMKilled',now())",
            (ds,),
        )
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            cur.execute(_rollback_body())
        assert "runs would be destroyed" in str(exc.value)
    pg_conn.rollback()


def test_a_role_that_bypasses_rls_still_cannot_rewrite_a_result(pg_conn):
    """service_role carries rolbypassrls, so no policy applies to it and the
    grant is the only thing that ever gated it. Anything holding the service key
    arrives as this role."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        cur.execute("SET LOCAL ROLE service_role")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "UPDATE scrna_de SET contrast = 'rewritten' WHERE id = %s", (de_id,)
            )
    pg_conn.rollback()


def test_the_measurements_themselves_cannot_be_rewritten(pg_conn):
    """The gene rows are the actual data. Refusing edits to the summary while
    leaving these open would protect the label and not the measurement."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        _gene_row(cur, ds, de_id, _gene(cur, ds), log2fc=2.0)
        for role in ("bloom_writer", "service_role"):
            # A refused statement aborts the transaction, so each attempt needs
            # its own savepoint to get back to a usable one.
            cur.execute("SAVEPOINT attempt")
            cur.execute(f"SET LOCAL ROLE {role}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("UPDATE scrna_de_genes SET log2fc = -99 WHERE de_id = %s",
                            (de_id,))
            cur.execute("ROLLBACK TO SAVEPOINT attempt")
    pg_conn.rollback()


def test_the_reachability_rule_is_validated_not_merely_declared(pg_conn):
    """NOT VALID would leave convalidated false forever, surviving dumps and
    restores, so nobody could ask whether the rule actually holds."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT convalidated FROM pg_constraint "
            "WHERE conname = 'scrna_de_result_is_somewhere'"
        )
        assert cur.fetchone()[0] is True


# --------------------------------------------------------------------------- #
# The access rules themselves, not just the grants beneath them
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("table", ["scrna_de_runs", "scrna_de_genes"])
def test_row_level_security_is_on(pg_conn, table):
    """Both tables rely on it entirely: every role but bloom_admin holds grants
    from the schema's default privileges and is stopped by policy alone."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass",
                    (f"public.{table}",))
        assert cur.fetchone()[0] is True


@pytest.mark.parametrize("table", ["scrna_de_runs", "scrna_de_genes"])
def test_the_policies_are_the_ones_scrna_de_has(pg_conn, table):
    """Deliberately without anon, and with writing limited to submitting: a
    writer may insert, and only the admin role may change what is there. The
    set is asserted whole rather than by absence."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT policyname, roles::text, cmd FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = %s", (table,)
        )
        suffix = table.replace("scrna_de_", "")
        assert {(n, r, c) for n, r, c in cur.fetchall()} == {
            (f"admin_all_scrna_de_{suffix}", "{bloom_admin}", "ALL"),
            (f"agent_read_scrna_de_{suffix}", "{bloom_agent}", "SELECT"),
            (f"user_read_scrna_de_{suffix}", "{bloom_user}", "SELECT"),
            (f"writer_insert_scrna_de_{suffix}", "{bloom_writer}", "INSERT"),
        }


def test_a_reader_role_actually_reads_through_its_policy(pg_conn):
    """The catalog checks above say the policy exists; this says it works."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        _result(cur, ds, _run(cur, ds))
        for role, expected in (("bloom_user", 1), ("bloom_agent", 1), ("anon", 0)):
            cur.execute("SAVEPOINT r")
            cur.execute(f"SET LOCAL ROLE {role}")
            cur.execute("SELECT count(*) FROM scrna_de_runs")
            assert cur.fetchone()[0] == expected, f"{role} read the wrong number"
            cur.execute("ROLLBACK TO SAVEPOINT r")
    pg_conn.rollback()


def test_the_dropped_update_policies_are_gone(pg_conn):
    """Asserted as an absence, because the subset check in the contrast suite
    only relaxes when an entry is removed from it."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT policyname FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = 'scrna_de' AND cmd = 'UPDATE'"
        )
        assert [r[0] for r in cur.fetchall()] == []


def test_a_writer_can_submit_an_analysis_and_its_genes(pg_conn):
    """Submitting is what a writer is for. Without an INSERT policy on the two
    new tables, a loader could write a summary claiming thousands of tested
    genes and have no way to write one of them."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        gene = _gene(cur, ds)
        cur.execute("SET LOCAL ROLE bloom_writer")
        cur.execute(
            "INSERT INTO scrna_de_runs (dataset_id, source, status, method, "
            "params_hash, completed_at) "
            "VALUES (%s, 'batch', 'complete', 'external', 'h', now()) RETURNING id",
            (ds,),
        )
        run = cur.fetchone()[0]
        de_id = _result(cur, ds, run)
        _gene_row(cur, ds, de_id, gene)
        cur.execute("SELECT count(*) FROM scrna_de_genes WHERE de_id = %s", (de_id,))
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_a_writer_still_cannot_change_what_it_submitted(pg_conn):
    """Insert is submitting; editing or removing a submitted result stays with
    the admin role."""
    with pg_conn.cursor() as cur:
        ds = _dataset(cur)
        de_id = _result(cur, ds, _run(cur, ds))
        _gene_row(cur, ds, de_id, _gene(cur, ds))
        for sql in ("UPDATE scrna_de_genes SET fdr = 0.5 WHERE de_id = %s",
                    "DELETE FROM scrna_de_genes WHERE de_id = %s",
                    "UPDATE scrna_de_runs SET status = 'failed' "
                    "WHERE id = (SELECT run_id FROM scrna_de WHERE id = %s)"):
            cur.execute("SAVEPOINT w")
            cur.execute("SET LOCAL ROLE bloom_writer")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute(sql, (de_id,))
            cur.execute("ROLLBACK TO SAVEPOINT w")
    pg_conn.rollback()
