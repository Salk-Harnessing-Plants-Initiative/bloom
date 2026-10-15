"""
Integration tests for members (bloom_user) uploading a new reference genome: they may start a new
genome's v1, add its two files once and finish it, and nothing more. Each test applies the genome
migrations and this one inside its own transaction and rolls it back.
"""

import json
import uuid

import pytest

from tests.integration.genome_reference_helpers import (
    BUCKET,
    SHA_A,
    SHA_B,
    WRITER,
    add_species,
    apply_migrations,
    as_admin,
    as_role,
    new_genome_name,
    ready,
    refused,
    start,
    status,
)
from tests.integration.test_rnaseq_runs import _find_one, _sql_body

psycopg = pytest.importorskip("psycopg")

MIGRATION = _find_one("migrations", "*_let_members_upload_genomes.sql")
ROLLBACK = _find_one("rollbacks", "*_let_members_upload_genomes_rollback.sql")
MEMBER = str(uuid.uuid4())
OTHER_MEMBER = str(uuid.uuid4())
START = "public.start_genome_version(text, bigint, text, text, text, text, text)"
FINISH = "public.finish_genome_version(bigint, text, bigint, text, bigint)"
ABANDON = "public.abandon_genome_version(bigint)"
CHECK = "public.member_may_upload_genome_file(text, text)"


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        apply_migrations(c)
        c.execute(_sql_body(MIGRATION))
        yield c
    pg_conn.rollback()


@pytest.fixture
def species_id(cur):
    return add_species(cur)


@pytest.fixture
def genome():
    return new_genome_name()


def member_start(cur, genome, species_id, user=MEMBER):
    """Start a version as a member: (version_id, version, fasta_path, gtf_path)."""
    as_role(cur, "bloom_user", user)
    cur.execute(
        "SELECT * FROM public.start_genome_version(p_genome => %s, p_species_id => %s)",
        (genome, species_id),
    )
    row = cur.fetchone()
    as_admin(cur)
    return row


def member_put(cur, name, size, user=MEMBER, owner=None):
    """Store a genome file as a member, owned by `owner` (the member by default)."""
    as_role(cur, "bloom_user", user)
    cur.execute(
        "INSERT INTO storage.objects (bucket_id, name, owner_id, metadata) VALUES (%s, %s, %s, %s)",
        (BUCKET, name, owner or user, json.dumps({"size": size, "mimetype": "application/gzip"})),
    )
    as_admin(cur)


def member_finish(cur, version_id, user=MEMBER):
    as_role(cur, "bloom_user", user)
    cur.execute("SELECT public.finish_genome_version(%s, %s, 100, %s, 50)", (version_id, SHA_A, SHA_B))
    version = cur.fetchone()[0]
    as_admin(cur)
    return version


def member_uploaded(cur, genome, species_id, user=MEMBER):
    row = member_start(cur, genome, species_id, user)
    member_put(cur, row[2], 100, user)
    member_put(cur, row[3], 50, user)
    return row


def versions_of(cur, genome):
    cur.execute(
        "SELECT v.version FROM public.genome_reference_versions v "
        "JOIN public.genome_references g ON g.id = v.genome_id WHERE g.name = %s ORDER BY 1",
        (genome,),
    )
    return [r[0] for r in cur.fetchall()]


def refused_as_member(cur, sql, params, match=None, user=MEMBER):
    as_role(cur, "bloom_user", user)
    refused(cur, sql, params, psycopg.errors.InsufficientPrivilege, match=match)
    as_admin(cur)


# --------------------------------------------------------------------------- #
# A member uploads a new genome
# --------------------------------------------------------------------------- #


def test_a_member_uploads_a_new_genome_as_its_first_version(cur, species_id, genome):
    row = member_uploaded(cur, genome, species_id)
    assert row[1] == 1
    member_finish(cur, row[0])
    assert status(cur, row[0]) == "ready"
    cur.execute(
        "SELECT g.created_by::text, v.created_by::text FROM public.genome_references g "
        "JOIN public.genome_reference_versions v ON v.genome_id = g.id WHERE g.name = %s",
        (genome,),
    )
    assert cur.fetchone() == (MEMBER, MEMBER), "the uploader is recorded on both"


# --------------------------------------------------------------------------- #
# Only new genomes
# --------------------------------------------------------------------------- #


START_SQL = "SELECT * FROM public.start_genome_version(p_genome => %s, p_species_id => %s)"


def test_a_member_cannot_add_a_version_to_a_writers_genome(cur, species_id, genome):
    ready(cur, genome, species_id)
    refused_as_member(cur, START_SQL, (genome, species_id), match="already exists")
    assert versions_of(cur, genome) == [1]


def test_a_member_cannot_add_a_version_to_their_own_genome(cur, species_id, genome):
    member_finish(cur, member_uploaded(cur, genome, species_id)[0])
    refused_as_member(cur, START_SQL, (genome, species_id), match="already exists")
    assert versions_of(cur, genome) == [1]


def test_a_member_cannot_retry_a_name_whose_upload_never_finished(cur, species_id, genome):
    member_start(cur, genome, species_id)
    refused_as_member(cur, START_SQL, (genome, species_id), match="already exists")


def test_a_member_cannot_version_a_genome_another_account_created_without_versions(
    cur, species_id, genome
):
    # The state a concurrent writer leaves between creating the genome and its version.
    cur.execute(
        "INSERT INTO public.genome_references (name, species_id, created_by) VALUES (%s, %s, %s)",
        (genome, species_id, WRITER),
    )
    refused_as_member(cur, START_SQL, (genome, species_id), match="already exists")
    assert versions_of(cur, genome) == []


def test_a_writer_can_still_add_a_version_to_a_members_genome(cur, species_id, genome):
    member_finish(cur, member_uploaded(cur, genome, species_id)[0])
    assert start(cur, genome, species=species_id)[1] == 2


def test_an_admin_session_without_a_role_is_not_treated_as_a_member(cur, species_id, genome):
    ready(cur, genome, species_id)
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (WRITER,))
    cur.execute(START_SQL, (genome, species_id))
    assert cur.fetchone()[1] == 2


# --------------------------------------------------------------------------- #
# Files: their own uploading version, once
# --------------------------------------------------------------------------- #


INSERT_FILE = (
    "INSERT INTO storage.objects (bucket_id, name, owner_id, metadata) "
    "VALUES (%s, %s, %s, '{\"size\": 1}')"
)


def test_a_member_cannot_add_a_file_to_another_accounts_version(cur, species_id, genome):
    row = start(cur, genome, species=species_id)
    refused_as_member(cur, INSERT_FILE, (BUCKET, row[2], MEMBER))


def test_a_member_cannot_store_a_file_owned_by_someone_else(cur, species_id, genome):
    row = member_start(cur, genome, species_id)
    refused_as_member(cur, INSERT_FILE, (BUCKET, row[2], OTHER_MEMBER))
    refused_as_member(cur, INSERT_FILE, (BUCKET, row[2], None))


def test_a_member_cannot_store_any_other_path_in_the_bucket(cur, species_id, genome):
    member_start(cur, genome, species_id)
    refused_as_member(cur, INSERT_FILE, (BUCKET, f"{genome}/v9/genome.fa.gz", MEMBER))


def test_a_member_cannot_add_files_once_the_version_is_ready(cur, species_id, genome):
    row = member_uploaded(cur, genome, species_id)
    member_finish(cur, row[0])
    cur.execute("SET LOCAL storage.allow_delete_query = 'true'")
    cur.execute("DELETE FROM storage.objects WHERE bucket_id = %s AND name = %s", (BUCKET, row[2]))
    refused_as_member(cur, INSERT_FILE, (BUCKET, row[2], MEMBER))


def test_a_member_cannot_replace_or_delete_a_genome_file(cur, species_id, genome):
    row = member_uploaded(cur, genome, species_id)
    as_role(cur, "bloom_user", MEMBER)
    cur.execute(
        "UPDATE storage.objects SET metadata = '{\"size\": 2}' WHERE bucket_id = %s AND name = %s",
        (BUCKET, row[2]),
    )
    assert cur.rowcount == 0, "no update rule for members in this bucket"
    as_admin(cur)
    cur.execute("SELECT has_table_privilege('bloom_user', 'storage.objects', 'DELETE')")
    assert cur.fetchone()[0] is False


# --------------------------------------------------------------------------- #
# Finishing: their own uploading version
# --------------------------------------------------------------------------- #


def test_a_member_cannot_finish_another_accounts_version(cur, species_id, genome):
    row = start(cur, genome, species=species_id)
    as_role(cur, "bloom_user", MEMBER)
    refused(
        cur,
        "SELECT public.finish_genome_version(%s, %s, 100, %s, 50)",
        (row[0], SHA_A, SHA_B),
        psycopg.errors.InsufficientPrivilege,
        match="someone else",
    )
    as_admin(cur)
    assert status(cur, row[0]) == "uploading"


def test_a_ready_version_cannot_be_finished_again(cur, species_id, genome):
    row = member_uploaded(cur, genome, species_id)
    member_finish(cur, row[0])
    as_role(cur, "bloom_user", MEMBER)
    cur.execute("SAVEPOINT again")
    with pytest.raises(psycopg.errors.Error, match="not uploading"):
        cur.execute("SELECT public.finish_genome_version(%s, %s, 100, %s, 50)", (row[0], SHA_A, SHA_B))
    cur.execute("ROLLBACK TO SAVEPOINT again")
    as_admin(cur)


# --------------------------------------------------------------------------- #
# Nothing else
# --------------------------------------------------------------------------- #


def test_members_get_start_and_finish_only(cur):
    cur.execute(
        "SELECT has_function_privilege('bloom_user', %s, 'EXECUTE'), "
        "has_function_privilege('bloom_user', %s, 'EXECUTE'), "
        "has_function_privilege('bloom_user', %s, 'EXECUTE')",
        (START, FINISH, ABANDON),
    )
    assert cur.fetchone() == (True, True, False)


@pytest.mark.parametrize("table", ["genome_references", "genome_reference_versions"])
@pytest.mark.parametrize("privilege", ["INSERT", "UPDATE", "DELETE", "TRUNCATE"])
def test_members_cannot_write_the_genome_tables_directly(cur, table, privilege):
    cur.execute("SELECT has_table_privilege('bloom_user', %s, %s)", (f"public.{table}", privilege))
    assert cur.fetchone()[0] is False


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("fn", [START, FINISH, CHECK])
def test_signed_out_and_plain_authenticated_callers_get_nothing(cur, role, fn):
    cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, fn))
    assert cur.fetchone()[0] is False


def test_the_file_check_is_only_for_members(cur):
    # Writers are members of bloom_user, so they inherit it; these roles are not.
    cur.execute(
        "SELECT r FROM unnest(ARRAY['bloom_agent','bloom_workflows']) r "
        "WHERE has_function_privilege(r, %s, 'EXECUTE')",
        (CHECK,),
    )
    assert cur.fetchall() == []


# --------------------------------------------------------------------------- #
# Re-running and rolling back
# --------------------------------------------------------------------------- #


def test_the_migration_runs_again_without_error(cur, species_id, genome):
    cur.execute(_sql_body(MIGRATION))
    assert member_start(cur, genome, species_id)[1] == 1


def test_the_rollback_takes_members_back_to_reading_only(cur, species_id, genome):
    cur.execute(_sql_body(ROLLBACK))
    cur.execute(
        "SELECT has_function_privilege('bloom_user', %s, 'EXECUTE'), "
        "has_function_privilege('bloom_user', %s, 'EXECUTE')",
        (START, FINISH),
    )
    assert cur.fetchone() == (False, False)
    cur.execute(
        "SELECT count(*) FROM pg_policies WHERE policyname = 'user_insert_own_genome_upload'"
    )
    assert cur.fetchone()[0] == 0
    assert start(cur, genome, species=species_id)[1] == 1, "writers upload as before"
