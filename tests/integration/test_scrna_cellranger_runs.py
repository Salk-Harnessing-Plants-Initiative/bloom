"""
Integration tests for scrna_cellranger_runs, the scrna_cellranger_dispatch queue and
request_scrna_cellranger_run: table defaults and checks, access per role, Realtime,
the request function, EXECUTE grants, re-apply and rollback.

Each test applies the migration inside its own transaction and rolls it back, so the
database is left unchanged.
"""

import re
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
RUNS_TABLE = "scrna_cellranger_runs"
QUEUE = "scrna_cellranger_dispatch"
REQUEST_FN = "request_scrna_cellranger_run"
REQUEST_SIG = "public.request_scrna_cellranger_run(text, text, uuid)"
USER = "00000000-0000-0000-0000-000000000001"
OTHER_USER = "00000000-0000-0000-0000-000000000002"


def _find_one(directory: str, glob: str) -> Path:
    matches = sorted((REPO_ROOT / "supabase" / directory).glob(glob))
    assert matches, f"no {glob} under supabase/{directory}"
    return matches[-1]


MIGRATION = _find_one("migrations", "*_create_scrna_cellranger_runs.sql")
ROLLBACK = _find_one("rollbacks", "*_create_scrna_cellranger_runs_rollback.sql")


def _sql_body(path: Path) -> str:
    """The file without its BEGIN;/COMMIT; lines."""
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        c.execute(_sql_body(MIGRATION))
        yield c
    pg_conn.rollback()


def _request(cur, sample="tinygex", reference="tiny_ref", user=USER):
    cur.execute("SET LOCAL ROLE bloom_workflows")
    cur.execute(f"SELECT {REQUEST_FN}(%s, %s, %s)", (sample, reference, user))
    run_id = cur.fetchone()[0]
    cur.execute("RESET ROLE")
    return run_id


def _queue_run_ids(cur) -> list[int]:
    cur.execute(f"SELECT message->>'run_id' FROM pgmq.q_{QUEUE} ORDER BY msg_id")
    return [int(r[0]) for r in cur.fetchall()]


def _insert(cur, sample="s", reference="r", run_key=None, requested_by=USER, **extra):
    fields = {
        "sample": sample,
        "reference": reference,
        "requested_by": requested_by,
        "run_key": run_key or f"{sample}__{reference}__{requested_by}",
        **extra,
    }
    cols = ", ".join(fields)
    placeholders = ", ".join(["%s"] * len(fields))
    cur.execute(
        f"INSERT INTO {RUNS_TABLE} ({cols}) VALUES ({placeholders}) RETURNING id",
        list(fields.values()),
    )
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# Table
# --------------------------------------------------------------------------- #


def test_run_row_defaults(cur):
    run_id = _insert(cur)
    cur.execute(
        "SELECT status, current_step, exit_code, message, step_pods, argo_workflow_name, "
        f"submitted_at, completed_at FROM {RUNS_TABLE} WHERE id = %s",
        (run_id,),
    )
    assert cur.fetchone() == ("queued", None, None, None, None, None, None, None)


def test_unknown_status_is_rejected(cur):
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(cur, status="complete")


def test_unknown_current_step_is_rejected(cur):
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(cur, current_step="record")


@pytest.mark.parametrize("column", ["sample", "reference"])
@pytest.mark.parametrize("name", ["../etc", "a/b", "", ".hidden", "x" * 101, "a__b"])
def test_unsafe_names_are_rejected_by_the_table(cur, column, name):
    values = {"sample": "s", "reference": "r", column: name}
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(cur, run_key="whatever", **values)


def test_run_key_must_be_sample_reference_and_requester(cur):
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(cur, sample="s", reference="r", run_key="s__r")


def test_requested_by_is_required(cur):
    with pytest.raises(psycopg.errors.NotNullViolation):
        _insert(cur, requested_by=None, run_key="s__r__")


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent", "bloom_workflows"])
def test_read_roles_can_select(cur, role):
    _request(cur)
    cur.execute(f"SET LOCAL ROLE {role}")
    cur.execute(f"SELECT count(*) FROM {RUNS_TABLE}")
    assert cur.fetchone()[0] >= 1
    cur.execute("RESET ROLE")


@pytest.mark.parametrize(
    "role",
    [
        "bloom_user",
        "bloom_agent",
        "bloom_workflows",
        "bloom_writer",
        "anon",
        "authenticated",
    ],
)
@pytest.mark.parametrize("privilege", ["INSERT", "UPDATE", "DELETE"])
def test_only_admin_can_write_the_table_directly(cur, role, privilege):
    cur.execute("SELECT has_table_privilege(%s, %s, %s)", (role, RUNS_TABLE, privilege))
    assert cur.fetchone()[0] is False


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_roles_without_a_grant_cannot_read(cur, role):
    cur.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (role, RUNS_TABLE))
    assert cur.fetchone()[0] is False


def test_the_table_is_in_the_realtime_publication(cur):
    cur.execute(
        "SELECT 1 FROM pg_publication_tables "
        "WHERE pubname = 'supabase_realtime' AND tablename = %s",
        (RUNS_TABLE,),
    )
    assert cur.fetchone() is not None


# --------------------------------------------------------------------------- #
# request_scrna_cellranger_run
# --------------------------------------------------------------------------- #


def test_request_writes_the_run_and_one_message(cur):
    before = _queue_run_ids(cur)
    run_id = _request(cur, sample="tinygex", reference="tiny_ref")

    cur.execute(
        "SELECT sample, reference, run_key, status, requested_by::text "
        f"FROM {RUNS_TABLE} WHERE id = %s",
        (run_id,),
    )
    assert cur.fetchone() == (
        "tinygex",
        "tiny_ref",
        f"tinygex__tiny_ref__{USER}",
        "queued",
        USER,
    )
    assert _queue_run_ids(cur) == before + [run_id]


def test_the_same_sample_against_two_references_gets_two_run_keys(cur):
    a = _request(cur, sample="root_a", reference="tair10")
    b = _request(cur, sample="root_a", reference="tair10_v2")
    cur.execute(
        f"SELECT run_key FROM {RUNS_TABLE} WHERE id IN (%s, %s) ORDER BY id", (a, b)
    )
    assert [r[0] for r in cur.fetchall()] == [
        f"root_a__tair10__{USER}",
        f"root_a__tair10_v2__{USER}",
    ]


def test_two_users_running_the_same_pair_get_two_run_keys(cur):
    a = _request(cur, sample="root_a", reference="tair10", user=USER)
    b = _request(cur, sample="root_a", reference="tair10", user=OTHER_USER)
    cur.execute(
        f"SELECT run_key FROM {RUNS_TABLE} WHERE id IN (%s, %s) ORDER BY id", (a, b)
    )
    assert [r[0] for r in cur.fetchall()] == [
        f"root_a__tair10__{USER}",
        f"root_a__tair10__{OTHER_USER}",
    ]


@pytest.mark.parametrize(
    "sample, reference, user",
    [
        ("../x", "tiny_ref", USER),
        ("a/b", "tiny_ref", USER),
        ("a__b", "tiny_ref", USER),
        ("tinygex", "../etc", USER),
        ("tinygex", "b__c", USER),
        (None, "tiny_ref", USER),
        ("tinygex", None, USER),
        ("tinygex", "tiny_ref", None),
    ],
)
def test_request_refuses_bad_input_and_writes_nothing(cur, sample, reference, user):
    cur.execute(f"SELECT count(*) FROM {RUNS_TABLE}")
    runs_before = cur.fetchone()[0]
    queued_before = _queue_run_ids(cur)
    cur.execute("SAVEPOINT bad_request")
    cur.execute("SET LOCAL ROLE bloom_workflows")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        cur.execute(f"SELECT {REQUEST_FN}(%s, %s, %s)", (sample, reference, user))
    cur.execute("ROLLBACK TO SAVEPOINT bad_request")
    cur.execute(f"SELECT count(*) FROM {RUNS_TABLE}")
    assert cur.fetchone()[0] == runs_before
    assert _queue_run_ids(cur) == queued_before


@pytest.mark.parametrize(
    "role, allowed",
    [
        ("bloom_workflows", True),
        ("PUBLIC", False),
        ("anon", False),
        ("authenticated", False),
        ("bloom_user", False),
        ("bloom_agent", False),
    ],
)
def test_request_execute_is_granted_to_bloom_workflows_only(cur, role, allowed):
    if role == "PUBLIC":
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_proc p, aclexplode(p.proacl) a "
            "WHERE p.oid = %s::regprocedure AND a.grantee = 0 AND a.privilege_type = 'EXECUTE')",
            (REQUEST_SIG,),
        )
    else:
        cur.execute(
            "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, REQUEST_SIG)
        )
    assert cur.fetchone()[0] is allowed


def test_request_function_is_security_definer_with_pinned_search_path(cur):
    cur.execute(
        "SELECT prosecdef, proconfig FROM pg_proc WHERE oid = %s::regprocedure",
        (REQUEST_SIG,),
    )
    secdef, config = cur.fetchone()
    assert secdef is True
    assert any(c.startswith("search_path=") for c in config)


# --------------------------------------------------------------------------- #
# Re-apply and rollback
# --------------------------------------------------------------------------- #


def test_migration_body_is_idempotent(cur):
    cur.execute(_sql_body(MIGRATION))
    cur.execute(
        "SELECT count(*) FROM pgmq.list_queues() WHERE queue_name = %s", (QUEUE,)
    )
    assert cur.fetchone()[0] == 1


def test_rollback_removes_everything(cur):
    cur.execute(_sql_body(ROLLBACK))
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = %s",
        (RUNS_TABLE,),
    )
    assert cur.fetchone() is None, "rollback did not drop the table"
    cur.execute("SELECT 1 FROM pg_proc WHERE proname = %s", (REQUEST_FN,))
    assert cur.fetchone() is None
    cur.execute("SELECT 1 FROM pgmq.list_queues() WHERE queue_name = %s", (QUEUE,))
    assert cur.fetchone() is None
