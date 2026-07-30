"""`bloom_mcp.usage.with_usage_recording` — per-tool usage recording.

Applied by `contract.wrap.register()` around each already-`@as_mcp_tool`-
wrapped callable (not inside `as_mcp_tool` itself — see
openspec/changes/add-bloommcp-caller-identity/design.md Decision 4). Records
one `bloommcp_usage` upsert per tool invocation, keyed on the tool's own name,
via `call_rpc` — faked here with `fake_bloommcp_rpc` (conftest.py) so no
network/Postgres is touched. A recording failure must never fail the
triggering tool call.
"""

from __future__ import annotations

import pytest

import bloom_mcp.identity as identity
from bloom_mcp.usage import with_usage_recording


@pytest.fixture
def set_current_identity():
    """Set `identity._current_identity` for the duration of a test, resetting
    afterward so it can't leak into a later test (ContextVar.set() persists
    across plain sequential calls in the same thread otherwise)."""
    tokens = []

    def _set(value):
        tokens.append(identity._current_identity.set(value))

    yield _set
    for token in reversed(tokens):
        identity._current_identity.reset(token)


def test_wrapped_tool_returns_the_same_result(fake_bloommcp_rpc):
    def qc_clean(params):
        return {"cleaned": True}

    wrapped = with_usage_recording(qc_clean)
    assert wrapped({"experiment": "turface_19"}) == {"cleaned": True}


def test_records_usage_with_tool_name_and_current_identity(
    fake_bloommcp_rpc, set_current_identity
):
    set_current_identity("11111111-1111-1111-1111-111111111111")

    def qc_clean(params):
        return {"cleaned": True}

    with_usage_recording(qc_clean)({"experiment": "turface_19"})

    assert fake_bloommcp_rpc.calls == [
        (
            "record_bloommcp_usage",
            {
                "p_identity": "11111111-1111-1111-1111-111111111111",
                "p_action": "qc_clean",
            },
        )
    ]


def test_records_usage_as_anonymous_when_no_identity_set(fake_bloommcp_rpc):
    def qc_clean(params):
        return {"cleaned": True}

    with_usage_recording(qc_clean)({"experiment": "turface_19"})

    assert fake_bloommcp_rpc.calls == [
        ("record_bloommcp_usage", {"p_identity": "anonymous", "p_action": "qc_clean"})
    ]


def test_records_usage_even_when_the_tool_raises(fake_bloommcp_rpc):
    def qc_clean(params):
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        with_usage_recording(qc_clean)({"experiment": "turface_19"})

    assert len(fake_bloommcp_rpc.calls) == 1
    assert fake_bloommcp_rpc.calls[0][0] == "record_bloommcp_usage"


def test_usage_recording_failure_does_not_fail_the_tool_call(monkeypatch):
    import bloom_mcp.supabase_client as sc

    def _boom(*_a, **_k):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(sc, "call_rpc", _boom)

    def qc_clean(params):
        return {"cleaned": True}

    # Must not raise, despite call_rpc raising internally.
    assert with_usage_recording(qc_clean)({"experiment": "turface_19"}) == {
        "cleaned": True
    }


def test_wrapper_preserves_name_and_signature_for_fastmcp_registration():
    """FastMCP introspects the wrapped callable's __name__/__signature__ to
    build its tool schema — this must survive the extra wrapping layer."""
    import inspect

    def qc_clean(params):
        return {"cleaned": True}

    qc_clean.__signature__ = inspect.Signature(
        [inspect.Parameter("params", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    )

    wrapped = with_usage_recording(qc_clean)
    assert wrapped.__name__ == "qc_clean"
    assert wrapped.__signature__ == qc_clean.__signature__
