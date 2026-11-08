"""The data tools tell MCP clients they only read."""

from __future__ import annotations

import asyncio

from bloom_mcp import server


def test_every_data_tool_is_marked_read_only():
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    names = [
        "bloom_data_list_tables",
        "bloom_data_query_table",
        "bloom_data_count_rows",
    ]
    for name in names:
        hints = tools[name].annotations
        assert hints is not None, name
        assert hints.readOnlyHint is True and hints.idempotentHint is True, name
        assert hints.openWorldHint is False, name
