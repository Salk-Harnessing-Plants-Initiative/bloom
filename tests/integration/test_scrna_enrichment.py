"""
Integration tests for a dataset's genome version and its enrichment runs: linking, the upload
handshake, what a term may name, write-once rules, privileges, and the migrations' rollbacks.
"""

import json

import pytest

from tests.integration.annotation_enrichment_helpers import (
    MIGRATIONS,
    PARAMS,
    ROLLBACKS,
    add_dataset,
    add_de,
    add_run_terms,
    apply_migrations,
    call,
    finish_run,
    link,
    ready_build,
    start_run,
    term_row,
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
)
from tests.integration.test_rnaseq_runs import _sql_body

psycopg = pytest.importorskip("psycopg")


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
    dataset_id, genes = add_dataset(cur, species_id)
    link(cur, dataset_id, version_id)
    de_run, de_ids = add_de(cur, dataset_id)
    return {"dataset": dataset_id, "genes": genes, "de_run": de_run, "de": de_ids,
            "build": ready_build(cur, version_id)}


def _genome_of(cur, dataset_id):
    cur.execute("SELECT genome_version_id FROM scrna_datasets WHERE id = %s", (dataset_id,))
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# A dataset's genome version
# --------------------------------------------------------------------------- #


def test_a_run_gives_its_dataset_its_genome_version(cur, species_id, version_id):
    dataset_id, _ = add_dataset(cur, species_id)
    run_id = add_run(cur, version_id)
    cur.execute("UPDATE rnaseq_runs SET dataset_id = %s WHERE id = %s", (dataset_id, run_id))
    assert _genome_of(cur, dataset_id) == version_id


def test_a_run_on_another_species_leaves_the_dataset_unlinked(cur, species_id, version_id):
    dataset_id, _ = add_dataset(cur, add_species(cur))
    run_id = add_run(cur, version_id)
    cur.execute("UPDATE rnaseq_runs SET dataset_id = %s WHERE id = %s", (dataset_id, run_id))
    assert _genome_of(cur, dataset_id) is None


def test_a_writer_links_an_older_dataset(cur, species_id, version_id):
    dataset_id, _ = add_dataset(cur, species_id)
    link(cur, dataset_id, version_id)
    assert _genome_of(cur, dataset_id) == version_id


def test_a_link_never_changes(cur, species_id, version_id):
    dataset_id, _ = add_dataset(cur, species_id)
    link(cur, dataset_id, version_id)
    other = ready(cur, new_genome_name(), species_id)[0]
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT public.link_scrna_dataset_genome(%s, %s)", (dataset_id, other),
            psycopg.errors.ObjectNotInPrerequisiteState, match="already linked")
    # Refused for every role, a writer's direct update and the superuser's alike.
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "UPDATE scrna_datasets SET genome_version_id = NULL WHERE id = %s", (dataset_id,),
            psycopg.errors.ObjectNotInPrerequisiteState)
    as_admin(cur)
    refused(cur, "UPDATE scrna_datasets SET genome_version_id = %s WHERE id = %s", (other, dataset_id),
            psycopg.errors.ObjectNotInPrerequisiteState)
    refused(cur, "UPDATE scrna_datasets SET species_id = %s WHERE id = %s",
            (add_species(cur), dataset_id), psycopg.errors.ObjectNotInPrerequisiteState)


def test_a_genome_of_another_species_is_refused(cur, version_id):
    dataset_id, _ = add_dataset(cur, add_species(cur))
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT public.link_scrna_dataset_genome(%s, %s)", (dataset_id, version_id),
            psycopg.errors.InvalidParameterValue, match="not the dataset's species")


# --------------------------------------------------------------------------- #
# Starting a run
# --------------------------------------------------------------------------- #


def test_a_run_needs_a_linked_dataset(cur, species_id, version_id):
    dataset_id, _ = add_dataset(cur, species_id)
    de_run, _ = add_de(cur, dataset_id)
    build_id = ready_build(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT public.start_scrna_enrichment_run(%s, %s, %s, '{}')",
            (dataset_id, de_run, build_id), psycopg.errors.ObjectNotInPrerequisiteState,
            match="not linked to a genome version")


def test_the_annotation_must_be_of_the_datasets_genome(cur, species_id, linked):
    other_build = ready_build(cur, ready(cur, new_genome_name(), species_id)[0])
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT public.start_scrna_enrichment_run(%s, %s, %s, '{}')",
            (linked["dataset"], linked["de_run"], other_build),
            psycopg.errors.InvalidParameterValue, match="not the dataset's")


@pytest.mark.parametrize("status", ["running", "failed"])
def test_the_analysis_must_be_complete(cur, linked, status):
    de_run, _ = add_de(cur, linked["dataset"], status=status)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT public.start_scrna_enrichment_run(%s, %s, %s, '{}')",
            (linked["dataset"], de_run, linked["build"]), psycopg.errors.ObjectNotInPrerequisiteState)


def test_the_same_settings_run_once_while_live(cur, linked):
    start_run(cur, linked["dataset"], linked["de_run"], linked["build"])
    as_role(cur, "bloom_writer", OTHER_WRITER)
    refused(cur, "SELECT public.start_scrna_enrichment_run(%s, %s, %s, %s, NULL)",
            (linked["dataset"], linked["de_run"], linked["build"], json.dumps(PARAMS)),
            psycopg.errors.UniqueViolation)
    assert start_run(cur, linked["dataset"], linked["de_run"], linked["build"],
                     params={**PARAMS, "top_n": 200}, user=OTHER_WRITER)


def test_a_run_records_the_pipeline_job_that_made_it(cur, version_id, linked):
    job = add_run(cur, version_id)
    run_id = start_run(cur, linked["dataset"], linked["de_run"], linked["build"], rnaseq_run_id=job)
    cur.execute("SELECT rnaseq_run_id FROM scrna_enrichment_runs WHERE id = %s", (run_id,))
    assert cur.fetchone()[0] == job
    refused(cur, "UPDATE scrna_enrichment_runs SET rnaseq_run_id = NULL WHERE id = %s", (run_id,),
            psycopg.errors.ObjectNotInPrerequisiteState)


def test_a_run_uploaded_by_hand_has_no_job(cur, linked):
    run_id = start_run(cur, linked["dataset"], linked["de_run"], linked["build"])
    cur.execute("SELECT rnaseq_run_id FROM scrna_enrichment_runs WHERE id = %s", (run_id,))
    assert cur.fetchone()[0] is None


def test_an_unknown_job_is_refused(cur, linked):
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT public.start_scrna_enrichment_run(%s, %s, %s, '{}', -1)",
            (linked["dataset"], linked["de_run"], linked["build"]),
            psycopg.errors.InvalidParameterValue, match="no Cell Ranger or enrichment job")


# --------------------------------------------------------------------------- #
# Terms
# --------------------------------------------------------------------------- #


def test_a_run_is_stored_and_finished(cur, linked):
    run_id = start_run(cur, linked["dataset"], linked["de_run"], linked["build"])
    genes = [linked["genes"][g] for g in ("G1", "G2")]
    rows = [term_row(linked["de"][0], genes),
            term_row(linked["de"][0], genes[:1], kind="kegg_pathway", term_id="map00195",
                     term_size=1)]
    assert add_run_terms(cur, run_id, rows) == 2
    finish_run(cur, run_id, 2)
    cur.execute("SELECT status, term_count FROM scrna_enrichment_runs WHERE id = %s", (run_id,))
    assert cur.fetchone() == ("ready", 2)


def test_a_run_with_nothing_significant_finishes_empty(cur, linked):
    run_id = start_run(cur, linked["dataset"], linked["de_run"], linked["build"])
    finish_run(cur, run_id, 0)
    cur.execute("SELECT term_count FROM scrna_enrichment_runs WHERE id = %s", (run_id,))
    assert cur.fetchone()[0] == 0


@pytest.mark.parametrize(
    "change, error, match",
    [
        ({"kind": "mapman", "term_id": "1.1"}, psycopg.errors.InvalidParameterValue, "holds only"),
        ({"gene_ids": [-1]}, psycopg.errors.InvalidParameterValue, "gene of dataset"),
        ({"hit_count": 4}, psycopg.errors.CheckViolation, None),
        ({"fdr": 0.0001}, psycopg.errors.CheckViolation, None),
        ({"list_size": 5}, psycopg.errors.CheckViolation, None),
        ({"direction": "both"}, psycopg.errors.CheckViolation, None),
    ],
)
def test_bad_terms_are_refused(cur, linked, change, error, match):
    run_id = start_run(cur, linked["dataset"], linked["de_run"], linked["build"])
    row = {**term_row(linked["de"][0], [linked["genes"]["G1"]]), **change}
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT public.add_scrna_enrichment_terms(%s, %s)", (run_id, json.dumps([row])),
            error, match=match)


def test_a_term_must_name_a_comparison_of_the_runs_analysis(cur, linked):
    run_id = start_run(cur, linked["dataset"], linked["de_run"], linked["build"])
    _, other_de = add_de(cur, linked["dataset"])
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT public.add_scrna_enrichment_terms(%s, %s)",
            (run_id, json.dumps([term_row(other_de[0], [linked["genes"]["G1"]])])),
            psycopg.errors.InvalidParameterValue, match="comparison of differential expression")


def test_finish_needs_the_count_sent(cur, linked):
    run_id = start_run(cur, linked["dataset"], linked["de_run"], linked["build"])
    add_run_terms(cur, run_id, [term_row(linked["de"][0], [linked["genes"]["G1"]])])
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT public.finish_scrna_enrichment_run(%s, 2)", (run_id,),
            psycopg.errors.InvalidParameterValue, match="holds 1 terms")


def test_another_writer_cannot_add_terms(cur, linked):
    run_id = start_run(cur, linked["dataset"], linked["de_run"], linked["build"])
    as_role(cur, "bloom_writer", OTHER_WRITER)
    refused(cur, "SELECT public.add_scrna_enrichment_terms(%s, %s)",
            (run_id, json.dumps([term_row(linked["de"][0], [linked["genes"]["G1"]])])),
            psycopg.errors.InsufficientPrivilege, match="someone else")


def test_abandon_removes_the_terms(cur, linked):
    run_id = start_run(cur, linked["dataset"], linked["de_run"], linked["build"])
    add_run_terms(cur, run_id, [term_row(linked["de"][0], [linked["genes"]["G1"]])])
    assert call(cur, "SELECT public.abandon_scrna_enrichment_run(%s)", (run_id,)) is True
    cur.execute("SELECT count(*) FROM scrna_enrichment_terms WHERE run_id = %s", (run_id,))
    assert cur.fetchone()[0] == 0


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE scrna_enrichment_runs SET term_count = 5 WHERE id = %(id)s",
        "UPDATE scrna_enrichment_runs SET status = 'abandoned' WHERE id = %(id)s",
        "DELETE FROM scrna_enrichment_runs WHERE id = %(id)s",
        "DELETE FROM scrna_enrichment_terms WHERE run_id = %(id)s",
        "UPDATE scrna_enrichment_terms SET fdr = 0.02 WHERE run_id = %(id)s",
    ],
)
def test_a_ready_run_never_changes(cur, linked, sql):
    run_id = start_run(cur, linked["dataset"], linked["de_run"], linked["build"])
    add_run_terms(cur, run_id, [term_row(linked["de"][0], [linked["genes"]["G1"]])])
    finish_run(cur, run_id, 1)
    refused(cur, sql, {"id": run_id}, psycopg.errors.ObjectNotInPrerequisiteState)


# --------------------------------------------------------------------------- #
# Privileges, applying again, rolling back
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("function", [
    "link_scrna_dataset_genome(bigint, bigint)",
    "start_scrna_enrichment_run(bigint, bigint, bigint, jsonb, bigint)",
    "add_scrna_enrichment_terms(bigint, jsonb)",
    "finish_scrna_enrichment_run(bigint, integer)",
    "abandon_scrna_enrichment_run(bigint)",
])
@pytest.mark.parametrize("role, allowed", [("bloom_writer", True), ("bloom_workflows", True),
                                           ("bloom_user", False), ("anon", False)])
def test_who_may_upload(cur, function, role, allowed):
    cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, function))
    assert cur.fetchone()[0] is allowed


@pytest.mark.parametrize("table", ["scrna_enrichment_runs", "scrna_enrichment_terms"])
def test_readers_select_and_only_admin_writes(cur, table):
    for role, privilege, allowed in (("bloom_user", "SELECT", True), ("bloom_agent", "SELECT", True),
                                     ("bloom_writer", "INSERT", False),
                                     ("bloom_workflows", "INSERT", False)):
        cur.execute("SELECT has_table_privilege(%s, %s, %s)", (role, f"public.{table}", privilege))
        assert cur.fetchone()[0] is allowed


def test_the_migrations_apply_twice(cur, linked):
    for migration in MIGRATIONS:
        cur.execute(_sql_body(migration))
    assert _genome_of(cur, linked["dataset"]) is not None


def test_the_rollbacks_remove_everything(cur, linked):
    for rollback in ROLLBACKS:
        cur.execute(_sql_body(rollback))
    cur.execute("SELECT to_regclass('public.genome_gene_terms'), "
                "to_regclass('public.scrna_enrichment_terms')")
    assert cur.fetchone() == (None, None)
    cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = "
                "'scrna_datasets' AND column_name = 'genome_version_id'")
    assert cur.fetchone()[0] == 0
