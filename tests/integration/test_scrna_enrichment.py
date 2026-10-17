"""
Integration tests for a dataset's genome version and its enrichment runs: linking, starting
and finishing against the stored file, write-once rules, the storage rules for enrichment
files, privileges, and the migrations' rollbacks.
"""

import json

import pytest

from tests.integration.annotation_enrichment_helpers import (
    FILE_BYTES,
    MIGRATIONS,
    PARAMS,
    ROLLBACKS,
    SHA,
    add_dataset,
    add_de,
    apply_migrations,
    call,
    finish_run,
    link,
    put_file,
    ready_build,
    start_run,
)
from tests.integration.genome_reference_helpers import (
    OTHER_WRITER,
    WRITER,
    add_run,
    add_species,
    as_admin,
    as_role,
    new_genome_name,
    ready,
    refused,
    start as start_version,
)
from tests.integration import _scrna_pipeline_load as load
from tests.integration.test_rnaseq_runs import _sql_body

psycopg = pytest.importorskip("psycopg")

START = "SELECT * FROM public.start_scrna_enrichment_run(%s, %s, %s, %s, %s)"
FINISH = "SELECT public.finish_scrna_enrichment_run(%s, %s, %s, %s, %s, %s)"


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        apply_migrations(c)
        yield c
    pg_conn.rollback()


@pytest.fixture
def species_id(cur):
    return add_species(cur)


@pytest.fixture
def version_id(cur, species_id):
    return ready(cur, new_genome_name(), species_id)[0]


@pytest.fixture
def linked(cur, species_id, version_id):
    """A linked dataset with a finished analysis and a ready eggNOG build."""
    dataset_id, _ = add_dataset(cur, species_id)
    link(cur, dataset_id, version_id)
    de_run, _ = add_de(cur, dataset_id)
    return {"dataset": dataset_id, "de_run": de_run, "build": ready_build(cur, version_id)}


def _genome_of(cur, dataset_id):
    cur.execute("SELECT genome_version_id FROM scrna_datasets WHERE id = %s", (dataset_id,))
    return cur.fetchone()[0]


def _start(cur, linked, **kw):
    return start_run(cur, linked["dataset"], linked["de_run"], linked["build"], **kw)


def _run(cur, run_id):
    cur.execute("SELECT status, file_sha256, file_bytes, term_count, rnaseq_run_id "
                "FROM scrna_enrichment_runs WHERE id = %s", (run_id,))
    return cur.fetchone()


def _gene_counts(cur, run_id):
    cur.execute("SELECT background_genes, annotated_genes FROM scrna_enrichment_runs "
                "WHERE id = %s", (run_id,))
    return cur.fetchone()


# --------------------------------------------------------------------------- #
# A dataset's genome version
# --------------------------------------------------------------------------- #


def _loaded_by_a_run(cur, species_id, run_version):
    """A pipeline-loaded dataset not yet linked to its Cell Ranger run, which records
    `run_version`: (pipeline user, run id, dataset id)."""
    pipeline, scientist = load.user(cur, workflows=True), load.user(cur)
    run_id = load.run(cur, scientist, species_id)
    cur.execute("UPDATE rnaseq_runs SET genome_version_id = %s WHERE id = %s", (run_version, run_id))
    return pipeline, run_id, load.loaded(cur, pipeline, species_id, run_id=run_id)


def _linked_by_a_run(cur, species_id, run_version):
    """A pipeline-loaded dataset linked to its Cell Ranger run, which records `run_version`."""
    pipeline, run_id, dataset_id = _loaded_by_a_run(cur, species_id, run_version)
    load.link(cur, pipeline, run_id, dataset_id)
    return dataset_id


def _withdraw(cur, version_id):
    as_admin(cur)
    cur.execute("UPDATE genome_reference_versions SET status = 'withdrawn', withdrawn_reason = 'x' "
                "WHERE id = %s", (version_id,))


def test_linking_a_run_gives_its_dataset_the_runs_genome_version(cur, species_id, version_id):
    assert _genome_of(cur, _linked_by_a_run(cur, species_id, version_id)) == version_id


def test_a_run_on_a_genome_of_another_species_leaves_the_dataset_for_a_writer(cur, species_id):
    elsewhere = ready(cur, new_genome_name(), add_species(cur))[0]
    assert _genome_of(cur, _linked_by_a_run(cur, species_id, elsewhere)) is None


def test_a_run_on_a_withdrawn_genome_version_leaves_the_dataset_for_a_writer(
    cur, species_id, version_id
):
    pipeline, run_id, dataset_id = _loaded_by_a_run(cur, species_id, version_id)
    _withdraw(cur, version_id)
    load.link(cur, pipeline, run_id, dataset_id)
    assert _genome_of(cur, dataset_id) is None


def test_a_run_never_replaces_a_writers_link(cur, species_id, version_id):
    pipeline, run_id, dataset_id = _loaded_by_a_run(cur, species_id, version_id)
    writers = ready(cur, new_genome_name(), species_id)[0]
    link(cur, dataset_id, writers)
    load.link(cur, pipeline, run_id, dataset_id)
    assert _genome_of(cur, dataset_id) == writers


def test_a_writer_links_an_older_dataset_once(cur, species_id, version_id):
    dataset_id, _ = add_dataset(cur, species_id)
    link(cur, dataset_id, version_id)
    assert _genome_of(cur, dataset_id) == version_id
    other = ready(cur, new_genome_name(), species_id)[0]
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT public.link_scrna_dataset_genome(%s, %s)", (dataset_id, other),
            psycopg.errors.ObjectNotInPrerequisiteState, match="already linked")


def test_a_genome_of_another_species_is_refused(cur, version_id):
    dataset_id, _ = add_dataset(cur, add_species(cur))
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT public.link_scrna_dataset_genome(%s, %s)", (dataset_id, version_id),
            psycopg.errors.InvalidParameterValue, match="not the dataset's species")


def test_a_genome_version_that_is_not_ready_is_refused(cur, species_id):
    dataset_id, _ = add_dataset(cur, species_id)
    uploading = start_version(cur, new_genome_name(), species=species_id)[0]
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT public.link_scrna_dataset_genome(%s, %s)", (dataset_id, uploading),
            psycopg.errors.InvalidParameterValue, match="is not ready")


# --------------------------------------------------------------------------- #
# Starting a run
# --------------------------------------------------------------------------- #


def test_a_run_gets_its_file_path(cur, linked):
    run_id, path = _start(cur, linked)
    assert path == f"enrichment/{linked['dataset']}/run-{run_id}.json.gz"
    assert _run(cur, run_id)[0] == "uploading"


def test_a_run_needs_a_linked_dataset(cur, species_id, version_id):
    dataset_id, _ = add_dataset(cur, species_id)
    de_run, _ = add_de(cur, dataset_id)
    build_id = ready_build(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, START, (dataset_id, de_run, build_id, "{}", None),
            psycopg.errors.ObjectNotInPrerequisiteState, match="not linked to a genome version")


def test_the_annotation_must_be_of_the_datasets_genome(cur, species_id, linked):
    other_build = ready_build(cur, ready(cur, new_genome_name(), species_id)[0])
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, START, (linked["dataset"], linked["de_run"], other_build, "{}", None),
            psycopg.errors.InvalidParameterValue, match="not the dataset's")


@pytest.mark.parametrize("status", ["running", "failed"])
def test_the_analysis_must_be_complete(cur, linked, status):
    de_run, _ = add_de(cur, linked["dataset"], status=status)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, START, (linked["dataset"], de_run, linked["build"], "{}", None),
            psycopg.errors.ObjectNotInPrerequisiteState)


def test_the_same_settings_run_once_while_live(cur, linked):
    _start(cur, linked)
    as_role(cur, "bloom_writer", OTHER_WRITER)
    refused(cur, START, (linked["dataset"], linked["de_run"], linked["build"], json.dumps(PARAMS), None),
            psycopg.errors.UniqueViolation)
    assert _start(cur, linked, params={**PARAMS, "top_n": 200}, user=OTHER_WRITER)


def test_a_run_records_the_pipeline_job_that_made_it(cur, version_id, linked):
    job = add_run(cur, version_id)
    run_id, _ = _start(cur, linked, rnaseq_run_id=job)
    assert _run(cur, run_id)[4] == job
    refused(cur, "UPDATE scrna_enrichment_runs SET rnaseq_run_id = NULL WHERE id = %s", (run_id,),
            psycopg.errors.ObjectNotInPrerequisiteState)


def test_an_unknown_job_is_refused(cur, linked):
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, START, (linked["dataset"], linked["de_run"], linked["build"], "{}", -1),
            psycopg.errors.InvalidParameterValue, match="no Cell Ranger or enrichment job")


# --------------------------------------------------------------------------- #
# Finishing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("terms", [2, 0])
def test_finish_records_the_file(cur, linked, terms):
    run_id, path = _start(cur, linked)
    put_file(cur, "scrna", path)
    finish_run(cur, run_id, terms=terms)
    assert _run(cur, run_id)[:4] == ("ready", SHA, FILE_BYTES, terms)
    assert _gene_counts(cur, run_id) == (4, 3)


def test_finish_records_no_annotated_genes(cur, linked):
    """Zero annotated genes is stored, so a gene ID mismatch shows instead of looking empty."""
    run_id, path = _start(cur, linked)
    put_file(cur, "scrna", path)
    finish_run(cur, run_id, terms=0, background=4, annotated=0)
    assert _gene_counts(cur, run_id) == (4, 0)


@pytest.mark.parametrize("stored, args, match", [
    (False, (SHA, FILE_BYTES, 2, 4, 3), "has not been uploaded"),
    (True, (SHA, FILE_BYTES + 1, 2, 4, 3), "bytes in storage"),
    (True, ("x", FILE_BYTES, 2, 4, 3), "64 lowercase hex"),
    (True, (SHA, FILE_BYTES, -1, 4, 3), "zero or more"),
    (True, (SHA, FILE_BYTES, 2, 0, 0), "at least one gene"),
    (True, (SHA, FILE_BYTES, 2, None, 3), "at least one gene"),
    (True, (SHA, FILE_BYTES, 2, 4, 5), "between 0 and the 4 background genes"),
    (True, (SHA, FILE_BYTES, 2, 4, -1), "between 0 and the 4 background genes"),
    (True, (SHA, FILE_BYTES, 2, 4, None), "between 0 and the 4 background genes"),
])
def test_finish_with_bad_details_is_refused(cur, linked, stored, args, match):
    run_id, path = _start(cur, linked)
    if stored:
        put_file(cur, "scrna", path)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, FINISH, (run_id, *args), psycopg.errors.InvalidParameterValue, match=match)


def test_another_writer_cannot_finish(cur, linked):
    run_id, path = _start(cur, linked)
    put_file(cur, "scrna", path)
    as_role(cur, "bloom_writer", OTHER_WRITER)
    refused(cur, FINISH, (run_id, SHA, FILE_BYTES, 2, 4, 3), psycopg.errors.InsufficientPrivilege,
            match="someone else")


def test_abandon(cur, linked):
    run_id, _ = _start(cur, linked)
    assert call(cur, "SELECT public.abandon_scrna_enrichment_run(%s)", (run_id,))[0] is True
    assert _run(cur, run_id)[0] == "abandoned"


@pytest.mark.parametrize("sql", [
    "UPDATE scrna_enrichment_runs SET term_count = 5 WHERE id = %(id)s",
    "UPDATE scrna_enrichment_runs SET annotated_genes = 1 WHERE id = %(id)s",
    "UPDATE scrna_enrichment_runs SET file_path = 'x' WHERE id = %(id)s",
    "UPDATE scrna_enrichment_runs SET status = 'abandoned' WHERE id = %(id)s",
    "DELETE FROM scrna_enrichment_runs WHERE id = %(id)s",
])
def test_a_ready_run_never_changes(cur, linked, sql):
    run_id, path = _start(cur, linked)
    put_file(cur, "scrna", path)
    finish_run(cur, run_id)
    refused(cur, sql, {"id": run_id}, psycopg.errors.ObjectNotInPrerequisiteState)


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #


def _insert_refused(cur, name, user=WRITER, role="bloom_writer"):
    as_role(cur, role, user)
    refused(cur, "INSERT INTO storage.objects (bucket_id, name, metadata) VALUES "
            "('scrna', %s, '{\"size\": 1}')", (name,), psycopg.errors.InsufficientPrivilege)
    as_admin(cur)


def test_only_the_runs_own_file_is_added_under_enrichment(cur, linked):
    _, path = _start(cur, linked)
    _insert_refused(cur, path, user=OTHER_WRITER)
    _insert_refused(cur, f"enrichment/{linked['dataset']}/run-999999.json.gz")
    _insert_refused(cur, f"enrichment/{linked['dataset']}/run-999999.json.gz", role="bloom_workflows")
    put_file(cur, "scrna", path)


def test_the_pipeline_adds_its_own_runs_file(cur, linked):
    pipeline = "00000000-0000-0000-0000-00000000aaaa"
    _, path = _start(cur, linked, user=pipeline, role="bloom_workflows")
    put_file(cur, "scrna", path, user=pipeline, role="bloom_workflows")


def test_writer_uploads_elsewhere_in_scrna_still_work(cur):
    put_file(cur, "scrna", "h5ad/test-enrichment-elsewhere.h5ad")


def test_a_writer_cannot_overwrite_an_enrichment_file(cur, linked):
    _, path = _start(cur, linked)
    put_file(cur, "scrna", path)
    as_role(cur, "bloom_writer", WRITER)
    cur.execute("UPDATE storage.objects SET metadata = '{\"size\": 2}' WHERE bucket_id = 'scrna' "
                "AND name = %s", (path,))
    assert cur.rowcount == 0
    as_admin(cur)


@pytest.mark.parametrize("command", ["UPDATE", "DELETE"])
def test_admin_cannot_change_a_ready_runs_file(cur, linked, command):
    run_id, path = _start(cur, linked)
    put_file(cur, "scrna", path)
    finish_run(cur, run_id)
    cur.execute("SET LOCAL storage.allow_delete_query = 'true'")
    as_role(cur, "bloom_admin", WRITER)
    cur.execute(
        "UPDATE storage.objects SET metadata = '{\"size\": 2}' WHERE name = %s" if command == "UPDATE"
        else "DELETE FROM storage.objects WHERE name = %s", (path,),
    )
    assert cur.rowcount == 0
    as_admin(cur)


# --------------------------------------------------------------------------- #
# Privileges, applying again, rolling back
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("function", [
    "link_scrna_dataset_genome(bigint, bigint)",
    "start_scrna_enrichment_run(bigint, bigint, bigint, jsonb, bigint)",
    "finish_scrna_enrichment_run(bigint, text, bigint, integer, integer, integer)",
    "abandon_scrna_enrichment_run(bigint)",
])
@pytest.mark.parametrize("role, allowed", [("bloom_writer", True), ("bloom_workflows", True),
                                           ("bloom_user", False), ("anon", False)])
def test_who_may_upload(cur, function, role, allowed):
    cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, function))
    assert cur.fetchone()[0] is allowed


@pytest.mark.parametrize("role, privilege, allowed", [
    ("bloom_user", "SELECT", True), ("bloom_agent", "SELECT", True),
    ("bloom_writer", "INSERT", False), ("bloom_workflows", "INSERT", False),
])
def test_table_access(cur, role, privilege, allowed):
    cur.execute("SELECT has_table_privilege(%s, 'public.scrna_enrichment_runs', %s)", (role, privilege))
    assert cur.fetchone()[0] is allowed


def test_the_migrations_apply_twice(cur, linked):
    for migration in MIGRATIONS:
        cur.execute(_sql_body(migration))
    assert _genome_of(cur, linked["dataset"]) is not None


def test_the_rollbacks_remove_everything(cur, linked):
    for rollback in ROLLBACKS:
        cur.execute(_sql_body(rollback))
    cur.execute("SELECT to_regclass('public.genome_annotation_builds'), "
                "to_regclass('public.scrna_enrichment_runs')")
    assert cur.fetchone() == (None, None)
    cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = "
                "'scrna_datasets' AND column_name = 'genome_version_id'")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT pg_get_functiondef("
                "'public.link_rnaseq_run_dataset(bigint, bigint)'::regprocedure)")
    assert "genome_version_id" not in cur.fetchone()[0], "the run link is restored"
