"""@as_mcp_tool maps a raised exception to a structured BloomMCPError.

Maps the spec "Structured Agent-Safe Errors" scenario: a declared exception
becomes a BloomMCPError (code + message + remedy), never a raw traceback.
"""

from __future__ import annotations

import pytest

from bloom_mcp.contract import BloomMCPError, as_mcp_tool

from .conftest import StubInput, StubOutput


class _ToolFailure(Exception):
    """A declared tool-side failure."""


def test_declared_exception_becomes_structured_error():
    """A raised exception surfaces as a complete BloomMCPError."""

    @as_mcp_tool(input_model=StubInput, output_model=StubOutput, errors=(_ToolFailure,))
    def stub_tool(params: StubInput) -> StubOutput:
        raise _ToolFailure("upstream blew up")

    with pytest.raises(BloomMCPError) as exc:
        stub_tool({"experiment": "turface_19"})

    err = exc.value
    assert err.code
    assert err.message
    assert err.remedy
    # The structured form is serializable and leaks no traceback.
    payload = err.to_dict()
    assert set(payload) >= {"code", "message", "remedy"}
    assert "Traceback" not in payload["message"]
