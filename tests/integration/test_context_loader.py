"""context_loader_node — deterministic agent-context injection before any LLM call.

These tests exercise the context-loader node directly (no LLM, no compose stack).
They verify the contract:
  - First call on a fresh state injects a marked SystemMessage.
  - Subsequent calls within the same thread are no-ops (idempotent).
  - The closure honours the bound tool_set.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

_LANGCHAIN_DIR = Path(__file__).resolve().parents[2] / "langchain"
if str(_LANGCHAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_LANGCHAIN_DIR))

# Some langchain modules read env vars at import time; provide safe defaults.
_TMP = tempfile.mkdtemp(prefix="context_loader_test_")
os.environ.setdefault("BLOOM_TRAITS_DIR", _TMP)
os.environ.setdefault("BLOOM_OUTPUT_DIR", _TMP)
os.environ.setdefault("BLOOM_PLOTS_DIR", _TMP)
os.environ.setdefault("BLOOM_PLOTS_URL", "http://test.invalid")
os.environ.setdefault("FRONTEND_URL", "http://test.invalid")
os.environ.setdefault("SUPABASE_URL", "http://test.invalid")
os.environ.setdefault("BLOOM_AGENT_KEY", "test-token-not-real")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key-not-real")
os.environ.setdefault("NEXT_PUBLIC_APP_URL", "http://test.invalid")

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from graph.context_loader import (  # noqa: E402
    CONTEXT_MARKER,
    make_context_loader_node,
)


def test_context_loader_injects_system_message_on_fresh_state():
    node = make_context_loader_node(tool_set="all")
    state = {"messages": [HumanMessage(content="hi")]}

    result = node(state)

    assert "messages" in result
    assert len(result["messages"]) == 1
    msg = result["messages"][0]
    assert isinstance(msg, SystemMessage)
    assert CONTEXT_MARKER in msg.content
    # The actual context content from get_agent_context is a non-empty string
    assert len(msg.content) > len(CONTEXT_MARKER) + 10


def test_context_loader_is_idempotent_on_second_call():
    """If a marked SystemMessage already exists in state, return {} (no-op)."""
    node = make_context_loader_node(tool_set="all")
    state_after_first = {
        "messages": [
            HumanMessage(content="hi"),
            SystemMessage(content=f"{CONTEXT_MARKER}\nsome cached context"),
        ]
    }
    result = node(state_after_first)

    # No new messages appended — node detected prior context-flagged message
    assert result == {}


def test_context_loader_appends_when_only_unmarked_system_message_exists():
    """A SystemMessage WITHOUT the marker (e.g. summary) doesn't count as
    prior context — node still injects."""
    node = make_context_loader_node(tool_set="all")
    state = {
        "messages": [
            SystemMessage(content="Conversation summary so far: ..."),
            HumanMessage(content="follow-up"),
        ]
    }
    result = node(state)

    assert "messages" in result
    assert isinstance(result["messages"][0], SystemMessage)
    assert CONTEXT_MARKER in result["messages"][0].content


def test_context_loader_respects_bound_tool_set():
    """Different tool_set bindings produce different context content."""
    all_node = make_context_loader_node(tool_set="all")
    scrna_node = make_context_loader_node(tool_set="scrna")

    state = {"messages": [HumanMessage(content="hi")]}
    all_result = all_node(state)
    scrna_result = scrna_node(state)

    all_content = all_result["messages"][0].content
    scrna_content = scrna_result["messages"][0].content

    # Tool sets differ — content must differ. Both contain the marker.
    assert CONTEXT_MARKER in all_content
    assert CONTEXT_MARKER in scrna_content
    assert all_content != scrna_content


def test_context_loader_handles_empty_state():
    """No messages in state: still inject the marker SystemMessage."""
    node = make_context_loader_node(tool_set="all")
    result = node({"messages": []})

    assert "messages" in result
    assert len(result["messages"]) == 1
    assert CONTEXT_MARKER in result["messages"][0].content
