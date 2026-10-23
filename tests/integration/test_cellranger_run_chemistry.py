"""
Integration tests for a Cell Ranger run's chemistry (request_scrna_cellranger_run's p_chemistry):
- left out, the run stores no chemistry and Cell Ranger detects it, as before;
- 'ARC-v1' (a 10x Multiome RNA library) is stored in params, for SRA and folder runs;
- any other value is refused and nothing is created or queued;
- only the new signature exists, and only bloom_workflows may call it;
- the table's params check allows ARC-v1 only;
- the migration runs twice, and the rollback puts back the 8-argument function and the check
  (refused while a run has a chemistry).

Each test builds the schema up to this migration inside its own transaction and rolls it
back, so the database is left unchanged.
"""

import pytest

from tests.integration.test_rnaseq_run_genome_version import MIGRATION as GENOME_MIGRATION
from tests.integration.test_rnaseq_run_genome_version import NEW_SIG as OLD_SIG
from tests.integration.test_rnaseq_run_genome_version import _schema, genome
from tests.integration.test_rnaseq_runs import USER, _find_one, _sql_body
from tests.integration.test_rnaseq_s3_folder_reads import FILES, URL
from tests.integration.test_rnaseq_sra_import import (
    QUEUE_TABLE,
    TABLE,
    _as_workflows,
    _bare,
    _can_execute,
    _count,
    _params,
    _refused,
    _signatures,
)
from tests.integration.test_run_reports_and_sra_rules import MIGRATION as REPORTS_MIGRATION

psycopg = pytest.importorskip("psycopg")
Jsonb = psycopg.types.json.Jsonb

MIGRATION = _find_one("migrations", "*_add_chemistry_to_cellranger_runs.sql")
ROLLBACK = _find_one("rollbacks", "*_add_chemistry_to_cellranger_runs_rollback.sql")
NEW_SIG = OLD_SIG.removesuffix(")") + ", text)"
RUNS = ["SRR26057468"]


@pytest.fixture
def cur(pg_conn):
    """After this migration, with an empty queue."""
    with pg_conn.cursor() as c:
        _schema(c)
        c.execute(_sql_body(GENOME_MIGRATION))
        c.execute(_sql_body(REPORTS_MIGRATION))
        c.execute(_sql_body(MIGRATION))
        c.execute(f"DELETE FROM {QUEUE_TABLE}")
        yield c
    pg_conn.rollback()


@pytest.fixture
def version(cur):
    version_id, _, _, _ = genome.ready(cur, genome.new_genome_name(), genome.add_species(cur))
    return version_id


def _import(cur, version_id, sample="cotseed", runs=RUNS, chemistry=None):
    """An SRA run; chemistry is only passed when given, as a caller from before would."""
    sql = ("SELECT request_scrna_cellranger_run(p_sample => %s, p_reference => NULL, "
           "p_requested_by => %s, p_sra_runs => %s, p_genome_version_id => %s")
    args = [sample, USER, runs, version_id]
    if chemistry is not None:
        sql += ", p_chemistry => %s"
        args.append(chemistry)
    return _as_workflows(cur, sql + ")", tuple(args))


def _folder(cur, version_id, chemistry):
    return _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => %s, p_reference => NULL, "
        "p_requested_by => %s, p_fastq_url => %s, p_fastq_files => %s, "
        "p_genome_version_id => %s, p_chemistry => %s)",
        ("col0", USER, URL, Jsonb(FILES), version_id, chemistry),
    )


def test_a_run_without_a_chemistry_stores_none(cur, version):
    run_id = _import(cur, version)
    assert "chemistry" not in _params(cur, run_id)
    assert _count(cur, QUEUE_TABLE) == 1


def test_a_null_chemistry_is_the_same_as_none(cur, version):
    run_id = _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => %s, p_reference => NULL, "
        "p_requested_by => %s, p_sra_runs => %s, p_genome_version_id => %s, p_chemistry => NULL)",
        ("cotseed", USER, RUNS, version),
    )
    assert "chemistry" not in _params(cur, run_id)


def test_a_multiome_rna_run_stores_its_chemistry(cur, version):
    run_id = _import(cur, version, chemistry="ARC-v1")
    params = _params(cur, run_id)
    assert set(params) == {"sample", "reference", "chemistry", "sra_runs"}
    assert (params["chemistry"], params["sra_runs"]) == ("ARC-v1", RUNS)
    assert _count(cur, QUEUE_TABLE) == 1


def test_a_folder_run_stores_its_chemistry_too(cur, version):
    run_id = _folder(cur, version, "ARC-v1")
    params = _params(cur, run_id)
    assert params["chemistry"] == "ARC-v1" and params["fastq_url"] == URL


@pytest.mark.parametrize("chemistry", ["SC3Pv3", "arc-v1", "ARC-v1 ", "", "auto"])
def test_any_other_chemistry_is_refused_and_nothing_is_queued(cur, version, chemistry):
    _refused(cur, _import, version, chemistry=chemistry,
             error=psycopg.errors.InvalidParameterValue, match="unknown chemistry")
    assert _count(cur, TABLE) == 0
    assert _count(cur, QUEUE_TABLE) == 0


def test_only_the_new_signature_exists(cur):
    assert _signatures(cur, "request_scrna_cellranger_run") == [_bare(NEW_SIG)]


@pytest.mark.parametrize("role,allowed", [
    ("bloom_workflows", True),
    ("bloom_user", False),
    ("anon", False),
    ("authenticated", False),
    ("service_role", False),
])
def test_only_the_pipeline_may_call_it(cur, role, allowed):
    assert _can_execute(cur, role, NEW_SIG) is allowed


def test_the_migration_runs_twice(cur, version):
    cur.execute(_sql_body(MIGRATION))
    assert _signatures(cur, "request_scrna_cellranger_run") == [_bare(NEW_SIG)]
    assert _params(cur, _import(cur, version, chemistry="ARC-v1"))["chemistry"] == "ARC-v1"


def test_the_table_refuses_any_other_chemistry_written_directly(cur, version):
    run_id = _import(cur, version, chemistry="ARC-v1")
    cur.execute("SAVEPOINT direct")
    with pytest.raises(psycopg.errors.CheckViolation, match="rnaseq_runs_scrna_cellranger_check"):
        cur.execute(
            f"UPDATE {TABLE} SET params = params || '{{\"chemistry\": \"SC3Pv3\"}}' WHERE id = %s",
            (run_id,),
        )
    cur.execute("ROLLBACK TO SAVEPOINT direct")


def test_the_rollback_restores_the_eight_argument_function_and_check(cur, version):
    run_id = _import(cur, version)
    cur.execute(_sql_body(ROLLBACK))
    assert _signatures(cur, "request_scrna_cellranger_run") == [_bare(OLD_SIG)]
    assert _can_execute(cur, "bloom_workflows", OLD_SIG)
    assert not _can_execute(cur, "authenticated", OLD_SIG)
    assert "chemistry" not in _params(cur, run_id)


def test_the_rollback_is_refused_while_a_run_has_a_chemistry(cur, version):
    _import(cur, version, chemistry="ARC-v1")
    _refused(cur, lambda c: c.execute(_sql_body(ROLLBACK)),
             error=psycopg.errors.CheckViolation, match="rnaseq_runs_scrna_cellranger_check")
