"""
Integration tests for genome annotation builds: numbering, the upload handshake, what each
source may add, write-once rules, and who may do what.
"""

import json

import pytest

from tests.integration.annotation_enrichment_helpers import (
    GO_TERMS,
    KEGG_TERMS,
    TABLES,
    add_gene_terms,
    add_terms,
    apply_migrations,
    call,
    gene_terms,
    ready_build,
    start_build,
)
from tests.integration.genome_reference_helpers import (
    OTHER_WRITER,
    WRITER,
    add_species,
    as_role,
    new_genome_name,
    ready,
    refused,
    start,
)

psycopg = pytest.importorskip("psycopg")

WRITER_FUNCTIONS = (
    "start_genome_annotation_build(bigint, text, jsonb)",
    "add_functional_terms(jsonb)",
    "add_genome_gene_terms(bigint, jsonb)",
    "add_genome_gene_descriptions(bigint, jsonb)",
    "finish_genome_annotation_build(bigint, integer, bigint)",
    "abandon_genome_annotation_build(bigint)",
)


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        apply_migrations(c)
        yield c
    pg_conn.rollback()


@pytest.fixture
def version_id(cur):
    return ready(cur, new_genome_name(), add_species(cur))[0]


def _status(cur, build_id):
    cur.execute("SELECT status FROM genome_annotation_builds WHERE id = %s", (build_id,))
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# Starting
# --------------------------------------------------------------------------- #


def test_builds_are_numbered_per_version_and_source(cur, version_id):
    eggnog, number = start_build(cur, version_id, "eggnog")
    assert number == 1 and _status(cur, eggnog) == "uploading"
    assert start_build(cur, version_id, "mercator")[1] == 1


def test_one_live_build_per_version_and_source(cur, version_id):
    start_build(cur, version_id, "eggnog")
    as_role(cur, "bloom_writer", OTHER_WRITER)
    refused(
        cur, "SELECT * FROM public.start_genome_annotation_build(%s, 'eggnog', '{}')", (version_id,),
        psycopg.errors.ObjectNotInPrerequisiteState, match="already has eggnog annotation build 1",
    )


def test_a_version_that_is_not_ready_is_refused(cur):
    uploading = start(cur, new_genome_name(), species=add_species(cur))[0]
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, "SELECT * FROM public.start_genome_annotation_build(%s, 'eggnog', '{}')", (uploading,),
        psycopg.errors.ObjectNotInPrerequisiteState, match="not ready",
    )


def test_an_unknown_source_is_refused(cur, version_id):
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, "SELECT * FROM public.start_genome_annotation_build(%s, 'interpro', '{}')",
        (version_id,), psycopg.errors.CheckViolation,
    )


# --------------------------------------------------------------------------- #
# Terms and rows
# --------------------------------------------------------------------------- #


def test_terms_are_added_and_renamed(cur):
    assert add_terms(cur, GO_TERMS) == 2
    renamed = [{**GO_TERMS[0], "name": "photosynthesis (renamed)"}]
    assert add_terms(cur, renamed) == 1
    assert add_terms(cur, renamed) == 0
    cur.execute("SELECT name FROM functional_terms WHERE term_id = 'GO:0015979'")
    assert cur.fetchone()[0] == "photosynthesis (renamed)"


@pytest.mark.parametrize(
    "term",
    [
        {"kind": "go", "term_id": "GO:1", "name": "x"},
        {"kind": "kegg_pathway", "term_id": "map1", "name": "x", "namespace": "BP"},
        {"kind": "reactome", "term_id": "R-1", "name": "x"},
        {"kind": "go", "term_id": "GO:1", "name": " ", "namespace": "BP"},
    ],
)
def test_bad_terms_are_refused(cur, term):
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT public.add_functional_terms(%s)", (json.dumps([term]),),
            psycopg.errors.CheckViolation)


def test_each_source_adds_only_its_kinds(cur, version_id):
    build_id, _ = start_build(cur, version_id, "tair-go")
    add_terms(cur, GO_TERMS + KEGG_TERMS)
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, "SELECT public.add_genome_gene_terms(%s, %s)",
        (build_id, json.dumps(gene_terms([("G1", "map00195")], kind="kegg_pathway"))),
        psycopg.errors.InvalidParameterValue, match="holds only go terms",
    )


def test_a_term_must_be_named_first(cur, version_id):
    build_id, _ = start_build(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(
        cur, "SELECT public.add_genome_gene_terms(%s, %s)",
        (build_id, json.dumps(gene_terms([("G1", "GO:9999999")]))), psycopg.errors.ForeignKeyViolation,
    )


@pytest.mark.parametrize("rows", [[], [{"gene_id": "G", "kind": "go", "term_id": "GO:0015979"}] * 5001])
def test_batches_are_1_to_5000_rows(cur, version_id, rows):
    build_id, _ = start_build(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT public.add_genome_gene_terms(%s, %s)", (build_id, json.dumps(rows)),
            psycopg.errors.InvalidParameterValue)


def test_another_writer_cannot_add_rows(cur, version_id):
    build_id, _ = start_build(cur, version_id)
    add_terms(cur, GO_TERMS)
    as_role(cur, "bloom_writer", OTHER_WRITER)
    refused(
        cur, "SELECT public.add_genome_gene_terms(%s, %s)",
        (build_id, json.dumps(gene_terms([("G1", "GO:0015979")]))),
        psycopg.errors.InsufficientPrivilege, match="someone else",
    )


def test_descriptions_need_a_name_or_description(cur, version_id):
    build_id, _ = start_build(cur, version_id)
    assert call(cur, "SELECT public.add_genome_gene_descriptions(%s, %s)", (build_id, json.dumps(
        [{"gene_id": "G1", "gene_name": "PSBA", "description": "photosystem II protein D1"}]))) == 1
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT public.add_genome_gene_descriptions(%s, %s)",
            (build_id, json.dumps([{"gene_id": "G2"}])), psycopg.errors.CheckViolation)


# --------------------------------------------------------------------------- #
# Finishing, abandoning, withdrawing
# --------------------------------------------------------------------------- #


def test_finish_records_the_counts(cur, version_id):
    build_id = ready_build(cur, version_id)
    cur.execute("SELECT status, gene_count, term_count FROM genome_annotation_builds WHERE id = %s",
                (build_id,))
    assert cur.fetchone() == ("ready", 3, 4)


@pytest.mark.parametrize("genes, rows", [(2, 3), (3, 2), (None, 3), (0, 0)])
def test_finish_with_wrong_counts_is_refused(cur, version_id, genes, rows):
    build_id, _ = start_build(cur, version_id)
    add_terms(cur, GO_TERMS)
    add_gene_terms(cur, build_id, gene_terms([("G1", "GO:0015979"), ("G2", "GO:0015979"),
                                              ("G3", "GO:0015979")]))
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT public.finish_genome_annotation_build(%s, %s, %s)", (build_id, genes, rows),
            psycopg.errors.InvalidParameterValue, match="holds 3 genes and 3 rows")


def test_a_build_finishes_once(cur, version_id):
    build_id = ready_build(cur, version_id)
    as_role(cur, "bloom_writer", WRITER)
    refused(cur, "SELECT public.finish_genome_annotation_build(%s, 3, 4)", (build_id,),
            psycopg.errors.ObjectNotInPrerequisiteState, match="is ready, not uploading")


def test_abandon_removes_the_rows(cur, version_id):
    build_id, _ = start_build(cur, version_id)
    add_terms(cur, GO_TERMS)
    add_gene_terms(cur, build_id, gene_terms([("G1", "GO:0015979")]))
    assert call(cur, "SELECT public.abandon_genome_annotation_build(%s)", (build_id,)) is True
    cur.execute("SELECT count(*) FROM genome_gene_terms WHERE build_id = %s", (build_id,))
    assert cur.fetchone()[0] == 0 and _status(cur, build_id) == "abandoned"
    assert start_build(cur, version_id)[1] == 2


def test_withdraw_frees_the_version_for_a_new_build(cur, version_id):
    build_id = ready_build(cur, version_id)
    cur.execute("SELECT public.withdraw_genome_annotation_build(%s, 'wrong GO release')", (build_id,))
    assert _status(cur, build_id) == "withdrawn"
    assert start_build(cur, version_id)[1] == 2


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE genome_annotation_builds SET term_count = 9 WHERE id = %(id)s",
        "UPDATE genome_annotation_builds SET tool_versions = '{}' WHERE id = %(id)s",
        "UPDATE genome_annotation_builds SET status = 'uploading' WHERE id = %(id)s",
        "DELETE FROM genome_annotation_builds WHERE id = %(id)s",
        "DELETE FROM genome_gene_terms WHERE build_id = %(id)s",
        "UPDATE genome_gene_terms SET direct = false WHERE build_id = %(id)s",
        "INSERT INTO genome_gene_terms VALUES (%(id)s, 'G9', 'go', 'GO:0015979', true)",
    ],
)
def test_a_ready_build_never_changes(cur, version_id, sql):
    build_id = ready_build(cur, version_id)
    refused(cur, sql, {"id": build_id}, psycopg.errors.ObjectNotInPrerequisiteState)


# --------------------------------------------------------------------------- #
# Privileges (asserted, never invoked as a denied role)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("table", TABLES[2:])
@pytest.mark.parametrize("role", ("bloom_user", "bloom_agent", "bloom_workflows"))
def test_readers_can_select(cur, table, role):
    cur.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (role, f"public.{table}"))
    assert cur.fetchone()[0] is True


@pytest.mark.parametrize("table", TABLES[2:])
@pytest.mark.parametrize("role", ("bloom_writer", "bloom_workflows", "bloom_user", "anon"))
def test_only_admin_writes_directly(cur, table, role):
    cur.execute("SELECT has_table_privilege(%s, %s, 'INSERT')", (role, f"public.{table}"))
    assert cur.fetchone()[0] is False


@pytest.mark.parametrize("function", WRITER_FUNCTIONS)
@pytest.mark.parametrize("role, allowed", [("bloom_writer", True), ("bloom_workflows", True),
                                           ("bloom_user", False), ("anon", False)])
def test_who_may_upload(cur, function, role, allowed):
    cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, function))
    assert cur.fetchone()[0] is allowed


def test_withdraw_is_admin_only(cur):
    for role, allowed in (("bloom_admin", True), ("bloom_writer", False), ("bloom_workflows", False)):
        cur.execute("SELECT has_function_privilege(%s, "
                    "'withdraw_genome_annotation_build(bigint, text)', 'EXECUTE')", (role,))
        assert cur.fetchone()[0] is allowed
