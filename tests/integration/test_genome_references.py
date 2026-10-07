"""
Integration tests for genome references: numbered, write-once versions of a genome's FASTA and
GTF, the start / finish / abandon functions bloomctl calls, the genome-references bucket's rules,
and the rollback.

Each test applies the migration inside its own transaction and rolls it back, so the database
is left unchanged. Roles are exercised with SET LOCAL ROLE and a request.jwt.claims sub, the
way PostgREST and Storage call in.
"""

import json
import uuid

import pytest

from tests.integration.test_rnaseq_runs import _find_one, _sql_body

psycopg = pytest.importorskip("psycopg")

MIGRATION = _find_one(
    "migrations", "*_add_genome_references_and_run_genome_version.sql"
)
ROLLBACK = _find_one(
    "rollbacks", "*_add_genome_references_and_run_genome_version_rollback.sql"
)
BUCKET = "genome-references"
TABLES = ("genome_references", "genome_reference_versions")

WRITER = str(uuid.uuid4())
OTHER_WRITER = str(uuid.uuid4())
SHA_A = "a" * 64
SHA_B = "b" * 64

GOOD_NAMES = ["tair10_araport11", "TAIR10.araport11", "GRCh38-2024-A", "g" * 64]
BAD_NAMES = [".hidden", "g" * 65, "a__b", "a/b", "has space", "../etc", ""]


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        for table in reversed(TABLES):
            c.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE")
        c.execute(_sql_body(MIGRATION))
        yield c
    pg_conn.rollback()


@pytest.fixture
def species_id(cur):
    cur.execute(
        "INSERT INTO public.species (genus, species, common_name) "
        "VALUES ('Testgenus', 'genomeus', %s) RETURNING id",
        (f"genome-test-{uuid.uuid4()}",),
    )
    return cur.fetchone()[0]


def _as(cur, role, user=None):
    """Act as `role`, signed in as `user` (no sub when None)."""
    cur.execute(f"SET LOCAL ROLE {role}")
    claims = {"role": role, **({"sub": user} if user else {})}
    cur.execute(
        "SELECT set_config('request.jwt.claims', %s, true)", (json.dumps(claims),)
    )


def _admin(cur):
    cur.execute("RESET ROLE")
    cur.execute("SELECT set_config('request.jwt.claims', '', true)")


def _refused(cur, sql, params, error, match=None):
    cur.execute("SAVEPOINT refused")
    try:
        with pytest.raises(error, match=match):
            cur.execute(sql, params)
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT refused")


def _start(cur, genome="tair10_araport11", species=None, user=WRITER, **extra):
    _as(cur, "bloom_writer", user)
    args = {"p_genome": genome, "p_species_id": species, **extra}
    cur.execute(
        "SELECT * FROM public.start_genome_version("
        + ", ".join(f"{k} => %({k})s" for k in args)
        + ")",
        args,
    )
    row = cur.fetchone()
    _admin(cur)
    return row


def _put_object(cur, name, size, role="bloom_writer", user=WRITER):
    _as(cur, role, user)
    cur.execute(
        "INSERT INTO storage.objects (bucket_id, name, metadata) VALUES (%s, %s, %s)",
        (BUCKET, name, json.dumps({"size": size, "mimetype": "application/gzip"})),
    )
    _admin(cur)


def _finish(cur, version_id, fasta_bytes=100, gtf_bytes=50, user=WRITER):
    _as(cur, "bloom_writer", user)
    cur.execute(
        "SELECT public.finish_genome_version(%s, %s, %s, %s, %s)",
        (version_id, SHA_A, fasta_bytes, SHA_B, gtf_bytes),
    )
    version = cur.fetchone()[0]
    _admin(cur)
    return version


def _uploaded(cur, species_id, user=WRITER):
    """A started version with both files stored: (version_id, version, fasta_path, gtf_path)."""
    row = _start(cur, species=species_id, user=user)
    _put_object(cur, row[2], 100, user=user)
    _put_object(cur, row[3], 50, user=user)
    return row


def _status(cur, version_id):
    cur.execute(
        "SELECT status, ready_at FROM public.genome_reference_versions WHERE id = %s",
        (version_id,),
    )
    return cur.fetchone()


# --------------------------------------------------------------------------- #
# Starting versions
# --------------------------------------------------------------------------- #


def test_a_new_genome_gets_version_1_with_its_paths(cur, species_id):
    version_id, version, fasta_path, gtf_path = _start(
        cur, species=species_id, p_assembly="TAIR10", p_annotation="Araport11"
    )
    assert (version, fasta_path, gtf_path) == (
        1,
        "tair10_araport11/v1/genome.fa.gz",
        "tair10_araport11/v1/genes.gtf.gz",
    )
    cur.execute(
        "SELECT g.species_id, g.created_by::text, v.status, v.assembly, v.annotation, "
        "v.created_by::text FROM genome_reference_versions v "
        "JOIN genome_references g ON g.id = v.genome_id WHERE v.id = %s",
        (version_id,),
    )
    assert cur.fetchone() == (
        species_id,
        WRITER,
        "uploading",
        "TAIR10",
        "Araport11",
        WRITER,
    )


def test_the_next_upload_of_a_genome_is_version_2(cur, species_id):
    _start(cur, species=species_id)
    assert _start(cur)[1] == 2


def test_the_same_species_may_be_given_again(cur, species_id):
    _start(cur, species=species_id)
    assert _start(cur, species=species_id)[1] == 2


def test_numbering_continues_past_an_abandoned_version(cur, species_id):
    version_id = _start(cur, species=species_id)[0]
    _as(cur, "bloom_writer", WRITER)
    cur.execute("SELECT public.abandon_genome_version(%s)", (version_id,))
    _admin(cur)
    assert _start(cur)[1] == 2


def test_a_version_number_is_used_once_per_genome(cur, species_id):
    version_id = _start(cur, species=species_id)[0]
    _refused(
        cur,
        "INSERT INTO genome_reference_versions (genome_id, version, fasta_path, gtf_path, "
        "created_by) SELECT genome_id, 1, 'x', 'y', %s FROM genome_reference_versions "
        "WHERE id = %s",
        (WRITER, version_id),
        psycopg.errors.UniqueViolation,
    )


@pytest.mark.parametrize("name", GOOD_NAMES)
def test_a_good_genome_name_is_accepted(cur, species_id, name):
    assert _start(cur, genome=name, species=species_id)[1] == 1


@pytest.mark.parametrize("name", BAD_NAMES)
def test_a_bad_genome_name_is_refused(cur, species_id, name):
    _as(cur, "bloom_writer", WRITER)
    _refused(
        cur,
        "SELECT * FROM public.start_genome_version(%s, %s)",
        (name, species_id),
        psycopg.errors.InvalidParameterValue,
    )


def test_a_new_genome_needs_a_species(cur):
    _as(cur, "bloom_writer", WRITER)
    _refused(
        cur,
        "SELECT * FROM public.start_genome_version('tair10_araport11')",
        None,
        psycopg.errors.InvalidParameterValue,
        match="give its species",
    )


def test_an_unknown_species_is_refused(cur):
    _as(cur, "bloom_writer", WRITER)
    _refused(
        cur,
        "SELECT * FROM public.start_genome_version('tair10_araport11', -1)",
        None,
        psycopg.errors.InvalidParameterValue,
        match="no species",
    )


def test_a_different_species_for_an_existing_genome_is_refused(cur, species_id):
    _start(cur, species=species_id)
    cur.execute(
        "INSERT INTO public.species (genus, species, common_name) "
        "VALUES ('Othergenus', 'other', %s) RETURNING id",
        (f"genome-test-{uuid.uuid4()}",),
    )
    other = cur.fetchone()[0]
    _as(cur, "bloom_writer", WRITER)
    _refused(
        cur,
        "SELECT * FROM public.start_genome_version('tair10_araport11', %s)",
        (other,),
        psycopg.errors.InvalidParameterValue,
        match="belongs to species",
    )


def test_a_different_description_for_an_existing_genome_is_refused(cur, species_id):
    _start(cur, species=species_id, p_description="Col-0")
    _as(cur, "bloom_writer", WRITER)
    _refused(
        cur,
        "SELECT * FROM public.start_genome_version('tair10_araport11', "
        "p_description => 'something else')",
        None,
        psycopg.errors.InvalidParameterValue,
        match="different description",
    )


def test_starting_without_signing_in_is_refused(cur, species_id):
    _as(cur, "bloom_writer", None)
    _refused(
        cur,
        "SELECT * FROM public.start_genome_version('tair10_araport11', %s)",
        (species_id,),
        psycopg.errors.InsufficientPrivilege,
    )


# --------------------------------------------------------------------------- #
# Finishing an upload
# --------------------------------------------------------------------------- #


def test_finishing_makes_the_version_ready_and_records_the_files(cur, species_id):
    version_id = _uploaded(cur, species_id)[0]
    assert _finish(cur, version_id) == 1

    cur.execute(
        "SELECT status, fasta_sha256, fasta_bytes, gtf_sha256, gtf_bytes, ready_at IS NOT NULL "
        "FROM genome_reference_versions WHERE id = %s",
        (version_id,),
    )
    assert cur.fetchone() == ("ready", SHA_A, 100, SHA_B, 50, True)


def test_finishing_twice_is_refused(cur, species_id):
    version_id = _uploaded(cur, species_id)[0]
    _finish(cur, version_id)
    _as(cur, "bloom_writer", WRITER)
    _refused(
        cur,
        "SELECT public.finish_genome_version(%s, %s, 100, %s, 50)",
        (version_id, SHA_A, SHA_B),
        psycopg.errors.ObjectNotInPrerequisiteState,
    )


def test_finishing_with_a_file_missing_names_it(cur, species_id):
    version_id, _, fasta_path, _ = _start(cur, species=species_id)
    _put_object(cur, fasta_path, 100)
    _as(cur, "bloom_writer", WRITER)
    _refused(
        cur,
        "SELECT public.finish_genome_version(%s, %s, 100, %s, 50)",
        (version_id, SHA_A, SHA_B),
        psycopg.errors.InvalidParameterValue,
        match="genes.gtf.gz has not been uploaded",
    )
    _admin(cur)
    assert _status(cur, version_id)[0] == "uploading"


def test_finishing_with_a_wrong_size_is_refused(cur, species_id):
    version_id = _uploaded(cur, species_id)[0]
    _as(cur, "bloom_writer", WRITER)
    _refused(
        cur,
        "SELECT public.finish_genome_version(%s, %s, 101, %s, 50)",
        (version_id, SHA_A, SHA_B),
        psycopg.errors.InvalidParameterValue,
        match="is 100 bytes in storage, not 101",
    )
    _admin(cur)
    assert _status(cur, version_id)[0] == "uploading"


@pytest.mark.parametrize("sha", ["A" * 64, "a" * 63, "g" * 64, None])
def test_a_malformed_checksum_is_refused(cur, species_id, sha):
    version_id = _uploaded(cur, species_id)[0]
    _as(cur, "bloom_writer", WRITER)
    _refused(
        cur,
        "SELECT public.finish_genome_version(%s, %s, 100, %s, 50)",
        (version_id, sha, SHA_B),
        psycopg.errors.InvalidParameterValue,
    )


def test_finishing_someone_elses_upload_is_refused(cur, species_id):
    version_id = _uploaded(cur, species_id)[0]
    _as(cur, "bloom_writer", OTHER_WRITER)
    _refused(
        cur,
        "SELECT public.finish_genome_version(%s, %s, 100, %s, 50)",
        (version_id, SHA_A, SHA_B),
        psycopg.errors.InsufficientPrivilege,
    )


# --------------------------------------------------------------------------- #
# Abandoning an upload
# --------------------------------------------------------------------------- #


def test_the_creator_can_abandon_an_upload_once(cur, species_id):
    version_id = _start(cur, species=species_id)[0]
    _as(cur, "bloom_writer", WRITER)
    cur.execute("SELECT public.abandon_genome_version(%s)", (version_id,))
    assert cur.fetchone()[0] is True
    cur.execute("SELECT public.abandon_genome_version(%s)", (version_id,))
    assert cur.fetchone()[0] is False
    _admin(cur)
    assert _status(cur, version_id)[0] == "abandoned"


def test_an_abandoned_upload_cannot_be_finished(cur, species_id):
    version_id = _uploaded(cur, species_id)[0]
    _as(cur, "bloom_writer", WRITER)
    cur.execute("SELECT public.abandon_genome_version(%s)", (version_id,))
    _refused(
        cur,
        "SELECT public.finish_genome_version(%s, %s, 100, %s, 50)",
        (version_id, SHA_A, SHA_B),
        psycopg.errors.ObjectNotInPrerequisiteState,
    )


def test_a_finished_upload_cannot_be_abandoned(cur, species_id):
    version_id = _uploaded(cur, species_id)[0]
    _finish(cur, version_id)
    _as(cur, "bloom_writer", WRITER)
    _refused(
        cur,
        "SELECT public.abandon_genome_version(%s)",
        (version_id,),
        psycopg.errors.ObjectNotInPrerequisiteState,
    )


def test_abandoning_someone_elses_upload_is_refused(cur, species_id):
    version_id = _start(cur, species=species_id)[0]
    _as(cur, "bloom_writer", OTHER_WRITER)
    _refused(
        cur,
        "SELECT public.abandon_genome_version(%s)",
        (version_id,),
        psycopg.errors.InsufficientPrivilege,
    )


# --------------------------------------------------------------------------- #
# What may change (checked as the superuser, which RLS and grants don't stop)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "assignment",
    [
        "fasta_sha256 = repeat('c', 64)",
        "gtf_bytes = 51",
        "ready_at = now() - interval '1 day'",
    ],
)
def test_a_finished_versions_files_cannot_change(cur, species_id, assignment):
    version_id = _uploaded(cur, species_id)[0]
    _finish(cur, version_id)
    _refused(
        cur,
        f"UPDATE genome_reference_versions SET {assignment} WHERE id = %s",
        (version_id,),
        psycopg.errors.ObjectNotInPrerequisiteState,
    )


@pytest.mark.parametrize(
    "assignment",
    [
        "version = 9",
        "fasta_path = 'elsewhere.fa.gz'",
        "assembly = 'other'",
        "notes = 'x'",
    ],
)
def test_a_versions_identity_and_sources_never_change(cur, species_id, assignment):
    version_id = _start(cur, species=species_id)[0]
    _refused(
        cur,
        f"UPDATE genome_reference_versions SET {assignment} WHERE id = %s",
        (version_id,),
        psycopg.errors.ObjectNotInPrerequisiteState,
    )


@pytest.mark.parametrize(
    "old, new",
    [("ready", "uploading"), ("ready", "abandoned"), ("abandoned", "uploading")],
)
def test_status_never_goes_backward(cur, species_id, old, new):
    version_id = _uploaded(cur, species_id)[0]
    if old == "ready":
        _finish(cur, version_id)
    else:
        _as(cur, "bloom_writer", WRITER)
        cur.execute("SELECT public.abandon_genome_version(%s)", (version_id,))
        _admin(cur)
    assert _status(cur, version_id)[0] == old
    _refused(
        cur,
        "UPDATE genome_reference_versions SET status = %s WHERE id = %s",
        (new, version_id),
        psycopg.errors.ObjectNotInPrerequisiteState,
    )


def test_a_genomes_name_and_species_never_change(cur, species_id):
    _start(cur, species=species_id)
    _refused(
        cur,
        "UPDATE genome_references SET name = 'renamed' WHERE name = 'tair10_araport11'",
        None,
        psycopg.errors.ObjectNotInPrerequisiteState,
    )


def test_a_genomes_description_can_be_edited(cur, species_id):
    _start(cur, species=species_id)
    cur.execute(
        "UPDATE genome_references SET description = 'Col-0' WHERE name = 'tair10_araport11'"
    )
    assert cur.rowcount == 1


# --------------------------------------------------------------------------- #
# The genome version a run used
# --------------------------------------------------------------------------- #


def _add_run(cur, genome_version_id=None):
    params = {"sample": "S1", "reference": "tair10_araport11"}
    cur.execute(
        "INSERT INTO rnaseq_runs (workflow_type, params, run_key, requested_by, "
        "genome_version_id) VALUES ('scrna-cellranger', %s, %s, %s, %s) RETURNING id",
        (
            json.dumps(params),
            f"S1__tair10_araport11__{WRITER}",
            WRITER,
            genome_version_id,
        ),
    )
    return cur.fetchone()[0]


def test_a_run_records_the_genome_version_it_used(cur, species_id):
    version_id = _uploaded(cur, species_id)[0]
    _finish(cur, version_id)
    run_id = _add_run(cur, version_id)
    cur.execute(
        "SELECT g.name, v.version FROM rnaseq_runs r "
        "JOIN genome_reference_versions v ON v.id = r.genome_version_id "
        "JOIN genome_references g ON g.id = v.genome_id WHERE r.id = %s",
        (run_id,),
    )
    assert cur.fetchone() == ("tair10_araport11", 1)


def test_a_run_without_a_genome_version_is_allowed(cur):
    run_id = _add_run(cur)
    cur.execute("SELECT genome_version_id FROM rnaseq_runs WHERE id = %s", (run_id,))
    assert cur.fetchone()[0] is None


def test_a_run_cannot_name_a_genome_version_that_does_not_exist(cur):
    _refused(
        cur,
        "INSERT INTO rnaseq_runs (workflow_type, params, run_key, requested_by, "
        "genome_version_id) VALUES ('scrna-cellranger', %s, %s, %s, -1)",
        (
            json.dumps({"sample": "S1", "reference": "tair10_araport11"}),
            f"S1__tair10_araport11__{WRITER}",
            WRITER,
        ),
        psycopg.errors.ForeignKeyViolation,
    )


def test_a_genome_version_used_by_a_run_cannot_be_deleted(cur, species_id):
    version_id = _uploaded(cur, species_id)[0]
    _finish(cur, version_id)
    _add_run(cur, version_id)
    _refused(
        cur,
        "DELETE FROM genome_reference_versions WHERE id = %s",
        (version_id,),
        psycopg.errors.ForeignKeyViolation,
    )


def test_the_run_column_is_readable_by_the_run_readers(cur):
    for role in ("bloom_user", "bloom_agent", "bloom_workflows"):
        cur.execute(
            "SELECT has_column_privilege(%s, 'public.rnaseq_runs', 'genome_version_id', "
            "'SELECT')",
            (role,),
        )
        assert cur.fetchone()[0] is True, role


# --------------------------------------------------------------------------- #
# The genome-references bucket
# --------------------------------------------------------------------------- #


def test_a_writer_can_upload_its_own_unfinished_versions_files(cur, species_id):
    _, _, fasta_path, gtf_path = _uploaded(cur, species_id)
    cur.execute(
        "SELECT name FROM storage.objects WHERE bucket_id = %s ORDER BY name", (BUCKET,)
    )
    assert [r[0] for r in cur.fetchall()] == sorted([fasta_path, gtf_path])


def test_a_writer_cannot_upload_another_path_in_the_bucket(cur, species_id):
    _start(cur, species=species_id)
    _as(cur, "bloom_writer", WRITER)
    _refused(
        cur,
        "INSERT INTO storage.objects (bucket_id, name) VALUES (%s, 'tair10_araport11/v1/x.gz')",
        (BUCKET,),
        psycopg.errors.InsufficientPrivilege,
    )


def test_a_writer_cannot_upload_to_someone_elses_version(cur, species_id):
    _, _, fasta_path, _ = _start(cur, species=species_id)
    _as(cur, "bloom_writer", OTHER_WRITER)
    _refused(
        cur,
        "INSERT INTO storage.objects (bucket_id, name) VALUES (%s, %s)",
        (BUCKET, fasta_path),
        psycopg.errors.InsufficientPrivilege,
    )


def test_a_writer_cannot_add_files_once_a_version_is_abandoned(cur, species_id):
    version_id, _, fasta_path, _ = _start(cur, species=species_id)
    _as(cur, "bloom_writer", WRITER)
    cur.execute("SELECT public.abandon_genome_version(%s)", (version_id,))
    _refused(
        cur,
        "INSERT INTO storage.objects (bucket_id, name) VALUES (%s, %s)",
        (BUCKET, fasta_path),
        psycopg.errors.InsufficientPrivilege,
    )


def test_a_writer_cannot_overwrite_a_stored_genome_file(cur, species_id):
    _, _, fasta_path, _ = _uploaded(cur, species_id)
    _as(cur, "bloom_writer", WRITER)
    cur.execute(
        "UPDATE storage.objects SET metadata = '{\"size\": 1}' "
        "WHERE bucket_id = %s AND name = %s",
        (BUCKET, fasta_path),
    )
    assert cur.rowcount == 0


@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent", "bloom_workflows"])
def test_readers_can_read_but_not_write_the_bucket(cur, species_id, role):
    _, _, fasta_path, _ = _uploaded(cur, species_id)
    _as(cur, role, WRITER)
    cur.execute(
        "SELECT count(*) FROM storage.objects WHERE bucket_id = %s AND name = %s",
        (BUCKET, fasta_path),
    )
    assert cur.fetchone()[0] == 1
    _refused(
        cur,
        "INSERT INTO storage.objects (bucket_id, name) VALUES (%s, 'tair10_araport11/v1/y.gz')",
        (BUCKET,),
        psycopg.errors.InsufficientPrivilege,
    )


def test_a_writers_access_to_other_buckets_is_unchanged(cur):
    _as(cur, "bloom_writer", WRITER)
    cur.execute(
        "INSERT INTO storage.objects (bucket_id, name) VALUES ('images', %s) RETURNING id",
        (f"genome-test/{uuid.uuid4()}.png",),
    )
    object_id = cur.fetchone()[0]
    cur.execute(
        "UPDATE storage.objects SET metadata = '{\"size\": 1}' WHERE id = %s",
        (object_id,),
    )
    assert cur.rowcount == 1


def test_the_bucket_is_private_gzip_only_and_500_mb(cur):
    cur.execute(
        "SELECT public, file_size_limit, allowed_mime_types FROM storage.buckets WHERE id = %s",
        (BUCKET,),
    )
    assert cur.fetchone() == (False, 524288000, ["application/gzip"])


# --------------------------------------------------------------------------- #
# Grants
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize(
    "role", ["bloom_user", "bloom_agent", "bloom_workflows", "bloom_writer"]
)
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


FUNCTIONS = [
    "public.start_genome_version(text, bigint, text, text, text, text, text)",
    "public.finish_genome_version(bigint, text, bigint, text, bigint)",
    "public.abandon_genome_version(bigint)",
]


@pytest.mark.parametrize("function", FUNCTIONS)
def test_only_writers_can_call_the_upload_functions(cur, function):
    allowed = {}
    for role in (
        "bloom_writer",
        "bloom_user",
        "bloom_agent",
        "bloom_workflows",
        "anon",
        "authenticated",
    ):
        cur.execute(
            "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, function)
        )
        allowed[role] = cur.fetchone()[0]
    assert allowed == {
        "bloom_writer": True,
        "bloom_user": False,
        "bloom_agent": False,
        "bloom_workflows": False,
        "anon": False,
        "authenticated": False,
    }


@pytest.mark.parametrize(
    "function",
    ["public._genome_object_bytes(text)"],
)
def test_internal_functions_are_not_callable(cur, function):
    for role in ("bloom_writer", "bloom_user", "anon", "authenticated"):
        cur.execute(
            "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, function)
        )
        assert cur.fetchone()[0] is False, role


# --------------------------------------------------------------------------- #
# Rollback
# --------------------------------------------------------------------------- #


def test_the_rollback_removes_everything(cur, species_id):
    version_id = _start(cur, species=species_id)[0]
    assert version_id
    cur.execute(_sql_body(ROLLBACK))

    for table in TABLES:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        assert cur.fetchone()[0] is None
    cur.execute("SELECT count(*) FROM storage.buckets WHERE id = %s", (BUCKET,))
    assert cur.fetchone()[0] == 0
    cur.execute(
        "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
        "AND table_name = 'rnaseq_runs' AND column_name = 'genome_version_id'"
    )
    assert cur.fetchone()[0] == 0
    cur.execute(
        "SELECT count(*) FROM pg_policies WHERE schemaname = 'storage' "
        "AND policyname LIKE '%%genome%%'"
    )
    assert cur.fetchone()[0] == 0


def test_the_rollback_refuses_while_the_bucket_holds_files(cur, species_id):
    _uploaded(cur, species_id)
    _refused(
        cur, _sql_body(ROLLBACK), None, psycopg.errors.RaiseException, match="non-empty"
    )
