"""
Integration tests for what may change in a genome reference once it exists: identity and
sources, finished files, status, withdrawing, deleting, and applying the migrations again.
Starting, finishing and abandoning uploads are in test_genome_references.py.
"""


import pytest

from tests.integration.genome_reference_helpers import (
    SHA_A,
    TABLES_MIGRATION,
    UPLOADS_MIGRATION,
    abandon,
    add_run,
    add_species,
    apply_migrations,
    new_genome_name,
    ready,
    refused,
    start,
    status,
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
# What may change (checked as the superuser, which RLS and grants don't stop)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "assignment",
    ["fasta_sha256 = repeat('c', 64)", "gtf_bytes = 51", "ready_at = now() - interval '1 day'"],
)
def test_a_finished_versions_files_cannot_change(cur, species_id, genome, assignment):
    version_id = ready(cur, genome, species_id)[0]
    refused(
        cur, f"UPDATE genome_reference_versions SET {assignment} WHERE id = %s", (version_id,),
        psycopg.errors.ObjectNotInPrerequisiteState,
    )


@pytest.mark.parametrize(
    "assignment",
    [
        "genome_id = genome_id + 1000000", "version = 9", "fasta_path = 'elsewhere.fa.gz'",
        "gtf_path = 'elsewhere.gtf.gz'", "assembly = 'other'", "annotation = 'other'",
        "source_url = 'https://other'", "notes = 'x'", "created_by = gen_random_uuid()",
        "created_at = now() - interval '1 day'",
    ],
)
def test_a_versions_identity_and_sources_never_change(cur, species_id, genome, assignment):
    version_id = start(cur, genome, species=species_id)[0]
    refused(
        cur, f"UPDATE genome_reference_versions SET {assignment} WHERE id = %s", (version_id,),
        psycopg.errors.ObjectNotInPrerequisiteState,
    )


@pytest.mark.parametrize(
    "assignment",
    ["name = 'renamed'", "species_id = %(other)s", "created_by = gen_random_uuid()",
     "created_at = now() - interval '1 day'", "next_version = 1"],
)
def test_a_genomes_identity_never_changes(cur, species_id, genome, assignment):
    start(cur, genome, species=species_id)
    refused(
        cur, f"UPDATE genome_references SET {assignment} WHERE name = %(name)s",
        {"other": add_species(cur), "name": genome},
        psycopg.errors.ObjectNotInPrerequisiteState,
    )


def test_a_genomes_description_can_be_edited(cur, species_id, genome):
    start(cur, genome, species=species_id)
    cur.execute("UPDATE genome_references SET description = 'Col-0' WHERE name = %s", (genome,))
    assert cur.rowcount == 1


@pytest.mark.parametrize(
    "old, new",
    [("ready", "uploading"), ("ready", "abandoned"), ("abandoned", "uploading"),
     ("abandoned", "ready"), ("withdrawn", "ready"), ("uploading", "withdrawn")],
)
def test_status_never_goes_backward(cur, species_id, genome, old, new):
    if old == "uploading":
        version_id = start(cur, genome, species=species_id)[0]
    elif old == "abandoned":
        version_id = uploaded(cur, genome, species_id)[0]
        abandon(cur, version_id)
    else:
        version_id = ready(cur, genome, species_id)[0]
        if old == "withdrawn":
            _withdraw(cur, version_id)
    assert status(cur, version_id) == old
    refused(
        cur, "UPDATE genome_reference_versions SET status = %s, withdrawn_reason = %s "
        "WHERE id = %s", (new, "x" if new == "withdrawn" else None, version_id),
        psycopg.errors.ObjectNotInPrerequisiteState,
    )


def test_an_admin_withdraws_a_ready_version_with_a_reason(cur, species_id, genome):
    version_id = ready(cur, genome, species_id)[0]
    _withdraw(cur, version_id, "GTF filtered the wrong biotypes")
    cur.execute(
        "SELECT status, withdrawn_at IS NOT NULL, fasta_sha256 FROM genome_reference_versions "
        "WHERE id = %s", (version_id,),
    )
    assert cur.fetchone() == ("withdrawn", True, SHA_A)


@pytest.mark.parametrize("reason", [None, "   "])
def test_withdrawing_needs_a_reason(cur, species_id, genome, reason):
    version_id = ready(cur, genome, species_id)[0]
    refused(
        cur, "UPDATE genome_reference_versions SET status = 'withdrawn', withdrawn_reason = %s "
        "WHERE id = %s", (reason, version_id), psycopg.errors.CheckViolation,
    )


def test_a_withdrawal_reason_cannot_be_added_without_withdrawing(cur, species_id, genome):
    version_id = ready(cur, genome, species_id)[0]
    refused(
        cur, "UPDATE genome_reference_versions SET withdrawn_reason = 'x' WHERE id = %s",
        (version_id,), psycopg.errors.ObjectNotInPrerequisiteState,
    )


@pytest.mark.parametrize("final", ["ready", "withdrawn"])
def test_a_finished_version_cannot_be_deleted(cur, species_id, genome, final):
    version_id = ready(cur, genome, species_id)[0]
    if final == "withdrawn":
        _withdraw(cur, version_id)
    refused(
        cur, "DELETE FROM genome_reference_versions WHERE id = %s", (version_id,),
        psycopg.errors.ObjectNotInPrerequisiteState, match="withdraw it instead",
    )


def test_a_genome_with_versions_cannot_be_deleted(cur, species_id, genome):
    start(cur, genome, species=species_id)
    refused(
        cur, "DELETE FROM genome_references WHERE name = %s", (genome,),
        psycopg.errors.ForeignKeyViolation,
    )


def test_applying_the_migrations_again_keeps_genomes_runs_and_grants(cur, species_id, genome):
    version_id = ready(cur, genome, species_id)[0]
    run_id = add_run(cur, version_id)
    grants_sql = (
        "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_name IN ('genome_references', 'genome_reference_versions') ORDER BY 1, 2"
    )
    cur.execute(grants_sql)
    grants = cur.fetchall()

    cur.execute(_sql_body(TABLES_MIGRATION))
    cur.execute(_sql_body(UPLOADS_MIGRATION))

    assert status(cur, version_id) == "ready"
    cur.execute("SELECT genome_version_id FROM rnaseq_runs WHERE id = %s", (run_id,))
    assert cur.fetchone()[0] == version_id
    cur.execute(
        "SELECT count(*) FROM pg_constraint WHERE conname = 'rnaseq_runs_genome_version_id_fkey'"
    )
    assert cur.fetchone()[0] == 1
    cur.execute(grants_sql)
    assert cur.fetchall() == grants
    assert start(cur, genome)[1] == 2
