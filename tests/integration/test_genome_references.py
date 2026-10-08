"""
Integration tests for genome references: numbered, write-once versions of a genome's FASTA and
GTF, and the start / finish / abandon functions bloomctl calls. What may change afterwards is in
test_genome_reference_rules.py; bucket access, the run link, grants and the rollbacks are in
test_genome_reference_access.py.
"""

import pytest

from tests.integration.genome_reference_helpers import (
    OTHER_WRITER,
    SHA_A,
    SHA_B,
    WRITER,
    abandon,
    add_species,
    apply_migrations,
    as_admin,
    as_role,
    finish,
    new_genome_name,
    put_object,
    ready,
    refused,
    start,
    status,
    uploaded,
)

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

GOOD_NAMES = ["tair10_araport11", "tair10.araport11", "grch38-2024-a", "g" * 64, "v2", "a.v"]
BAD_NAMES = [
    "TAIR10", "Tair10", ".hidden", "g" * 65, "a__b", "a/b", "has space", "../etc", "",
    "tair10.v2", "tair10.v10",
]
FINISH_SQL = "SELECT public.finish_genome_version(%s, %s, %s, %s, %s)"


def _withdraw(cur, version_id, reason="wrong GTF"):
    cur.execute(
        "UPDATE genome_reference_versions SET status = 'withdrawn', withdrawn_reason = %s "
        "WHERE id = %s", (reason, version_id),
    )


# --------------------------------------------------------------------------- #
# Starting versions
# --------------------------------------------------------------------------- #


def test_a_new_genome_gets_version_1_with_its_paths(cur, species_id, genome):
    version_id, version, fasta_path, gtf_path = start(
        cur, genome, species=species_id, p_assembly="TAIR10", p_annotation="Araport11"
    )
    assert (version, fasta_path, gtf_path) == (
        1, f"{genome}/v1/genome.fa.gz", f"{genome}/v1/genes.gtf.gz",
    )
    cur.execute(
        "SELECT g.species_id, g.created_by::text, v.status, v.assembly, v.annotation, "
        "v.created_by::text FROM genome_reference_versions v "
        "JOIN genome_references g ON g.id = v.genome_id WHERE v.id = %s",
        (version_id,),
    )
    assert cur.fetchone() == (species_id, WRITER, "uploading", "TAIR10", "Araport11", WRITER)


def test_the_next_upload_of_a_genome_is_version_2(cur, species_id, genome):
    start(cur, genome, species=species_id)
    assert start(cur, genome)[1] == 2


def test_the_same_species_and_description_may_be_given_again(cur, species_id, genome):
    start(cur, genome, species=species_id, p_description="Col-0")
    assert start(cur, genome, species=species_id, p_description="Col-0")[1] == 2


def test_a_deleted_versions_number_is_not_reused(cur, species_id, genome):
    version_id = start(cur, genome, species=species_id)[0]
    abandon(cur, version_id)
    cur.execute("DELETE FROM genome_reference_versions WHERE id = %s", (version_id,))
    assert start(cur, genome)[1:] == (
        2, f"{genome}/v2/genome.fa.gz", f"{genome}/v2/genes.gtf.gz",
    )


def test_a_directly_inserted_version_is_numbered_and_placed_like_any_other(
    cur, species_id, genome
):
    start(cur, genome, species=species_id)
    cur.execute(
        "INSERT INTO genome_reference_versions (genome_id, version, fasta_path, gtf_path, "
        "created_by) SELECT id, 1, 'elsewhere', 'elsewhere', %s FROM genome_references "
        "WHERE name = %s RETURNING version, fasta_path, gtf_path, status",
        (WRITER, genome),
    )
    assert cur.fetchone() == (
        2, f"{genome}/v2/genome.fa.gz", f"{genome}/v2/genes.gtf.gz", "uploading",
    )


def test_a_version_cannot_be_inserted_as_ready(cur, species_id, genome):
    start(cur, genome, species=species_id)
    refused(
        cur,
        "INSERT INTO genome_reference_versions (genome_id, version, fasta_path, gtf_path, "
        "status, fasta_sha256, fasta_bytes, gtf_sha256, gtf_bytes, ready_at, created_by) "
        "SELECT id, 9, 'x', 'y', 'ready', %s, 1, %s, 1, now(), %s FROM genome_references "
        "WHERE name = %s",
        (SHA_A, SHA_B, WRITER, genome),
        psycopg.errors.ObjectNotInPrerequisiteState,
    )


@pytest.mark.parametrize("name", GOOD_NAMES)
def test_a_good_genome_name_is_accepted(cur, species_id, name):
    assert start(cur, name, species=species_id)[1] == 1


@pytest.mark.parametrize("name", BAD_NAMES)
def test_a_bad_genome_name_is_refused(cur, species_id, name):
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, "SELECT * FROM public.start_genome_version(%s, %s)", (name, species_id),
        psycopg.errors.InvalidParameterValue, match="lowercase",
    )


@pytest.mark.parametrize("name", ["TAIR10", "tair10.v2", "a__b"])
def test_the_table_refuses_a_bad_name_too(cur, species_id, name):
    refused(
        cur, "INSERT INTO genome_references (name, species_id, created_by) VALUES (%s, %s, %s)",
        (name, species_id, WRITER), psycopg.errors.CheckViolation,
    )


def test_a_new_genome_needs_a_species(cur, genome):
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, "SELECT * FROM public.start_genome_version(%s)", (genome,),
        psycopg.errors.InvalidParameterValue, match="give its species",
    )


@pytest.mark.parametrize("deleted", [False, True])
def test_an_unknown_or_deleted_species_is_refused(cur, genome, deleted):
    species = add_species(cur) if deleted else -1
    if deleted:
        cur.execute("UPDATE species SET deleted_at = now() WHERE id = %s", (species,))
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, "SELECT * FROM public.start_genome_version(%s, %s)", (genome, species),
        psycopg.errors.InvalidParameterValue, match="no species",
    )


def test_a_different_species_for_an_existing_genome_is_refused(cur, species_id, genome):
    start(cur, genome, species=species_id)
    other = add_species(cur)
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, "SELECT * FROM public.start_genome_version(%s, %s)", (genome, other),
        psycopg.errors.InvalidParameterValue, match="belongs to species",
    )


def test_a_different_description_names_the_stored_one(cur, species_id, genome):
    start(cur, genome, species=species_id, p_description="Col-0")
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, "SELECT * FROM public.start_genome_version(%s, p_description => 'other')",
        (genome,), psycopg.errors.InvalidParameterValue,
        match="already has the description 'Col-0'",
    )


@pytest.mark.parametrize("call", ["start", "finish", "abandon"])
def test_each_upload_function_needs_a_signed_in_user(cur, species_id, genome, call):
    version_id = uploaded(cur, genome, species_id)[0]
    sql = {
        "start": ("SELECT * FROM public.start_genome_version(%s, %s)", (genome, species_id)),
        "finish": (FINISH_SQL, (version_id, SHA_A, 100, SHA_B, 50)),
        "abandon": ("SELECT public.abandon_genome_version(%s)", (version_id,)),
    }[call]
    as_role(cur, "bloom_writer", None)
    refused(cur, *sql, psycopg.errors.InsufficientPrivilege, match="sign in")


# --------------------------------------------------------------------------- #
# Finishing and abandoning
# --------------------------------------------------------------------------- #


def test_finishing_makes_the_version_ready_and_records_the_files(cur, species_id, genome):
    version_id = uploaded(cur, genome, species_id)[0]
    assert finish(cur, version_id) == 1
    cur.execute(
        "SELECT status, fasta_sha256, fasta_bytes, gtf_sha256, gtf_bytes, ready_at IS NOT NULL "
        "FROM genome_reference_versions WHERE id = %s", (version_id,),
    )
    assert cur.fetchone() == ("ready", SHA_A, 100, SHA_B, 50, True)


def test_finishing_twice_is_refused(cur, species_id, genome):
    version_id = ready(cur, genome, species_id)[0]
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, FINISH_SQL, (version_id, SHA_A, 100, SHA_B, 50),
        psycopg.errors.ObjectNotInPrerequisiteState,
    )


@pytest.mark.parametrize("missing", ["genome.fa.gz", "genes.gtf.gz"])
def test_finishing_with_a_file_missing_names_it(cur, species_id, genome, missing):
    version_id, _, fasta_path, gtf_path = start(cur, genome, species=species_id)
    if missing == "genes.gtf.gz":
        put_object(cur, fasta_path, 100)
    else:
        put_object(cur, gtf_path, 50)
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, FINISH_SQL, (version_id, SHA_A, 100, SHA_B, 50),
        psycopg.errors.InvalidParameterValue, match=f"{missing} has not been uploaded",
    )
    as_admin(cur)
    assert status(cur, version_id) == "uploading"


def test_files_at_the_right_paths_in_another_bucket_do_not_count(cur, species_id, genome):
    version_id, _, fasta_path, gtf_path = start(cur, genome, species=species_id)
    put_object(cur, fasta_path, 100, bucket="images")
    put_object(cur, gtf_path, 50, bucket="images")
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, FINISH_SQL, (version_id, SHA_A, 100, SHA_B, 50),
        psycopg.errors.InvalidParameterValue, match="has not been uploaded",
    )


@pytest.mark.parametrize(
    "sizes, message",
    [((101, 50), "is 100 bytes in storage, not 101"), ((100, 49), "is 50 bytes in storage, not 49")],
)
def test_finishing_with_a_wrong_size_is_refused(cur, species_id, genome, sizes, message):
    version_id = uploaded(cur, genome, species_id)[0]
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, FINISH_SQL, (version_id, SHA_A, sizes[0], SHA_B, sizes[1]),
        psycopg.errors.InvalidParameterValue, match=message,
    )
    as_admin(cur)
    assert status(cur, version_id) == "uploading"


@pytest.mark.parametrize("which", ["fasta", "gtf"])
@pytest.mark.parametrize("sha", ["A" * 64, "a" * 63, "g" * 64, None])
def test_a_malformed_checksum_is_refused(cur, species_id, genome, which, sha):
    version_id = uploaded(cur, genome, species_id)[0]
    shas = (sha, SHA_B) if which == "fasta" else (SHA_A, sha)
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, FINISH_SQL, (version_id, shas[0], 100, shas[1], 50),
        psycopg.errors.InvalidParameterValue, match="checksums",
    )


@pytest.mark.parametrize("sizes", [(0, 50), (100, -1), (None, 50)])
def test_a_size_that_is_not_positive_is_refused(cur, species_id, genome, sizes):
    version_id = uploaded(cur, genome, species_id)[0]
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, FINISH_SQL, (version_id, SHA_A, sizes[0], SHA_B, sizes[1]),
        psycopg.errors.InvalidParameterValue, match="sizes must be positive",
    )


@pytest.mark.parametrize("call", ["finish", "abandon"])
def test_an_unknown_version_is_refused(cur, call):
    as_role(cur, "bloom_writer", WRITER)
    sql = (
        (FINISH_SQL, (-1, SHA_A, 1, SHA_B, 1)) if call == "finish"
        else ("SELECT public.abandon_genome_version(-1)", None)
    )
    refused(cur, *sql, psycopg.errors.InvalidParameterValue, match="no genome version")


@pytest.mark.parametrize("call", ["finish", "abandon"])
def test_someone_elses_upload_cannot_be_finished_or_abandoned(cur, species_id, genome, call):
    version_id = uploaded(cur, genome, species_id)[0]
    as_role(cur, "bloom_writer", OTHER_WRITER)
    sql = (
        (FINISH_SQL, (version_id, SHA_A, 100, SHA_B, 50)) if call == "finish"
        else ("SELECT public.abandon_genome_version(%s)", (version_id,))
    )
    refused(cur, *sql, psycopg.errors.InsufficientPrivilege, match="someone else")


def test_the_creator_can_abandon_an_upload_once(cur, species_id, genome):
    version_id = start(cur, genome, species=species_id)[0]
    assert abandon(cur, version_id) is True
    assert abandon(cur, version_id) is False
    assert status(cur, version_id) == "abandoned"


def test_an_abandoned_upload_cannot_be_finished(cur, species_id, genome):
    version_id = uploaded(cur, genome, species_id)[0]
    abandon(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, FINISH_SQL, (version_id, SHA_A, 100, SHA_B, 50),
        psycopg.errors.ObjectNotInPrerequisiteState,
    )


def test_a_ready_version_cannot_be_abandoned(cur, species_id, genome):
    version_id = ready(cur, genome, species_id)[0]
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, "SELECT public.abandon_genome_version(%s)", (version_id,),
        psycopg.errors.ObjectNotInPrerequisiteState,
    )
