"""
Integration tests for who can reach genome references: the genome-references bucket's rules for
each role, the run's genome version link, grants on the tables and functions, and the two
rollbacks.
"""

import json
import uuid

import pytest

from tests.integration.genome_reference_helpers import (
    BUCKET,
    OTHER_WRITER,
    TABLES,
    TABLES_ROLLBACK,
    UPLOADS_ROLLBACK,
    WRITER,
    abandon,
    add_species,
    add_run,
    apply_migrations,
    as_admin,
    as_role,
    new_genome_name,
    put_object,
    ready,
    refused,
    start,
    uploaded,
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
def genome():
    """A genome name no other test or real upload uses."""
    return new_genome_name()

INSERT_OBJECT = "INSERT INTO storage.objects (bucket_id, name) VALUES (%s, %s)"


def _objects_named(cur, *names):
    cur.execute(
        "SELECT name FROM storage.objects WHERE bucket_id = %s AND name = ANY(%s) ORDER BY name",
        (BUCKET, list(names)),
    )
    return [r[0] for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# The bucket: writers
# --------------------------------------------------------------------------- #


def test_a_writer_can_upload_its_own_unfinished_versions_files(cur, species_id, genome):
    _, _, fasta_path, gtf_path = uploaded(cur, genome, species_id)
    assert _objects_named(cur, fasta_path, gtf_path) == sorted([fasta_path, gtf_path])


def test_a_writer_cannot_upload_another_path_in_the_bucket(cur, species_id, genome):
    start(cur, genome, species=species_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, INSERT_OBJECT, (BUCKET, f"{genome}/v1/extra.gz"),
        psycopg.errors.InsufficientPrivilege,
    )


def test_a_writer_cannot_upload_to_someone_elses_version(cur, species_id, genome):
    _, _, fasta_path, _ = start(cur, genome, species=species_id)
    as_role(cur, "bloom_writer", OTHER_WRITER)
    refused(cur, INSERT_OBJECT, (BUCKET, fasta_path), psycopg.errors.InsufficientPrivilege)


def test_a_writer_with_no_user_id_cannot_upload(cur, species_id, genome):
    _, _, fasta_path, _ = start(cur, genome, species=species_id)
    as_role(cur, "bloom_writer", None)
    refused(cur, INSERT_OBJECT, (BUCKET, fasta_path), psycopg.errors.InsufficientPrivilege)


def test_a_writer_cannot_add_files_once_a_version_is_abandoned(cur, species_id, genome):
    version_id, _, fasta_path, _ = start(cur, genome, species=species_id)
    abandon(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, INSERT_OBJECT, (BUCKET, fasta_path), psycopg.errors.InsufficientPrivilege)


def test_a_writer_cannot_overwrite_a_stored_genome_file(cur, species_id, genome):
    _, _, fasta_path, _ = uploaded(cur, genome, species_id)
    as_role(cur, "bloom_writer", WRITER)
    cur.execute(
        "UPDATE storage.objects SET metadata = '{\"size\": 1}' WHERE bucket_id = %s AND name = %s",
        (BUCKET, fasta_path),
    )
    assert cur.rowcount == 0


def test_a_writer_cannot_move_a_file_into_the_bucket(cur, species_id, genome):
    _, _, fasta_path, _ = start(cur, genome, species=species_id)
    object_id = put_object(cur, f"genome-test/{uuid.uuid4()}.gz", 100, bucket="images")
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, "UPDATE storage.objects SET bucket_id = %s, name = %s WHERE id = %s",
        (BUCKET, fasta_path, object_id), psycopg.errors.InsufficientPrivilege,
    )


def test_a_writer_cannot_delete_a_genome_file(cur, species_id, genome):
    _, _, fasta_path, _ = uploaded(cur, genome, species_id)
    cur.execute("SET LOCAL storage.allow_delete_query = 'true'")
    as_role(cur, "bloom_writer", WRITER)
    cur.execute("DELETE FROM storage.objects WHERE bucket_id = %s AND name = %s", (BUCKET, fasta_path))
    assert cur.rowcount == 0


def test_a_writers_access_to_other_buckets_is_unchanged(cur):
    object_id = put_object(cur, f"genome-test/{uuid.uuid4()}.png", 1, bucket="images")
    as_role(cur, "bloom_writer", WRITER)
    cur.execute("UPDATE storage.objects SET metadata = '{\"size\": 2}' WHERE id = %s", (object_id,))
    assert cur.rowcount == 1


# --------------------------------------------------------------------------- #
# The bucket: admins and readers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("final", ["ready", "withdrawn"])
def test_an_admin_cannot_change_or_delete_a_finished_versions_files(
    cur, species_id, genome, final
):
    version_id, _, fasta_path, _ = ready(cur, genome, species_id)
    if final == "withdrawn":
        cur.execute(
            "UPDATE genome_reference_versions SET status = 'withdrawn', withdrawn_reason = 'x' "
            "WHERE id = %s", (version_id,),
        )
    cur.execute("SET LOCAL storage.allow_delete_query = 'true'")
    as_role(cur, "bloom_admin", WRITER)
    where = "WHERE bucket_id = %s AND name = %s"
    cur.execute(f"UPDATE storage.objects SET metadata = '{{\"size\": 1}}' {where}", (BUCKET, fasta_path))
    assert cur.rowcount == 0
    cur.execute(f"DELETE FROM storage.objects {where}", (BUCKET, fasta_path))
    assert cur.rowcount == 0


def test_an_admin_can_clear_an_abandoned_versions_files(cur, species_id, genome):
    version_id, _, fasta_path, _ = uploaded(cur, genome, species_id)
    abandon(cur, version_id)
    cur.execute("SET LOCAL storage.allow_delete_query = 'true'")
    as_role(cur, "bloom_admin", WRITER)
    cur.execute("DELETE FROM storage.objects WHERE bucket_id = %s AND name = %s", (BUCKET, fasta_path))
    assert cur.rowcount == 1


@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent", "bloom_workflows"])
def test_readers_can_read_but_not_write_the_bucket(cur, species_id, genome, role):
    _, _, fasta_path, _ = uploaded(cur, genome, species_id)
    as_role(cur, role, WRITER)
    assert _objects_named(cur, fasta_path) == [fasta_path]
    refused(
        cur, INSERT_OBJECT, (BUCKET, f"{genome}/v1/other.gz"), psycopg.errors.InsufficientPrivilege
    )


def test_each_reader_has_its_own_read_policy_on_the_bucket(cur):
    # bloom_agent already reads every bucket, so reading alone would not prove its policy.
    cur.execute(
        "SELECT policyname, roles, cmd FROM pg_policies WHERE schemaname = 'storage' "
        "AND tablename = 'objects' AND policyname LIKE '%%read_genome_references_objects' "
        "ORDER BY policyname"
    )
    assert cur.fetchall() == [
        ("agent_read_genome_references_objects", ["bloom_agent"], "SELECT"),
        ("user_read_genome_references_objects", ["bloom_user"], "SELECT"),
        ("workflows_read_genome_references_objects", ["bloom_workflows"], "SELECT"),
    ]


def test_bucket_policy_names_differ_from_the_table_policy_names(cur):
    cur.execute(
        "SELECT policyname FROM pg_policies WHERE policyname LIKE '%%genome_references%%' "
        "GROUP BY policyname HAVING count(*) > 1"
    )
    assert cur.fetchall() == []


def test_the_bucket_is_private_gzip_only_and_500_mb(cur):
    cur.execute(
        "SELECT public, file_size_limit, allowed_mime_types FROM storage.buckets WHERE id = %s",
        (BUCKET,),
    )
    assert cur.fetchone() == (False, 524288000, ["application/gzip"])


# --------------------------------------------------------------------------- #
# The genome version a run used
# --------------------------------------------------------------------------- #


def test_a_run_records_the_genome_version_it_used(cur, species_id, genome):
    version_id = ready(cur, genome, species_id)[0]
    run_id = add_run(cur, version_id)
    cur.execute(
        "SELECT g.name, v.version FROM rnaseq_runs r "
        "JOIN genome_reference_versions v ON v.id = r.genome_version_id "
        "JOIN genome_references g ON g.id = v.genome_id WHERE r.id = %s",
        (run_id,),
    )
    assert cur.fetchone() == (genome, 1)


def test_a_run_without_a_genome_version_is_allowed(cur):
    run_id = add_run(cur)
    cur.execute("SELECT genome_version_id FROM rnaseq_runs WHERE id = %s", (run_id,))
    assert cur.fetchone()[0] is None


@pytest.mark.parametrize("state", ["uploading", "abandoned", "withdrawn"])
def test_a_run_cannot_name_a_version_that_is_not_ready(cur, species_id, genome, state):
    if state == "withdrawn":
        version_id = ready(cur, genome, species_id)[0]
        cur.execute(
            "UPDATE genome_reference_versions SET status = 'withdrawn', withdrawn_reason = 'x' "
            "WHERE id = %s", (version_id,),
        )
    else:
        version_id = start(cur, genome, species=species_id)[0]
        if state == "abandoned":
            abandon(cur, version_id)
    params = json.dumps({"sample": "S1", "reference": "tair10_araport11"})
    refused(
        cur,
        "INSERT INTO rnaseq_runs (workflow_type, params, run_key, requested_by, "
        "genome_version_id) VALUES ('scrna-cellranger', %s, %s, %s, %s)",
        (params, f"S1__tair10_araport11__{WRITER}", WRITER, version_id),
        psycopg.errors.InvalidParameterValue, match="is not ready",
    )


def test_a_run_can_be_given_its_version_once(cur, species_id, genome):
    version_id = ready(cur, genome, species_id)[0]
    run_id = add_run(cur)
    cur.execute("UPDATE rnaseq_runs SET genome_version_id = %s WHERE id = %s", (version_id, run_id))
    assert cur.rowcount == 1


@pytest.mark.parametrize("new", ["other", None])
def test_a_runs_genome_version_cannot_change_or_clear(cur, species_id, genome, new):
    first = ready(cur, genome, species_id)[0]
    second = ready(cur, genome, species_id)[0]
    run_id = add_run(cur, first)
    refused(
        cur, "UPDATE rnaseq_runs SET genome_version_id = %s WHERE id = %s",
        (second if new else None, run_id), psycopg.errors.ObjectNotInPrerequisiteState,
    )


def test_a_genome_version_used_by_a_run_cannot_be_deleted(cur, species_id, genome):
    version_id = ready(cur, genome, species_id)[0]
    add_run(cur, version_id)
    cur.execute(
        "SELECT confdeltype FROM pg_constraint WHERE conname = 'rnaseq_runs_genome_version_id_fkey'"
    )
    assert cur.fetchone()[0] == "a"


def test_a_run_cannot_name_a_genome_version_that_does_not_exist(cur):
    refused(
        cur,
        "INSERT INTO rnaseq_runs (workflow_type, params, run_key, requested_by, "
        "genome_version_id) VALUES ('scrna-cellranger', %s, %s, %s, -1)",
        (json.dumps({"sample": "S1", "reference": "t"}), f"S1__t__{WRITER}", WRITER),
        psycopg.errors.InvalidParameterValue,
    )


# --------------------------------------------------------------------------- #
# Grants
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent", "bloom_workflows", "bloom_writer"])
def test_readers_have_select_only(cur, table, role):
    cur.execute(
        "SELECT has_table_privilege(%s, %s, 'SELECT'), "
        "has_table_privilege(%s, %s, 'INSERT, UPDATE, DELETE, TRUNCATE')",
        (role, f"public.{table}", role, f"public.{table}"),
    )
    assert cur.fetchone() == (True, False)


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_anon_and_authenticated_have_no_table_access(cur, table, role):
    cur.execute(
        "SELECT has_table_privilege(%s, %s, 'SELECT, INSERT, UPDATE, DELETE')",
        (role, f"public.{table}"),
    )
    assert cur.fetchone()[0] is False


UPLOAD_FUNCTIONS = [
    "public.start_genome_version(text, bigint, text, text, text, text, text)",
    "public.finish_genome_version(bigint, text, bigint, text, bigint)",
    "public.abandon_genome_version(bigint)",
]
ROLES = ("bloom_writer", "bloom_admin", "bloom_user", "bloom_agent", "bloom_workflows", "anon",
         "authenticated")


def _executable_by(cur, function):
    out = {}
    for role in ROLES:
        cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, function))
        out[role] = cur.fetchone()[0]
    return out


@pytest.mark.parametrize("function", UPLOAD_FUNCTIONS)
def test_only_writers_can_call_the_upload_functions(cur, function):
    assert _executable_by(cur, function) == {r: r == "bloom_writer" for r in ROLES}


@pytest.mark.parametrize(
    "function",
    ["public._genome_object_bytes(text)", "public.rnaseq_runs_keep_genome_version()",
     "public.genome_reference_versions_guard()", "public.genome_reference_versions_number()",
     "public.genome_references_keep_identity()"],
)
def test_internal_functions_are_not_callable(cur, function):
    assert _executable_by(cur, function) == {r: False for r in ROLES}


# --------------------------------------------------------------------------- #
# Rollbacks
# --------------------------------------------------------------------------- #


def test_the_rollbacks_remove_everything(cur, species_id, genome):
    start(cur, genome, species=species_id)
    cur.execute(_sql_body(UPLOADS_ROLLBACK))
    cur.execute(_sql_body(TABLES_ROLLBACK))

    for table in TABLES:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        assert cur.fetchone()[0] is None
    cur.execute("SELECT count(*) FROM storage.buckets WHERE id = %s", (BUCKET,))
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM pg_policies WHERE policyname LIKE '%%genome%%'")
    assert cur.fetchone()[0] == 0
    cur.execute(
        "SELECT count(*) FROM pg_proc WHERE proname IN ('start_genome_version', "
        "'finish_genome_version', 'abandon_genome_version', '_genome_object_bytes', "
        "'rnaseq_runs_keep_genome_version', 'genome_name_ok')"
    )
    assert cur.fetchone()[0] == 0
    cur.execute(
        "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
        "AND table_name = 'rnaseq_runs' AND column_name = 'genome_version_id'"
    )
    assert cur.fetchone()[0] == 0


def test_the_uploads_rollback_refuses_while_the_bucket_holds_files(cur, species_id, genome):
    uploaded(cur, genome, species_id)
    as_admin(cur)
    refused(cur, _sql_body(UPLOADS_ROLLBACK), None, psycopg.errors.RaiseException, match="non-empty")
