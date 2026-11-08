"""One usage line per data-tool call: what was read, never a filter's value."""

from __future__ import annotations

import logging

import pytest

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.sections.bloom_data._common import usage_log
from bloom_mcp.sections.bloom_data.count_rows import count_rows
from bloom_mcp.sections.bloom_data.list_tables import list_tables
from bloom_mcp.sections.bloom_data.query_table import query_table


class _Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def lines():
    handler = _Lines()
    usage_log.addHandler(handler)
    yield handler.lines
    usage_log.removeHandler(handler)


def test_a_read_logs_tool_table_filter_columns_rows_and_outcome(reader, lines):
    query_table(
        {
            "table": "species",
            "filters": [{"column": "common_name", "op": "eq", "value": "secret plant"}],
        }
    )
    assert len(lines) == 1
    line = lines[0]
    for part in (
        "tool=query_table",
        "table=species",
        "filters=common_name",
        "outcome=ok",
    ):
        assert part in line
    assert "rows=0" in line and "chars=" in line and "ms=" in line
    assert "secret plant" not in line


def test_each_tool_logs_one_line(reader, lines):
    list_tables({})
    count_rows({"table": "species"})
    assert [line.split()[1] for line in lines] == [
        "tool=list_tables",
        "tool=count_rows",
    ]
    assert "rows=120" in lines[1]


def test_a_refused_call_logs_its_error_code(reader, lines):
    with pytest.raises(BloomMCPError):
        query_table({"table": "checkpoints"})
    assert "outcome=invalid_input" in lines[0] and "rows=None" in lines[0]


def test_a_failed_read_logs_the_code_claude_got(reader, lines):
    reader.slow_tables.add("species")
    with pytest.raises(BloomMCPError):
        query_table({"table": "species"})
    assert "outcome=tool_error" in lines[0]


def test_the_usage_line_does_not_depend_on_the_root_logger():
    assert usage_log.propagate is False
    assert usage_log.isEnabledFor(logging.INFO)
    assert usage_log.handlers
