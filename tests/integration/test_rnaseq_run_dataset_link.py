"""
Integration tests for link_rnaseq_run_dataset, which the status poller calls once a Cell
Ranger run's load-dataset step succeeds, with the dataset that step reports: it links the run
to that dataset, copies the form's details into it and gives it to the scientist who asked for
the run; it refuses a dataset that is unfinished, removed, of another species, not loaded by
the pipeline or already linked, and a run that hasn't run or has failed. Also: the migrations
run again, and the rollbacks put the name lookup back and take the pipeline's access back.

LOCAL ONLY: `pg_conn` connects as `supabase_admin` (BYPASSRLS) and every test rolls back.
"""

import pytest

from tests.integration import _scrna_pipeline_load as load
from tests.integration.test_rnaseq_runs import _find_one, _sql_body

psycopg = pytest.importorskip("psycopg")

MIGRATION = _find_one("migrations", "*_let_pipeline_load_scrna_datasets.sql")
ROLLBACK = _find_one("rollbacks", "*_let_pipeline_load_scrna_datasets_rollback.sql")
BY_ID_MIGRATION = _find_one("migrations", "*_link_rnaseq_run_by_dataset_id.sql")
BY_ID_ROLLBACK = _find_one("rollbacks", "*_link_rnaseq_run_by_dataset_id_rollback.sql")
NAME_LINK = "public.link_rnaseq_run_dataset(bigint)"


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


def _remove(cur, dataset_id):
    cur.execute("UPDATE public.scrna_datasets SET deleted_at = now() WHERE id = %s", (dataset_id,))


# --------------------------------------------------------------------------- #
# Linking a run to its dataset
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", ["running", "succeeded"])
def test_a_run_is_linked_and_its_dataset_handed_to_the_scientist(cur, pipeline, scientist,
                                                                 species, status):
    dataset_id = load.loaded(cur, pipeline, species)
    run_id = load.run(cur, scientist, species, status=status, accession="GSE1",
                      experiment_name="Root time course", citation="Doe 2026",
                      source_url="https://example.org/gse1", attributes={"tissue": "root"})
    # The poller may sign in as another pipeline account than the one that loaded.
    poller = load.user(cur, workflows=True)
    assert load.link(cur, poller, run_id, dataset_id) == dataset_id
    cur.execute("SELECT dataset_id FROM public.rnaseq_runs WHERE id = %s", (run_id,))
    assert cur.fetchone()[0] == dataset_id
    cur.execute("SELECT url, metadata, created_by::text FROM public.scrna_datasets "
                "WHERE id = %s", (dataset_id,))
    url, metadata, owner = cur.fetchone()
    assert url == "https://example.org/gse1"
    assert metadata["source"] == {"origin": "public", "accession": "GSE1",
                                  "experiment_name": "Root time course",
                                  "citation": "Doe 2026", "attributes": {"tissue": "root"},
                                  "rnaseq_run_id": run_id}
    assert metadata["expected_cells"] == 1, "the load's own metadata was replaced"
    assert owner == scientist
    # Reopened, so only the hand-over can be what stops the pipeline writing to it.
    cur.execute("UPDATE public.scrna_datasets SET ingested_at = NULL WHERE id = %s",
                (dataset_id,))
    load.sign_in(cur, pipeline)
    assert load.finish(cur, dataset_id) == 0, "the pipeline can still write the scientist's dataset"


def test_linking_again_returns_the_runs_dataset(cur, pipeline, scientist, species):
    dataset_id = load.loaded(cur, pipeline, species)
    later = load.loaded(cur, pipeline, species, name="Root atlas_v2")
    run_id = load.run(cur, scientist, species)
    assert load.link(cur, pipeline, run_id, dataset_id) == dataset_id
    assert load.link(cur, pipeline, run_id, later) == dataset_id


def test_the_reported_dataset_is_linked_whatever_the_form_named(cur, pipeline, scientist,
                                                                species):
    taken = load.loaded(cur, pipeline, species)
    versioned = load.loaded(cur, pipeline, species, name="Root atlas_v2")
    run_id = load.run(cur, scientist, species, name="Root atlas")
    assert load.link(cur, pipeline, run_id, versioned) == versioned
    cur.execute("SELECT created_by::text FROM public.scrna_datasets WHERE id = %s", (taken,))
    assert cur.fetchone()[0] == pipeline, "the dataset under the typed name was left alone"


def test_deleting_a_dataset_keeps_its_run(cur):
    cur.execute("SELECT confdeltype FROM pg_constraint "
                "WHERE conname = 'rnaseq_runs_dataset_id_fkey'")
    assert cur.fetchone()[0] == "n", "deleting a dataset must clear the link, not the run"


@pytest.mark.parametrize("case, error, match", [
    ("unfinished", psycopg.errors.ObjectNotInPrerequisiteState, "not finished"),
    ("missing", psycopg.errors.NoDataFound, "no dataset"),
    ("removed", psycopg.errors.NoDataFound, "no dataset"),
    ("other species", psycopg.errors.InvalidParameterValue, "not of run"),
    ("not the pipeline's", psycopg.errors.InsufficientPrivilege, "not loaded by the pipeline"),
    ("queued run", psycopg.errors.ObjectNotInPrerequisiteState, "has not succeeded"),
    ("failed run", psycopg.errors.ObjectNotInPrerequisiteState, "has not succeeded"),
    ("already linked", psycopg.errors.UniqueViolation, "already linked to run"),
    ("no dataset id", psycopg.errors.InvalidParameterValue, "dataset id are required"),
])
def test_a_run_isnt_linked_to_the_wrong_dataset(cur, pipeline, scientist, species, case, error,
                                                match):
    status = {"queued run": "queued", "failed run": "failed"}.get(case, "running")
    dataset_id = None
    if case == "unfinished":
        load.sign_in(cur, pipeline)
        dataset_id, _ = load.create(cur, species)
        cur.execute("RESET ROLE")
    elif case == "not the pipeline's":
        load.sign_in(cur, scientist, role="bloom_writer")
        dataset_id, _ = load.create(cur, species)
        cur.execute("RESET ROLE")
        cur.execute("UPDATE public.scrna_datasets SET ingested_at = now() WHERE id = %s",
                    (dataset_id,))
    elif case == "missing":
        cur.execute("SELECT coalesce(max(id), 0) + 1000 FROM public.scrna_datasets")
        dataset_id = cur.fetchone()[0]
    elif case == "other species":
        dataset_id = load.loaded(cur, pipeline, load.species(cur))
    elif case != "no dataset id":
        dataset_id = load.loaded(cur, pipeline, species)
        if case == "removed":
            _remove(cur, dataset_id)
        if case == "already linked":
            other = load.run(cur, scientist, species, sample="root2")
            cur.execute("UPDATE public.rnaseq_runs SET dataset_id = %s WHERE id = %s",
                        (dataset_id, other))
    run_id = load.run(cur, scientist, species, status=status)
    load.sign_in(cur, pipeline)
    load.refused(cur, "SELECT public.link_rnaseq_run_dataset(%s, %s)", (run_id, dataset_id),
                 error=error, match=match)


def test_the_name_lookup_is_gone(cur):
    cur.execute("SELECT to_regprocedure(%s)", (NAME_LINK,))
    assert cur.fetchone()[0] is None, "there is one link function"


# --------------------------------------------------------------------------- #
# Re-run and rollback
# --------------------------------------------------------------------------- #


def test_the_migrations_can_be_run_again(cur, pipeline, scientist, species):
    cur.execute(_sql_body(MIGRATION))
    cur.execute(_sql_body(BY_ID_MIGRATION))
    cur.execute(_sql_body(BY_ID_MIGRATION))
    cur.execute("SELECT to_regprocedure(%s)", (NAME_LINK,))
    assert cur.fetchone()[0] is None
    dataset_id = load.loaded(cur, pipeline, species)
    run_id = load.run(cur, scientist, species)
    assert load.link(cur, pipeline, run_id, dataset_id) == dataset_id


def test_the_by_id_rollback_puts_the_name_lookup_back(cur, pipeline, scientist, species):
    dataset_id = load.loaded(cur, pipeline, species)
    run_id = load.run(cur, scientist, species)
    cur.execute(_sql_body(BY_ID_ROLLBACK))
    cur.execute("SELECT to_regprocedure(%s)", (load.LINK,))
    assert cur.fetchone()[0] is None
    for role, allowed in (("bloom_workflows", True), ("bloom_user", False), ("anon", False)):
        cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, NAME_LINK))
        assert cur.fetchone()[0] is allowed, role
    load.sign_in(cur, pipeline)
    cur.execute("SELECT public.link_rnaseq_run_dataset(%s)", (run_id,))
    assert cur.fetchone()[0] == dataset_id


def test_the_rollbacks_take_the_pipelines_access_back(cur, pipeline, species):
    dataset_id = load.loaded(cur, pipeline, species)
    cur.execute(_sql_body(BY_ID_ROLLBACK))
    cur.execute(_sql_body(ROLLBACK))
    for fn in (load.LINK, NAME_LINK, load.MAY_LOAD, "public.set_created_by_as_owner()"):
        cur.execute("SELECT to_regprocedure(%s)", (fn,))
        assert cur.fetchone()[0] is None, fn
    for table in ("scrna_datasets", *load.CHILD_TABLES, *load.READ_ONLY_TABLES):
        cur.execute("SELECT has_table_privilege('bloom_workflows', %s, 'SELECT'), "
                    "has_any_column_privilege('bloom_workflows', %s, 'INSERT')",
                    (f"public.{table}", f"public.{table}"))
        assert cur.fetchone() == (False, False), table
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = 'rnaseq_runs' AND column_name = 'dataset_id'")
    assert cur.fetchone() is None
    cur.execute("SELECT count(*) FROM pg_policies WHERE roles = '{bloom_workflows}' "
                "AND (tablename IN ('species', 'scrna_datasets', 'scrna_clusters', "
                "'scrna_genotypes', 'scrna_cells', 'scrna_genes', 'scrna_counts', "
                "'scrna_cluster_stats', 'scrna_cluster_neighbors', 'scrna_de') "
                "OR policyname LIKE 'workflows\\_%\\_scrna')")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT tgfoid::regproc::text FROM pg_trigger "
                "WHERE tgname = 'set_created_by_scrna_datasets'")
    assert cur.fetchone()[0] == "set_created_by"
    cur.execute("SELECT count(*) FROM public.scrna_datasets WHERE id = %s", (dataset_id,))
    assert cur.fetchone()[0] == 1, "the rollback removed a loaded dataset"
