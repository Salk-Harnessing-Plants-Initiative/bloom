"""
Integration tests for genome browser builds: numbering and folders, finishing against the
stored files, the five-role file record, write-once rules, the storage rules for build files,
and who may do what.
"""

import json

import pytest

from tests.integration.annotation_enrichment_helpers import (
    ready_build as ready_annotation_build,
)
from tests.integration.genome_browser_helpers import (
    BUCKET,
    FILES,
    BROWSER_MIGRATIONS,
    ROLLBACKS,
    SHA,
    TOOLS,
    add_genes,
    apply_migrations,
    files_json,
    finish_build,
    put_files,
    ready_build,
    start_build,
)
from tests.integration.test_rnaseq_runs import _sql_body
from tests.integration.genome_reference_helpers import (
    OTHER_WRITER,
    WRITER,
    add_species,
    as_admin,
    as_role,
    new_genome_name,
    put_object,
    ready,
    refused,
    start,
)

psycopg = pytest.importorskip("psycopg")

FINISH = "SELECT public.finish_genome_browser_build(%s, %s, %s, %s)"
START = "SELECT * FROM public.start_genome_browser_build(%s, '{}')"
WRITER_FUNCTIONS = (
    "start_genome_browser_build(bigint, jsonb)",
    "add_genome_genes(bigint, jsonb)",
    "finish_genome_browser_build(bigint, jsonb, boolean, integer)",
    "abandon_genome_browser_build(bigint)",
)


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        apply_migrations(c)
        yield c
    pg_conn.rollback()


@pytest.fixture
def genome():
    return new_genome_name()


@pytest.fixture
def version_id(cur, genome):
    return ready(cur, genome, add_species(cur))[0]


def _build(cur, build_id):
    cur.execute("SELECT status, files, gtf_was_sorted, ready_at IS NOT NULL "
                "FROM genome_browser_builds WHERE id = %s", (build_id,))
    return cur.fetchone()


# --------------------------------------------------------------------------- #
# Starting
# --------------------------------------------------------------------------- #


def test_builds_are_numbered_per_version_with_their_folder(cur, genome, version_id):
    build_id, number, folder = start_build(cur, version_id)
    assert (number, folder) == (1, f"{genome}/v1/browser/b1")
    assert _build(cur, build_id) == ("uploading", None, None, False)


def test_the_tools_used_are_kept(cur, version_id):
    build_id, _, _ = start_build(cur, version_id)
    cur.execute("SELECT tool_versions FROM genome_browser_builds WHERE id = %s", (build_id,))
    assert cur.fetchone()[0] == TOOLS


def test_one_live_build_per_version(cur, version_id):
    start_build(cur, version_id)
    as_role(cur, "bloom_writer", OTHER_WRITER)
    refused(cur, START, (version_id,), psycopg.errors.ObjectNotInPrerequisiteState,
            match="already has browser build 1")


@pytest.mark.parametrize("state", ["uploading", "withdrawn"])
def test_a_version_that_is_not_ready_is_refused(cur, genome, state):
    species = add_species(cur)
    if state == "uploading":
        version_id = start(cur, genome, species=species)[0]
    else:
        version_id = ready(cur, genome, species)[0]
        cur.execute("UPDATE public.genome_reference_versions SET status = 'withdrawn', "
                    "withdrawn_reason = 'wrong assembly' WHERE id = %s", (version_id,))
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, START, (version_id,), psycopg.errors.ObjectNotInPrerequisiteState,
            match="not ready")


def test_an_unknown_version_is_refused(cur):
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, START, (2 ** 40,), psycopg.errors.ForeignKeyViolation, match="no genome version")


# --------------------------------------------------------------------------- #
# Finishing
# --------------------------------------------------------------------------- #


def test_finish_records_the_five_files(cur, version_id):
    build_id, folder = ready_build(cur, version_id)
    status, files, sorted_gtf, has_ready_at = _build(cur, build_id)
    assert (status, sorted_gtf, has_ready_at) == ("ready", True, True)
    assert files == {
        name: {"sha256": SHA, "bytes": size} for name, (_, size) in FILES.items()
    }


def test_finish_without_the_files_is_refused(cur, version_id):
    build_id, _, folder = start_build(cur, version_id)
    put_files(cur, folder, only={"fasta", "fasta_fai", "fasta_gzi", "gtf"})
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, FINISH, (build_id, files_json(), True, 2), psycopg.errors.InvalidParameterValue,
            match="genes.gtf.gz.tbi has not been uploaded")


def test_finish_against_a_different_size_is_refused(cur, version_id):
    build_id, _, folder = start_build(cur, version_id)
    put_files(cur, folder)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, FINISH, (build_id, files_json(fasta_gzi={"sha256": SHA, "bytes": 65}), True, 2),
            psycopg.errors.InvalidParameterValue, match="is 64 bytes in storage, not 65")


@pytest.mark.parametrize("files, match", [
    (files_json(gtf_tbi=None), "exactly the five"),
    (files_json(extra={"sha256": SHA, "bytes": 1}), "exactly the five"),
    (files_json(gtf={"sha256": "E" * 64, "bytes": 2048}), "exactly the five"),
    (files_json(gtf={"sha256": SHA, "bytes": 0}), "exactly the five"),
    (files_json(gtf={"sha256": SHA, "bytes": -1}), "exactly the five"),
    (files_json(gtf={"bytes": 2048}), "exactly the five"),
    (json.dumps(["genome.fa.gz"]), "exactly the five"),
])
def test_finish_with_a_bad_file_record_is_refused(cur, version_id, files, match):
    build_id, _, folder = start_build(cur, version_id)
    put_files(cur, folder)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, FINISH, (build_id, files, True, 2), psycopg.errors.InvalidParameterValue,
            match=match)


def test_another_writer_cannot_finish(cur, version_id):
    build_id, _, folder = start_build(cur, version_id)
    put_files(cur, folder)
    as_role(cur, "bloom_writer", OTHER_WRITER)
    refused(cur, FINISH, (build_id, files_json(), True, 2), psycopg.errors.InsufficientPrivilege,
            match="someone else")


def test_a_build_finishes_once(cur, version_id):
    build_id, _ = ready_build(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, FINISH, (build_id, files_json(), True, 2),
            psycopg.errors.ObjectNotInPrerequisiteState, match="is ready, not uploading")


def test_the_pipeline_can_make_a_build(cur, version_id):
    pipeline = "00000000-0000-0000-0000-00000000aaaa"
    build_id, _, folder = start_build(cur, version_id, user=pipeline, role="bloom_workflows")
    put_files(cur, folder, user=pipeline, role="bloom_workflows")
    add_genes(cur, build_id, user=pipeline, role="bloom_workflows")
    finish_build(cur, build_id, user=pipeline, role="bloom_workflows")
    assert _build(cur, build_id)[0] == "ready"


def test_a_build_records_an_unsorted_source_gtf(cur, version_id):
    build_id, _, folder = start_build(cur, version_id)
    put_files(cur, folder)
    add_genes(cur, build_id)
    finish_build(cur, build_id, sorted_gtf=False)
    assert _build(cur, build_id)[2] is False


# --------------------------------------------------------------------------- #
# Abandoning, withdrawing, write-once
# --------------------------------------------------------------------------- #


def test_abandon_frees_the_next_number(cur, version_id):
    build_id, _, _ = start_build(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    cur.execute("SELECT public.abandon_genome_browser_build(%s)", (build_id,))
    assert cur.fetchone()[0] is True
    as_admin(cur)
    assert _build(cur, build_id)[0] == "abandoned"
    assert start_build(cur, version_id)[1] == 2


def test_withdraw_allows_a_new_build_in_a_new_folder(cur, genome, version_id):
    build_id, _ = ready_build(cur, version_id)
    cur.execute("SELECT public.withdraw_genome_browser_build(%s, 'tabix index truncated')",
                (build_id,))
    assert _build(cur, build_id)[0] == "withdrawn"
    cur.execute("SELECT withdrawn_at IS NOT NULL FROM genome_browser_builds WHERE id = %s",
                (build_id,))
    assert cur.fetchone()[0] is True
    assert start_build(cur, version_id)[2] == f"{genome}/v1/browser/b2"


def test_withdraw_needs_a_reason(cur, version_id):
    build_id, _ = ready_build(cur, version_id)
    refused(cur, "SELECT public.withdraw_genome_browser_build(%s, '  ')", (build_id,),
            psycopg.errors.CheckViolation)


@pytest.mark.parametrize("assignment", [
    "files = '{}'", "gtf_was_sorted = false", "gene_count = 9", "folder = 'x'",
    "tool_versions = '{}'",
    "build = 7", "status = 'uploading'", "status = 'abandoned'",
])
def test_a_ready_build_never_changes(cur, version_id, assignment):
    build_id, _ = ready_build(cur, version_id)
    refused(cur, f"UPDATE genome_browser_builds SET {assignment} WHERE id = %s", (build_id,),
            (psycopg.errors.ObjectNotInPrerequisiteState, psycopg.errors.CheckViolation))


@pytest.mark.parametrize("status", ["ready", "withdrawn"])
def test_a_finished_build_cannot_be_deleted(cur, version_id, status):
    build_id, _ = ready_build(cur, version_id)
    if status == "withdrawn":
        cur.execute("SELECT public.withdraw_genome_browser_build(%s, 'remade')", (build_id,))
    refused(cur, "DELETE FROM genome_browser_builds WHERE id = %s", (build_id,),
            psycopg.errors.ObjectNotInPrerequisiteState)


def test_an_uploading_build_cannot_jump_to_withdrawn(cur, version_id):
    build_id, _, _ = start_build(cur, version_id)
    refused(cur, "UPDATE genome_browser_builds SET status = 'withdrawn', "
            "withdrawn_reason = 'no' WHERE id = %s", (build_id,),
            psycopg.errors.ObjectNotInPrerequisiteState, match="cannot go from uploading")


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #


def _insert_refused(cur, name, user=WRITER, role="bloom_writer"):
    as_role(cur, role, user)
    refused(cur, "INSERT INTO storage.objects (bucket_id, name, metadata) VALUES "
            "('genome-references', %s, '{\"size\": 1}')", (name,),
            psycopg.errors.InsufficientPrivilege)
    as_admin(cur)


def test_the_bucket_takes_the_index_files_and_not_anything(cur):
    cur.execute("SELECT allowed_mime_types, file_size_limit, public FROM storage.buckets "
                "WHERE id = %s", (BUCKET,))
    types, limit, is_public = cur.fetchone()
    assert set(types) == {"application/gzip", "application/octet-stream", "text/plain"}
    assert (limit, is_public) == (524288000, False)


def test_another_writer_cannot_add_a_build_file(cur, version_id):
    _, _, folder = start_build(cur, version_id)
    _insert_refused(cur, f"{folder}/genome.fa.gz.fai", user=OTHER_WRITER)


def test_no_file_is_taken_outside_the_builds_five_names(cur, genome, version_id):
    _, _, folder = start_build(cur, version_id)
    for name in (f"{folder}/genome.fa", f"{folder}/notes.txt",
                 f"{genome}/v1/browser/b9/genes.gtf.gz"):
        _insert_refused(cur, name)
        _insert_refused(cur, name, role="bloom_workflows")


def test_a_ready_builds_file_cannot_be_added_again(cur, version_id):
    build_id, folder = ready_build(cur, version_id)
    cur.execute("SET LOCAL storage.allow_delete_query = 'true'")
    cur.execute("DELETE FROM storage.objects WHERE name = %s", (f"{folder}/genes.gtf.gz.tbi",))
    _insert_refused(cur, f"{folder}/genes.gtf.gz.tbi")


@pytest.mark.parametrize("command", ["UPDATE", "DELETE"])
@pytest.mark.parametrize("role", ["bloom_admin", "bloom_writer"])
def test_a_finished_builds_files_cannot_be_changed(cur, version_id, command, role):
    _, folder = ready_build(cur, version_id)
    name = f"{folder}/genome.fa.gz.gzi"
    cur.execute("SET LOCAL storage.allow_delete_query = 'true'")
    as_role(cur, role, WRITER)
    cur.execute("SAVEPOINT no_effect")
    try:
        cur.execute(
            "UPDATE storage.objects SET metadata = '{\"size\": 2}' WHERE name = %s"
            if command == "UPDATE" else "DELETE FROM storage.objects WHERE name = %s", (name,),
        )
        assert cur.rowcount == 0
    except psycopg.errors.InsufficientPrivilege:
        pass
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT no_effect")
        as_admin(cur)


@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent", "bloom_workflows"])
def test_the_build_files_can_be_read(cur, version_id, role):
    _, folder = ready_build(cur, version_id)
    as_role(cur, role, WRITER)
    cur.execute("SELECT count(*) FROM storage.objects WHERE bucket_id = %s AND name LIKE %s",
                (BUCKET, f"{folder}/%"))
    assert cur.fetchone()[0] == 5
    as_admin(cur)


def test_genome_version_and_annotation_uploads_still_work(cur, genome):
    row = start(cur, genome, species=add_species(cur))
    put_object(cur, row[2], 100)
    put_object(cur, row[3], 50)


# --------------------------------------------------------------------------- #
# Privileges (asserted, never invoked as a denied role)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role, privilege, allowed", [
    ("bloom_user", "SELECT", True), ("bloom_agent", "SELECT", True),
    ("bloom_workflows", "SELECT", True), ("bloom_writer", "INSERT", False),
    ("bloom_workflows", "INSERT", False), ("bloom_user", "UPDATE", False),
    ("anon", "SELECT", False), ("authenticated", "SELECT", False),
])
def test_table_access(cur, role, privilege, allowed):
    cur.execute("SELECT has_table_privilege(%s, 'public.genome_browser_builds', %s)",
                (role, privilege))
    assert cur.fetchone()[0] is allowed


@pytest.mark.parametrize("function", WRITER_FUNCTIONS)
@pytest.mark.parametrize("role, allowed", [("bloom_writer", True), ("bloom_workflows", True),
                                           ("bloom_user", False), ("anon", False),
                                           ("authenticated", False)])
def test_who_may_make_a_build(cur, function, role, allowed):
    cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, function))
    assert cur.fetchone()[0] is allowed


@pytest.mark.parametrize("role, allowed", [("bloom_admin", True), ("bloom_writer", False),
                                           ("bloom_workflows", False)])
def test_withdraw_is_admin_only(cur, role, allowed):
    cur.execute("SELECT has_function_privilege(%s, "
                "'withdraw_genome_browser_build(bigint, text)', 'EXECUTE')", (role,))
    assert cur.fetchone()[0] is allowed


# --------------------------------------------------------------------------- #
# Running it again, and rolling it back
# --------------------------------------------------------------------------- #


def test_the_migration_can_be_run_again(cur, genome, version_id):
    build_id, folder = ready_build(cur, version_id)
    for migration in BROWSER_MIGRATIONS:
        cur.execute(_sql_body(migration))
    assert _build(cur, build_id)[0] == "ready"
    cur.execute("SELECT public.withdraw_genome_browser_build(%s, 'remade')", (build_id,))
    assert start_build(cur, version_id)[2] == f"{genome}/v1/browser/b2"


def test_the_rollback_puts_back_the_gzip_bucket_and_the_annotation_rule(cur, version_id):
    ready_build(cur, version_id)
    for rollback in ROLLBACKS:
        cur.execute(_sql_body(rollback))
    cur.execute("SELECT to_regclass('public.genome_browser_builds') IS NULL, "
                "allowed_mime_types FROM storage.buckets WHERE id = %s", (BUCKET,))
    gone, types = cur.fetchone()
    assert (gone, types) == (True, ["application/gzip"])
    # The writer upload rule is back to the genome files and annotation builds.
    ready_annotation_build(cur, version_id)
