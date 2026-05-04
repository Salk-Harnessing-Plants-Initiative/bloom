"""top_router_node — LLM-driven request classifier with freeform fallback.

These tests exercise the router node directly with a mocked LLM (no live
network, no compose stack). They verify the contract:

  - Success: LLM returns a valid Literal value → state["route"] = that value.
  - LLM exception → fallback to "freeform" + log a warning.
  - LLM returns an out-of-enum value → fallback to "freeform".
  - Structured-output parse failure → fallback to "freeform".

Per the "Freeform fallback at every router level" master decision, a
classification error MUST never wedge the request — it always degrades to
the freeform leaf so the agent keeps responding.
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_LANGCHAIN_DIR = Path(__file__).resolve().parents[2] / "langchain"
if str(_LANGCHAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_LANGCHAIN_DIR))

_TMP = tempfile.mkdtemp(prefix="top_router_test_")
os.environ.setdefault("BLOOM_TRAITS_DIR", _TMP)
os.environ.setdefault("BLOOM_OUTPUT_DIR", _TMP)
os.environ.setdefault("BLOOM_PLOTS_DIR", _TMP)
os.environ.setdefault("BLOOM_PLOTS_URL", "http://test.invalid")
os.environ.setdefault("FRONTEND_URL", "http://test.invalid")
os.environ.setdefault("SUPABASE_URL", "http://test.invalid")
os.environ.setdefault("BLOOM_AGENT_KEY", "test-token-not-real")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key-not-real")
os.environ.setdefault("NEXT_PUBLIC_APP_URL", "http://test.invalid")

from langchain_core.messages import HumanMessage  # noqa: E402

from graph.router import (  # noqa: E402
    FALLBACK_ROUTE,
    TopRouteDecision,
    make_top_router_node,
)


@pytest.fixture
def anyio_backend():
    """Pin async tests to asyncio only — trio isn't a project dependency."""
    return "asyncio"


def _mock_llm_returning(value):
    """Build a mock chat model where with_structured_output returns a chain
    whose ainvoke yields the given value."""
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=value)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    return llm


def _mock_llm_raising(exc: Exception):
    """Build a mock chat model whose structured-output ainvoke raises exc."""
    structured = MagicMock()
    structured.ainvoke = AsyncMock(side_effect=exc)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    return llm


# ─── Success path ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_router_success_phenotyping():
    decision = TopRouteDecision(route="phenotyping")
    llm = _mock_llm_returning(decision)
    node = make_top_router_node(llm)

    result = await node({"messages": [HumanMessage(content="list cylinder plants from wave 2")]})

    assert result == {"route": "phenotyping"}


@pytest.mark.anyio
async def test_router_success_analysis():
    decision = TopRouteDecision(route="analysis")
    llm = _mock_llm_returning(decision)
    node = make_top_router_node(llm)

    result = await node({"messages": [HumanMessage(content="run a QC report on alfalfa")]})

    assert result == {"route": "analysis"}


@pytest.mark.anyio
async def test_router_uses_most_recent_user_message():
    """Router should classify based on the latest HumanMessage, not the first."""
    decision = TopRouteDecision(route="scrna")
    llm = _mock_llm_returning(decision)
    node = make_top_router_node(llm)

    state = {
        "messages": [
            HumanMessage(content="something earlier and unrelated"),
            HumanMessage(content="show me the UMAP for dataset X"),
        ]
    }
    result = await node(state)

    assert result == {"route": "scrna"}


# ─── Fallback paths — every error degrades to freeform ───────────────────────


@pytest.mark.anyio
async def test_router_falls_back_on_llm_exception():
    """Network failure or LLM crash should not wedge the request."""
    llm = _mock_llm_raising(RuntimeError("vLLM unreachable"))
    node = make_top_router_node(llm)

    result = await node({"messages": [HumanMessage(content="anything")]})

    assert result == {"route": FALLBACK_ROUTE}
    assert FALLBACK_ROUTE == "freeform"


@pytest.mark.anyio
async def test_router_falls_back_on_parse_error():
    """If the LLM's structured output can't be parsed (validation error
    from the wrapping layer), fall back to freeform."""
    from pydantic import ValidationError as _VE
    # Construct a real pydantic ValidationError by intentionally validating
    # an out-of-enum value, so the exception path matches what production
    # would see when with_structured_output retries fail.
    try:
        TopRouteDecision(route="not_a_real_route")
    except _VE as exc:
        validation_error = exc

    llm = _mock_llm_raising(validation_error)
    node = make_top_router_node(llm)

    result = await node({"messages": [HumanMessage(content="ambiguous prompt")]})

    assert result == {"route": FALLBACK_ROUTE}


@pytest.mark.anyio
async def test_router_falls_back_when_no_user_message():
    """Defensive: state with no HumanMessage shouldn't crash; route to freeform."""
    llm = _mock_llm_returning(TopRouteDecision(route="phenotyping"))
    node = make_top_router_node(llm)

    result = await node({"messages": []})

    # Empty input should not even reach the LLM; freeform fallback.
    assert result == {"route": FALLBACK_ROUTE}


# ─── Determinism ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_router_node_signature_returns_only_route_field():
    """The router updates only state['route'], nothing else."""
    decision = TopRouteDecision(route="freeform")
    llm = _mock_llm_returning(decision)
    node = make_top_router_node(llm)

    result = await node({"messages": [HumanMessage(content="hi")]})

    assert set(result.keys()) == {"route"}
