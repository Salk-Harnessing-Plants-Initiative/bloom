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


def test_every_readable_table_pages_by_its_primary_key_in_index_order(pg_conn):
    access = _table_access()
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname,
                   (SELECT array_agg(a.attname ORDER BY k.ord)
                      FROM unnest(i.indkey) WITH ORDINALITY k(attnum, ord)
                      JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum)
              FROM pg_class c JOIN pg_index i ON i.indrelid = c.oid AND i.indisprimary
             WHERE c.relnamespace = 'public'::regnamespace AND c.relname = ANY (%s)
            """,
            (sorted(access.READABLE_TABLES),),
        )
        keys = {name: tuple(cols) for name, cols in cur.fetchall()}
    wrong = {
        t: (access.paging_key(t), keys[t])
        for t in keys
        if access.paging_key(t) != keys[t]
    }
    assert not wrong, f"declared paging key differs from the primary key: {wrong}"


def test_every_readable_view_has_a_paging_key_on_its_own_columns(pg_conn):
    access = _table_access()
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname, array_agg(a.attname)
              FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0
             WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('v', 'm')
               AND c.relname = ANY (%s)
             GROUP BY c.relname
            """,
            (sorted(access.READABLE_TABLES),),
        )
        views = {name: set(cols) for name, cols in cur.fetchall()}
    missing = sorted(v for v in views if v not in access.PAGING_KEYS)
    assert not missing, f"readable views with no paging key: {missing}"
    bad = {
        v: access.PAGING_KEYS[v]
        for v in views
        if not set(access.PAGING_KEYS[v]) <= views[v]
    }
    assert not bad, f"paging keys naming columns the view doesn't have: {bad}"


def test_every_owner_rights_view_over_deletable_rows_has_a_deleted_note(pg_conn):
    """A view without security_invoker skips the read rule that hides deleted rows, so a
    readable one over a table with deleted rows fails here until its note says so."""
    access = _table_access()
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT v.relname
              FROM pg_depend d
              JOIN pg_rewrite r ON r.oid = d.objid
              JOIN pg_class v ON v.oid = r.ev_class
              JOIN pg_class t ON t.oid = d.refobjid
              JOIN pg_namespace n ON n.oid = v.relnamespace
             WHERE n.nspname = 'public' AND v.relkind = 'v' AND t.oid <> v.oid
               AND NOT coalesce(v.reloptions::text[] && ARRAY[
                     'security_invoker=true', 'security_invoker=on'], false)
               AND EXISTS (SELECT 1 FROM information_schema.columns c
                            WHERE c.table_schema = 'public' AND c.table_name = t.relname
                              AND c.column_name IN ('deleted', 'deleted_at'))
            """
        )
        views = {row[0] for row in cur.fetchall()} & access.READABLE_TABLES
    missing = sorted(v for v in views if "deleted" not in access.TABLE_NOTES.get(v, ""))
    assert views, "expected the cylinder views that join cyl_experiments"
    assert missing == [], f"add a deleted-rows note for: {missing}"


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
    "notes": list_tables({"contains": "cyl_scan_traits"}).model_dump(),
    "species": query_table({"table": "species", "limit": 1}).model_dump(),
    "people": query_table({"table": "people", "limit": 5}).model_dump(),
    "count_species": count_rows({"table": "species"}).model_dump(),
    "count_view": count_rows({"table": "cyl_scans_extended"}).model_dump(),
    "count_missing": count_rows({"table": "cyl_scan_traits_latest", "filters": [
        {"column": "scan_id", "op": "eq", "value": -1}]}).model_dump(),
}
try:
    query_table({"table": "checkpoints"})
    out["excluded"] = None
except BloomMCPError as exc:
    out["excluded"] = exc.code
print(json.dumps(out, default=str))
"""


def _run_in_bloommcp(script: str) -> dict:
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
        ["docker", "exec", ids[0], "python", "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def tool_results() -> dict:
    return _run_in_bloommcp(_SCRIPT)


def test_list_tables_shows_data_tables_and_hides_chat_internals(tool_results):
    tables = tool_results["tables"]
    assert {"species", "genes", "cyl_scans", "gravi_scans", "scrna_datasets"} <= set(
        tables
    )
    assert not {"checkpoints", "chat_threads", "bloommcp_usage"} & set(tables)


def test_trait_values_are_offered_only_through_the_latest_rule_views(tool_results):
    listed = tool_results["notes"]
    assert listed["tables"] == ["cyl_scan_traits_latest", "cyl_scan_traits_source"]
    assert set(listed["notes"]) == set(listed["tables"])


def test_people_email_is_never_listed_or_returned(tool_results):
    assert "email" not in tool_results["people_columns"]
    assert "email" not in tool_results["people"]["columns"]
    assert all("email" not in row for row in tool_results["people"]["rows"])


def test_query_table_reads_a_page(tool_results):
    page = tool_results["species"]
    assert page["returned"] <= 1
    assert page["offset"] == 0


def test_count_rows_counts_tables_and_views_exactly(tool_results):
    assert tool_results["count_species"]["approximate"] is False
    assert isinstance(tool_results["count_species"]["count"], int)
    assert tool_results["count_view"]["approximate"] is False


def test_a_filtered_count_for_a_missing_id_is_an_exact_zero(tool_results):
    assert tool_results["count_missing"]["count"] == 0
    assert tool_results["count_missing"]["approximate"] is False


def test_an_excluded_table_is_refused(tool_results):
    assert tool_results["excluded"] == "tool_error"


_GENE = "TEST_BLOOMMCP_EMAIL_REDACTION"
_GENE_SCRIPT = f"""
import json
from bloom_mcp.sections.bloom_data.query_table import query_table

page = query_table({{"table": "gene_candidates", "filters": [
    {{"column": "gene", "op": "eq", "value": "{_GENE}"}}]}})
print(json.dumps(page.rows, default=str))
"""


@pytest.fixture
def gene_candidate_with_emails(pg_conn):
    logs = [{"userid": "ada@salk.edu", "status": "suspected", "date": "2026-01-02"}]
    legacy = [{"user_email": "ben@salk.edu", "note": "sent"}]
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO genes (gene_id) VALUES (%s)", (_GENE,))
        cur.execute(
            "INSERT INTO gene_candidates (gene, status_logs, legacy_progress_logs_jsonb)"
            " VALUES (%s, %s::jsonb, %s::jsonb)",
            (_GENE, json.dumps(logs), json.dumps(legacy)),
        )
    pg_conn.commit()
    try:
        yield
    finally:
        with pg_conn.cursor() as cur:
            cur.execute("DELETE FROM gene_candidates WHERE gene = %s", (_GENE,))
            cur.execute("DELETE FROM genes WHERE gene_id = %s", (_GENE,))
        pg_conn.commit()


def test_emails_in_gene_candidate_logs_never_reach_claude(gene_candidate_with_emails):
    rows = _run_in_bloommcp(_GENE_SCRIPT)
    assert len(rows) == 1
    assert rows[0]["status_logs"][0]["status"] == "suspected"
    assert "@" not in json.dumps(rows)
