"""count_rows: exact on small tables, a labelled planner estimate on large ones."""

from __future__ import annotations

import pytest

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.sections.bloom_data.count_rows import count_rows


def test_a_small_table_is_counted_exactly(reader):
    result = count_rows({"table": "species"})
    assert (result.count, result.approximate) == (120, False)
    assert ("species", True) in reader.counts


def test_a_large_table_gets_the_planner_estimate(reader):
    result = count_rows({"table": "cyl_scan_traits"})
    assert result.approximate is True
    assert result.count == 31_800_000
    assert ("cyl_scan_traits", True) not in reader.counts
    assert "estimate" in result.note


def test_a_filtered_large_table_is_still_approximate(reader):
    result = count_rows(
        {
            "table": "cyl_scan_traits",
            "filters": [{"column": "scan_id", "op": "eq", "value": 10}],
        }
    )
    assert result.approximate is True


def test_a_never_analyzed_table_is_estimated_not_counted(reader):
    reader.estimates["species"] = None
    result = count_rows({"table": "species"})
    assert result.approximate is True
    assert ("species", True) not in reader.counts


def test_an_exact_count_that_times_out_falls_back_to_the_estimate(reader):
    reader.slow_tables.add("species")
    result = count_rows({"table": "species"})
    assert result.approximate is True


def test_an_excluded_table_is_refused(reader):
    with pytest.raises(BloomMCPError) as err:
        count_rows({"table": "chat_threads"})
    assert err.value.code == "tool_error"
