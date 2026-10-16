"""
Integration tests for genome annotation builds: numbering and file paths, finishing against
the stored file, write-once rules, the storage rules for annotation files, and who may do what.
"""

import pytest

from tests.integration.annotation_enrichment_helpers import (
    FILE_BYTES,
    SHA,
    apply_migrations,
    call,
    finish_build,
    put_file,
    ready_build,
    start_build,
)
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

BUCKET = "genome-references"
FINISH = "SELECT public.finish_genome_annotation_build(%s, %s, %s, %s, %s)"
WRITER_FUNCTIONS = (
    "start_genome_annotation_build(bigint, text, jsonb)",
    "finish_genome_annotation_build(bigint, text, bigint, integer, bigint)",
    "abandon_genome_annotation_build(bigint)",
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
    cur.execute("SELECT status, file_sha256, file_bytes, gene_count, term_count "
                "FROM genome_annotation_builds WHERE id = %s", (build_id,))
    return cur.fetchone()


# --------------------------------------------------------------------------- #
# Starting
# --------------------------------------------------------------------------- #


def test_builds_are_numbered_per_version_and_source_with_their_file(cur, genome, version_id):
    build_id, number, path = start_build(cur, version_id, "eggnog")
    assert (number, path) == (1, f"{genome}/v1/annotation/eggnog-b1.json.gz")
    assert _build(cur, build_id)[0] == "uploading"
    assert start_build(cur, version_id, "tair-go")[1:] == (1, f"{genome}/v1/annotation/tair-go-b1.json.gz")


def test_one_live_build_per_version_and_source(cur, version_id):
    start_build(cur, version_id)
    as_role(cur, "bloom_writer", OTHER_WRITER)
    refused(cur, "SELECT * FROM public.start_genome_annotation_build(%s, 'eggnog', '{}')",
            (version_id,), psycopg.errors.ObjectNotInPrerequisiteState,
            match="already has eggnog annotation build 1")


def test_a_version_that_is_not_ready_is_refused(cur, genome):
    uploading = start(cur, genome, species=add_species(cur))[0]
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT * FROM public.start_genome_annotation_build(%s, 'eggnog', '{}')",
            (uploading,), psycopg.errors.ObjectNotInPrerequisiteState, match="not ready")


def test_an_unknown_source_is_refused(cur, version_id):
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT * FROM public.start_genome_annotation_build(%s, 'interpro', '{}')",
            (version_id,), psycopg.errors.CheckViolation)


# --------------------------------------------------------------------------- #
# Finishing
# --------------------------------------------------------------------------- #


def test_finish_records_the_file_and_counts(cur, version_id):
    build_id = ready_build(cur, version_id)
    assert _build(cur, build_id) == ("ready", SHA, FILE_BYTES, 3, 4)


def test_finish_without_the_file_is_refused(cur, version_id):
    build_id, _, _ = start_build(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, FINISH, (build_id, SHA, FILE_BYTES, 3, 4), psycopg.errors.InvalidParameterValue,
            match="has not been uploaded")


@pytest.mark.parametrize(
    "args, match",
    [
        ((SHA, FILE_BYTES + 1, 3, 4), "bytes in storage"),
        (("E" * 64, FILE_BYTES, 3, 4), "64 lowercase hex"),
        ((SHA, FILE_BYTES, 0, 4), "at least one gene"),
        ((SHA, FILE_BYTES, 3, None), "at least one gene"),
    ],
)
def test_finish_with_bad_details_is_refused(cur, version_id, args, match):
    build_id, _, path = start_build(cur, version_id)
    put_file(cur, BUCKET, path)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, FINISH, (build_id, *args), psycopg.errors.InvalidParameterValue, match=match)


def test_another_writer_cannot_finish(cur, version_id):
    build_id, _, path = start_build(cur, version_id)
    put_file(cur, BUCKET, path)
    as_role(cur, "bloom_writer", OTHER_WRITER)
    refused(cur, FINISH, (build_id, SHA, FILE_BYTES, 3, 4), psycopg.errors.InsufficientPrivilege,
            match="someone else")


def test_a_build_finishes_once(cur, version_id):
    build_id = ready_build(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, FINISH, (build_id, SHA, FILE_BYTES, 3, 4),
            psycopg.errors.ObjectNotInPrerequisiteState, match="is ready, not uploading")


def test_the_pipeline_can_upload_a_build(cur, version_id):
    pipeline = "00000000-0000-0000-0000-00000000aaaa"
    build_id, _, path = start_build(cur, version_id, user=pipeline, role="bloom_workflows")
    put_file(cur, BUCKET, path, user=pipeline, role="bloom_workflows")
    finish_build(cur, build_id, user=pipeline, role="bloom_workflows")
    assert _build(cur, build_id)[0] == "ready"


# --------------------------------------------------------------------------- #
# Abandoning, withdrawing, write-once
# --------------------------------------------------------------------------- #


def test_abandon_frees_the_next_number(cur, version_id):
    build_id, _, _ = start_build(cur, version_id)
    assert call(cur, "SELECT public.abandon_genome_annotation_build(%s)", (build_id,))[0] is True
    assert _build(cur, build_id)[0] == "abandoned"
    assert start_build(cur, version_id)[1] == 2


def test_withdraw_allows_a_new_build_in_a_new_file(cur, genome, version_id):
    build_id = ready_build(cur, version_id)
    cur.execute("SELECT public.withdraw_genome_annotation_build(%s, 'wrong GO release')", (build_id,))
    assert _build(cur, build_id)[0] == "withdrawn"
    assert start_build(cur, version_id)[2] == f"{genome}/v1/annotation/eggnog-b2.json.gz"


@pytest.mark.parametrize(
    "assignment",
    ["term_count = 9", "file_sha256 = repeat('f', 64)", "file_bytes = 1", "file_path = 'x'",
     "tool_versions = '{}'", "status = 'uploading'"],
)
def test_a_ready_build_never_changes(cur, version_id, assignment):
    build_id = ready_build(cur, version_id)
    refused(cur, f"UPDATE genome_annotation_builds SET {assignment} WHERE id = %s", (build_id,),
            (psycopg.errors.ObjectNotInPrerequisiteState, psycopg.errors.CheckViolation))


def test_a_ready_build_cannot_be_deleted(cur, version_id):
    build_id = ready_build(cur, version_id)
    refused(cur, "DELETE FROM genome_annotation_builds WHERE id = %s", (build_id,),
            psycopg.errors.ObjectNotInPrerequisiteState)


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #


def _insert_refused(cur, name, user=WRITER, role="bloom_writer"):
    as_role(cur, role, user)
    refused(cur, "INSERT INTO storage.objects (bucket_id, name, metadata) VALUES "
            "('genome-references', %s, '{\"size\": 1}')", (name,), psycopg.errors.InsufficientPrivilege)
    as_admin(cur)


def test_another_writer_cannot_add_the_file(cur, version_id):
    _, _, path = start_build(cur, version_id)
    _insert_refused(cur, path, user=OTHER_WRITER)


def test_no_file_is_taken_outside_the_builds_path(cur, genome, version_id):
    start_build(cur, version_id)
    _insert_refused(cur, f"{genome}/v1/annotation/eggnog-b9.json.gz")
    _insert_refused(cur, f"{genome}/v1/annotation/eggnog-b9.json.gz", role="bloom_workflows")


def test_a_ready_builds_file_cannot_be_added_again(cur, version_id):
    build_id, _, path = start_build(cur, version_id)
    put_file(cur, BUCKET, path)
    finish_build(cur, build_id)
    cur.execute("SET LOCAL storage.allow_delete_query = 'true'")
    cur.execute("DELETE FROM storage.objects WHERE name = %s", (path,))
    _insert_refused(cur, path)


@pytest.mark.parametrize("command", ["UPDATE", "DELETE"])
def test_admin_cannot_change_a_ready_builds_file(cur, version_id, command):
    build_id, _, path = start_build(cur, version_id)
    put_file(cur, BUCKET, path)
    finish_build(cur, build_id)
    cur.execute("SET LOCAL storage.allow_delete_query = 'true'")
    as_role(cur, "bloom_admin", WRITER)
    cur.execute(
        "UPDATE storage.objects SET metadata = '{\"size\": 2}' WHERE name = %s" if command == "UPDATE"
        else "DELETE FROM storage.objects WHERE name = %s", (path,),
    )
    assert cur.rowcount == 0
    as_admin(cur)


def test_genome_version_uploads_still_work(cur, genome):
    row = start(cur, genome, species=add_species(cur))
    put_object(cur, row[2], 100)
    put_object(cur, row[3], 50)


# --------------------------------------------------------------------------- #
# Privileges (asserted, never invoked as a denied role)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role, privilege, allowed", [
    ("bloom_user", "SELECT", True), ("bloom_agent", "SELECT", True),
    ("bloom_workflows", "SELECT", True), ("bloom_writer", "INSERT", False),
    ("bloom_workflows", "INSERT", False), ("anon", "SELECT", False),
])
def test_table_access(cur, role, privilege, allowed):
    cur.execute("SELECT has_table_privilege(%s, 'public.genome_annotation_builds', %s)",
                (role, privilege))
    assert cur.fetchone()[0] is allowed


@pytest.mark.parametrize("function", WRITER_FUNCTIONS)
@pytest.mark.parametrize("role, allowed", [("bloom_writer", True), ("bloom_workflows", True),
                                           ("bloom_user", False), ("anon", False)])
def test_who_may_upload(cur, function, role, allowed):
    cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, function))
    assert cur.fetchone()[0] is allowed


@pytest.mark.parametrize("role, allowed", [("bloom_admin", True), ("bloom_writer", False),
                                           ("bloom_workflows", False)])
def test_withdraw_is_admin_only(cur, role, allowed):
    cur.execute("SELECT has_function_privilege(%s, "
                "'withdraw_genome_annotation_build(bigint, text)', 'EXECUTE')", (role,))
    assert cur.fetchone()[0] is allowed
