"""
Integration tests for link_rnaseq_run_dataset, which the status poller calls once a Cell
Ranger run's load-dataset step succeeds: it links the run to the dataset the pipeline loaded,
copies the form's details into it and gives it to the scientist who asked for the run; it
refuses an unfinished dataset, one the pipeline didn't load, or one already linked. Also: the
migration runs again, and the rollback takes the pipeline's access back.

LOCAL ONLY: `pg_conn` connects as `supabase_admin` (BYPASSRLS) and every test rolls back.
"""

import pytest

from tests.integration.test_pipeline_scrna_dataset_load import (
    CHILD_TABLES,
    LINK,
    MAY_LOAD,
    MIGRATION,
    READ_ONLY_TABLES,
    ROLLBACK,
    _as,
    _create,
    _finish,
    _link,
    _loaded,
    _refused,
    _run,
    _species,
    _user,
)
from tests.integration.test_rnaseq_runs import _sql_body

psycopg = pytest.importorskip("psycopg")


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        yield c
    pg_conn.rollback()


@pytest.fixture
def pipeline(cur):
    return _user(cur, workflows=True)


@pytest.fixture
def scientist(cur):
    return _user(cur)


@pytest.fixture
def species(cur):
    return _species(cur)


# --------------------------------------------------------------------------- #
# Linking a run to its dataset
# --------------------------------------------------------------------------- #


def test_a_run_is_linked_and_its_dataset_handed_to_the_scientist(cur, pipeline, scientist,
                                                                 species):
    dataset_id = _loaded(cur, pipeline, species)
    run_id = _run(cur, scientist, species, accession="GSE1", citation="Doe 2026",
                  source_url="https://example.org/gse1", attributes={"tissue": "root"})
    assert _link(cur, pipeline, run_id) == dataset_id
    cur.execute("SELECT dataset_id FROM public.rnaseq_runs WHERE id = %s", (run_id,))
    assert cur.fetchone()[0] == dataset_id
    cur.execute("SELECT url, metadata, created_by::text FROM public.scrna_datasets "
                "WHERE id = %s", (dataset_id,))
    url, metadata, owner = cur.fetchone()
    assert url == "https://example.org/gse1"
    assert metadata["source"] == {"origin": "public", "accession": "GSE1",
                                  "citation": "Doe 2026", "attributes": {"tissue": "root"},
                                  "rnaseq_run_id": run_id}
    assert metadata["expected_cells"] == 1, "the load's own metadata was replaced"
    assert owner == scientist
    _as(cur, pipeline)
    assert _finish(cur, dataset_id) == 0, "the pipeline can still write the scientist's dataset"


def test_linking_again_returns_the_same_dataset(cur, pipeline, scientist, species):
    dataset_id = _loaded(cur, pipeline, species)
    run_id = _run(cur, scientist, species)
    assert _link(cur, pipeline, run_id) == _link(cur, pipeline, run_id) == dataset_id


@pytest.mark.parametrize("case, error", [
    ("unfinished", psycopg.errors.ObjectNotInPrerequisiteState),
    ("missing", psycopg.errors.NoDataFound),
    ("not the pipeline's", psycopg.errors.InsufficientPrivilege),
    ("queued run", psycopg.errors.ObjectNotInPrerequisiteState),
    ("already linked", psycopg.errors.UniqueViolation),
])
def test_a_run_isnt_linked_to_the_wrong_dataset(cur, pipeline, scientist, species, case, error):
    status = "queued" if case == "queued run" else "running"
    if case == "unfinished":
        _as(cur, pipeline)
        _create(cur, species)
        cur.execute("RESET ROLE")
    elif case == "not the pipeline's":
        _as(cur, scientist, role="bloom_writer")
        dataset_id, _ = _create(cur, species)
        cur.execute("RESET ROLE")
        cur.execute("UPDATE public.scrna_datasets SET ingested_at = now() WHERE id = %s",
                    (dataset_id,))
    elif case != "missing":
        dataset_id = _loaded(cur, pipeline, species)
        if case == "already linked":
            other = _run(cur, scientist, species, sample="root2")
            cur.execute("UPDATE public.rnaseq_runs SET dataset_id = %s WHERE id = %s",
                        (dataset_id, other))
    run_id = _run(cur, scientist, species, status=status)
    _as(cur, pipeline)
    _refused(cur, "SELECT public.link_rnaseq_run_dataset(%s)", (run_id,), error=error)


# --------------------------------------------------------------------------- #
# Re-run and rollback
# --------------------------------------------------------------------------- #


def test_the_migration_can_be_run_again(cur, pipeline, scientist, species):
    cur.execute(_sql_body(MIGRATION))
    dataset_id = _loaded(cur, pipeline, species)
    assert _link(cur, pipeline, _run(cur, scientist, species)) == dataset_id


def test_the_rollback_takes_the_pipelines_access_back(cur, pipeline, scientist, species):
    dataset_id = _loaded(cur, pipeline, species)
    cur.execute(_sql_body(ROLLBACK))
    for fn in (LINK, MAY_LOAD, "public.set_created_by_as_owner()"):
        cur.execute("SELECT to_regprocedure(%s)", (fn,))
        assert cur.fetchone()[0] is None, fn
    for table in ("scrna_datasets", *CHILD_TABLES, *READ_ONLY_TABLES):
        cur.execute("SELECT has_table_privilege('bloom_workflows', %s, 'SELECT'), "
                    "has_any_column_privilege('bloom_workflows', %s, 'INSERT')",
                    (f"public.{table}", f"public.{table}"))
        assert cur.fetchone() == (False, False), table
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = 'rnaseq_runs' AND column_name = 'dataset_id'")
    assert cur.fetchone() is None
    cur.execute("SELECT count(*) FROM pg_policies WHERE policyname LIKE 'workflows\\_%%scrna%%' "
                "OR (policyname = 'workflows_select_species')")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT tgfoid::regproc::text FROM pg_trigger "
                "WHERE tgname = 'set_created_by_scrna_datasets'")
    assert cur.fetchone()[0] == "set_created_by"
    cur.execute("SELECT has_table_privilege('bloom_user', 'public.scrna_datasets', 'INSERT')")
    assert cur.fetchone()[0] is True, "the rollback didn't give bloom_user its insert back"
    cur.execute("SELECT count(*) FROM public.scrna_datasets WHERE id = %s", (dataset_id,))
    assert cur.fetchone()[0] == 1, "the rollback removed a loaded dataset"
