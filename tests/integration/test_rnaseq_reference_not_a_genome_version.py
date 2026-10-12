"""
Integration tests for keeping reference names apart from genome versions:
- a run started by a reference name alone can't use a name ending in .v<N>, the form a run on a
  genome version takes, so the two never share a run key or output folder;
- a run on a genome version, and ordinary reference names, are unaffected;
- only bloom_workflows may start a run, now without service_role;
- the migration runs again cleanly, and the rollback removes the rule.

Each test builds the schema up to this migration inside its own transaction and rolls it
back, so the database is left unchanged.
"""

import pytest

from tests.integration import genome_reference_helpers as genome
from tests.integration.test_rnaseq_run_genome_version import MIGRATION as VERSION_MIGRATION
from tests.integration.test_rnaseq_run_genome_version import NEW_SIG, _schema, _start
from tests.integration.test_rnaseq_runs import USER, _find_one, _sql_body
from tests.integration.test_rnaseq_sra_import import QUEUE_TABLE, TABLE, _can_execute, _refused

psycopg = pytest.importorskip("psycopg")

MIGRATION = _find_one("migrations", "*_keep_reference_names_apart_from_genome_versions.sql")
ROLLBACK = _find_one(
    "rollbacks", "*_keep_reference_names_apart_from_genome_versions_rollback.sql"
)
RULE = "rnaseq_runs_reference_not_a_genome_version"


@pytest.fixture
def cur(pg_conn):
    """After this migration, with an empty queue."""
    with pg_conn.cursor() as c:
        _schema(c)
        c.execute(_sql_body(VERSION_MIGRATION))
        c.execute(_sql_body(MIGRATION))
        c.execute(f"DELETE FROM {QUEUE_TABLE}")
        yield c
    pg_conn.rollback()


def _has_rule(cur):
    cur.execute(
        "SELECT count(*) FROM pg_constraint WHERE conname = %s "
        "AND conrelid = 'public.rnaseq_runs'::regclass",
        (RULE,),
    )
    return cur.fetchone()[0] == 1


@pytest.mark.parametrize("reference", ["tair10.v1", "athaliana_col0.v12", "x.v0"])
def test_a_reference_name_written_like_a_version_is_refused(cur, reference):
    _refused(cur, _start, reference=reference, error=psycopg.errors.CheckViolation,
             match=RULE)


@pytest.mark.parametrize("reference", ["tiny_ref", "Athaliana.Col-0.HPIv02", "tair10.v1a",
                                       "ref.version2", "ref.v"])
def test_other_reference_names_still_start_a_run(cur, reference):
    run_id = _start(cur, reference=reference)
    cur.execute(f"SELECT params ->> 'reference' FROM {TABLE} WHERE id = %s", (run_id,))
    assert cur.fetchone()[0] == reference


def test_a_run_on_a_genome_version_is_unaffected(cur):
    name = genome.new_genome_name()
    version_id = genome.ready(cur, name, genome.add_species(cur))[0]
    run_id = _start(cur, version_id)
    cur.execute(f"SELECT params ->> 'reference' FROM {TABLE} WHERE id = %s", (run_id,))
    assert cur.fetchone()[0] == f"{name}.v1"


def test_a_direct_write_is_held_to_the_rule_too(cur):
    cur.execute("SAVEPOINT bad")
    with pytest.raises(psycopg.errors.CheckViolation, match=RULE):
        cur.execute(
            f"INSERT INTO {TABLE} (workflow_type, params, run_key, requested_by) "
            "VALUES ('scrna-cellranger', %s, %s, %s)",
            (psycopg.types.json.Jsonb({"sample": "col0", "reference": "tair10.v1"}),
             f"col0__tair10.v1__{USER}", USER),
        )
    cur.execute("ROLLBACK TO SAVEPOINT bad")


@pytest.mark.parametrize("role, allowed", [
    ("bloom_workflows", True), ("service_role", False), ("bloom_user", False),
    ("bloom_writer", False), ("bloom_agent", False), ("authenticated", False), ("anon", False),
])
def test_only_the_pipeline_may_start_a_run(cur, role, allowed):
    assert _can_execute(cur, role, NEW_SIG) is allowed


def test_the_migration_can_be_run_again(cur):
    cur.execute(_sql_body(MIGRATION))
    assert _has_rule(cur)
    assert not _can_execute(cur, "service_role", NEW_SIG)


def test_the_rollback_removes_the_rule(cur):
    cur.execute(_sql_body(ROLLBACK))
    assert not _has_rule(cur)
    assert _can_execute(cur, "service_role", NEW_SIG)
    assert _start(cur, reference="tair10.v1")
