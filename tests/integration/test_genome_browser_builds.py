"""
Integration tests for genome browser builds: numbering and folders, finishing against the
stored files, the five-role file record, and the write-once rules. Storage rules and who may do
what are in test_genome_browser_build_access.py.
"""

import json

import pytest

from tests.integration.genome_browser_helpers import (
    FILES,
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
from tests.integration.genome_reference_helpers import (
    OTHER_WRITER,
    WRITER,
    add_species,
    as_admin,
    as_role,
    new_genome_name,
    ready,
    refused,
    start,
)

psycopg = pytest.importorskip("psycopg")

FINISH = "SELECT public.finish_genome_browser_build(%s, %s, %s)"
START = "SELECT * FROM public.start_genome_browser_build(%s, '{}')"
ABANDON = "SELECT public.abandon_genome_browser_build(%s)"
WITHDRAW = "SELECT public.withdraw_genome_browser_build(%s, %s)"
NOT_NOW = psycopg.errors.ObjectNotInPrerequisiteState


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
    cur.execute("SELECT status, files, ready_at IS NOT NULL "
                "FROM genome_browser_builds WHERE id = %s", (build_id,))
    return cur.fetchone()


# --------------------------------------------------------------------------- #
# Starting
# --------------------------------------------------------------------------- #


def test_builds_are_numbered_per_version_with_their_folder(cur, genome, version_id):
    build_id, number, folder = start_build(cur, version_id)
    assert (number, folder) == (1, f"{genome}/v1/browser/b1")
    assert _build(cur, build_id) == ("uploading", None, False)
    other = ready(cur, new_genome_name(), add_species(cur))[0]
    assert start_build(cur, other)[1] == 1


def test_the_tools_used_are_kept(cur, version_id):
    build_id, _, _ = start_build(cur, version_id)
    cur.execute("SELECT tool_versions FROM genome_browser_builds WHERE id = %s", (build_id,))
    assert cur.fetchone()[0] == TOOLS


def test_one_live_build_per_version(cur, version_id):
    start_build(cur, version_id)
    as_role(cur, "bloom_writer", OTHER_WRITER)
    refused(cur, START, (version_id,), NOT_NOW, match="already has browser build 1")


def test_the_index_holds_one_live_build_whatever_inserts_it(cur, version_id):
    start_build(cur, version_id)
    as_admin(cur)
    refused(cur, "INSERT INTO genome_browser_builds (version_id, build, folder, created_by) "
            "VALUES (%s, 0, '', %s)", (version_id, WRITER), psycopg.errors.UniqueViolation,
            match="genome_browser_builds_one_live")


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
    status, files, has_ready_at = _build(cur, build_id)
    assert (status, has_ready_at) == ("ready", True)
    assert files == {
        name: {"sha256": SHA, "bytes": size} for name, (_, size) in FILES.items()
    }


def test_finish_without_the_files_is_refused(cur, version_id):
    build_id, _, folder = start_build(cur, version_id)
    put_files(cur, folder, only={"fasta", "fasta_fai", "fasta_gzi", "gff3"})
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, FINISH, (build_id, files_json(), 2), psycopg.errors.InvalidParameterValue,
            match="genes.gff3.gz.tbi has not been uploaded")


def test_finish_against_a_different_size_is_refused(cur, version_id):
    build_id, _, folder = start_build(cur, version_id)
    put_files(cur, folder)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, FINISH, (build_id, files_json(fasta_gzi={"sha256": SHA, "bytes": 65}), 2),
            psycopg.errors.InvalidParameterValue, match="is 64 bytes in storage, not 65")


@pytest.mark.parametrize("files, match", [
    (files_json(gff3_tbi=None), "exactly the five"),
    (files_json(extra={"sha256": SHA, "bytes": 1}), "exactly the five"),
    (files_json(gff3={"sha256": "E" * 64, "bytes": 2048}), "exactly the five"),
    (files_json(gff3={"sha256": SHA, "bytes": 0}), "exactly the five"),
    (files_json(gff3={"sha256": SHA, "bytes": -1}), "exactly the five"),
    (files_json(gff3={"bytes": 2048}), "exactly the five"),
    (json.dumps(["genome.fa.gz"]), "exactly the five"),
    (files_json(gff3=None, gtf={"sha256": SHA, "bytes": 2048}), "exactly the five"),
    (files_json(gff3={"sha256": SHA, "bytes": "2048"}), "exactly the five"),
    (files_json(gff3={"sha256": SHA, "bytes": 2048, "note": "x"}), "exactly the five"),
    (files_json(gff3={"sha256": SHA, "bytes": 10 ** 19}), "exactly the five"),
])
def test_finish_with_a_bad_file_record_is_refused(cur, version_id, files, match):
    build_id, _, folder = start_build(cur, version_id)
    put_files(cur, folder)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, FINISH, (build_id, files, 2), psycopg.errors.InvalidParameterValue,
            match=match)


def test_a_build_of_a_version_withdrawn_meanwhile_cannot_finish(cur, version_id):
    build_id, _, folder = start_build(cur, version_id)
    put_files(cur, folder)
    add_genes(cur, build_id)
    cur.execute("UPDATE public.genome_reference_versions SET status = 'withdrawn', "
                "withdrawn_reason = 'wrong assembly' WHERE id = %s", (version_id,))
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, FINISH, (build_id, files_json(), 2), NOT_NOW, match="cannot be made ready")


def test_another_writer_cannot_finish(cur, version_id):
    build_id, _, folder = start_build(cur, version_id)
    put_files(cur, folder)
    as_role(cur, "bloom_writer", OTHER_WRITER)
    refused(cur, FINISH, (build_id, files_json(), 2), psycopg.errors.InsufficientPrivilege,
            match="someone else")


def test_a_build_finishes_once(cur, version_id):
    build_id, _ = ready_build(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, FINISH, (build_id, files_json(), 2), NOT_NOW, match="is ready, not uploading")


def test_the_pipeline_can_make_a_build(cur, version_id):
    pipeline = "00000000-0000-0000-0000-00000000aaaa"
    build_id, _, folder = start_build(cur, version_id, user=pipeline, role="bloom_workflows")
    put_files(cur, folder, user=pipeline, role="bloom_workflows")
    add_genes(cur, build_id, user=pipeline, role="bloom_workflows")
    finish_build(cur, build_id, user=pipeline, role="bloom_workflows")
    assert _build(cur, build_id)[0] == "ready"


# --------------------------------------------------------------------------- #
# Abandoning, withdrawing, write-once
# --------------------------------------------------------------------------- #


def test_abandon_frees_the_next_number(cur, version_id):
    build_id, _, _ = start_build(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    cur.execute(ABANDON, (build_id,))
    assert cur.fetchone()[0] is True
    cur.execute(ABANDON, (build_id,))
    assert cur.fetchone()[0] is False
    as_admin(cur)
    assert _build(cur, build_id)[0] == "abandoned"
    assert start_build(cur, version_id)[1] == 2


def test_a_ready_build_cannot_be_abandoned(cur, version_id):
    build_id, _ = ready_build(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, ABANDON, (build_id,), NOT_NOW, match="is ready, not uploading")


def test_another_writer_cannot_abandon(cur, version_id):
    build_id, _, _ = start_build(cur, version_id)
    as_role(cur, "bloom_writer", OTHER_WRITER)
    refused(cur, ABANDON, (build_id,), psycopg.errors.InsufficientPrivilege, match="someone else")


def test_withdraw_allows_a_new_build_in_a_new_folder(cur, genome, version_id):
    build_id, _ = ready_build(cur, version_id)
    cur.execute("SELECT public.withdraw_genome_browser_build(%s, 'tabix index truncated')",
                (build_id,))
    assert _build(cur, build_id)[0] == "withdrawn"
    cur.execute("SELECT withdrawn_at IS NOT NULL FROM genome_browser_builds WHERE id = %s",
                (build_id,))
    assert cur.fetchone()[0] is True
    assert start_build(cur, version_id)[2] == f"{genome}/v1/browser/b2"


def test_the_admin_role_itself_can_withdraw(cur, version_id):
    build_id, _ = ready_build(cur, version_id)
    as_role(cur, "bloom_admin", WRITER)
    cur.execute("SELECT public.withdraw_genome_browser_build(%s, 'wrong GTF')", (build_id,))
    as_admin(cur)
    assert _build(cur, build_id)[0] == "withdrawn"


@pytest.mark.parametrize("reason", ["  ", None, "x" * 1001])
def test_withdraw_needs_a_reason(cur, version_id, reason):
    build_id, _ = ready_build(cur, version_id)
    refused(cur, WITHDRAW, (build_id, reason), psycopg.errors.CheckViolation)


# Each is refused by the guard itself, not by a check the table would apply anyway.
@pytest.mark.parametrize("assignment", [
    "files = '{}'", f"files = jsonb_set(files, '{{fasta,sha256}}', '\"{'d' * 64}\"')",
    "gene_count = 9", "ready_at = ready_at - interval '1 day'", "folder = 'x'",
    "tool_versions = '{}'", "build = 7", "status = 'uploading'", "status = 'abandoned'",
])
@pytest.mark.parametrize("status", ["ready", "withdrawn"])
def test_a_finished_build_never_changes(cur, version_id, status, assignment):
    build_id, _ = ready_build(cur, version_id)
    if status == "withdrawn":
        cur.execute(WITHDRAW, (build_id, "remade"))
    refused(cur, f"UPDATE genome_browser_builds SET {assignment} WHERE id = %s", (build_id,),
            NOT_NOW)


@pytest.mark.parametrize("assignment", [
    "status = 'ready'", "withdrawn_reason = 'other'", "withdrawn_at = now() - interval '1 day'",
])
def test_a_withdrawal_is_final(cur, version_id, assignment):
    build_id, _ = ready_build(cur, version_id)
    cur.execute(WITHDRAW, (build_id, "remade"))
    refused(cur, f"UPDATE genome_browser_builds SET {assignment} WHERE id = %s", (build_id,),
            NOT_NOW)


@pytest.mark.parametrize("status", ["uploading", "abandoned", "ready", "withdrawn"])
def test_no_build_is_deleted(cur, version_id, status):
    if status in ("uploading", "abandoned"):
        build_id = start_build(cur, version_id)[0]
        if status == "abandoned":
            as_role(cur, "bloom_writer", WRITER)
            cur.execute(ABANDON, (build_id,))
            as_admin(cur)
    else:
        build_id, _ = ready_build(cur, version_id)
        if status == "withdrawn":
            cur.execute(WITHDRAW, (build_id, "remade"))
    refused(cur, "DELETE FROM genome_browser_builds WHERE id = %s", (build_id,), NOT_NOW,
            match="instead of deleting it")


def test_admin_cannot_make_a_build_ready_without_its_genes(cur, version_id):
    build_id, _, folder = start_build(cur, version_id)
    put_files(cur, folder)
    add_genes(cur, build_id)
    as_admin(cur)
    refused(cur, "UPDATE genome_browser_builds SET status = 'ready', files = %s, gene_count = 9, "
            "ready_at = now() WHERE id = %s", (files_json(), build_id),
            psycopg.errors.InvalidParameterValue, match="holds 2 genes, not 9")


def test_admin_cannot_abandon_a_build_that_still_holds_genes(cur, version_id):
    build_id, _, _ = start_build(cur, version_id)
    add_genes(cur, build_id)
    as_admin(cur)
    refused(cur, "UPDATE genome_browser_builds SET status = 'abandoned' WHERE id = %s",
            (build_id,), NOT_NOW, match="still holds genes")


def test_an_uploading_build_cannot_jump_to_withdrawn(cur, version_id):
    build_id, _, _ = start_build(cur, version_id)
    refused(cur, "UPDATE genome_browser_builds SET status = 'withdrawn', "
            "withdrawn_reason = 'no' WHERE id = %s", (build_id,), NOT_NOW,
            match="cannot go from uploading")
