"""@as_mcp_tool: Pydantic I/O validation + the register(mcp) seam.

Maps the spec "Uniform Tool Contract Decorator" scenarios: a decorated stub
validates I/O and is discoverable once registered; invalid input is rejected
before the body runs; invalid output is an internal contract breach. All run
with no live Supabase.
"""

from __future__ import annotations

import asyncio

import pytest

from bloom_mcp.contract import BloomMCPError, as_mcp_tool, register

from .conftest import StubInput, StubOutput


def test_valid_input_returns_validated_output_and_is_registrable():
    """Valid input returns the output object; the tool lists via the register seam."""
    from fastmcp import FastMCP

    @as_mcp_tool(input_model=StubInput, output_model=StubOutput)
    def stub_tool(params: StubInput) -> StubOutput:
        return StubOutput(n_components=3)

    out = stub_tool({"experiment": "turface_19"})
    assert isinstance(out, StubOutput)
    assert out.n_components == 3

    mcp = register(FastMCP("test"), stub_tool)

    async def _names():
        from fastmcp import Client

        async with Client(mcp) as client:
            return {t.name for t in await client.list_tools()}

    assert "stub_tool" in asyncio.run(_names())


def test_params_accepted_positionally_and_by_keyword():
    """The advertised signature matches: `params` works positionally and by keyword."""

    @as_mcp_tool(input_model=StubInput, output_model=StubOutput)
    def stub_tool(params: StubInput) -> StubOutput:
        return StubOutput(n_components=len(params.experiment))

    by_dict = stub_tool({"experiment": "abc"})
    by_keyword = stub_tool(params={"experiment": "abc"})
    by_model = stub_tool(params=StubInput(experiment="abc"))

    assert by_dict.n_components == by_keyword.n_components == by_model.n_components == 3


def test_invalid_input_rejected_before_body_runs(recorder):
    """Input violating the model surfaces as BloomMCPError; body never runs."""

    @as_mcp_tool(input_model=StubInput, output_model=StubOutput)
    def stub_tool(params: StubInput) -> StubOutput:
        recorder["body_ran"] = True
        return StubOutput(n_components=3)

    with pytest.raises(BloomMCPError) as exc:
        stub_tool({"seed": 7})  # missing required `experiment`

    assert exc.value.code == "invalid_input"
    assert "body_ran" not in recorder


def test_invalid_output_is_internal_contract_breach():
    """Output violating the model surfaces as a coded internal BloomMCPError."""

    @as_mcp_tool(input_model=StubInput, output_model=StubOutput)
    def stub_tool(params: StubInput):
        return {"n_components": "not-an-int"}

    with pytest.raises(BloomMCPError) as exc:
        stub_tool({"experiment": "turface_19"})

    assert exc.value.code == "internal_output_contract"
