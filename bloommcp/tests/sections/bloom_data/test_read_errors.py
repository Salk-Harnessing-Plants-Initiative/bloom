"""A failed read tells Claude what to change, never the database's own error or code."""

from __future__ import annotations

import re

import pytest

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access.table_ports import (
    BadFilterValue,
    DatabaseUnavailable,
    TableReadError,
)
from bloom_mcp.sections.bloom_data.count_rows import count_rows
from bloom_mcp.sections.bloom_data.query_table import query_table

_CALLS = [
    lambda: query_table({"table": "species"}),
    lambda: count_rows({"table": "species"}),
]


@pytest.mark.parametrize("call", _CALLS, ids=["query_table", "count_rows"])
@pytest.mark.parametrize(
    ("raised", "code", "says"),
    [
        (BadFilterValue("22P02 detail"), "invalid_input", "doesn't fit its column"),
        (DatabaseUnavailable("ConnectError"), "tool_error", "couldn't be reached"),
        (TableReadError("42501 secret_x"), "tool_error", "couldn't run this read"),
    ],
    ids=["bad value", "unreachable", "refused"],
)
def test_a_failed_read_is_reported_plainly(reader, call, raised, code, says):
    reader.fail_with = raised
    with pytest.raises(BloomMCPError) as err:
        call()
    assert err.value.code == code
    assert says in err.value.message
    text = f"{err.value.message} {err.value.remedy}"
    assert not re.search(r"\b[0-9A-Z]{5}\b", text), "no database code"
    assert "secret_x" not in text and "ConnectError" not in text
