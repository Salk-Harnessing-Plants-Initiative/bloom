"""count_rows: exact unless too many rows match; an estimate is always labelled."""

from __future__ import annotations

import pytest

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.sections.bloom_data.count_rows import (
    EXACT_NOTE,
    TIMED_OUT_NOTE,
    TOO_MANY_NOTE,
    count_rows,
)


def test_a_small_table_is_counted_exactly(reader):
    result = count_rows({"table": "species"})
    assert (result.count, result.approximate) == (120, False)
    assert result.note == EXACT_NOTE


def test_a_whole_large_table_gets_the_estimate_and_is_never_counted(reader):
    result = count_rows({"table": "cyl_scan_traits_source"})
    assert (result.count, result.approximate) == (31_800_000, True)
    assert ("cyl_scan_traits_source", True) not in reader.counts
    assert result.note == TOO_MANY_NOTE


def test_a_filtered_count_on_a_large_table_is_exact(reader):
    result = count_rows(
        {
            "table": "cyl_scan_traits_source",
            "filters": [{"column": "scan_id", "op": "eq", "value": 99999}],
        }
    )
    assert (result.count, result.approximate) == (0, False)


def test_a_view_is_counted_exactly(reader):
    result = count_rows({"table": "cyl_scans_extended"})
    assert (result.count, result.approximate) == (5, False)


def test_a_filter_matching_too_many_rows_gets_the_estimate(reader):
    reader.filtered_guess = 500_000
    result = count_rows(
        {
            "table": "cyl_scan_traits_source",
            "filters": [{"column": "value", "op": "gt", "value": 0}],
        }
    )
    assert (result.count, result.approximate) == (500_000, True)
    assert result.note == TOO_MANY_NOTE


def test_an_exact_count_that_times_out_falls_back_to_the_estimate(reader):
    reader.slow_tables.add("species")
    result = count_rows({"table": "species"})
    assert (result.count, result.approximate) == (120, True)
    assert result.note == TIMED_OUT_NOTE


def test_every_estimate_tells_claude_to_say_so():
    for note in (TOO_MANY_NOTE, TIMED_OUT_NOTE):
        assert "estimate" in note.lower() and "tell the user" in note.lower()


def test_an_excluded_table_is_refused(reader):
    with pytest.raises(BloomMCPError) as err:
        count_rows({"table": "chat_threads"})
    assert err.value.code == "tool_error"


def test_log_columns_cannot_be_filtered_in_a_count(reader):
    with pytest.raises(BloomMCPError) as err:
        count_rows(
            {
                "table": "gene_candidates",
                "filters": [{"column": "status_logs", "op": "contains", "value": "@"}],
            }
        )
    assert err.value.code == "tool_error"
