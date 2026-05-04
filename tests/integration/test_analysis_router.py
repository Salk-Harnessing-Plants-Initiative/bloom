"""analysis_router_node — second-level classifier with analysis_freeform fallback.

Mirrors the top-level router pattern but scoped to the 6 analysis sub-buckets.
Tests run against the node directly with a mocked LLM (no live network, no
compose stack). Per master Decision: every classification error degrades to
analysis_freeform so the request keeps moving.
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

_TMP = tempfile.mkdtemp(prefix="analysis_router_test_")
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

from graph.analysis import (  # noqa: E402
    ANALYSIS_FALLBACK_ROUTE,
    AnalysisRouteDecision,
    make_analysis_router_node,
)


@pytest.fixture
def anyio_backend():
    """Pin async tests to asyncio only — trio isn't a project dependency."""
    return "asyncio"


def _mock_llm_returning(value):
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=value)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    return llm


def _mock_llm_raising(exc: Exception):
    structured = MagicMock()
    structured.ainvoke = AsyncMock(side_effect=exc)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    return llm


# ─── Success path — one test per real bucket ────────────────────────────────


@pytest.mark.anyio
async def test_router_success_qc():
    llm = _mock_llm_returning(AnalysisRouteDecision(route="qc"))
    node = make_analysis_router_node(llm)
    result = await node({"messages": [HumanMessage(content="run a QC report on alfalfa")]})
    assert result == {"analysis_route": "qc"}


@pytest.mark.anyio
async def test_router_success_stats():
    llm = _mock_llm_returning(AnalysisRouteDecision(route="stats"))
    node = make_analysis_router_node(llm)
    result = await node({"messages": [HumanMessage(content="calculate heritability")]})
    assert result == {"analysis_route": "stats"}


@pytest.mark.anyio
async def test_router_success_dimred_cluster():
    llm = _mock_llm_returning(AnalysisRouteDecision(route="dimred_cluster"))
    node = make_analysis_router_node(llm)
    result = await node({"messages": [HumanMessage(content="run PCA on the wave 2 traits")]})
    assert result == {"analysis_route": "dimred_cluster"}


@pytest.mark.anyio
async def test_router_success_viz():
    llm = _mock_llm_returning(AnalysisRouteDecision(route="viz"))
    node = make_analysis_router_node(llm)
    result = await node({"messages": [HumanMessage(content="plot a histogram of root_length")]})
    assert result == {"analysis_route": "viz"}


@pytest.mark.anyio
async def test_router_success_correlation():
    llm = _mock_llm_returning(AnalysisRouteDecision(route="correlation"))
    node = make_analysis_router_node(llm)
    result = await node({"messages": [HumanMessage(content="correlate traits across waves")]})
    assert result == {"analysis_route": "correlation"}


@pytest.mark.anyio
async def test_router_uses_most_recent_user_message():
    llm = _mock_llm_returning(AnalysisRouteDecision(route="viz"))
    node = make_analysis_router_node(llm)
    state = {
        "messages": [
            HumanMessage(content="something earlier"),
            HumanMessage(content="now make a heatmap"),
        ]
    }
    result = await node(state)
    assert result == {"analysis_route": "viz"}


# ─── Fallback paths — every error degrades to analysis_freeform ─────────────


@pytest.mark.anyio
async def test_router_falls_back_on_llm_exception():
    llm = _mock_llm_raising(RuntimeError("vLLM unreachable"))
    node = make_analysis_router_node(llm)
    result = await node({"messages": [HumanMessage(content="anything")]})
    assert result == {"analysis_route": ANALYSIS_FALLBACK_ROUTE}
    assert ANALYSIS_FALLBACK_ROUTE == "analysis_freeform"


@pytest.mark.anyio
async def test_router_falls_back_on_validation_error():
    from pydantic import ValidationError as _VE
    try:
        AnalysisRouteDecision(route="not_a_real_bucket")
    except _VE as exc:
        validation_error = exc

    llm = _mock_llm_raising(validation_error)
    node = make_analysis_router_node(llm)
    result = await node({"messages": [HumanMessage(content="ambiguous")]})
    assert result == {"analysis_route": ANALYSIS_FALLBACK_ROUTE}


@pytest.mark.anyio
async def test_router_falls_back_when_no_user_message():
    llm = _mock_llm_returning(AnalysisRouteDecision(route="qc"))
    node = make_analysis_router_node(llm)
    result = await node({"messages": []})
    assert result == {"analysis_route": ANALYSIS_FALLBACK_ROUTE}


# ─── Determinism + hygiene ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_router_node_returns_only_analysis_route_field():
    """The node updates only state['analysis_route'], not state['route']
    or state['messages'] — no clobbering of parent-graph state."""
    llm = _mock_llm_returning(AnalysisRouteDecision(route="analysis_freeform"))
    node = make_analysis_router_node(llm)
    result = await node({"messages": [HumanMessage(content="hi")]})
    assert set(result.keys()) == {"analysis_route"}


@pytest.mark.anyio
async def test_router_handles_explicit_analysis_freeform_response():
    """If the LLM itself decides analysis_freeform (not via fallback), pass it through."""
    llm = _mock_llm_returning(AnalysisRouteDecision(route="analysis_freeform"))
    node = make_analysis_router_node(llm)
    result = await node({"messages": [HumanMessage(content="explore the data freely")]})
    assert result == {"analysis_route": "analysis_freeform"}
