"""
Integration tests for importing a Cell Ranger run's sample from SRA:
- params may carry 1 to 9 run accessions, and nothing else is added;
- request_scrna_cellranger_run takes them, and refuses bad ones, a registered name and a
  name already being imported;
- the new steps can be reported;
- register_rnaseq_sample registers the run's own sample once;
- only bloom_workflows may call either function;
- the rollback restores the earlier checks and signature.

Each test builds the schema up to this migration inside its own transaction and rolls it
back, so the database is left unchanged.
"""

import pytest

from tests.integration.test_rnaseq_runs import MIGRATION as RNASEQ_RUNS_MIGRATION
from tests.integration.test_rnaseq_runs import (
    OTHER_USER,
    USER,
    _find_one,
    _sql_body,
    _to_cellranger_schema,
)

psycopg = pytest.importorskip("psycopg")
Jsonb = psycopg.types.json.Jsonb

TABLE = "rnaseq_runs"
SAMPLES = "rnaseq_samples"
QUEUE_TABLE = "pgmq.q_rnaseq_dispatch"
OLD_SIG = "public.request_scrna_cellranger_run(text, text, uuid, jsonb)"
NEW_SIG = "public.request_scrna_cellranger_run(text, text, uuid, jsonb, text[])"
REGISTER_SIG = "public.register_rnaseq_sample(bigint, integer, bigint)"
STATUS_SIG = "public.update_rnaseq_run_status(bigint, text, text, jsonb, integer, text)"

EARLIER = [
    RNASEQ_RUNS_MIGRATION,
    _find_one("migrations", "*_limit_cellranger_sample_names.sql"),
    _find_one("migrations", "*_add_rnaseq_run_status_function.sql"),
    _find_one("migrations", "*_create_rnaseq_samples_and_references.sql"),
    _find_one("migrations", "*_add_rnaseq_run_metadata.sql"),
]
MIGRATION = _find_one("migrations", "*_add_sra_import_to_rnaseq_runs.sql")
ROLLBACK = _find_one("rollbacks", "*_add_sra_import_to_rnaseq_runs_rollback.sql")
RUNS = ["SRR28503597", "SRR28503598"]
NEW_STEPS = ["fetch-sra", "preprocess", "cluster", "build-h5ad"]


def _build(c):
    _to_cellranger_schema(c)
    c.execute(f"DROP FUNCTION IF EXISTS {STATUS_SIG}")
    c.execute(f"DROP TABLE IF EXISTS public.{SAMPLES} CASCADE")
    c.execute("DROP TABLE IF EXISTS public.rnaseq_references CASCADE")
    for path in EARLIER:
        c.execute(_sql_body(path))


@pytest.fixture
def old(pg_conn):
    """Before this migration."""
    with pg_conn.cursor() as c:
        _build(c)
        c.execute(f"DELETE FROM {QUEUE_TABLE}")
        yield c
    pg_conn.rollback()


@pytest.fixture
def cur(pg_conn):
    """After this migration, with an empty queue."""
    with pg_conn.cursor() as c:
        _build(c)
        c.execute(_sql_body(MIGRATION))
        c.execute(f"DELETE FROM {QUEUE_TABLE}")
        yield c
    pg_conn.rollback()


def _as_workflows(cur, sql, params=()):
    """On an error the role stays set until the caller rolls back to its savepoint."""
    cur.execute("SET LOCAL ROLE bloom_workflows")
    cur.execute(sql, params)
    value = cur.fetchone()[0]
    cur.execute("RESET ROLE")
    return value


def _request(cur, sample="shahan_sc_1", runs=RUNS, user=USER, reference="tiny_ref"):
    return _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => %s, p_reference => %s, "
        "p_requested_by => %s, p_sra_runs => %s)",
        (sample, reference, user, runs),
    )


def _register(cur, run_id, fastq_count=6, total_bytes=11_902_105_823):
    return _as_workflows(
        cur,
        "SELECT register_rnaseq_sample(p_run_id => %s, p_fastq_count => %s, p_total_bytes => %s)",
        (run_id, fastq_count, total_bytes),
    )


def _refused(cur, fn, *args, error, match=None, **kwargs):
    """fn fails with `error` and changes nothing."""
    cur.execute("SAVEPOINT refused")
    try:
        with pytest.raises(error, match=match):
            fn(cur, *args, **kwargs)
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT refused")


def _params(cur, run_id):
    cur.execute(f"SELECT params FROM {TABLE} WHERE id = %s", (run_id,))
    return cur.fetchone()[0]


def _count(cur, table):
    cur.execute(f"SELECT count(*) FROM {table}")
    return cur.fetchone()[0]


def _insert_run(cur, params, user=USER):
    """A run written directly, as an admin could, so the table check alone decides."""
    key = f"{params['sample']}__{params['reference']}__{user}"
    cur.execute(
        f"INSERT INTO {TABLE} (workflow_type, params, run_key, requested_by) "
        "VALUES ('scrna-cellranger', %s, %s, %s)",
        (Jsonb(params), key, user),
    )


def _can_execute(cur, role, sig):
    cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, sig))
    return cur.fetchone()[0]


def _bare(sig):
    """A signature as pg_proc's regprocedure text writes it: no schema, no spaces."""
    return sig.removeprefix("public.").replace(", ", ",")


def _signatures(cur, name):
    cur.execute(
        "SELECT oid::regprocedure::text FROM pg_proc WHERE proname = %s ORDER BY 1", (name,)
    )
    return [r[0] for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


def test_an_import_stores_its_runs_in_params_in_the_order_given(cur):
    run_id = _request(cur, runs=["SRR28503598", "SRR28503597"])
    assert _params(cur, run_id) == {
        "sample": "shahan_sc_1",
        "reference": "tiny_ref",
        "sra_runs": ["SRR28503598", "SRR28503597"],
    }


def test_an_import_is_queued_once(cur):
    run_id = _request(cur)
    cur.execute(f"SELECT message->>'run_id' FROM {QUEUE_TABLE}")
    assert [r[0] for r in cur.fetchall()] == [str(run_id)]


def test_a_run_without_accessions_is_unchanged(cur):
    run_id = _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => 'tinygex', p_reference => 'tiny_ref', "
        "p_requested_by => %s, p_metadata => %s)",
        (USER, Jsonb({"dataset_name": "Col-0 root tip"})),
    )
    assert _params(cur, run_id) == {"sample": "tinygex", "reference": "tiny_ref"}


def test_err_and_drr_accessions_and_nine_runs_are_accepted(cur):
    nine = [f"SRR100000{i}" for i in range(1, 10)]
    assert _params(cur, _request(cur, runs=nine))["sra_runs"] == nine
    assert _request(cur, sample="ena_1", runs=["ERR1000001", "DRR1000002"])


@pytest.mark.parametrize(
    "runs",
    [
        [],
        [f"SRR100000{i}" for i in range(10)],
        ["SRR1000001", "SRR1000001"],
        ["GSE152766"],
        ["SRR12"],
        ["srr1000001"],
        ["SRR1000001 "],
        [None],
        [["SRR1000001"]],
    ],
    ids=["empty", "ten", "twice", "study", "too-short", "lowercase", "padded", "null", "nested"],
)
def test_bad_accessions_are_refused_and_nothing_is_queued(cur, runs):
    _refused(cur, _request, runs=runs, error=psycopg.errors.Error)
    assert _count(cur, TABLE) == 0
    assert _count(cur, QUEUE_TABLE) == 0


def test_a_registered_name_is_refused(cur):
    cur.execute(f"INSERT INTO {SAMPLES} (name) VALUES ('shahan_sc_1')")
    _refused(
        cur, _request, error=psycopg.errors.UniqueViolation, match="already registered"
    )
    assert _count(cur, TABLE) == 0


def test_a_name_already_being_imported_is_refused_even_for_another_user(cur):
    _request(cur)
    _refused(
        cur,
        _request,
        user=OTHER_USER,
        runs=["SRR2000001"],
        error=psycopg.errors.UniqueViolation,
        match="already being imported",
    )


@pytest.mark.parametrize("status", ["succeeded", "failed", "skipped"])
def test_a_finished_import_no_longer_holds_the_name(cur, status):
    run_id = _request(cur)
    cur.execute(f"UPDATE {TABLE} SET status = %s WHERE id = %s", (status, run_id))
    assert _request(cur, user=OTHER_USER, runs=["SRR2000001"])


def test_a_registered_sample_can_still_be_counted_without_importing(cur):
    cur.execute(f"INSERT INTO {SAMPLES} (name) VALUES ('shahan_sc_1')")
    run_id = _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => 'shahan_sc_1', "
        "p_reference => 'tiny_ref', p_requested_by => %s)",
        (USER,),
    )
    assert "sra_runs" not in _params(cur, run_id)


# --------------------------------------------------------------------------- #
# The table check
# --------------------------------------------------------------------------- #


def test_the_table_accepts_valid_accessions_written_directly(cur):
    _insert_run(cur, {"sample": "s", "reference": "tiny_ref", "sra_runs": RUNS})
    assert _count(cur, TABLE) == 1


@pytest.mark.parametrize(
    "sra_runs",
    ["SRR1000001", [], [f"SRR100000{i}" for i in range(10)], ["GSE1"], [1], {"a": "SRR1000001"}],
    ids=["string", "empty", "ten", "study", "number", "object"],
)
def test_the_table_refuses_bad_accessions_written_directly(cur, sra_runs):
    cur.execute("SAVEPOINT bad")
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_run(cur, {"sample": "s", "reference": "tiny_ref", "sra_runs": sra_runs})
    cur.execute("ROLLBACK TO SAVEPOINT bad")


def test_the_table_still_refuses_any_other_key(cur):
    cur.execute("SAVEPOINT bad")
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_run(cur, {"sample": "s", "reference": "tiny_ref", "igc_url": "x"})
    cur.execute("ROLLBACK TO SAVEPOINT bad")


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("step", NEW_STEPS)
def test_the_new_steps_can_be_reported(cur, step):
    run_id = _request(cur)
    cur.execute(f"UPDATE {TABLE} SET status = 'submitted' WHERE id = %s", (run_id,))
    assert _as_workflows(
        cur,
        "SELECT update_rnaseq_run_status(%s, 'running', %s, NULL, NULL, NULL)",
        (run_id, step),
    )
    cur.execute(f"SELECT current_step FROM {TABLE} WHERE id = %s", (run_id,))
    assert cur.fetchone()[0] == step


def test_an_unknown_step_is_still_refused(cur):
    run_id = _request(cur)
    cur.execute("SAVEPOINT bad")
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute(f"UPDATE {TABLE} SET current_step = 'annotate' WHERE id = %s", (run_id,))
    cur.execute("ROLLBACK TO SAVEPOINT bad")


# --------------------------------------------------------------------------- #
# Registering the sample
# --------------------------------------------------------------------------- #


def test_the_sample_is_registered_from_the_runs_own_details(cur):
    run_id = _request(cur, user=OTHER_USER)
    sample_id = _register(cur, run_id)
    cur.execute(
        f"SELECT id, name, source, source_ref, fastq_count, total_bytes, registered_by::text "
        f"FROM {SAMPLES} WHERE name = 'shahan_sc_1'"
    )
    assert cur.fetchone() == (
        sample_id,
        "shahan_sc_1",
        "sra",
        "SRR28503597,SRR28503598",
        6,
        11_902_105_823,
        OTHER_USER,
    )


def test_registering_twice_registers_once(cur):
    run_id = _request(cur)
    first = _register(cur, run_id)
    assert _register(cur, run_id, fastq_count=7) == first
    assert _count(cur, SAMPLES) == 1


def test_a_run_that_doesnt_import_cant_register(cur):
    run_id = _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(p_sample => 'tinygex', p_reference => 'tiny_ref', "
        "p_requested_by => %s)",
        (USER,),
    )
    _refused(cur, _register, run_id, error=psycopg.errors.InvalidParameterValue)
    assert _count(cur, SAMPLES) == 0


def test_an_unknown_run_cant_register(cur):
    _refused(cur, _register, 999_999, error=psycopg.errors.NoDataFound)


@pytest.mark.parametrize(
    "fastq_count, total_bytes", [(0, 100), (3, 0), (None, 100), (3, None), (-1, 100)]
)
def test_counts_must_be_positive(cur, fastq_count, total_bytes):
    run_id = _request(cur)
    _refused(
        cur,
        _register,
        run_id,
        fastq_count=fastq_count,
        total_bytes=total_bytes,
        error=psycopg.errors.InvalidParameterValue,
    )


def test_a_name_registered_meanwhile_from_elsewhere_is_not_taken_over(cur):
    run_id = _request(cur)
    cur.execute(f"INSERT INTO {SAMPLES} (name, source) VALUES ('shahan_sc_1', 's3')")
    _refused(cur, _register, run_id, error=psycopg.errors.UniqueViolation)
    cur.execute(f"SELECT source FROM {SAMPLES} WHERE name = 'shahan_sc_1'")
    assert cur.fetchone()[0] == "s3"


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("sig", [NEW_SIG, REGISTER_SIG])
def test_only_bloom_workflows_may_call_the_functions(cur, sig):
    assert _can_execute(cur, "bloom_workflows", sig)
    for role in ("anon", "authenticated", "bloom_user", "bloom_writer", "bloom_agent", "public"):
        assert not _can_execute(cur, role, sig), role


def test_the_old_signature_is_gone(cur):
    assert _signatures(cur, "request_scrna_cellranger_run") == [_bare(NEW_SIG)]


# --------------------------------------------------------------------------- #
# Rollback
# --------------------------------------------------------------------------- #


def test_the_rollback_restores_the_earlier_checks_and_signature(cur):
    cur.execute(_sql_body(ROLLBACK))
    assert _signatures(cur, "request_scrna_cellranger_run") == [_bare(OLD_SIG)]
    assert _signatures(cur, "register_rnaseq_sample") == []
    cur.execute("SAVEPOINT bad")
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_run(cur, {"sample": "s", "reference": "tiny_ref", "sra_runs": RUNS})
    cur.execute("ROLLBACK TO SAVEPOINT bad")


def test_the_rollback_stops_when_a_run_imports_from_sra(cur):
    _request(cur)
    cur.execute("SAVEPOINT bad")
    with pytest.raises(psycopg.errors.RaiseException, match="import from SRA"):
        cur.execute(_sql_body(ROLLBACK))
    cur.execute("ROLLBACK TO SAVEPOINT bad")


def test_the_migration_keeps_existing_runs(old):
    old.execute(
        "SELECT request_scrna_cellranger_run(p_sample => 'tinygex', p_reference => 'tiny_ref', "
        "p_requested_by => %s, p_metadata => NULL)",
        (USER,),
    )
    old.execute(_sql_body(MIGRATION))
    assert _count(old, TABLE) == 1
