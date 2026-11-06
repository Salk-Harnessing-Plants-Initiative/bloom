"""The workers' queue, function and parameter names match the database's.

Read out of services/workflows rather than copied here, so a rename on either side
fails this test instead of every call at run time. Catalog reads only.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytest.importorskip("psycopg")

WORKFLOWS = Path(__file__).resolve().parents[2] / "services" / "workflows"
VERBS = ("enqueue", "claim", "progress", "complete", "fail")
# The video_queue.py function that sends each verb's parameters.
SENDER = {"claim": "claim", "progress": "report", "complete": "complete", "fail": "fail"}


def _queues() -> dict[str, dict[str, str]]:
    """Each Queue(...) in video_queue.py, as {its variable: {field: literal}}."""
    tree = ast.parse((WORKFLOWS / "video_queue.py").read_text())
    queues = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "id", None) == "Queue"
        ):
            queues[node.targets[0].id] = {
                kw.arg: kw.value.value for kw in node.value.keywords
            }
    assert set(queues) == {"PLATE_QUEUE", "CYL_QUEUE"}, queues
    return queues


def _dict_keys(call: ast.Call) -> set[str]:
    for arg in call.args:
        if isinstance(arg, ast.Dict):
            return {k.value for k in arg.keys}
    raise AssertionError(f"no parameter dict in the call at line {call.lineno}")


def _sent_keys() -> dict[str, set[str]]:
    """The parameter names each wrapper sends, by verb (enqueue by queue)."""
    tree = ast.parse((WORKFLOWS / "video_queue.py").read_text())
    sent = {}
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef) and fn.name in SENDER.values():
            rpc = next(
                n
                for n in ast.walk(fn)
                if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "rpc"
            )
            verb = next(v for v, f in SENDER.items() if f == fn.name)
            sent[verb] = _dict_keys(rpc)
    assert set(sent) == set(SENDER), sent

    # The enqueue parameters are built by the two routes.
    for source, queue, caller in (
        ("plate_request.py", "PLATE_QUEUE", "enqueue_job"),
        ("main.py", "CYL_QUEUE", "enqueue"),
    ):
        tree = ast.parse((WORKFLOWS / source).read_text())
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", None) == caller
            and any(getattr(a, "id", None) == queue for a in n.args)
        ]
        assert len(calls) == 1, (source, len(calls))
        sent[f"enqueue:{queue}"] = _dict_keys(calls[0])
    return sent


def _inputs(cur, function: str) -> tuple[list[str], int]:
    """A public function's input parameter names, and how many have no default."""
    cur.execute(
        "SELECT proargnames, proargmodes, pronargs, pronargdefaults FROM pg_proc "
        "WHERE proname = %s AND pronamespace = 'public'::regnamespace",
        (function,),
    )
    rows = cur.fetchall()
    assert len(rows) == 1, f"{function}: {len(rows)} functions by that name"
    names, modes, nargs, ndefaults = rows[0]
    inputs = [n for n, m in zip(names, modes or ["i"] * len(names)) if m in ("i", "b")]
    assert len(inputs) == nargs
    return inputs, nargs - ndefaults


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        yield c
    pg_conn.rollback()


@pytest.mark.parametrize("queue", ["PLATE_QUEUE", "CYL_QUEUE"])
def test_the_queue_the_worker_names_exists(cur, queue):
    name = _queues()[queue]["name"]
    cur.execute("SELECT 1 FROM pgmq.list_queues() WHERE queue_name = %s", (name,))
    assert cur.fetchone() is not None, f"no pgmq queue {name}"


@pytest.mark.parametrize("queue", ["PLATE_QUEUE", "CYL_QUEUE"])
@pytest.mark.parametrize("verb", VERBS)
def test_each_call_sends_parameters_the_function_takes(cur, queue, verb):
    """Every name sent is a parameter, and every parameter without a default is sent."""
    function = _queues()[queue][verb]
    sent = _sent_keys()[f"enqueue:{queue}" if verb == "enqueue" else verb]
    inputs, required = _inputs(cur, function)

    assert sent <= set(inputs), f"{function} has no parameter {sorted(sent - set(inputs))}"
    missing = set(inputs[:required]) - sent
    assert not missing, f"{function} needs {sorted(missing)}"


def test_the_web_reads_only_columns_it_is_granted(cur):
    """The web's job columns, read from its source, against the column grants."""
    source = (
        Path(__file__).resolve().parents[2] / "web" / "lib" / "supabase" / "video-jobs.ts"
    ).read_text()
    columns = re.search(r'const JOB_COLUMNS =\s*"([^"]+)"', source).group(1)
    for table in ("gravi_plate_video_jobs", "cyl_video_jobs"):
        for column in (c.strip() for c in columns.split(",")):
            cur.execute(
                "SELECT has_column_privilege('bloom_user', %s, %s, 'SELECT')",
                (f"public.{table}", column),
            )
            assert cur.fetchone()[0] is True, f"bloom_user cannot read {table}.{column}"
