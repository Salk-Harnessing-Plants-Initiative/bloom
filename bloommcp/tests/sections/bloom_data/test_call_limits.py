"""One call stays light: offset, in-list length and reply size are bounded."""

from __future__ import annotations

import pytest

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.sections.bloom_data._common import MAX_IN_VALUES
from bloom_mcp.sections.bloom_data.count_rows import count_rows
from bloom_mcp.sections.bloom_data.query_table import (
    MAX_CELL_CHARS,
    MAX_OFFSET,
    MAX_REPLY_CHARS,
    query_table,
)


def test_an_offset_at_the_limit_is_read(reader):
    assert query_table({"table": "species", "offset": MAX_OFFSET}).returned == 0


def test_an_offset_past_the_limit_is_refused_before_any_read(reader):
    with pytest.raises(BloomMCPError) as err:
        query_table({"table": "species", "offset": MAX_OFFSET + 1})
    assert err.value.code == "invalid_input"
    # gte, not gt: the sort column can repeat, and gt would skip the rest of the last value.
    assert (
        "gte" in err.value.remedy
        and "drop the rows you already have" in err.value.remedy
    )
    assert reader.calls == []


@pytest.mark.parametrize("tool", [query_table, count_rows], ids=["query", "count"])
def test_an_in_list_past_the_limit_is_refused(reader, tool):
    values = list(range(MAX_IN_VALUES + 1))
    with pytest.raises(BloomMCPError) as err:
        tool(
            {
                "table": "species",
                "filters": [{"column": "id", "op": "in", "value": values}],
            }
        )
    assert err.value.code == "invalid_input"
    assert str(MAX_IN_VALUES) in err.value.remedy
    assert reader.calls == []


def test_an_in_list_at_the_limit_is_read(reader):
    values = list(range(1, MAX_IN_VALUES + 1))
    result = query_table(
        {"table": "species", "filters": [{"column": "id", "op": "in", "value": values}]}
    )
    assert result.returned == 50


def _wide_species(reader):
    for row in reader.tables["species"]:
        row["common_name"] = "x" * MAX_CELL_CHARS


def test_a_page_of_large_rows_stops_early_and_continues_without_gaps(reader):
    _wide_species(reader)
    first = query_table(
        {"table": "species", "columns": ["id", "common_name"], "limit": 500}
    )
    assert first.cut_short and first.has_more
    assert first.returned < 120
    assert sum(len(str(r)) for r in first.rows) <= MAX_REPLY_CHARS * 1.1
    second = query_table(
        {
            "table": "species",
            "columns": ["id", "common_name"],
            "limit": 500,
            "offset": first.next_offset,
        }
    )
    ids = [r["id"] for r in first.rows + second.rows]
    assert ids == list(range(1, len(ids) + 1))


def test_a_small_page_is_not_cut_short(reader):
    result = query_table({"table": "species", "limit": 500})
    assert result.cut_short is False
    assert result.returned == 120 and result.has_more is False


def test_one_row_larger_than_the_budget_is_still_returned(reader, monkeypatch):
    monkeypatch.setattr("bloom_mcp.sections.bloom_data._page.MAX_REPLY_CHARS", 10)
    result = query_table({"table": "species", "limit": 5})
    assert result.returned == 1 and result.cut_short and result.next_offset == 1


def test_the_descriptions_quote_the_limits_in_force():
    from bloom_mcp.sections.bloom_data.query_table import MAX_LIMIT, QueryTableParams

    assert f"at most {MAX_LIMIT} rows" in query_table.__doc__
    fields = QueryTableParams.model_fields
    assert f"max {MAX_LIMIT}" in fields["limit"].description
    assert f"max {MAX_OFFSET:,}" in fields["offset"].description


def test_the_description_names_the_cell_cut_and_nan_values():
    assert str(MAX_CELL_CHARS) in query_table.__doc__
    assert "NaN" in query_table.__doc__


def test_a_table_with_no_readable_columns_is_refused_not_read_whole(reader):
    reader.tables["species"] = [{"email": "a@b.org"}]
    reader.table_columns = lambda: {"species": []}
    with pytest.raises(BloomMCPError) as err:
        query_table({"table": "species"})
    assert "no columns" in err.value.message
    assert reader.calls == []


def test_a_count_needs_no_readable_column(reader):
    reader.table_columns = lambda: {"species": []}
    assert count_rows({"table": "species"}).count == 120


def test_emails_are_hidden_before_long_values_are_cut():
    from bloom_mcp.data_access import table_access
    from bloom_mcp.sections.bloom_data._page import MAX_CELL_CHARS, fit_page

    # The address straddles the cut, so cutting first would leave part of it.
    text = "x" * (MAX_CELL_CHARS - 8) + " ann.lee@salk.edu" + "y" * 50
    page = fit_page([{"status_logs": text}], 1, frozenset({"status_logs"}))
    value = page.rows[0]["status_logs"]
    assert "ann." not in value and "@salk" not in value
    assert table_access.EMAIL_HIDDEN[:8] in value or value.endswith("…[cut]")
