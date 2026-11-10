"""A migration that adds or drops a column doesn't break reads until the cache expires."""

from __future__ import annotations

import pytest

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.sections.bloom_data.count_rows import count_rows
from bloom_mcp.sections.bloom_data.query_table import query_table


def test_a_dropped_column_rereads_the_list_and_retries_once(reader):
    reader.changed_tables.add("species")
    result = query_table({"table": "species"})
    assert result.returned == 50
    assert reader.refreshes == 1
    assert reader.calls == ["species", "species"]


def test_a_dropped_column_in_a_count_is_retried_too(reader):
    reader.changed_tables.add("species")
    assert count_rows({"table": "species"}).count == 120


def test_when_the_list_was_just_reread_the_change_is_reported(reader):
    reader.changed_tables.add("species")
    reader.may_refresh = False
    with pytest.raises(BloomMCPError) as err:
        query_table({"table": "species"})
    assert err.value.code == "tool_error"
    assert "just changed" in err.value.message
    assert reader.calls == ["species"]


def test_a_new_column_is_found_after_one_reread(reader):
    for row in reader.tables["species"]:
        row["new_col"] = 1
    reader_columns = reader.table_columns

    stale = {"species": [c for c in reader_columns()["species"] if c.name != "new_col"]}
    reader.table_columns = lambda: stale if reader.refreshes == 0 else reader_columns()
    result = query_table({"table": "species", "columns": ["new_col"], "limit": 1})
    assert result.rows == [{"new_col": 1}]
    assert reader.refreshes == 1


def test_an_unknown_column_is_still_refused_after_the_reread(reader):
    with pytest.raises(BloomMCPError) as err:
        query_table({"table": "species", "columns": ["nope"]})
    assert "nope" in err.value.message
    assert reader.refreshes == 1


def test_a_new_table_is_found_after_one_reread(reader):
    rows = reader.tables.pop("genes")
    reader_columns = reader.table_columns

    def columns():
        if reader.refreshes:
            reader.tables["genes"] = rows
        return reader_columns()

    reader.table_columns = columns
    assert query_table({"table": "genes", "limit": 1}).returned == 1
