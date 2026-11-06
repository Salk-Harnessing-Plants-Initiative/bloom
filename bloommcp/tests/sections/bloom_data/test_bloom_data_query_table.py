"""query_table: one allow-listed table, filtered, capped and paged."""

from __future__ import annotations

import pytest

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.sections.bloom_data.query_table import MAX_CELL_CHARS, query_table


def test_default_page_is_50_rows_with_a_next_offset(reader):
    result = query_table({"table": "species"})
    assert result.returned == 50
    assert result.has_more is True
    assert result.next_offset == 50
    assert result.rows[0]["id"] == 1


def test_last_page_has_no_next_offset(reader):
    result = query_table({"table": "species", "offset": 100, "limit": 50})
    assert result.returned == 20
    assert result.has_more is False
    assert result.next_offset is None


def test_limit_is_capped_at_500(reader):
    with pytest.raises(BloomMCPError) as err:
        query_table({"table": "species", "limit": 501})
    assert err.value.code == "invalid_input"


def test_filters_and_order(reader):
    result = query_table(
        {
            "table": "species",
            "columns": ["id"],
            "filters": [{"column": "id", "op": "lte", "value": 3}],
            "order_by": "id",
            "descending": True,
        }
    )
    assert result.rows == [{"id": 3}, {"id": 2}, {"id": 1}]


def test_contains_filter_matches_case_insensitively(reader):
    result = query_table(
        {
            "table": "species",
            "filters": [
                {"column": "common_name", "op": "contains", "value": "PLANT 11"}
            ],
        }
    )
    assert {row["id"] for row in result.rows} == {
        11,
        110,
        111,
        112,
        113,
        114,
        115,
        116,
        117,
        118,
        119,
    }


def test_email_is_never_returned(reader):
    result = query_table({"table": "people"})
    assert all("email" not in row for row in result.rows)
    assert "email" not in result.columns


@pytest.mark.parametrize(
    "params",
    [
        {"table": "people", "columns": ["email"]},
        {
            "table": "people",
            "filters": [{"column": "email", "op": "eq", "value": "ada@salk.edu"}],
        },
        {"table": "people", "order_by": "email"},
    ],
    ids=["select", "filter", "order"],
)
def test_a_hidden_column_cannot_be_selected_filtered_or_ordered(reader, params):
    with pytest.raises(BloomMCPError) as err:
        query_table(params)
    assert err.value.code == "tool_error"
    assert "email" in err.value.message
    assert reader.calls == []


def test_an_excluded_table_is_refused_before_any_read(reader):
    with pytest.raises(BloomMCPError) as err:
        query_table({"table": "checkpoints"})
    assert err.value.code == "tool_error"
    assert "list_tables" in err.value.remedy
    assert reader.calls == []


def test_an_unknown_column_is_refused(reader):
    with pytest.raises(BloomMCPError) as err:
        query_table({"table": "species", "columns": ["nope"]})
    assert "nope" in err.value.message


def test_a_timeout_asks_to_narrow_the_query(reader):
    reader.slow_tables.add("cyl_scan_traits")
    with pytest.raises(BloomMCPError) as err:
        query_table({"table": "cyl_scan_traits"})
    assert err.value.code == "tool_error"
    assert "filter" in err.value.message.lower()


def test_long_values_are_cut(reader):
    reader.tables["species"][0]["common_name"] = "x" * (MAX_CELL_CHARS + 50)
    result = query_table({"table": "species", "limit": 1})
    assert len(result.rows[0]["common_name"]) <= MAX_CELL_CHARS + 20
    assert result.truncated_values is True
