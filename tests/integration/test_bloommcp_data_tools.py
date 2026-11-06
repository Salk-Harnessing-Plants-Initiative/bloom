"""Live checks of bloommcp's bloom_data tools against the running stack.

- Every public table and view is classified in bloommcp's allow-list (readable or excluded), and
  every readable name exists, so a new table is never silently invisible or a stale name kept.
- No readable table has an email column that is not hidden.
- The tools run inside the bloommcp container, reading PostgREST as bloom_agent: tables listed
  without the excluded ones, a page of rows, emails never returned, an exact count and a
  planner estimate.

Unit counterpart: bloommcp/tests/sections/bloom_data/.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

TABLE_ACCESS = (
    Path(__file__).resolve().parents[2]
    / "bloommcp/src/bloom_mcp/data_access/table_access.py"
)
IN_CI = os.environ.get("GITHUB_ACTIONS") == "true"


def _unavailable(reason: str):
    if IN_CI:
        pytest.fail(reason)
    pytest.skip(reason)


def _table_access():
    spec = importlib.util.spec_from_file_location("table_access", TABLE_ACCESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _public_relations(pg_conn) -> set[str]:
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm')
            """
        )
        return {r[0] for r in cur.fetchall()}


def test_every_table_and_view_is_classified(pg_conn):
    access = _table_access()
    unclassified = (
        _public_relations(pg_conn) - access.READABLE_TABLES - access.EXCLUDED_TABLES
    )
    assert unclassified == set(), (
        "add each to READABLE_TABLES or EXCLUDED_TABLES in "
        f"bloommcp/src/bloom_mcp/data_access/table_access.py: {sorted(unclassified)}"
    )


def test_every_readable_table_exists(pg_conn):
    missing = _table_access().READABLE_TABLES - _public_relations(pg_conn)
    assert missing == set(), f"readable but not in the database: {sorted(missing)}"


def test_every_email_column_on_a_readable_table_is_hidden(pg_conn):
    """A new email column, or a newly readable table with one, fails here until it is hidden
    or its table is excluded."""
    access = _table_access()
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name FROM information_schema.columns
             WHERE table_schema = 'public' AND column_name ILIKE '%%email%%'
            """
        )
        exposed = sorted(
            f"{table}.{column}"
            for table, column in cur.fetchall()
            if table in access.READABLE_TABLES
            and column not in access.HIDDEN_COLUMNS.get(table, frozenset())
        )
    assert exposed == [], f"hide these or exclude their tables: {exposed}"


_SCRIPT = """
import json
from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.sections.bloom_data.count_rows import count_rows
from bloom_mcp.sections.bloom_data.list_tables import list_tables
from bloom_mcp.sections.bloom_data.query_table import query_table

out = {
    "tables": list_tables({}).tables,
    "people_columns": list_tables({"table": "people"}).columns,
    "species": query_table({"table": "species", "limit": 1}).model_dump(),
    "people": query_table({"table": "people", "limit": 5}).model_dump(),
    "count_species": count_rows({"table": "species"}).model_dump(),
    "count_view": count_rows({"table": "cyl_scans_extended"}).model_dump(),
}
try:
    query_table({"table": "checkpoints"})
    out["excluded"] = None
except BloomMCPError as exc:
    out["excluded"] = exc.code
print(json.dumps(out, default=str))
"""


@pytest.fixture(scope="module")
def tool_results() -> dict:
    ps = subprocess.run(
        ["docker", "ps", "-q", "--filter", "label=com.docker.compose.service=bloommcp"],
        capture_output=True,
        text=True,
        check=False,
    )
    ids = ps.stdout.split()
    if len(ids) != 1:
        _unavailable(f"expected one running bloommcp container, found {len(ids)}")
    result = subprocess.run(
        ["docker", "exec", ids[0], "python", "-c", _SCRIPT],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_list_tables_shows_data_tables_and_hides_chat_internals(tool_results):
    tables = tool_results["tables"]
    assert {"species", "genes", "cyl_scans", "gravi_scans", "scrna_datasets"} <= set(
        tables
    )
    assert not {"checkpoints", "chat_threads", "bloommcp_usage"} & set(tables)


def test_people_email_is_never_listed_or_returned(tool_results):
    assert "email" not in tool_results["people_columns"]
    assert "email" not in tool_results["people"]["columns"]
    assert all("email" not in row for row in tool_results["people"]["rows"])


def test_query_table_reads_a_page(tool_results):
    page = tool_results["species"]
    assert page["returned"] <= 1
    assert page["offset"] == 0


def test_count_rows_counts_exactly_and_estimates_a_view(tool_results):
    assert tool_results["count_species"]["approximate"] is False
    assert isinstance(tool_results["count_species"]["count"], int)
    # Views have no planner row estimate of their own, so the count is the planner's.
    assert tool_results["count_view"]["approximate"] is True


def test_an_excluded_table_is_refused(tool_results):
    assert tool_results["excluded"] == "tool_error"
