"""
Integration tests for the Cell Ranger sample-name rule: sample names are letters, digits,
'_' and '-', at most 64 characters, in both the rnaseq_runs check and
request_scrna_cellranger_run, while reference names keep their rule.

Each test starts from the schema before this migration, applies it inside its own
transaction, and rolls it back, so the database is left unchanged.
"""

import pytest

from tests.integration.test_rnaseq_runs import (
    MIGRATION as RNASEQ_RUNS_MIGRATION,
)
from tests.integration.test_rnaseq_runs import (
    USER,
    _find_one,
    _sql_body,
    _to_cellranger_schema,
)

psycopg = pytest.importorskip("psycopg")

TABLE = "rnaseq_runs"
REQUEST_SIG = "public.request_scrna_cellranger_run(text, text, uuid)"
MIGRATION = _find_one("migrations", "*_limit_cellranger_sample_names.sql")
ROLLBACK = _find_one("rollbacks", "*_limit_cellranger_sample_names_rollback.sql")

GOOD_SAMPLES = ["tinygex", "Col0_root_rep1", "S1-rep1", "a", "x" * 64, "rep1-", "rep1_"]
BAD_SAMPLES = ["S1.rep1", "x" * 65, "-rep1", "_rep1", "a__b", "has space", "tinygex\n"]


@pytest.fixture
def before(pg_conn):
    """The schema just before this migration."""
    with pg_conn.cursor() as c:
        _to_cellranger_schema(c)
        c.execute(_sql_body(RNASEQ_RUNS_MIGRATION))
        yield c
    pg_conn.rollback()


@pytest.fixture
def cur(before):
    before.execute(_sql_body(MIGRATION))
    return before


def _request(cur, sample, reference="tiny_ref"):
    cur.execute("SET LOCAL ROLE bloom_workflows")
    cur.execute(
        "SELECT request_scrna_cellranger_run(%s, %s, %s)", (sample, reference, USER)
    )
    run_id = cur.fetchone()[0]
    cur.execute("RESET ROLE")
    return run_id


def _refused(cur, sql, params, error):
    cur.execute("SAVEPOINT refused")
    try:
        with pytest.raises(error):
            cur.execute(sql) if params is None else cur.execute(sql, params)
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT refused")


def _insert(cur, sample, reference="r"):
    cur.execute(
        f"INSERT INTO {TABLE} (workflow_type, params, run_key, requested_by) "
        "VALUES ('scrna-cellranger', %s, %s, %s)",
        (
            psycopg.types.json.Jsonb({"sample": sample, "reference": reference}),
            f"{sample}__{reference}__{USER}",
            USER,
        ),
    )


def _count(cur):
    cur.execute(f"SELECT count(*) FROM {TABLE}")
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# The request function
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("sample", GOOD_SAMPLES)
def test_a_sample_cellranger_can_use_is_accepted(cur, sample):
    run_id = _request(cur, sample)
    cur.execute(f"SELECT params->>'sample' FROM {TABLE} WHERE id = %s", (run_id,))
    assert cur.fetchone()[0] == sample


@pytest.mark.parametrize("sample", BAD_SAMPLES)
def test_a_sample_cellranger_cannot_use_is_refused_and_nothing_written(cur, sample):
    cur.execute("SET LOCAL ROLE bloom_workflows")
    _refused(
        cur,
        "SELECT request_scrna_cellranger_run(%s, %s, %s)",
        (sample, "tiny_ref", USER),
        psycopg.errors.InvalidParameterValue,
    )
    cur.execute("RESET ROLE")
    assert _count(cur) == 0


@pytest.mark.parametrize("reference", ["tair10.araport11", "r" * 100])
def test_reference_names_keep_their_rule(cur, reference):
    run_id = _request(cur, "tinygex", reference)
    cur.execute(f"SELECT params->>'reference' FROM {TABLE} WHERE id = %s", (run_id,))
    assert cur.fetchone()[0] == reference


# --------------------------------------------------------------------------- #
# The table check
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("sample", BAD_SAMPLES)
def test_the_table_refuses_a_sample_cellranger_cannot_use(cur, sample):
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(cur, sample)


def test_the_table_still_accepts_a_dotted_reference(cur):
    _insert(cur, "tinygex", reference="tair10.araport11")
    assert _count(cur) == 1


# --------------------------------------------------------------------------- #
# Applying the migration
# --------------------------------------------------------------------------- #


def test_the_migration_stops_if_a_stored_run_breaks_the_rule(before):
    _request(before, "S1.rep1")
    _refused(before, _sql_body(MIGRATION), None, psycopg.errors.RaiseException)


def test_stored_runs_that_fit_are_kept(before):
    run_id = _request(before, "Col0_root_rep1")
    before.execute(_sql_body(MIGRATION))
    before.execute(f"SELECT status FROM {TABLE} WHERE id = %s", (run_id,))
    assert before.fetchone()[0] == "queued"


def test_reapplying_the_migration_changes_nothing(cur):
    _request(cur, "tinygex")
    cur.execute(_sql_body(MIGRATION))
    assert _count(cur) == 1


@pytest.mark.parametrize(
    "role, allowed",
    [("bloom_workflows", True), ("anon", False), ("authenticated", False)],
)
def test_the_request_function_keeps_its_grants(cur, role, allowed):
    cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, REQUEST_SIG))
    assert cur.fetchone()[0] is allowed


def test_the_rollback_puts_back_the_looser_rule(cur):
    cur.execute(_sql_body(ROLLBACK))
    _request(cur, "S1.rep1")
    _insert(cur, "x" * 100)
    assert _count(cur) == 2
