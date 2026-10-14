"""
Integration tests for starting a Cell Ranger run on a genome version from Bloom:
- request_scrna_cellranger_run takes a ready genome version, records it on the run, and uses
  <genome>.v<N> as the run's reference name and in its run key;
- a version that isn't ready, or doesn't exist, is refused and nothing is queued;
- a version and a reference name together are refused; a reference name alone still works;
- S3-folder runs take a genome version the same way;
- the two new steps are allowed as a run's current step;
- only bloom_workflows may call the function, and the rollback restores the earlier one.

Each test builds the schema up to this migration inside its own transaction and rolls it
back, so the database is left unchanged.
"""

import pytest

from tests.integration import genome_reference_helpers as genome
from tests.integration.test_rnaseq_runs import USER, _find_one, _sql_body
from tests.integration.test_rnaseq_s3_folder_reads import FILES, URL
from tests.integration.test_rnaseq_s3_folder_reads import MIGRATION as FOLDER_MIGRATION
from tests.integration.test_rnaseq_sra_import import MIGRATION as SRA_MIGRATION
from tests.integration.test_rnaseq_sra_import import (
    QUEUE_TABLE,
    TABLE,
    _as_workflows,
    _bare,
    _build,
    _can_execute,
    _count,
    _params,
    _refused,
    _signatures,
)

psycopg = pytest.importorskip("psycopg")
Jsonb = psycopg.types.json.Jsonb

OLD_SIG = "public.request_scrna_cellranger_run(text, text, uuid, jsonb, text[], text, jsonb)"
NEW_SIG = (
    "public.request_scrna_cellranger_run(text, text, uuid, jsonb, text[], text, jsonb, bigint)"
)
MIGRATION = _find_one("migrations", "*_start_cellranger_runs_on_genome_versions.sql")
ROLLBACK = _find_one("rollbacks", "*_start_cellranger_runs_on_genome_versions_rollback.sql")


def _schema(c):
    _build(c)
    c.execute(_sql_body(SRA_MIGRATION))
    c.execute(_sql_body(FOLDER_MIGRATION))
    genome.apply_migrations(c)


@pytest.fixture
def cur(pg_conn):
    """After this migration, with an empty queue."""
    with pg_conn.cursor() as c:
        _schema(c)
        c.execute(_sql_body(MIGRATION))
        c.execute(f"DELETE FROM {QUEUE_TABLE}")
        yield c
    pg_conn.rollback()


@pytest.fixture
def ready_version(cur):
    """A ready version of a new genome: (version_id, genome name, version number)."""
    name = genome.new_genome_name()
    version_id, number, _, _ = genome.ready(cur, name, genome.add_species(cur))
    return version_id, name, number


def _start(cur, version_id=None, sample="col0", reference=None, url=None, files=None):
    return _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => %s, p_reference => %s, "
        "p_requested_by => %s, p_fastq_url => %s, p_fastq_files => %s, "
        "p_genome_version_id => %s)",
        (sample, reference, USER, url, None if files is None else Jsonb(files), version_id),
    )


def _run(cur, run_id):
    cur.execute(f"SELECT genome_version_id, run_key FROM {TABLE} WHERE id = %s", (run_id,))
    return cur.fetchone()


# --------------------------------------------------------------------------- #
# Starting on a genome version
# --------------------------------------------------------------------------- #


def test_a_ready_version_is_recorded_and_named_on_the_run(cur, ready_version):
    version_id, name, number = ready_version
    run_id = _start(cur, version_id)
    reference = f"{name}.v{number}"
    assert _params(cur, run_id) == {"sample": "col0", "reference": reference}
    assert _run(cur, run_id) == (version_id, f"col0__{reference}__{USER}")
    assert _count(cur, QUEUE_TABLE) == 1, "the run is queued"


@pytest.mark.parametrize("make", ["abandoned", "withdrawn", "uploading"])
def test_a_version_that_isnt_ready_is_refused_and_nothing_queued(cur, make):
    name = genome.new_genome_name()
    species = genome.add_species(cur)
    if make == "uploading":
        version_id = genome.uploaded(cur, name, species)[0]
    elif make == "abandoned":
        version_id = genome.uploaded(cur, name, species)[0]
        genome.abandon(cur, version_id)
    else:
        version_id = genome.ready(cur, name, species)[0]
        cur.execute(
            "UPDATE public.genome_reference_versions SET status = 'withdrawn', "
            "withdrawn_reason = 'wrong annotation', withdrawn_at = now() WHERE id = %s",
            (version_id,),
        )
    _refused(cur, _start, version_id, error=psycopg.errors.InvalidParameterValue,
             match=f"is {make}, not ready")
    assert _count(cur, TABLE) == 0 and _count(cur, QUEUE_TABLE) == 0


def test_an_unknown_version_is_refused(cur):
    _refused(cur, _start, 999_999_999, error=psycopg.errors.InvalidParameterValue,
             match="no genome version")
    assert _count(cur, TABLE) == 0


def test_a_version_and_a_reference_name_together_are_refused(cur, ready_version):
    _refused(cur, _start, ready_version[0], reference="tiny_ref",
             error=psycopg.errors.InvalidParameterValue, match="not both")


def test_a_reference_name_alone_still_starts_a_run(cur):
    run_id = _start(cur, reference="tiny_ref")
    assert _params(cur, run_id) == {"sample": "col0", "reference": "tiny_ref"}
    assert _run(cur, run_id)[0] is None


def test_a_folder_run_takes_a_genome_version(cur, ready_version):
    version_id, name, number = ready_version
    run_id = _start(cur, version_id, url=URL, files=FILES)
    params = _params(cur, run_id)
    assert params["reference"] == f"{name}.v{number}" and params["fastq_url"] == URL
    assert _run(cur, run_id)[0] == version_id


def test_a_folder_run_is_refused_on_a_key_already_run_with_the_same_version(cur, ready_version):
    _start(cur, ready_version[0], url=URL, files=FILES)
    _refused(cur, _start, ready_version[0], url=URL, files=FILES,
             error=psycopg.errors.UniqueViolation, match="has already been processed")


@pytest.mark.parametrize("step", ["download-genome", "build-reference"])
def test_the_new_steps_are_allowed(cur, ready_version, step):
    run_id = _start(cur, ready_version[0])
    cur.execute(f"UPDATE {TABLE} SET current_step = %s WHERE id = %s", (step, run_id))
    cur.execute(f"SELECT current_step FROM {TABLE} WHERE id = %s", (run_id,))
    assert cur.fetchone()[0] == step


# --------------------------------------------------------------------------- #
# Access, re-run and rollback
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role, allowed", [
    ("bloom_workflows", True), ("bloom_user", False), ("bloom_writer", False),
    ("bloom_agent", False), ("authenticated", False), ("anon", False),
])
def test_only_the_pipeline_may_start_a_run(cur, role, allowed):
    assert _can_execute(cur, role, NEW_SIG) is allowed


def test_there_is_one_request_function(cur):
    assert _signatures(cur, "request_scrna_cellranger_run") == [_bare(NEW_SIG)]


def test_the_migration_can_be_run_again(cur, ready_version):
    cur.execute(_sql_body(MIGRATION))
    assert _signatures(cur, "request_scrna_cellranger_run") == [_bare(NEW_SIG)]
    assert _run(cur, _start(cur, ready_version[0]))[0] == ready_version[0]


def test_the_rollback_restores_the_reference_name_function(cur, ready_version):
    _start(cur, ready_version[0])
    cur.execute(_sql_body(ROLLBACK))
    assert _signatures(cur, "request_scrna_cellranger_run") == [_bare(OLD_SIG)]
    assert _can_execute(cur, "bloom_workflows", OLD_SIG)
    assert not _can_execute(cur, "bloom_user", OLD_SIG)
    cur.execute(f"SELECT count(*) FROM {TABLE} WHERE genome_version_id IS NOT NULL")
    assert cur.fetchone()[0] == 1, "runs keep their genome version"


def test_the_rollback_refuses_while_a_run_reports_a_new_step(cur, ready_version):
    run_id = _start(cur, ready_version[0])
    cur.execute(f"UPDATE {TABLE} SET current_step = 'build-reference' WHERE id = %s", (run_id,))
    cur.execute("SAVEPOINT rb")
    with pytest.raises(psycopg.errors.RaiseException, match="clear current_step"):
        cur.execute(_sql_body(ROLLBACK))
    cur.execute("ROLLBACK TO SAVEPOINT rb")
