"""Live check that the indexes bloommcp's sort and contains rules rely on still exist.

bloommcp lets Claude sort only by columns that lead an index, and use contains on a large
table only where a trigram index is; a dropped index would make those reads scan the table.
Unit counterpart: bloommcp/tests/sections/bloom_data/test_table_indexes.py.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

TABLE_INDEXES = (
    Path(__file__).resolve().parents[2]
    / "bloommcp/src/bloom_mcp/data_access/table_indexes.py"
)


def _table_indexes():
    spec = importlib.util.spec_from_file_location("table_indexes", TABLE_INDEXES)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _leading_columns(pg_conn, where: str) -> dict[str, set[str]]:
    with pg_conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT c.relname, a.attname
              FROM pg_index i
              JOIN pg_class c ON c.oid = i.indrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace
              JOIN pg_class ic ON ic.oid = i.indexrelid
              JOIN pg_am am ON am.oid = ic.relam
              JOIN pg_opclass oc ON oc.oid = i.indclass[0]
              JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = i.indkey[0]
             WHERE n.nspname = 'public' AND {where}
            """
        )
        found: dict[str, set[str]] = {}
        for table, column in cur.fetchall():
            found.setdefault(table, set()).add(column)
        return found


def test_every_sortable_column_leads_a_btree_index(pg_conn):
    indexed = _leading_columns(
        pg_conn, "am.amname = 'btree' AND i.indkey[0] <> 0 AND i.indpred IS NULL"
    )
    missing = {
        table: sorted(columns - indexed.get(table, set()))
        for table, columns in _table_indexes().SORTABLE_COLUMNS.items()
        if columns - indexed.get(table, set())
    }
    assert not missing, f"listed as sortable but no index leads with them: {missing}"


def test_every_contains_column_has_a_trigram_index(pg_conn):
    indexed = _leading_columns(pg_conn, "oc.opcname LIKE '%trgm%'")
    missing = {
        table: sorted(columns - indexed.get(table, set()))
        for table, columns in _table_indexes().CONTAINS_COLUMNS.items()
        if columns - indexed.get(table, set())
    }
    assert not missing, f"listed for contains but no trigram index: {missing}"
