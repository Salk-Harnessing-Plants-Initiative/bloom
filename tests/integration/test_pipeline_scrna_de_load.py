"""
Integration tests for the pipeline loading a run's default differential expression with its
dataset (20261012230000):
- bloom_workflows can add a DE run, its comparisons and genes, and each cluster's statistics,
  for a dataset it is still loading, as `bloomctl scrna hdf5 upload --de-key` does;
- it can't add them to a finished, removed or another account's dataset, and changes or deletes
  none of them;
- a run can report the `de` step;
- service_role can no longer start a run;
- the migration runs again cleanly, and the rollback puts the earlier rights back.

LOCAL ONLY: `pg_conn` connects as `supabase_admin` (BYPASSRLS) and every test rolls back.
Privileges a role is denied are checked with has_*_privilege, not by calling as that role.
"""

import pytest

from tests.integration import _scrna_pipeline_load as load
from tests.integration.test_rnaseq_runs import _find_one, _sql_body

psycopg = pytest.importorskip("psycopg")

MIGRATION = _find_one("migrations", "*_let_pipeline_load_default_de.sql")
ROLLBACK = _find_one("rollbacks", "*_let_pipeline_load_default_de_rollback.sql")
REQUEST_FN = (
    "public.request_scrna_cellranger_run(text, text, uuid, jsonb, text[], text, jsonb, bigint, text)"
)
# The rule a write must pass: the 20261005220000 function, applied to the row's dataset.
MAY_LOAD_CHECK = "workflows_may_load_scrna_dataset(dataset_id)"
RLS_REFUSAL = psycopg.errors.InsufficientPrivilege


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        yield c
    pg_conn.rollback()


@pytest.fixture
def pipeline(cur):
    return load.user(cur, workflows=True)


@pytest.fixture
def scientist(cur):
    return load.user(cur)


@pytest.fixture
def species(cur):
    return load.species(cur)


@pytest.fixture
def loading(cur, pipeline, species):
    """A dataset the pipeline has filled but not finished, signed in as the pipeline."""
    load.sign_in(cur, pipeline)
    dataset_id, _ = load.create(cur, species)
    load.fill(cur, dataset_id)
    return dataset_id


def _count(cur, table, dataset_id):
    cur.execute(f"SELECT count(*) FROM public.{table} WHERE dataset_id = %s", (dataset_id,))
    return cur.fetchone()[0]


def _privileges(cur, table):
    t = f"public.{table}"
    cur.execute(
        "SELECT has_table_privilege('bloom_workflows', %s, 'SELECT'), "
        "has_table_privilege('bloom_workflows', %s, 'INSERT'), "
        "has_any_column_privilege('bloom_workflows', %s, 'UPDATE'), "
        "has_table_privilege('bloom_workflows', %s, 'DELETE')",
        (t, t, t, t),
    )
    return cur.fetchone()


# --------------------------------------------------------------------------- #
# Rights
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("table", load.DE_TABLES)
def test_the_pipeline_adds_de_only_under_the_load_rule(cur, table):
    cur.execute(
        "SELECT cmd, roles::text, with_check FROM pg_policies WHERE schemaname = 'public' "
        "AND tablename = %s AND policyname = %s",
        (table, f"workflows_insert_{table}"),
    )
    cmd, roles, check = cur.fetchone()
    assert (cmd, roles) == ("INSERT", "{bloom_workflows}")
    assert MAY_LOAD_CHECK in check


@pytest.mark.parametrize("table", load.DE_TABLES)
def test_the_pipeline_has_no_other_write_rule_on_de(cur, table):
    cur.execute(
        "SELECT policyname FROM pg_policies WHERE schemaname = 'public' AND tablename = %s "
        "AND 'bloom_workflows' = ANY(roles) AND cmd NOT IN ('SELECT', 'INSERT')",
        (table,),
    )
    assert cur.fetchall() == []


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def test_the_pipeline_loads_de_with_a_dataset_it_is_loading(cur, loading):
    load.add_de(cur, loading)
    assert load.finish(cur, loading) == 1
    cur.execute("RESET ROLE")
    for table in load.DE_TABLES:
        assert _count(cur, table, loading) == 1, table


def test_the_pipeline_reads_back_what_it_wrote(cur, loading):
    load.add_de(cur, loading)
    for table in load.DE_TABLES:
        assert _count(cur, table, loading) == 1, table


def test_a_finished_dataset_gets_no_more_de(cur, loading):
    assert load.finish(cur, loading) == 1
    load.refused(cur, "INSERT INTO public.scrna_de_runs (dataset_id, source, status, method, "
                 "params_hash, completed_at) VALUES (%s, 'batch', 'complete', 'm', 'h', now())",
                 (loading,), error=RLS_REFUSAL)
    load.refused(cur, "INSERT INTO public.scrna_cluster_stats (dataset_id, cluster_id, "
                 "cell_count, pct) VALUES (%s, '0', 1, 100)", (loading,), error=RLS_REFUSAL)


def test_a_removed_dataset_gets_no_de(cur, loading):
    cur.execute("RESET ROLE")
    cur.execute("UPDATE public.scrna_datasets SET deleted_at = now() WHERE id = %s", (loading,))
    cur.execute("SET LOCAL ROLE bloom_workflows")
    load.refused(cur, "INSERT INTO public.scrna_cluster_stats (dataset_id, cluster_id, "
                 "cell_count, pct) VALUES (%s, '0', 1, 100)", (loading,), error=RLS_REFUSAL)


def test_another_accounts_dataset_gets_no_de(cur, scientist, loading):
    cur.execute("RESET ROLE")
    cur.execute("UPDATE public.scrna_datasets SET created_by = %s WHERE id = %s",
                (scientist, loading))
    cur.execute("SET LOCAL ROLE bloom_workflows")
    load.refused(cur, "INSERT INTO public.scrna_de_runs (dataset_id, source, status, method, "
                 "params_hash, completed_at) VALUES (%s, 'batch', 'complete', 'm', 'h', now())",
                 (loading,), error=RLS_REFUSAL)


@pytest.mark.parametrize("table", load.DE_TABLES)
def test_the_pipeline_changes_and_deletes_no_de(cur, table):
    select, insert, update, delete = _privileges(cur, table)
    assert (select, insert, update, delete) == (True, True, False, False)


# --------------------------------------------------------------------------- #
# The run's step, and who starts runs
# --------------------------------------------------------------------------- #


def test_a_run_can_report_the_de_step(cur, scientist, species):
    run_id = load.run(cur, scientist, species)
    cur.execute("UPDATE public.rnaseq_runs SET current_step = 'de' WHERE id = %s", (run_id,))
    cur.execute("SELECT current_step FROM public.rnaseq_runs WHERE id = %s", (run_id,))
    assert cur.fetchone()[0] == "de"


def test_an_unknown_step_is_still_refused(cur, scientist, species):
    run_id = load.run(cur, scientist, species)
    load.refused(cur, "UPDATE public.rnaseq_runs SET current_step = 'annotate' WHERE id = %s",
                 (run_id,), error=psycopg.errors.CheckViolation)


@pytest.mark.parametrize("role, allowed", [
    ("bloom_workflows", True), ("service_role", False), ("authenticated", False),
    ("anon", False), ("bloom_user", False), ("bloom_writer", False),
])
def test_only_the_pipeline_may_start_a_run(cur, role, allowed):
    cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, REQUEST_FN))
    assert cur.fetchone()[0] is allowed


# --------------------------------------------------------------------------- #
# Re-running and rolling back
# --------------------------------------------------------------------------- #


def test_the_migration_can_be_run_again(cur, loading):
    cur.execute("RESET ROLE")
    cur.execute(_sql_body(MIGRATION))
    cur.execute("SET LOCAL ROLE bloom_workflows")
    load.add_de(cur, loading)
    assert _count(cur, "scrna_de_genes", loading) == 1


def test_the_rollback_puts_the_earlier_rights_back(cur, scientist, species):
    cur.execute(_sql_body(ROLLBACK))
    for table in ("scrna_de", "scrna_cluster_stats"):
        assert _privileges(cur, table) == (True, False, False, False), table
    for table in ("scrna_de_runs", "scrna_de_genes"):
        assert _privileges(cur, table) == (False, False, False, False), table
    cur.execute("SELECT has_function_privilege('service_role', %s, 'EXECUTE')", (REQUEST_FN,))
    assert cur.fetchone()[0] is True
    run_id = load.run(cur, scientist, species)
    load.refused(cur, "UPDATE public.rnaseq_runs SET current_step = 'de' WHERE id = %s",
                 (run_id,), error=psycopg.errors.CheckViolation)


def test_the_rollback_refuses_while_a_run_reports_de(cur, scientist, species):
    run_id = load.run(cur, scientist, species)
    cur.execute("UPDATE public.rnaseq_runs SET current_step = 'de' WHERE id = %s", (run_id,))
    cur.execute("SAVEPOINT rb")
    with pytest.raises(psycopg.errors.RaiseException, match="clear current_step"):
        cur.execute(_sql_body(ROLLBACK))
    cur.execute("ROLLBACK TO SAVEPOINT rb")
