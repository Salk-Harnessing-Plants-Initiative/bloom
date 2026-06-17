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


def test_undeclared_exception_does_not_leak_internal_detail():
    """An undeclared exception's detail goes to logs, not the agent message."""

    @as_mcp_tool(input_model=StubInput, output_model=StubOutput)
    def stub_tool(params: StubInput) -> StubOutput:
        raise RuntimeError("postgres://user:pw@db.internal/secret bucket=private")

    with pytest.raises(BloomMCPError) as exc:
        stub_tool({"experiment": "turface_19"})

    err = exc.value
    assert err.code == "internal_error"
    assert "postgres://" not in err.message
    assert "secret" not in err.message
    assert "ref:" in err.message  # correlation id for server-side lookup


def test_input_validation_does_not_leak_offending_values():
    """Input-validation errors surface field locations + types, never values."""

    @as_mcp_tool(input_model=StubInput, output_model=StubOutput)
    def stub_tool(params: StubInput) -> StubOutput:
        return StubOutput(n_components=3)

    with pytest.raises(BloomMCPError) as exc:
        stub_tool({"experiment": "turface_19", "seed": "leak-me-not"})

    err = exc.value
    assert err.code == "invalid_input"
    assert "leak-me-not" not in err.message
    assert "seed" in err.message  # the field location is named
