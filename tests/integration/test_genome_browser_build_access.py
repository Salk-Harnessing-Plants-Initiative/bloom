"""
Integration tests for genome browser builds' storage rules and privileges: which files a writer,
the pipeline and an admin may add or change, who may read them, who may run which function, and
running the migrations again or rolling them back.
"""

import pytest

from tests.integration.annotation_enrichment_helpers import (
    ready_build as ready_annotation_build,
)
from tests.integration.genome_browser_helpers import (
    BROWSER_MIGRATIONS,
    BUCKET,
    ROLLBACKS,
    apply_migrations,
    put_files,
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
from tests.integration.test_rnaseq_runs import _sql_body

psycopg = pytest.importorskip("psycopg")

WITHDRAW = "SELECT public.withdraw_genome_browser_build(%s, %s)"
WRITER_FUNCTIONS = (
    "start_genome_browser_build(bigint, jsonb)",
    "add_genome_browser_genes(bigint, jsonb)",
    "finish_genome_browser_build(bigint, jsonb, integer)",
    "abandon_genome_browser_build(bigint)",
)
# Called only by the build functions, the triggers or the table's check.
INTERNAL_FUNCTIONS = (
    "_own_browser_build(bigint)",
    "genome_browser_builds_number()",
    "genome_browser_builds_guard()",
    "genome_browser_genes_guard()",
    "genome_browser_files_ok(jsonb)",
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


def _status(cur, build_id):
    cur.execute("SELECT status FROM genome_browser_builds WHERE id = %s", (build_id,))
    return cur.fetchone()[0]


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
                 f"{genome}/v1/browser/b9/genes.gff3.gz"):
        _insert_refused(cur, name)
        _insert_refused(cur, name, role="bloom_workflows")


@pytest.mark.parametrize("role", ["bloom_writer", "bloom_admin"])
def test_a_ready_builds_file_cannot_be_added_again(cur, version_id, role):
    build_id, folder = ready_build(cur, version_id)
    cur.execute("SET LOCAL storage.allow_delete_query = 'true'")
    cur.execute("DELETE FROM storage.objects WHERE name = %s", (f"{folder}/genes.gff3.gz.tbi",))
    _insert_refused(cur, f"{folder}/genes.gff3.gz.tbi", role=role)


@pytest.mark.parametrize("command", ["UPDATE", "DELETE"])
def test_admin_can_change_an_uploading_builds_files(cur, version_id, command):
    _, _, folder = start_build(cur, version_id)
    put_files(cur, folder)
    name = f"{folder}/genome.fa.gz.gzi"
    cur.execute("SET LOCAL storage.allow_delete_query = 'true'")
    as_role(cur, "bloom_admin", WRITER)
    cur.execute(
        "UPDATE storage.objects SET metadata = '{\"size\": 2}' WHERE name = %s"
        if command == "UPDATE" else "DELETE FROM storage.objects WHERE name = %s", (name,),
    )
    assert cur.rowcount == 1
    as_admin(cur)


@pytest.mark.parametrize("command", ["UPDATE", "DELETE"])
@pytest.mark.parametrize("role", ["bloom_admin", "bloom_writer"])
@pytest.mark.parametrize("status", ["ready", "withdrawn"])
def test_a_finished_builds_files_cannot_be_changed(cur, version_id, command, role, status):
    build_id, folder = ready_build(cur, version_id)
    if status == "withdrawn":
        cur.execute(WITHDRAW, (build_id, "remade"))
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
    ("bloom_workflows", "SELECT", True), ("bloom_writer", "SELECT", True),
    ("bloom_writer", "INSERT", False), ("bloom_workflows", "INSERT", False),
    ("bloom_user", "UPDATE", False), ("anon", "SELECT", False), ("authenticated", "SELECT", False),
])
def test_table_access(cur, role, privilege, allowed):
    cur.execute("SELECT has_table_privilege(%s, 'public.genome_browser_builds', %s)",
                (role, privilege))
    assert cur.fetchone()[0] is allowed


@pytest.mark.parametrize("function", WRITER_FUNCTIONS)
@pytest.mark.parametrize("role, allowed", [("bloom_writer", True), ("bloom_workflows", True),
                                           ("bloom_user", False), ("bloom_agent", False),
                                           ("bloom_admin", False), ("anon", False),
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


@pytest.mark.parametrize("function", INTERNAL_FUNCTIONS)
@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent", "bloom_writer", "bloom_workflows",
                                  "anon", "authenticated"])
def test_internal_functions_are_not_callable(cur, function, role):
    cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, f"public.{function}"))
    assert cur.fetchone()[0] is False


@pytest.mark.parametrize("function, roles", [
    ("may_upload_browser_file(text)", {"bloom_writer", "bloom_workflows"}),
    ("genome_browser_build_roles()", {"bloom_user", "bloom_writer", "bloom_workflows",
                                      "bloom_admin"}),
])
def test_who_may_call_the_file_helpers(cur, function, roles):
    for role in ("bloom_user", "bloom_agent", "bloom_writer", "bloom_workflows", "bloom_admin",
                 "anon", "authenticated"):
        cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                    (role, f"public.{function}"))
        assert cur.fetchone()[0] is (role in roles), role


# --------------------------------------------------------------------------- #
# Running it again, and rolling it back
# --------------------------------------------------------------------------- #


def test_the_migration_can_be_run_again(cur, genome, version_id):
    build_id, folder = ready_build(cur, version_id)
    for migration in BROWSER_MIGRATIONS:
        cur.execute(_sql_body(migration))
    assert _status(cur, build_id) == "ready"
    cur.execute(WITHDRAW, (build_id, "remade"))
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
