"""
Integration tests for keeping run reports and retiring the SRA name rules:
- the pipeline may write Cell Ranger reports under reports/ in the scrna bucket, besides h5ad/
  and counts/, and nowhere else there;
- a run needs an S3 folder or SRA run IDs; a sample name alone is refused, even a registered one;
- an imported name isn't locked across scientists, and the registered samples aren't checked;
- a run is refused while its run key (its folder on the cluster) has a run that hasn't failed,
  whatever either run reads from;
- the same scientist's same SRA runs against the same genome under another name are still
  refused;
- register_rnaseq_sample is gone; the start function keeps its signature and caller;
- the migration runs twice, and the rollback puts back the earlier rules.

Each test builds the schema up to this migration inside its own transaction and rolls it
back, so the database is left unchanged.
"""

import pytest

from tests.integration.test_rnaseq_run_genome_version import MIGRATION as GENOME_MIGRATION
from tests.integration.test_rnaseq_run_genome_version import NEW_SIG as SIG
from tests.integration.test_rnaseq_run_genome_version import _schema, genome
from tests.integration.test_rnaseq_runs import OTHER_USER, USER, _find_one, _sql_body
from tests.integration.test_rnaseq_s3_folder_reads import FILES, URL
from tests.integration.test_rnaseq_sra_import import (
    QUEUE_TABLE,
    SAMPLES,
    TABLE,
    _as_workflows,
    _bare,
    _can_execute,
    _count,
    _params,
    _refused,
    _signatures,
)

psycopg = pytest.importorskip("psycopg")
Jsonb = psycopg.types.json.Jsonb

MIGRATION = _find_one("migrations", "*_keep_run_reports_and_retire_sra_name_rules.sql")
ROLLBACK = _find_one("rollbacks", "*_keep_run_reports_and_retire_sra_name_rules_rollback.sql")
REGISTER_SIG = "public.register_rnaseq_sample(bigint, integer, bigint)"
RUNS = ["SRR28503597", "SRR28503598"]
OTHER_RUNS = ["SRR2000001"]


@pytest.fixture
def cur(pg_conn):
    """After this migration, with an empty queue."""
    with pg_conn.cursor() as c:
        _schema(c)
        c.execute(_sql_body(GENOME_MIGRATION))
        c.execute(_sql_body(MIGRATION))
        c.execute(f"DELETE FROM {QUEUE_TABLE}")
        yield c
    pg_conn.rollback()


@pytest.fixture
def version(cur):
    version_id, _, _, _ = genome.ready(cur, genome.new_genome_name(), genome.add_species(cur))
    return version_id


@pytest.fixture
def other_version(cur):
    version_id, _, _, _ = genome.ready(cur, genome.new_genome_name(), genome.add_species(cur))
    return version_id


def _start(cur, version_id, sample="root_tip", runs=None, user=USER, url=None, files=None):
    return _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => %s, p_reference => NULL, "
        "p_requested_by => %s, p_sra_runs => %s, p_fastq_url => %s, p_fastq_files => %s, "
        "p_genome_version_id => %s)",
        (sample, user, runs, url, None if files is None else Jsonb(files), version_id),
    )


def _import(cur, version_id, sample="root_tip", runs=RUNS, user=USER):
    return _start(cur, version_id, sample=sample, runs=runs, user=user)


def _folder(cur, version_id, user=USER):
    # FILES are named for col0.
    return _start(cur, version_id, sample="col0", user=user, url=URL, files=FILES)


def _status(cur, run_id, status):
    cur.execute(f"UPDATE {TABLE} SET status = %s WHERE id = %s", (status, run_id))


def _function_exists(cur, sig):
    cur.execute("SELECT to_regprocedure(%s) IS NOT NULL", (sig,))
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# Run reports in the scrna bucket
# --------------------------------------------------------------------------- #


def _upload(cur, name):
    cur.execute(
        "INSERT INTO storage.buckets (id, name) VALUES ('scrna', 'scrna') ON CONFLICT (id) DO NOTHING"
    )
    cur.execute("SET LOCAL ROLE bloom_workflows")
    cur.execute(
        "INSERT INTO storage.objects (bucket_id, name) VALUES ('scrna', %s) RETURNING id", (name,)
    )
    oid = cur.fetchone()[0]
    cur.execute("RESET ROLE")
    return oid


@pytest.mark.parametrize(
    "name",
    ["reports/8/web_summary.html", "reports/scrna-cellranger-x/web_summary.html",
     "h5ad/abc.h5ad.gz", "counts/col0_7_/AT1G01010.json"],
)
def test_the_pipeline_may_write_reports_and_dataset_files(cur, name):
    assert _upload(cur, name)


@pytest.mark.parametrize("name", ["other/x.html", "web_summary.html", "reportsx/8/a.html"])
def test_the_pipeline_may_not_write_elsewhere_in_scrna(cur, name):
    cur.execute("SAVEPOINT refused")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _upload(cur, name)
    cur.execute("ROLLBACK TO SAVEPOINT refused")


def test_the_pipeline_may_replace_a_report(cur):
    oid = _upload(cur, "reports/8/web_summary.html")
    cur.execute("SET LOCAL ROLE bloom_workflows")
    cur.execute("UPDATE storage.objects SET version = 'second' WHERE id = %s", (oid,))
    assert cur.rowcount == 1
    cur.execute("RESET ROLE")


# --------------------------------------------------------------------------- #
# A source is required
# --------------------------------------------------------------------------- #


def test_a_sample_name_alone_is_refused_and_nothing_is_queued(cur, version):
    _refused(
        cur, _start, version, error=psycopg.errors.InvalidParameterValue,
        match="give an S3 folder or SRA run IDs to read root_tip from",
    )
    assert _count(cur, TABLE) == 0
    assert _count(cur, QUEUE_TABLE) == 0


def test_a_registered_sample_alone_is_refused_too(cur, version):
    cur.execute(f"INSERT INTO {SAMPLES} (name, source_ref) VALUES ('root_tip', %s)",
                (",".join(RUNS),))
    _refused(cur, _start, version, error=psycopg.errors.InvalidParameterValue,
             match="give an S3 folder or SRA run IDs")


@pytest.mark.parametrize("runs", [[], ["SRR1"], ["SRR28503597", "SRR28503597"]])
def test_bad_run_ids_are_still_refused(cur, version, runs):
    _refused(cur, _import, version, runs=runs, error=psycopg.errors.InvalidParameterValue,
             match="distinct SRA run IDs")


def test_an_import_stores_its_runs_and_queues_once(cur, version):
    run_id = _import(cur, version)
    assert _params(cur, run_id)["sra_runs"] == RUNS
    assert _count(cur, QUEUE_TABLE) == 1


# --------------------------------------------------------------------------- #
# No name lock, no registered names
# --------------------------------------------------------------------------- #


def test_two_scientists_may_import_under_the_same_name(cur, version):
    first = _import(cur, version)
    second = _import(cur, version, runs=OTHER_RUNS, user=OTHER_USER)
    assert first != second
    assert _count(cur, QUEUE_TABLE) == 2


def test_a_name_registered_for_other_reads_may_be_imported(cur, version):
    cur.execute(f"INSERT INTO {SAMPLES} (name, source_ref) VALUES ('root_tip', 'SRR9999999')")
    assert _import(cur, version)


def test_a_request_takes_no_lock_on_the_name(cur, version, pg_conninfo):
    _import(cur, version)
    with psycopg.connect(pg_conninfo) as other, other.cursor() as c:
        c.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
                  ("rnaseq_sra_import:root_tip",))
        assert c.fetchone()[0] is True
        other.rollback()


def test_a_request_holds_its_run_keys_lock_until_it_commits(cur, version, pg_conninfo):
    run_id = _import(cur, version)
    cur.execute(f"SELECT run_key FROM {TABLE} WHERE id = %s", (run_id,))
    (key,) = cur.fetchone()
    with psycopg.connect(pg_conninfo) as other, other.cursor() as c:
        c.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
                  ("rnaseq_run_key:" + key,))
        assert c.fetchone()[0] is False
        other.rollback()


# --------------------------------------------------------------------------- #
# One live run per folder
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", ["queued", "submitted", "running", "succeeded"])
def test_other_reads_under_the_same_name_and_genome_are_refused(cur, version, status):
    _status(cur, _import(cur, version), status)
    _refused(cur, _import, version, runs=OTHER_RUNS, error=psycopg.errors.UniqueViolation,
             match="already has run")
    assert _count(cur, QUEUE_TABLE) == 1


def test_other_reads_may_reuse_the_name_once_its_run_failed(cur, version):
    _status(cur, _import(cur, version), "failed")
    assert _import(cur, version, runs=OTHER_RUNS)


def test_other_reads_may_reuse_the_name_against_another_genome(cur, version, other_version):
    _import(cur, version)
    assert _import(cur, other_version, runs=OTHER_RUNS)


def test_an_import_is_refused_on_a_folder_runs_key(cur, version):
    _folder(cur, version)
    _refused(cur, _import, version, sample="col0", error=psycopg.errors.UniqueViolation,
             match="already has run")


def test_a_folder_run_is_refused_on_an_imports_key(cur, version):
    _import(cur, version, sample="col0")
    _refused(cur, _folder, version, error=psycopg.errors.UniqueViolation,
             match="already been processed")


# --------------------------------------------------------------------------- #
# The same reads under another name
# --------------------------------------------------------------------------- #


def test_the_same_reads_under_another_name_are_refused(cur, version):
    first = _import(cur, version)
    _refused(cur, _import, version, sample="root_tip_again", runs=list(reversed(RUNS)),
             error=psycopg.errors.UniqueViolation,
             match=f"already run as root_tip \\(run {first}\\)")


def test_the_same_reads_may_run_again_once_their_run_failed(cur, version):
    _status(cur, _import(cur, version), "failed")
    assert _import(cur, version, sample="root_tip_again")


def test_another_scientist_may_run_the_same_reads(cur, version):
    _import(cur, version)
    assert _import(cur, version, user=OTHER_USER)


# --------------------------------------------------------------------------- #
# Signature, access, re-run and rollback
# --------------------------------------------------------------------------- #


def test_the_signature_is_unchanged_and_registration_is_gone(cur):
    assert _signatures(cur, "request_scrna_cellranger_run") == [_bare(SIG)]
    assert not _function_exists(cur, REGISTER_SIG)


@pytest.mark.parametrize(
    "role, allowed",
    [("bloom_workflows", True), ("service_role", False), ("authenticated", False),
     ("anon", False), ("bloom_user", False)],
)
def test_only_bloom_workflows_may_start_a_run(cur, role, allowed):
    assert _can_execute(cur, role, SIG) is allowed


def test_the_migration_runs_twice(cur, version):
    cur.execute(_sql_body(MIGRATION))
    assert _import(cur, version)
    _refused(cur, _start, version, error=psycopg.errors.InvalidParameterValue)


def test_the_rollback_puts_back_the_earlier_rules(cur, version):
    cur.execute(_sql_body(ROLLBACK))
    cur.execute(f"INSERT INTO {SAMPLES} (name, source_ref) VALUES ('registered', 'SRR9999999')")
    assert _start(cur, version, sample="registered")
    _import(cur, version)
    _refused(cur, _import, version, runs=OTHER_RUNS, user=OTHER_USER,
             error=psycopg.errors.UniqueViolation, match="still being imported")
    assert _function_exists(cur, REGISTER_SIG)
    assert _can_execute(cur, "bloom_workflows", REGISTER_SIG) is True
    assert _can_execute(cur, "service_role", SIG) is False
    cur.execute("SAVEPOINT refused")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _upload(cur, "reports/8/web_summary.html")
    cur.execute("ROLLBACK TO SAVEPOINT refused")


def test_the_rollback_keeps_runs_started_under_the_new_rules(cur, version):
    _import(cur, version)
    _import(cur, version, runs=OTHER_RUNS, user=OTHER_USER)
    cur.execute(_sql_body(ROLLBACK))
    assert _count(cur, TABLE) == 2
