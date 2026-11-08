"""Filter values that would give a silent wrong answer are refused before any read."""

from __future__ import annotations

import pytest

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.sections.bloom_data.count_rows import count_rows
from bloom_mcp.sections.bloom_data.query_table import query_table


def _filter(op, value, column="common_name"):
    return {
        "table": "species",
        "filters": [{"column": column, "op": op, "value": value}],
    }


@pytest.mark.parametrize("op", ["eq", "neq", "gt", "gte", "lt", "lte", "contains"])
@pytest.mark.parametrize("tool", [query_table, count_rows], ids=["query", "count"])
def test_null_is_refused_and_points_to_is_null(reader, tool, op):
    with pytest.raises(BloomMCPError) as err:
        tool(_filter(op, None))
    assert err.value.code == "invalid_input"
    assert "null" in err.value.message and "is_null" in err.value.remedy
    assert reader.calls == []


def test_a_null_inside_an_in_list_is_refused(reader):
    with pytest.raises(BloomMCPError) as err:
        query_table(_filter("in", ["plant 1", None]))
    assert err.value.code == "invalid_input"
    assert "is_null" in err.value.remedy


@pytest.mark.parametrize("value", [["plant 1"], {"a": 1}], ids=["list", "object"])
def test_a_comparison_takes_one_value(reader, value):
    with pytest.raises(BloomMCPError) as err:
        query_table(_filter("eq", value))
    assert err.value.code == "invalid_input"
    assert "one value" in err.value.message


def test_is_null_still_finds_empty_values(reader):
    rows = query_table(_filter("is_null", True, column="deleted_at")).rows
    assert len(rows) == 50


@pytest.mark.parametrize("value", ["plant 1", 3, 2.5, True])
def test_single_values_are_accepted(reader, value):
    query_table(_filter("eq", value))
