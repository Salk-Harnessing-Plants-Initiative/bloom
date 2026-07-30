"""`register()` applies per-tool usage recording (openspec
add-bloommcp-caller-identity design.md Decision 4) — verified through an
actual in-process FastMCP round-trip, not just by inspecting `register()`'s
implementation, so this proves the wrapping survives real tool registration
and invocation (preserved `__name__`/`__signature__`, etc.).

`as_mcp_tool` itself stays untouched and I/O-free — this exercises the outer
wrapper `register()` now applies, not a change to the contract decorator.
"""

from __future__ import annotations

import asyncio

from bloom_mcp.contract import as_mcp_tool, register

from .conftest import StubInput, StubOutput


def test_register_wraps_tools_with_usage_recording(fake_bloommcp_rpc):
    from fastmcp import FastMCP

    @as_mcp_tool(input_model=StubInput, output_model=StubOutput)
    def stub_tool(params: StubInput) -> StubOutput:
        return StubOutput(n_components=3)

    mcp = register(FastMCP("test"), stub_tool)

    async def _call():
        from fastmcp import Client

        async with Client(mcp) as client:
            return await client.call_tool(
                "stub_tool", {"params": {"experiment": "turface_19"}}
            )

    asyncio.run(_call())

    assert len(fake_bloommcp_rpc.calls) == 1
    function_name, params = fake_bloommcp_rpc.calls[0]
    assert function_name == "record_bloommcp_usage"
    assert params["p_action"] == "stub_tool"
    assert params["p_identity"] == "anonymous"
