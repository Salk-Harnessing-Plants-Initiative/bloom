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


def _public_columns(pg_conn) -> set[tuple[str, str]]:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema = 'public'"
        )
        return set(cur.fetchall())


def test_every_hidden_column_exists(pg_conn):
    """A renamed column would be readable under its new name, so a stale entry fails here."""
    access = _table_access()
    present = _public_columns(pg_conn)
    stale = sorted(
        f"{table}.{column}"
        for table, columns in access.HIDDEN_COLUMNS.items()
        for column in columns
        if (table, column) not in present
    )
    assert stale == [], f"these hidden columns no longer exist: {stale}"


def test_every_error_message_column_on_a_readable_table_is_hidden(pg_conn):
    """Raw error text names internal hosts and paths; a new such column fails until hidden."""
    access = _table_access()
    exposed = sorted(
        f"{table}.{column}"
        for table, column in _public_columns(pg_conn)
        if column in {"error_message", "message"}
        and table in access.READABLE_TABLES
        and column not in access.HIDDEN_COLUMNS.get(table, frozenset())
    )
    assert exposed == [], f"hide these or exclude their tables: {exposed}"


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
    "people_columns": [c.name for c in list_tables({"table": "people"}).columns],
    "scan_columns": list_tables({"table": "cyl_scans"}).model_dump()["columns"],
    "described": {t: [c.model_dump() for c in list_tables({"table": t}).columns]
                  for t in list_tables({}).tables},
    "notes": list_tables({"contains": "cyl_scan_traits"}).model_dump(),
    "species": query_table({"table": "species", "limit": 1}).model_dump(),
    "people": query_table({"table": "people", "limit": 5}).model_dump(),
    "count_species": count_rows({"table": "species"}).model_dump(),
    "count_view": count_rows({"table": "cyl_scans_extended"}).model_dump(),
    "longest_in": query_table({"table": "species", "columns": ["id"], "filters": [
        {"column": "common_name", "op": "in",
         "value": [f"TEST_BLOOMMCP {i:03d}" for i in range(200)]}]}).returned,
    "count_missing": count_rows({"table": "cyl_scan_traits_latest", "filters": [
        {"column": "scan_id", "op": "eq", "value": -1}]}).model_dump(),
}
try:
    query_table({"table": "checkpoints"})
    out["excluded"] = None
except BloomMCPError as exc:
    out["excluded"] = exc.code
try:
    query_table({"table": "species", "filters": [
        {"column": "id", "op": "eq", "value": "not-a-number"}]})
    out["bad_value"] = None
except BloomMCPError as exc:
    out["bad_value"] = exc.to_dict()
try:
    query_table({"table": "species", "filters": [{"column": "common_name", "op": "in",
        "value": ["TEST_BLOOMMCP " + "x" * 60 + f" {i}" for i in range(200)]}]})
    out["too_long"] = None
except BloomMCPError as exc:
    out["too_long"] = exc.to_dict()
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


def test_columns_show_their_type_and_the_table_they_point_to(tool_results):
    columns = {c["name"]: c for c in tool_results["scan_columns"]}
    assert columns["plant_id"] == {
        "name": "plant_id",
        "type": "bigint",
        "references": "cyl_plants.id",
    }
    assert columns["date_scanned"]["type"] == "date"


def test_every_reference_points_to_a_readable_table_and_column(tool_results):
    described = tool_results["described"]
    bad = sorted(
        f"{table}.{c['name']} -> {c['references']}"
        for table, columns in described.items()
        for c in columns
        if c["references"]
        and c["references"].split(".")[1]
        not in {x["name"] for x in described.get(c["references"].split(".")[0], [])}
    )
    assert bad == [], bad


# The columns each note tells Claude to filter or join on.
_NOTE_COLUMNS = {
    "scrna_datasets": {"name", "kind"},
    "scrna_de_runs": {"status", "completed_at"},
    "scrna_de": {"run_id", "cluster_id", "contrast", "tested"},
    "scrna_de_genes": {"de_id"},
    "cyl_experiments": {"deleted"},
    "cyl_scan_traits_source": {"is_latest"},
    "cyl_scans_extended": {"experiment_id"},
    "cyl_plants_extended": {"experiment_id"},
    "cyl_wave_timeline": {"experiment_name"},
}


def test_every_column_a_note_names_is_readable(tool_results):
    described = tool_results["described"]
    missing = sorted(
        f"{table}.{column}"
        for table, columns in _NOTE_COLUMNS.items()
        for column in columns
        if column not in {c["name"] for c in described.get(table, [])}
    )
    assert missing == [], f"notes name columns Claude can't read: {missing}"


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


def test_a_wrong_type_filter_is_explained_without_the_database_code(tool_results):
    error = tool_results["bad_value"]
    assert error["code"] == "invalid_input"
    assert "doesn't fit its column" in error["message"]
    assert "22P02" not in f"{error['message']} {error['remedy']}"


def test_200_short_in_values_fit_in_one_request(tool_results):
    assert tool_results["longest_in"] == 0


def test_a_request_over_the_gateway_limit_asks_to_split_the_list(tool_results):
    error = tool_results["too_long"]
    assert error["code"] == "invalid_input"
    assert "Split" in error["remedy"]


def test_an_excluded_table_is_refused(tool_results):
    assert tool_results["excluded"] == "invalid_input"


# Species names carrying every character a filter must take literally.
_ODD_NAMES = {
    -9101: "TEST_BLOOMMCP Col*0",
    -9102: "TEST_BLOOMMCP Col%0",
    -9103: "TEST_BLOOMMCP Col_0",
    -9104: "TEST_BLOOMMCP ColA0",
    -9105: 'TEST_BLOOMMCP Col"0',
    -9106: "TEST_BLOOMMCP Col\\0",
    -9107: "TEST_BLOOMMCP Col,0 (x)",
}
_ODD_SCRIPT = f"""
import json
from bloom_mcp.sections.bloom_data.query_table import query_table

def names(op, value):
    rows = query_table({{"table": "species", "columns": ["common_name"],
        "filters": [{{"column": "common_name", "op": op, "value": value}}]}}).rows
    return sorted(r["common_name"] for r in rows)

odd = {list(_ODD_NAMES.values())!r}
out = {{"contains": {{t: names("contains", t) for t in
    ["col*0", "col%0", "col_0", 'col"0', "col\\\\0", "col,0 (x)"]}},
    "in": names("in", odd[4:]),
    "everything": names("contains", "TEST_BLOOMMCP Col")}}
print(json.dumps(out))
"""


@pytest.fixture
def species_with_odd_names(pg_conn):
    with pg_conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO species (id, common_name) VALUES (%s, %s)",
            list(_ODD_NAMES.items()),
        )
    pg_conn.commit()
    try:
        yield
    finally:
        with pg_conn.cursor() as cur:
            cur.execute("DELETE FROM species WHERE id = ANY(%s)", (list(_ODD_NAMES),))
        pg_conn.commit()


def test_filters_take_every_character_literally(species_with_odd_names):
    out = _run_in_bloommcp(_ODD_SCRIPT)
    names = list(_ODD_NAMES.values())
    assert out["everything"] == sorted(names)
    for text, found in out["contains"].items():
        assert found == [n for n in sorted(names) if text in n.lower()], text
    assert out["in"] == sorted(names[4:])


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
