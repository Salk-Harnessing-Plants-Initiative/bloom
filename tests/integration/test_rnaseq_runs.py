"""
Integration tests for rnaseq_runs, the rnaseq_dispatch queue and their functions: the move
from scrna_cellranger_runs, the per-type checks, access per role, Realtime, requesting a
Cell Ranger run, claim/complete/fail, EXECUTE grants, re-apply and rollback.

Each test starts from the Cell Ranger schema as the two earlier migrations created it,
applies this migration inside its own transaction, and rolls it back, so the database is
left unchanged.
"""

import re
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
TABLE = "rnaseq_runs"
QUEUE = "rnaseq_dispatch"
OLD_QUEUE = "scrna_cellranger_dispatch"
USER = "00000000-0000-0000-0000-000000000001"
OTHER_USER = "00000000-0000-0000-0000-000000000002"
FUNCTIONS = {
    "request": "public.request_scrna_cellranger_run(text, text, uuid)",
    "claim": "public.claim_rnaseq_run(integer, integer)",
    "complete": "public.complete_rnaseq_run(bigint, bigint, text)",
    "fail": "public.fail_rnaseq_run(bigint, bigint, text)",
}
OLD_FUNCTIONS = (
    "public.claim_scrna_cellranger_run(integer, integer)",
    "public.complete_scrna_cellranger_run(bigint, bigint, text)",
    "public.fail_scrna_cellranger_run(bigint, bigint, text)",
)


def _find_one(directory: str, glob: str) -> Path:
    matches = sorted((REPO_ROOT / "supabase" / directory).glob(glob))
    assert matches, f"no {glob} under supabase/{directory}"
    return matches[-1]


RUNS_MIGRATION = _find_one("migrations", "*_create_scrna_cellranger_runs.sql")
FUNCTIONS_MIGRATION = _find_one(
    "migrations", "*_add_scrna_cellranger_dispatch_functions.sql"
)
MIGRATION = _find_one("migrations", "*_move_cellranger_runs_to_rnaseq_runs.sql")
ROLLBACK = _find_one("rollbacks", "*_move_cellranger_runs_to_rnaseq_runs_rollback.sql")


def _sql_body(path: Path) -> str:
    """The file without its BEGIN;/COMMIT; lines."""
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def _queue_exists(c, name) -> bool:
    c.execute("SELECT 1 FROM pgmq.list_queues() WHERE queue_name = %s", (name,))
    return c.fetchone() is not None


def _to_cellranger_schema(c):
    """The schema the two Cell Ranger migrations left, whatever state the database is in."""
    if _queue_exists(c, QUEUE):
        c.execute("SELECT pgmq.drop_queue(%s)", (QUEUE,))
    c.execute(f"DROP TABLE IF EXISTS public.{TABLE} CASCADE")
    for sig in (
        *FUNCTIONS.values(),
        # The request function once rnaseq_runs.metadata exists; dropped too, or the
        # three-argument calls below would be ambiguous.
        "public.request_scrna_cellranger_run(text, text, uuid, jsonb)",
        "public._check_rnaseq_message(bigint, bigint)",
    ):
        c.execute(f"DROP FUNCTION IF EXISTS {sig}")
    c.execute(_sql_body(RUNS_MIGRATION))
    c.execute(_sql_body(FUNCTIONS_MIGRATION))
    c.execute(f"DELETE FROM pgmq.q_{OLD_QUEUE}")


@pytest.fixture
def old(pg_conn):
    """Before this migration."""
    with pg_conn.cursor() as c:
        _to_cellranger_schema(c)
        yield c
    pg_conn.rollback()


@pytest.fixture
def cur(pg_conn):
    """After this migration, with an empty table and queue."""
    with pg_conn.cursor() as c:
        _to_cellranger_schema(c)
        c.execute(_sql_body(MIGRATION))
        yield c
    pg_conn.rollback()


def _as_workflows(cur, sql, params=()):
    cur.execute("SET LOCAL ROLE bloom_workflows")
    cur.execute(sql, params)
    rows = cur.fetchall() if cur.description else None
    cur.execute("RESET ROLE")
    return rows


def _request(cur, sample="tinygex", reference="tiny_ref", user=USER):
    rows = _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(%s, %s, %s)",
        (sample, reference, user),
    )
    return rows[0][0]


def _claim(cur, vt=60, max_reads=5):
    return _as_workflows(cur, "SELECT * FROM claim_rnaseq_run(%s, %s)", (vt, max_reads))


def _call(cur, fn, *args):
    rows = _as_workflows(cur, f"SELECT {fn}(%s, %s, %s)", args)
    return rows[0][0]


def _run(cur, run_id):
    cur.execute(
        "SELECT status, message, argo_workflow_name, submitted_at IS NOT NULL, "
        f"completed_at IS NOT NULL FROM {TABLE} WHERE id = %s",
        (run_id,),
    )
    return cur.fetchone()


def _count(cur, kind, queue=QUEUE):
    cur.execute(f"SELECT count(*) FROM pgmq.{kind}_{queue}")
    return cur.fetchone()[0]


def _send(cur, message: str):
    cur.execute("SELECT pgmq.send(%s, %s::jsonb)", (QUEUE, message))


def _insert(cur, params, run_key, workflow_type="scrna-cellranger", **extra):
    fields = {
        "workflow_type": workflow_type,
        "params": psycopg.types.json.Jsonb(params),
        "run_key": run_key,
        "requested_by": USER,
        **extra,
    }
    cols = ", ".join(fields)
    cur.execute(
        f"INSERT INTO {TABLE} ({cols}) VALUES ({', '.join(['%s'] * len(fields))}) "
        "RETURNING id",
        list(fields.values()),
    )
    return cur.fetchone()[0]


def _refused(cur, sql, params, error):
    cur.execute("SAVEPOINT refused")
    try:
        with pytest.raises(error):
            # Without params, a literal % in the SQL is not read as a placeholder.
            cur.execute(sql) if params is None else cur.execute(sql, params)
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT refused")


GOOD = {"sample": "s", "reference": "r"}
GOOD_KEY = f"s__r__{USER}"


# --------------------------------------------------------------------------- #
# The move from scrna_cellranger_runs
# --------------------------------------------------------------------------- #


def test_existing_runs_keep_their_id_and_get_type_and_params(old):
    old.execute("SELECT request_scrna_cellranger_run('root_a', 'tair10', %s)", (USER,))
    run_id = old.fetchone()[0]
    old.execute(_sql_body(MIGRATION))
    old.execute(
        f"SELECT workflow_type, params, run_key, status FROM {TABLE} WHERE id = %s",
        (run_id,),
    )
    assert old.fetchone() == (
        "scrna-cellranger",
        {"sample": "root_a", "reference": "tair10"},
        f"root_a__tair10__{USER}",
        "queued",
    )


def test_a_waiting_message_moves_to_the_shared_queue_and_can_be_claimed(old):
    old.execute("SELECT request_scrna_cellranger_run('root_a', 'tair10', %s)", (USER,))
    run_id = old.fetchone()[0]
    old.execute(_sql_body(MIGRATION))
    assert not _queue_exists(old, OLD_QUEUE)
    assert _claim(old)[0][0] == run_id


def test_the_cellranger_columns_and_names_are_gone(cur):
    cur.execute("SELECT to_regclass('public.scrna_cellranger_runs')")
    assert cur.fetchone()[0] is None
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s",
        (TABLE,),
    )
    columns = {r[0] for r in cur.fetchall()}
    assert {"workflow_type", "params"} <= columns
    assert not {"sample", "reference"} & columns
    cur.execute("SELECT policyname FROM pg_policies WHERE tablename = %s", (TABLE,))
    assert sorted(r[0] for r in cur.fetchall()) == [
        "admin_all_rnaseq_runs",
        "agent_read_rnaseq_runs",
        "user_read_rnaseq_runs",
        "workflows_read_rnaseq_runs",
    ]


@pytest.mark.parametrize("sig", OLD_FUNCTIONS)
def test_the_cellranger_dispatch_functions_are_dropped(cur, sig):
    cur.execute("SELECT to_regprocedure(%s)", (sig,))
    assert cur.fetchone()[0] is None


# --------------------------------------------------------------------------- #
# Table checks
# --------------------------------------------------------------------------- #


def test_a_valid_cellranger_row_gets_the_defaults(cur):
    run_id = _insert(cur, GOOD, GOOD_KEY)
    cur.execute(
        f"SELECT status, current_step, exit_code, message FROM {TABLE} WHERE id = %s",
        (run_id,),
    )
    assert cur.fetchone() == ("queued", None, None, None)


def test_an_unknown_workflow_type_is_refused(cur):
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(cur, GOOD, GOOD_KEY, workflow_type="fastqc")


@pytest.mark.parametrize(
    "params",
    [
        {"sample": "s"},
        {"reference": "r"},
        {"sample": "s", "reference": "r", "extra": 1},
        {"sample": 5, "reference": "r"},
        {"sample": "s", "reference": ["r"]},
        {},
    ],
)
def test_cellranger_params_must_be_exactly_sample_and_reference(cur, params):
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(cur, params, GOOD_KEY)


def test_cellranger_params_must_be_an_object(cur):
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(cur, ["s", "r"], GOOD_KEY)


@pytest.mark.parametrize("field", ["sample", "reference"])
@pytest.mark.parametrize(
    "name", ["../etc", "a/b", "", ".hidden", "x" * 101, "a__b", "tinygex\n"]
)
def test_unsafe_names_are_refused_by_the_table(cur, field, name):
    params = {**GOOD, field: name}
    key = f"{params['sample']}__{params['reference']}__{USER}"
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(cur, params, key)


def test_the_run_key_must_be_built_from_the_params_and_requester(cur):
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(cur, GOOD, "s__r")


def test_the_requester_is_required(cur):
    with pytest.raises(psycopg.errors.NotNullViolation):
        _insert(cur, GOOD, "s__r__", requested_by=None)


def test_a_cellranger_step_is_accepted_and_another_is_refused(cur):
    _insert(cur, GOOD, GOOD_KEY, current_step="count")
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(cur, GOOD, GOOD_KEY, current_step="align")


def test_an_unknown_status_is_refused(cur):
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(cur, GOOD, GOOD_KEY, status="complete")


@pytest.mark.parametrize(
    "index", ["rnaseq_runs_workflow_type_status_idx", "rnaseq_runs_created_at_idx"]
)
def test_the_job_list_indexes_exist(cur, index):
    cur.execute("SELECT to_regclass(%s)", (f"public.{index}",))
    assert cur.fetchone()[0] is not None


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent", "bloom_workflows"])
def test_read_roles_can_select(cur, role):
    _request(cur)
    cur.execute(f"SET LOCAL ROLE {role}")
    cur.execute(f"SELECT count(*) FROM {TABLE}")
    assert cur.fetchone()[0] == 1
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
    cur.execute("SELECT has_table_privilege(%s, %s, %s)", (role, TABLE, privilege))
    assert cur.fetchone()[0] is False


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_roles_without_a_grant_cannot_read(cur, role):
    cur.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (role, TABLE))
    assert cur.fetchone()[0] is False


def test_the_table_is_in_the_realtime_publication(cur):
    cur.execute(
        "SELECT 1 FROM pg_publication_tables "
        "WHERE pubname = 'supabase_realtime' AND tablename = %s",
        (TABLE,),
    )
    assert cur.fetchone() is not None


# --------------------------------------------------------------------------- #
# request_scrna_cellranger_run
# --------------------------------------------------------------------------- #


def test_a_request_writes_one_row_and_one_shared_queue_message(cur):
    run_id = _request(cur, sample="tinygex", reference="tiny_ref")
    cur.execute(
        f"SELECT workflow_type, params, run_key, requested_by::text FROM {TABLE} "
        "WHERE id = %s",
        (run_id,),
    )
    assert cur.fetchone() == (
        "scrna-cellranger",
        {"sample": "tinygex", "reference": "tiny_ref"},
        f"tinygex__tiny_ref__{USER}",
        USER,
    )
    cur.execute(f"SELECT message FROM pgmq.q_{QUEUE}")
    assert cur.fetchall() == [({"run_id": run_id},)]


def test_two_users_running_the_same_pair_get_two_run_keys(cur):
    a = _request(cur, sample="root_a", reference="tair10", user=USER)
    b = _request(cur, sample="root_a", reference="tair10", user=OTHER_USER)
    cur.execute(f"SELECT run_key FROM {TABLE} WHERE id IN (%s, %s) ORDER BY id", (a, b))
    assert [r[0] for r in cur.fetchall()] == [
        f"root_a__tair10__{USER}",
        f"root_a__tair10__{OTHER_USER}",
    ]


@pytest.mark.parametrize(
    "sample, reference, user",
    [
        ("../x", "tiny_ref", USER),
        ("a__b", "tiny_ref", USER),
        ("tinygex", "b__c", USER),
        (None, "tiny_ref", USER),
        ("tinygex", None, USER),
        ("tinygex", "tiny_ref", None),
    ],
)
def test_a_bad_request_is_refused_and_writes_nothing(cur, sample, reference, user):
    cur.execute("SET LOCAL ROLE bloom_workflows")
    _refused(
        cur,
        "SELECT request_scrna_cellranger_run(%s, %s, %s)",
        (sample, reference, user),
        psycopg.errors.InvalidParameterValue,
    )
    cur.execute("RESET ROLE")
    cur.execute(f"SELECT count(*) FROM {TABLE}")
    assert cur.fetchone()[0] == 0
    assert _count(cur, "q") == 0


# --------------------------------------------------------------------------- #
# claim_rnaseq_run
# --------------------------------------------------------------------------- #


def test_claim_on_an_empty_queue_returns_nothing(cur):
    assert _claim(cur) == []


def test_claim_returns_the_run_with_its_type_and_params(cur):
    run_id = _request(cur, sample="root_a", reference="tair10")
    (row,) = _claim(cur)
    assert row[:4] == (
        run_id,
        "scrna-cellranger",
        {"sample": "root_a", "reference": "tair10"},
        f"root_a__tair10__{USER}",
    )
    assert isinstance(row[4], int)


def test_a_claimed_message_is_hidden_from_the_next_claim(cur):
    _request(cur)
    assert len(_claim(cur)) == 1
    assert _claim(cur) == []


def test_claim_drops_a_stale_message_and_returns_the_next_run(cur):
    stale = _request(cur, sample="stale")
    fresh = _request(cur, sample="fresh")
    cur.execute(f"UPDATE {TABLE} SET status = 'running' WHERE id = %s", (stale,))
    assert _claim(cur)[0][0] == fresh
    assert (_count(cur, "q"), _count(cur, "a")) == (1, 0)
    assert _run(cur, stale)[0] == "running"


def test_a_message_for_a_missing_run_is_deleted(cur):
    _send(cur, '{"run_id": 999999999}')
    assert _claim(cur) == []
    assert (_count(cur, "q"), _count(cur, "a")) == (0, 0)


@pytest.mark.parametrize(
    "message",
    [
        '{"x": 1}',
        '{"run_id": "abc"}',
        '{"run_id": -1}',
        '{"run_id": 1.5}',
        '{"run_id": 99999999999999999999}',
    ],
)
def test_an_unusable_message_is_archived_and_the_next_run_returned(cur, message):
    _send(cur, message)
    run_id = _request(cur)
    assert _claim(cur)[0][0] == run_id
    assert _count(cur, "a") == 1


def test_a_run_redelivered_past_the_limit_is_failed_and_archived(cur):
    run_id = _request(cur)
    # vt=0 makes the message visible again at once, as after a worker crash.
    assert len(_claim(cur, vt=0, max_reads=2)) == 1
    assert len(_claim(cur, vt=0, max_reads=2)) == 1
    assert _claim(cur, vt=0, max_reads=2) == []
    status, message, _, _, completed = _run(cur, run_id)
    assert (status, message, completed) == (
        "failed",
        "not submitted after 2 attempts",
        True,
    )
    assert (_count(cur, "q"), _count(cur, "a")) == (0, 1)


@pytest.mark.parametrize("vt, max_reads", [(None, 5), (60, None), (-1, 5), (60, 0)])
def test_claim_refuses_bad_arguments(cur, vt, max_reads):
    _request(cur)
    cur.execute("SET LOCAL ROLE bloom_workflows")
    _refused(
        cur,
        "SELECT * FROM claim_rnaseq_run(%s, %s)",
        (vt, max_reads),
        psycopg.errors.InvalidParameterValue,
    )
    cur.execute("RESET ROLE")
    assert _count(cur, "q") == 1


# --------------------------------------------------------------------------- #
# complete_rnaseq_run
# --------------------------------------------------------------------------- #


def test_complete_records_the_workflow_and_deletes_the_message(cur):
    run_id = _request(cur)
    msg_id = _claim(cur)[0][4]
    assert _call(cur, "complete_rnaseq_run", run_id, msg_id, "wf-1") is True
    assert _run(cur, run_id) == ("submitted", None, "wf-1", True, False)
    assert (_count(cur, "q"), _count(cur, "a")) == (0, 0)


def test_complete_leaves_a_run_that_moved_on_and_reports_it(cur):
    run_id = _request(cur)
    msg_id = _claim(cur)[0][4]
    cur.execute(
        f"UPDATE {TABLE} SET status = 'running', argo_workflow_name = 'wf-1' "
        "WHERE id = %s",
        (run_id,),
    )
    assert _call(cur, "complete_rnaseq_run", run_id, msg_id, "wf-2") is False
    assert _run(cur, run_id)[:3] == ("running", None, "wf-1")
    assert _count(cur, "q") == 0


@pytest.mark.parametrize("name", [None, "", "   "])
def test_complete_refuses_an_empty_workflow_name(cur, name):
    run_id = _request(cur)
    msg_id = _claim(cur)[0][4]
    cur.execute("SET LOCAL ROLE bloom_workflows")
    _refused(
        cur,
        "SELECT complete_rnaseq_run(%s, %s, %s)",
        (run_id, msg_id, name),
        psycopg.errors.InvalidParameterValue,
    )
    cur.execute("RESET ROLE")
    assert _run(cur, run_id)[0] == "queued"


@pytest.mark.parametrize("fn", ["complete_rnaseq_run", "fail_rnaseq_run"])
def test_a_message_of_another_run_is_refused_and_nothing_changes(cur, fn):
    a = _request(cur, sample="a")
    b = _request(cur, sample="b")
    cur.execute(
        f"SELECT msg_id FROM pgmq.q_{QUEUE} WHERE message->>'run_id' = %s", (str(b),)
    )
    b_msg = cur.fetchone()[0]
    cur.execute("SET LOCAL ROLE bloom_workflows")
    _refused(
        cur,
        f"SELECT {fn}(%s, %s, %s)",
        (a, b_msg, "x"),
        psycopg.errors.InvalidParameterValue,
    )
    cur.execute("RESET ROLE")
    assert _run(cur, a)[0] == _run(cur, b)[0] == "queued"
    assert _count(cur, "q") == 2


# --------------------------------------------------------------------------- #
# fail_rnaseq_run
# --------------------------------------------------------------------------- #


def test_fail_marks_the_run_failed_and_archives_the_message(cur):
    run_id = _request(cur)
    msg_id = _claim(cur)[0][4]
    assert _call(cur, "fail_rnaseq_run", run_id, msg_id, "rejected") is True
    assert _run(cur, run_id) == ("failed", "rejected", None, False, True)
    assert (_count(cur, "q"), _count(cur, "a")) == (0, 1)


def test_fail_leaves_a_submitted_run_and_reports_it(cur):
    run_id = _request(cur)
    msg_id = _claim(cur)[0][4]
    _call(cur, "complete_rnaseq_run", run_id, msg_id, "wf-1")
    assert _call(cur, "fail_rnaseq_run", run_id, msg_id, "late") is False
    assert _run(cur, run_id)[:3] == ("submitted", None, "wf-1")


@pytest.mark.parametrize("message", [None, "", "  "])
def test_fail_refuses_an_empty_message(cur, message):
    run_id = _request(cur)
    msg_id = _claim(cur)[0][4]
    cur.execute("SET LOCAL ROLE bloom_workflows")
    _refused(
        cur,
        "SELECT fail_rnaseq_run(%s, %s, %s)",
        (run_id, msg_id, message),
        psycopg.errors.InvalidParameterValue,
    )
    cur.execute("RESET ROLE")
    assert _run(cur, run_id)[0] == "queued"


# --------------------------------------------------------------------------- #
# Grants and function settings
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fn", sorted(FUNCTIONS))
@pytest.mark.parametrize(
    "role, allowed",
    [
        ("bloom_workflows", True),
        ("public", False),
        ("anon", False),
        ("authenticated", False),
        ("bloom_user", False),
        ("bloom_agent", False),
    ],
)
def test_execute_is_granted_to_bloom_workflows_only(cur, fn, role, allowed):
    cur.execute(
        "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, FUNCTIONS[fn])
    )
    assert cur.fetchone()[0] is allowed


@pytest.mark.parametrize("role", ["public", "bloom_workflows", "anon", "authenticated"])
def test_the_message_check_is_not_callable_directly(cur, role):
    cur.execute(
        "SELECT has_function_privilege(%s, 'public._check_rnaseq_message(bigint, bigint)', "
        "'EXECUTE')",
        (role,),
    )
    assert cur.fetchone()[0] is False


@pytest.mark.parametrize(
    "sig", [*FUNCTIONS.values(), "public._check_rnaseq_message(bigint, bigint)"]
)
def test_functions_are_security_definer_with_pinned_search_path(cur, sig):
    cur.execute(
        "SELECT prosecdef, proconfig FROM pg_proc WHERE oid = %s::regprocedure", (sig,)
    )
    secdef, config = cur.fetchone()
    assert secdef is True
    assert "search_path=pg_catalog, public, pgmq" in config


# --------------------------------------------------------------------------- #
# Re-apply and rollback
# --------------------------------------------------------------------------- #


def test_reapplying_the_migration_keeps_runs_messages_and_grants(cur):
    run_id = _request(cur)
    cur.execute(_sql_body(MIGRATION))
    assert _count(cur, "q") == 1
    assert _claim(cur)[0][0] == run_id
    cur.execute(
        "SELECT has_function_privilege('anon', %s, 'EXECUTE')", (FUNCTIONS["claim"],)
    )
    assert cur.fetchone()[0] is False


def test_rollback_restores_the_cellranger_table_queue_and_functions(cur):
    run_id = _request(cur, sample="root_a", reference="tair10")
    cur.execute(_sql_body(ROLLBACK))
    cur.execute(
        "SELECT sample, reference, run_key FROM scrna_cellranger_runs WHERE id = %s",
        (run_id,),
    )
    assert cur.fetchone() == ("root_a", "tair10", f"root_a__tair10__{USER}")
    assert _count(cur, "q", OLD_QUEUE) == 1
    assert not _queue_exists(cur, QUEUE)
    for sig in OLD_FUNCTIONS:
        cur.execute("SELECT to_regprocedure(%s)", (sig,))
        assert cur.fetchone()[0] is not None, sig
    for sig in list(FUNCTIONS.values())[1:]:
        cur.execute("SELECT to_regprocedure(%s)", (sig,))
        assert cur.fetchone()[0] is None, sig


def test_rollback_refuses_when_other_workflow_types_exist(cur):
    cur.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT rnaseq_runs_workflow_type_check")
    _insert(cur, {"x": 1}, "k", workflow_type="fastqc")
    _refused(cur, _sql_body(ROLLBACK), None, psycopg.errors.RaiseException)
