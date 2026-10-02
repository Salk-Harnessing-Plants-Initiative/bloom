"""
Integration tests for reading a Cell Ranger run's FASTQs from an S3 folder:
- params may carry the folder and the files found in it, and only with each other;
- request_scrna_cellranger_run takes them, and refuses a bad folder, bad files, a lane
  without its R1 or R2, and a folder together with SRA run IDs;
- a folder run is refused when its run key already has a run that hasn't failed, since the
  key is also its output folder;
- SRA imports and registered samples still start as before;
- only bloom_workflows may call the function;
- the rollback restores the earlier check and signature.

Each test builds the schema up to this migration inside its own transaction and rolls it
back, so the database is left unchanged.
"""

import pytest

from tests.integration.test_rnaseq_runs import OTHER_USER, USER, _find_one, _sql_body
from tests.integration.test_rnaseq_sra_import import MIGRATION as SRA_MIGRATION
from tests.integration.test_rnaseq_sra_import import (
    QUEUE_TABLE,
    TABLE,
    _as_workflows,
    _bare,
    _mark,
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


def _request(cur, sample="col0", url=URL, files=FILES, runs=None, reference="tiny_ref", user=USER):
    return _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => %s, p_reference => %s, "
        "p_requested_by => %s, p_sra_runs => %s, p_fastq_url => %s, p_fastq_files => %s)",
        (sample, reference, user, runs, url, None if files is None else Jsonb(files)),
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
             match="needs an R1 and an R2")


def test_each_s_number_needs_its_own_r1_and_r2(cur):
    files = [_file("col0_S1_L001_R1_001.fastq.gz"), _file("col0_S2_L001_R2_001.fastq.gz")]
    _refused(cur, _request, files=files, error=psycopg.errors.InvalidParameterValue,
             match="every S number")


def test_two_complete_s_numbers_are_accepted(cur):
    files = [_file(f"col0_S{n}_L001_{r}_001.fastq.gz") for n in (1, 2) for r in ("R1", "R2")]
    assert _request(cur, files=files) > 0


def test_a_read_both_plain_and_gzipped_is_refused(cur):
    files = [_file("col0_S1_L001_R1_001.fastq.gz"), _file("col0_S1_L001_R1_001.fastq"),
             _file("col0_S1_L001_R2_001.fastq.gz")]
    _refused(cur, _request, files=files, error=psycopg.errors.InvalidParameterValue,
             match=r"as \.fastq and \.fastq\.gz")


def test_a_file_listed_twice_is_refused(cur):
    _refused(cur, _request, files=[FILES[0], FILES[0], FILES[1]],
             error=psycopg.errors.InvalidParameterValue, match="listed twice")


def test_more_than_96_files_are_refused(cur):
    files = [_file(f"col0_S1_L{lane:03d}_{read}_001.fastq.gz")
             for lane in range(1, 50) for read in ("R1", "R2")]
    _refused(cur, _request, files=files, error=psycopg.errors.InvalidParameterValue,
             match="2 to 96")


def test_a_folder_run_on_the_key_of_an_import_is_refused(cur):
    """The import's results would land in the same output folder."""
    imported = _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => 'col0', p_reference => 'tiny_ref', "
        "p_requested_by => %s, p_sra_runs => %s)",
        (USER, ["SRR28503597"]),
    )
    _refused(cur, _request, error=psycopg.errors.UniqueViolation, match=f"run {imported}")


@pytest.mark.parametrize("status", ["queued", "submitted", "running", "succeeded", "skipped"])
def test_a_folder_run_on_a_key_already_processed_is_refused(cur, status):
    first = _request(cur)
    _mark(cur, first, status)
    _refused(
        cur, _request, url="s3://lab-data/run43/",
        error=psycopg.errors.UniqueViolation,
        match=rf"col0 against tiny_ref has already been processed \(run {first}\)\. To process "
              r"these reads as a new sample, rename the FASTQs",
    )
    assert _count(cur, TABLE) == 1


def test_the_same_folder_again_is_refused_too(cur):
    _request(cur)
    _refused(cur, _request, error=psycopg.errors.UniqueViolation, match="already been processed")


def test_a_folder_run_can_follow_a_failed_one(cur):
    first = _request(cur)
    _mark(cur, first, "failed")
    assert _request(cur) > first


def test_a_folder_run_is_refused_on_a_registered_samples_key(cur):
    registered = _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => 'col0', p_reference => 'tiny_ref', "
        "p_requested_by => %s)",
        (USER,),
    )
    _mark(cur, registered, "succeeded")
    _refused(cur, _request, error=psycopg.errors.UniqueViolation, match=f"run {registered}")


def _sra(cur, sample="col0", reference="tiny_ref"):
    return _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => %s, p_reference => %s, "
        "p_requested_by => %s, p_sra_runs => %s)",
        (sample, reference, USER, ["SRR28503597"]),
    )


def _registered(cur, sample="col0", reference="tiny_ref"):
    return _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => %s, p_reference => %s, "
        "p_requested_by => %s)",
        (sample, reference, USER),
    )


@pytest.mark.parametrize("start", [_sra, _registered])
def test_an_sra_or_registered_run_on_a_folder_runs_key_is_refused(cur, start):
    folder = _request(cur)
    _mark(cur, folder, "succeeded")
    _refused(cur, start, error=psycopg.errors.UniqueViolation,
             match=rf"col0 against tiny_ref already has run {folder} from an S3 folder")
    assert _count(cur, TABLE) == 1


@pytest.mark.parametrize("start", [_sra, _registered])
def test_an_sra_or_registered_run_can_follow_a_failed_folder_run(cur, start):
    _mark(cur, _request(cur), "failed")
    assert start(cur) > 0


def test_an_sra_import_on_another_reference_is_another_key(cur):
    _mark(cur, _request(cur), "succeeded")
    assert _sra(cur, reference="other_ref") > 0


def test_a_registered_sample_can_still_be_run_again(cur):
    """Same name, same reads: the second run is skipped by the pipeline, as before."""
    _mark(cur, _registered(cur), "succeeded")
    assert _registered(cur) > 0


def test_the_run_key_lock_is_held_until_the_request_commits(cur, pg_conninfo):
    """A folder run and any other run on one key can't both pass the checks at once."""
    _request(cur)
    key = f"rnaseq_run_key:col0__tiny_ref__{USER}"
    with psycopg.connect(pg_conninfo) as other, other.cursor() as c:
        c.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
        assert c.fetchone()[0] is False
        c.execute(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"rnaseq_run_key:col1__tiny_ref__{USER}",),
        )
        assert c.fetchone()[0] is True


def _sra_ids(cur, runs, sample="col0", reference="tiny_ref", user=USER):
    return _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => %s, p_reference => %s, "
        "p_requested_by => %s, p_sra_runs => %s)",
        (sample, reference, user, runs),
    )


def _register(cur, run_id, name, runs):
    """What the poller does once an import's download succeeds."""
    cur.execute(
        "INSERT INTO rnaseq_samples (name, source, source_ref, fastq_count, total_bytes, registered_by) "
        "VALUES (%s, 'sra', %s, 2, 10, %s)",
        (name, ",".join(runs), USER),
    )


SRR = ["SRR28503597", "SRR28503598"]


def test_the_same_sra_reads_against_the_same_reference_are_refused(cur):
    first = _sra_ids(cur, SRR)
    _mark(cur, first, "succeeded")
    _register(cur, first, "col0", SRR)
    _refused(
        cur, _sra_ids, list(reversed(SRR)), error=psycopg.errors.UniqueViolation,
        match=rf"SRR28503598, SRR28503597 against tiny_ref was already run as col0 \(run {first}\)",
    )


def test_the_same_sra_reads_under_another_name_are_refused(cur):
    first = _sra_ids(cur, SRR)
    _mark(cur, first, "succeeded")
    _register(cur, first, "col0", SRR)
    _refused(cur, _sra_ids, SRR, sample="col0_again", error=psycopg.errors.UniqueViolation,
             match=f"already run as col0 \\(run {first}\\)")


def test_the_same_sra_reads_against_another_reference_are_a_new_run(cur):
    first = _sra_ids(cur, SRR)
    _mark(cur, first, "succeeded")
    _register(cur, first, "col0", SRR)
    second = _sra_ids(cur, SRR, reference="other_ref")
    assert _params(cur, second) == {"sample": "col0", "reference": "other_ref", "sra_runs": SRR}


def test_a_failed_sra_run_can_be_run_again(cur):
    first = _sra_ids(cur, SRR)
    _mark(cur, first, "failed")
    assert _sra_ids(cur, SRR) > first


def test_a_registered_name_with_other_sra_reads_is_refused(cur):
    first = _sra_ids(cur, SRR)
    _mark(cur, first, "succeeded")
    _register(cur, first, "col0", SRR)
    _refused(cur, _sra_ids, ["SRR99999999"], reference="other_ref",
             error=psycopg.errors.UniqueViolation,
             match="sample col0 is already used for SRR28503597,SRR28503598; choose another name")


def test_another_scientist_can_run_the_same_sra_reads(cur):
    _mark(cur, _sra_ids(cur, SRR), "succeeded")
    assert _sra_ids(cur, SRR, sample="col0_b", user=OTHER_USER) > 0


def test_another_reference_or_scientist_is_another_key(cur):
    _request(cur)
    assert _request(cur, reference="other_ref") > 0
    assert _request(cur, user=OTHER_USER) > 0


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


def test_the_table_accepts_the_limits(cur):
    """96 files, an ETag of 200 characters, a size of 0 and a URL of 1,024 characters."""
    files = [_file(f"col0_S1_L{n:03d}_{r}_001.fastq.gz", size=0, etag="e" * 200)
             for n in range(1, 49) for r in ("R1", "R2")]
    url = "s3://lab-data/" + "a" * (1024 - len("s3://lab-data/") - 1) + "/"
    assert len(files) == 96 and len(url) == 1024
    _insert_run(cur, _folder_params(fastq_url=url, fastq_files=files))
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
    # A file without its size or ETag, an empty object, or one that isn't an object at all.
    _folder_params(fastq_files=[{"name": "col0_S1_L001_R1_001.fastq.gz"},
                                {"name": "col0_S1_L001_R2_001.fastq.gz"}]),
    _folder_params(fastq_files=[{}, {}]),
    _folder_params(fastq_files=[1, 2]),
    _folder_params(fastq_files=[{**FILES[0], "key": "x"}, FILES[1]]),
    _folder_params(fastq_files=[_file("col0_S1_L001_R1_001.fastq.gz", size=1.5), FILES[1]]),
    _folder_params(fastq_files=[_file("col0_S1_L001_R1_001.fastq.gz", etag=""), FILES[1]]),
    _folder_params(fastq_files=[_file("col0_S1_L001_R1_001.fastq.gz", etag="e" * 201), FILES[1]]),
    _folder_params(fastq_url="s3://lab-data/" + "a" * 1020 + "/"),
    _folder_params(fastq_url="s3://Lab-Data/run42/"),
    _folder_params(fastq_url=7),
    _folder_params(fastq_files=[_file(f"col0_S1_L{n:03d}_{r}_001.fastq.gz")
                                for n in range(1, 50) for r in ("R1", "R2")]),
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
