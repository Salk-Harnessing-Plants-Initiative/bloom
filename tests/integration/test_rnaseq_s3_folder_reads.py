"""
Integration tests for reading a Cell Ranger run's FASTQs from an S3 folder:
- params may carry the folder and the files found in it, and only with each other;
- request_scrna_cellranger_run takes them, and refuses a bad folder, bad files, a lane
  without its R1 or R2, and a folder together with SRA run IDs;
- SRA imports and registered samples still start as before;
- only bloom_workflows may call the function;
- the rollback restores the earlier check and signature.

Each test builds the schema up to this migration inside its own transaction and rolls it
back, so the database is left unchanged.
"""

import pytest

from tests.integration.test_rnaseq_runs import USER, _find_one, _sql_body
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

OLD_SIG = "public.request_scrna_cellranger_run(text, text, uuid, jsonb, text[])"
NEW_SIG = "public.request_scrna_cellranger_run(text, text, uuid, jsonb, text[], text, jsonb)"
MIGRATION = _find_one("migrations", "*_add_s3_folder_reads_to_rnaseq_runs.sql")
ROLLBACK = _find_one("rollbacks", "*_add_s3_folder_reads_to_rnaseq_runs_rollback.sql")
URL = "s3://lab-data/run42/"
FILES = [
    {"name": "col0_S1_L001_R1_001.fastq.gz", "size": 1_204_331_118, "etag": '"9b2cf535f27731c974343645a3985328"'},
    {"name": "col0_S1_L001_R2_001.fastq.gz", "size": 3_918_440_211, "etag": '"e1f0d1b2c3a4-12"'},
]


@pytest.fixture
def old(pg_conn):
    """Before this migration."""
    with pg_conn.cursor() as c:
        _build(c)
        c.execute(_sql_body(SRA_MIGRATION))
        c.execute(f"DELETE FROM {QUEUE_TABLE}")
        yield c
    pg_conn.rollback()


@pytest.fixture
def cur(pg_conn):
    """After this migration, with an empty queue."""
    with pg_conn.cursor() as c:
        _build(c)
        c.execute(_sql_body(SRA_MIGRATION))
        c.execute(_sql_body(MIGRATION))
        c.execute(f"DELETE FROM {QUEUE_TABLE}")
        yield c
    pg_conn.rollback()


def _request(cur, sample="col0", url=URL, files=FILES, runs=None, reference="tiny_ref"):
    return _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => %s, p_reference => %s, "
        "p_requested_by => %s, p_sra_runs => %s, p_fastq_url => %s, p_fastq_files => %s)",
        (sample, reference, USER, runs, url, None if files is None else Jsonb(files)),
    )


def _insert_run(cur, params):
    """A run written directly, as an admin could, so the table check alone decides."""
    key = f"{params['sample']}__{params['reference']}__{USER}"
    cur.execute(
        f"INSERT INTO {TABLE} (workflow_type, params, run_key, requested_by) "
        "VALUES ('scrna-cellranger', %s, %s, %s)",
        (Jsonb(params), key, USER),
    )


def _file(name, size=10, etag='"abc"'):
    return {"name": name, "size": size, "etag": etag}


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


def test_a_folder_run_stores_its_folder_and_files(cur):
    run_id = _request(cur)
    assert _params(cur, run_id) == {
        "sample": "col0",
        "reference": "tiny_ref",
        "fastq_url": URL,
        "fastq_files": FILES,
    }
    assert _count(cur, QUEUE_TABLE) == 1


def test_files_are_stored_in_name_order(cur):
    run_id = _request(cur, files=list(reversed(FILES)))
    assert [f["name"] for f in _params(cur, run_id)["fastq_files"]] == [f["name"] for f in FILES]


def test_several_lanes_and_index_reads_are_accepted(cur):
    files = [
        _file(f"col0_S1_L00{lane}_{read}_001.fastq.gz")
        for lane in (1, 2)
        for read in ("R1", "R2", "I1")
    ]
    run_id = _request(cur, files=files)
    assert len(_params(cur, run_id)["fastq_files"]) == 6


def test_reads_already_in_blooms_bucket_are_a_folder_like_any_other(cur):
    run_id = _request(cur, sample="shahan_sc_1", url="s3://bloomv2-workflows/raw_reads/shahan_sc_1/",
                      files=[_file("shahan_sc_1_S1_L001_R1_001.fastq.gz"),
                             _file("shahan_sc_1_S1_L001_R2_001.fastq.gz")])
    assert _params(cur, run_id)["fastq_url"] == "s3://bloomv2-workflows/raw_reads/shahan_sc_1/"


def test_a_folder_and_sra_runs_together_are_refused(cur):
    _refused(cur, _request, runs=["SRR28503597"], error=psycopg.errors.InvalidParameterValue,
             match="not both")
    assert _count(cur, QUEUE_TABLE) == 0


@pytest.mark.parametrize("url, files", [(URL, None), (None, FILES)])
def test_a_folder_needs_its_files_and_files_need_a_folder(cur, url, files):
    _refused(cur, _request, url=url, files=files, error=psycopg.errors.InvalidParameterValue,
             match="needs the files")


@pytest.mark.parametrize("url", [
    "s3://lab-data/",                 # no folder
    "s3://lab-data/run42",            # no trailing slash
    "https://lab-data.s3.amazonaws.com/run42/",
    "s3://Lab-Data/run42/",           # bucket names are lower case
    "s3://lab-data/run42/../other/",
    "s3://lab-data/run 42/",
    "s3://lab-data/" + "a" * 1020 + "/",
])
def test_a_bad_folder_is_refused(cur, url):
    _refused(cur, _request, url=url, error=psycopg.errors.InvalidParameterValue,
             match="looks like s3://bucket/folder/")


def test_a_nested_folder_is_accepted(cur):
    run_id = _request(cur, url="s3://lab-data/2026/run42/col0/")
    assert _params(cur, run_id)["fastq_url"] == "s3://lab-data/2026/run42/col0/"


@pytest.mark.parametrize("files", [
    [_file("reads_1.fastq.gz"), _file("reads_2.fastq.gz")],                               # not Illumina-named
    [_file("col1_S1_L001_R1_001.fastq.gz"), _file("col1_S1_L001_R2_001.fastq.gz")],       # another sample
    [_file("col0_S1_L001_R1_001.fastq.gz", size=-1), _file("col0_S1_L001_R2_001.fastq.gz")],
    [_file("col0_S1_L001_R1_001.fastq.gz", size=1.5), _file("col0_S1_L001_R2_001.fastq.gz")],
    [_file("col0_S1_L001_R1_001.fastq.gz", size="10"), _file("col0_S1_L001_R2_001.fastq.gz")],
    [_file("col0_S1_L001_R1_001.fastq.gz", etag=""), _file("col0_S1_L001_R2_001.fastq.gz")],
    [{"name": "col0_S1_L001_R1_001.fastq.gz", "size": 1}, _file("col0_S1_L001_R2_001.fastq.gz")],
    [{**_file("col0_S1_L001_R1_001.fastq.gz"), "key": "x"}, _file("col0_S1_L001_R2_001.fastq.gz")],
    [_file("col0_S1_L001_R1_001.fastq.gz")],                                              # one file
    "col0_S1_L001_R1_001.fastq.gz",
])
def test_bad_files_are_refused_and_nothing_is_queued(cur, files):
    _refused(cur, _request, files=files, error=psycopg.errors.InvalidParameterValue)
    assert _count(cur, QUEUE_TABLE) == 0


def test_a_lane_without_its_r2_is_refused(cur):
    files = [_file("col0_S1_L001_R1_001.fastq.gz"), _file("col0_S1_L001_R2_001.fastq.gz"),
             _file("col0_S1_L002_R1_001.fastq.gz")]
    _refused(cur, _request, files=files, error=psycopg.errors.InvalidParameterValue,
             match="R1 and an R2")


def test_a_file_listed_twice_is_refused(cur):
    _refused(cur, _request, files=[FILES[0], FILES[0], FILES[1]],
             error=psycopg.errors.InvalidParameterValue, match="listed twice")


def test_more_than_96_files_are_refused(cur):
    files = [_file(f"col0_S1_L{lane:03d}_{read}_001.fastq.gz")
             for lane in range(1, 50) for read in ("R1", "R2")]
    _refused(cur, _request, files=files, error=psycopg.errors.InvalidParameterValue,
             match="2 to 96")


def test_a_folder_run_doesnt_wait_for_an_import_of_the_same_name(cur):
    _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => 'col0', p_reference => 'tiny_ref', "
        "p_requested_by => %s, p_sra_runs => %s)",
        (USER, ["SRR28503597"]),
    )
    assert _request(cur) > 0


def test_sra_imports_and_registered_samples_start_as_before(cur):
    sra = _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => 'shahan_sc_1', p_reference => 'tiny_ref', "
        "p_requested_by => %s, p_sra_runs => %s)",
        (USER, ["SRR28503597"]),
    )
    registered = _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => 'tinygex', p_reference => 'tiny_ref', "
        "p_requested_by => %s)",
        (USER,),
    )
    assert _params(cur, sra) == {"sample": "shahan_sc_1", "reference": "tiny_ref",
                                 "sra_runs": ["SRR28503597"]}
    assert _params(cur, registered) == {"sample": "tinygex", "reference": "tiny_ref"}


# --------------------------------------------------------------------------- #
# The table check
# --------------------------------------------------------------------------- #


def _folder_params(**overrides):
    return {"sample": "col0", "reference": "tiny_ref", "fastq_url": URL, "fastq_files": FILES,
            **overrides}


def test_the_table_accepts_a_folder_written_directly(cur):
    _insert_run(cur, _folder_params())
    assert _count(cur, TABLE) == 1


@pytest.mark.parametrize("params", [
    {"sample": "col0", "reference": "tiny_ref", "fastq_url": URL},
    {"sample": "col0", "reference": "tiny_ref", "fastq_files": FILES},
    _folder_params(sra_runs=["SRR28503597"]),
    _folder_params(fastq_url="s3://lab-data/run42"),
    _folder_params(fastq_url="s3://lab-data/../x/"),
    _folder_params(fastq_files=[FILES[0]]),
    _folder_params(fastq_files=[_file("col1_S1_L001_R1_001.fastq.gz"),
                                _file("col1_S1_L001_R2_001.fastq.gz")]),
    _folder_params(fastq_files=[_file("col0_S1_L001_R1_001.fastq.gz", size=-1), FILES[1]]),
    _folder_params(fastq_files=[_file("col0_S1_L001_R1_001.fastq.gz", etag=7), FILES[1]]),
    _folder_params(fastq_files="nope"),
])
def test_the_table_refuses_a_bad_folder_written_directly(cur, params):
    cur.execute("SAVEPOINT bad")
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_run(cur, params)
    cur.execute("ROLLBACK TO SAVEPOINT bad")


def test_the_table_still_refuses_any_other_key(cur):
    cur.execute("SAVEPOINT bad")
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_run(cur, _folder_params(extra=1))
    cur.execute("ROLLBACK TO SAVEPOINT bad")


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #


def test_only_bloom_workflows_may_call_the_function(cur):
    assert _can_execute(cur, "bloom_workflows", NEW_SIG)
    for role in ("anon", "authenticated", "bloom_user", "bloom_writer", "bloom_agent", "public"):
        assert not _can_execute(cur, role, NEW_SIG), role


def test_the_old_signature_is_gone(cur):
    assert _signatures(cur, "request_scrna_cellranger_run") == [_bare(NEW_SIG)]


# --------------------------------------------------------------------------- #
# Rollback
# --------------------------------------------------------------------------- #


def test_the_rollback_restores_the_earlier_check_and_signature(cur):
    cur.execute(_sql_body(ROLLBACK))
    assert _signatures(cur, "request_scrna_cellranger_run") == [_bare(OLD_SIG)]
    assert not _can_execute(cur, "anon", OLD_SIG)
    cur.execute("SAVEPOINT bad")
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_run(cur, _folder_params())
    cur.execute("ROLLBACK TO SAVEPOINT bad")


def test_the_rollback_stops_when_a_run_reads_from_a_folder(cur):
    _request(cur)
    cur.execute("SAVEPOINT bad")
    with pytest.raises(psycopg.errors.RaiseException, match="S3 folder"):
        cur.execute(_sql_body(ROLLBACK))
    cur.execute("ROLLBACK TO SAVEPOINT bad")


def test_the_migration_keeps_existing_runs(old):
    old.execute(
        "SELECT request_scrna_cellranger_run(p_sample => 'tinygex', p_reference => 'tiny_ref', "
        "p_requested_by => %s, p_sra_runs => %s)",
        (USER, ["SRR28503597"]),
    )
    old.execute(_sql_body(MIGRATION))
    assert _count(old, TABLE) == 1
