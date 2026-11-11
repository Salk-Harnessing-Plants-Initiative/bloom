"""
Integration tests for a browser build's gene positions: adding them in batches, the gene count
checked on finish, which genes readers see, write-once rules, and who may do what.
"""

import json

import pytest

from tests.integration.genome_browser_helpers import (
    GENES,
    ROLLBACKS,
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
)
from tests.integration.test_rnaseq_runs import _sql_body

psycopg = pytest.importorskip("psycopg")

ADD = "SELECT public.add_genome_browser_genes(%s, %s)"
FINISH = "SELECT public.finish_genome_browser_build(%s, %s, %s)"
PIPELINE = "00000000-0000-0000-0000-00000000aaaa"


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        apply_migrations(c)
        yield c
    pg_conn.rollback()


@pytest.fixture
def version_id(cur):
    return ready(cur, new_genome_name(), add_species(cur))[0]


def _gene(**overrides):
    return {**GENES[0], **overrides}


def _positions(cur, version_id, role="bloom_user"):
    """A version's genes as a reader sees them; no status filter, so the read rule must hide them."""
    as_role(cur, role, WRITER)
    cur.execute("SELECT g.gene_id, g.gene_name, g.contig, g.gene_start, g.gene_end, g.strand "
                "FROM genome_browser_genes g JOIN genome_browser_builds b ON b.id = g.browser_build_id "
                "WHERE b.version_id = %s ORDER BY g.gene_id", (version_id,))
    rows = cur.fetchall()
    as_admin(cur)
    return rows


def _gene_rows(cur, build_id, role=None):
    if role:
        as_role(cur, role, WRITER)
    cur.execute("SELECT count(*) FROM genome_browser_genes WHERE browser_build_id = %s", (build_id,))
    count = cur.fetchone()[0]
    as_admin(cur)
    return count


# --------------------------------------------------------------------------- #
# Adding genes and finishing
# --------------------------------------------------------------------------- #


def test_a_ready_builds_genes_are_shown_at_their_positions(cur, version_id):
    build_id, _ = ready_build(cur, version_id)
    assert _positions(cur, version_id) == [
        (g["gene_id"], g["gene_name"], g["contig"], g["gene_start"], g["gene_end"], g["strand"])
        for g in GENES
    ]
    cur.execute("SELECT gene_count FROM genome_browser_builds WHERE id = %s", (build_id,))
    assert cur.fetchone()[0] == len(GENES)


def test_genes_can_be_added_in_several_batches(cur, version_id):
    build_id, _, folder = start_build(cur, version_id)
    put_files(cur, folder)
    assert add_genes(cur, build_id, GENES[:1]) == 1
    assert add_genes(cur, build_id, GENES[1:]) == 1
    finish_build(cur, build_id)
    assert len(_positions(cur, version_id)) == 2


@pytest.mark.parametrize("count", [1, 3])
def test_finish_with_a_different_gene_count_is_refused(cur, version_id, count):
    build_id, _, folder = start_build(cur, version_id)
    put_files(cur, folder)
    add_genes(cur, build_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, FINISH, (build_id, files_json(), count),
            psycopg.errors.InvalidParameterValue, match=f"holds 2 genes, not {count}")


def test_finish_with_no_genes_is_refused(cur, version_id):
    build_id, _, folder = start_build(cur, version_id)
    put_files(cur, folder)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, FINISH, (build_id, files_json(), 0),
            psycopg.errors.InvalidParameterValue, match="at least one gene")


@pytest.mark.parametrize("genes", [[], [GENES[0]] * 5001, {"gene_id": "AT1G01010"}, None])
def test_a_batch_must_be_1_to_5000_genes(cur, version_id, genes):
    build_id, _, _ = start_build(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, ADD, (build_id, None if genes is None else json.dumps(genes)),
            psycopg.errors.InvalidParameterValue, match="1 to 5000 genes")


@pytest.mark.parametrize("gene", [
    _gene(gene_id=" "), _gene(gene_id=None), _gene(contig=""), _gene(gene_start=0),
    _gene(gene_end=3000), _gene(strand="x"), _gene(gene_id="A" * 201),
])
def test_a_malformed_gene_is_refused(cur, version_id, gene):
    build_id, _, _ = start_build(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, ADD, (build_id, json.dumps([gene])),
            (psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation))


def test_a_batch_sent_again_is_skipped(cur, version_id):
    build_id, _, folder = start_build(cur, version_id)
    assert add_genes(cur, build_id, GENES[:1]) == 1
    assert add_genes(cur, build_id, GENES) == 1
    assert add_genes(cur, build_id, GENES) == 0
    assert _gene_rows(cur, build_id) == 2
    put_files(cur, folder)
    finish_build(cur, build_id)


def test_a_gene_sent_again_at_a_different_position_is_refused(cur, version_id):
    build_id, _, _ = start_build(cur, version_id)
    add_genes(cur, build_id, GENES[:1])
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, ADD, (build_id, json.dumps([_gene(gene_end=6000)])),
            psycopg.errors.UniqueViolation, match="AT1G01010 was already added .* different details")


def test_a_gene_twice_in_one_batch_is_refused(cur, version_id):
    build_id, _, _ = start_build(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, ADD, (build_id, json.dumps([GENES[0], GENES[0]])),
            psycopg.errors.InvalidParameterValue, match="AT1G01010 appears more than once")


def test_another_writer_cannot_add_genes(cur, version_id):
    build_id, _, _ = start_build(cur, version_id)
    as_role(cur, "bloom_writer", OTHER_WRITER)
    refused(cur, ADD, (build_id, json.dumps(GENES)), psycopg.errors.InsufficientPrivilege,
            match="someone else")


def test_the_pipeline_can_add_genes(cur, version_id):
    build_id, _, _ = start_build(cur, version_id, user=PIPELINE, role="bloom_workflows")
    assert add_genes(cur, build_id, user=PIPELINE, role="bloom_workflows") == 2


# --------------------------------------------------------------------------- #
# Which genes readers see
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent", "bloom_workflows"])
def test_an_unfinished_builds_genes_are_not_shown(cur, version_id, role):
    build_id, _, _ = start_build(cur, version_id)
    add_genes(cur, build_id)
    assert _positions(cur, version_id, role) == []
    assert _gene_rows(cur, build_id, role) == 0
    assert _gene_rows(cur, build_id) == 2


def test_a_withdrawn_builds_genes_are_not_shown(cur, version_id):
    build_id, _ = ready_build(cur, version_id)
    cur.execute("SELECT public.withdraw_genome_browser_build(%s, 'wrong GTF')", (build_id,))
    assert _positions(cur, version_id) == []
    assert _gene_rows(cur, build_id, "bloom_agent") == 0
    assert _gene_rows(cur, build_id) == 2


def test_abandon_removes_the_genes(cur, version_id):
    build_id, _, _ = start_build(cur, version_id)
    add_genes(cur, build_id)
    as_role(cur, "bloom_writer", WRITER)
    cur.execute("SELECT public.abandon_genome_browser_build(%s)", (build_id,))
    as_admin(cur)
    assert _gene_rows(cur, build_id) == 0


@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent", "bloom_workflows"])
def test_the_positions_can_be_read(cur, version_id, role):
    ready_build(cur, version_id)
    assert len(_positions(cur, version_id, role)) == 2


# --------------------------------------------------------------------------- #
# Write-once
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", ["ready", "withdrawn"])
@pytest.mark.parametrize("statement", [
    "UPDATE genome_browser_genes SET gene_start = 1 WHERE browser_build_id = %s",
    "DELETE FROM genome_browser_genes WHERE browser_build_id = %s",
    "INSERT INTO genome_browser_genes (browser_build_id, gene_id, contig, gene_start, gene_end, strand) "
    "VALUES (%s, 'AT9G99999', 'Chr9', 1, 2, '+')",
])
def test_a_finished_builds_genes_never_change(cur, version_id, status, statement):
    build_id, _ = ready_build(cur, version_id)
    if status == "withdrawn":
        cur.execute("SELECT public.withdraw_genome_browser_build(%s, 'remade')", (build_id,))
    refused(cur, statement, (build_id,), psycopg.errors.ObjectNotInPrerequisiteState,
            match=f"is {status}")


def test_genes_cannot_be_added_to_a_ready_build(cur, version_id):
    build_id, _ = ready_build(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, ADD, (build_id, json.dumps([_gene(gene_id="AT9G99999")])),
            psycopg.errors.ObjectNotInPrerequisiteState, match="is ready, not uploading")


# --------------------------------------------------------------------------- #
# Privileges (asserted, never invoked as a denied role)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role, privilege, allowed", [
    ("bloom_user", "SELECT", True), ("bloom_agent", "SELECT", True),
    ("bloom_workflows", "SELECT", True), ("bloom_writer", "INSERT", False),
    ("bloom_workflows", "INSERT", False), ("bloom_user", "DELETE", False),
    ("anon", "SELECT", False), ("authenticated", "SELECT", False),
])
def test_gene_access(cur, role, privilege, allowed):
    cur.execute("SELECT has_table_privilege(%s, 'public.genome_browser_genes', %s)", (role, privilege))
    assert cur.fetchone()[0] is allowed


def test_the_rollback_removes_the_genes(cur, version_id):
    ready_build(cur, version_id)
    for rollback in ROLLBACKS:
        cur.execute(_sql_body(rollback))
    cur.execute("SELECT to_regclass('public.genome_browser_genes'), "
                "to_regprocedure('public.add_genome_browser_genes(bigint, jsonb)')")
    assert cur.fetchone() == (None, None)
