"""qc_leaf — Tier 3 specialized analysis leaf for QC + outlier detection.

Verifies the leaf's tool-filtering contract: only QC + outlier tools (plus
foundational MCP tools) reach the leaf. Non-QC analysis tools (stats, dimred,
viz, correlation) are excluded so the LLM reasons over a tight surface.

Tests run against the leaf factory and helpers directly. The compiled
ReAct subgraph itself is exercised via the prebuilt `create_react_agent`
machinery in upstream LangGraph; we don't re-test that here.
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_LANGCHAIN_DIR = Path(__file__).resolve().parents[2] / "langchain"
if str(_LANGCHAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_LANGCHAIN_DIR))

_TMP = tempfile.mkdtemp(prefix="qc_leaf_test_")
os.environ.setdefault("BLOOM_TRAITS_DIR", _TMP)
os.environ.setdefault("BLOOM_OUTPUT_DIR", _TMP)
os.environ.setdefault("BLOOM_PLOTS_DIR", _TMP)
os.environ.setdefault("BLOOM_PLOTS_URL", "http://test.invalid")
os.environ.setdefault("FRONTEND_URL", "http://test.invalid")
os.environ.setdefault("SUPABASE_URL", "http://test.invalid")
os.environ.setdefault("BLOOM_AGENT_KEY", "test-token-not-real")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key-not-real")
os.environ.setdefault("NEXT_PUBLIC_APP_URL", "http://test.invalid")

from graph.leaves.qc import (  # noqa: E402
    QC_LEAF_TOOL_NAMES,
    build_qc_leaf,
    filter_qc_tools,
)


def _mock_tool(name: str):
    """Build a stub object with a `.name` attribute mimicking a LangChain tool."""
    tool = MagicMock()
    tool.name = name
    return tool


def _mock_llm():
    """Bare-minimum mock that satisfies create_react_agent's bind path."""
    llm = MagicMock()
    # create_react_agent calls llm.bind_tools or llm.with_config; return self.
    llm.bind_tools = MagicMock(return_value=llm)
    llm.with_config = MagicMock(return_value=llm)
    return llm


# ─── Tool filtering ──────────────────────────────────────────────────────────


def test_filter_qc_tools_includes_all_expected_tool_names():
    """Every QC + outlier + foundational tool name in the registry passes through."""
    fake_tools = [_mock_tool(n) for n in QC_LEAF_TOOL_NAMES]
    filtered = filter_qc_tools(fake_tools)
    assert {t.name for t in filtered} == QC_LEAF_TOOL_NAMES


def test_filter_qc_tools_excludes_non_qc_analysis_tools():
    """stats / dimred / clustering / viz / correlation tools are filtered out."""
    out_of_scope = [
        "get_trait_statistics",
        "run_anova_by_genotype",
        "calculate_heritability",
        "run_pca",
        "get_pca_feature_contributions",
        "run_kmeans_clustering",
        "plot_trait_histograms",
        "plot_correlation_heatmap",
        "run_cross_experiment_correlations",
    ]
    in_scope = list(QC_LEAF_TOOL_NAMES)
    fake_tools = [_mock_tool(n) for n in out_of_scope + in_scope]

    filtered = filter_qc_tools(fake_tools)

    # Only in-scope tools come through
    assert {t.name for t in filtered} == set(in_scope)
    # Nothing from out-of-scope leaks
    for name in out_of_scope:
        assert name not in {t.name for t in filtered}


def test_filter_qc_tools_handles_empty_input():
    assert filter_qc_tools([]) == []
    assert filter_qc_tools(None) == []


def test_filter_qc_tools_handles_unknown_tool_names_gracefully():
    """Tool names not in the QC registry are dropped without error."""
    fake_tools = [_mock_tool("totally_made_up_tool"), _mock_tool("inspect_data_quality")]
    filtered = filter_qc_tools(fake_tools)
    assert {t.name for t in filtered} == {"inspect_data_quality"}


# ─── Foundational tools — always present ─────────────────────────────────────


def test_qc_leaf_tool_set_includes_all_foundational_tools():
    """The four ALWAYS_INCLUDE_MCP_TOOLS must be in the QC tool set so a fresh
    chat session can self-orient even before any QC-specific tool runs."""
    foundational = {
        "list_available_experiments",
        "load_experiment_data",
        "inspect_data_quality",
        "list_existing_analyses",
    }
    assert foundational.issubset(QC_LEAF_TOOL_NAMES)


# ─── Leaf factory ────────────────────────────────────────────────────────────


def test_build_qc_leaf_returns_a_compiled_subgraph():
    """The factory returns the result of create_react_agent (a compiled subgraph)."""
    llm = _mock_llm()
    fake_mcp_tools = [_mock_tool(n) for n in QC_LEAF_TOOL_NAMES]

    with patch("graph.leaves.qc.create_react_agent") as mock_react:
        mock_react.return_value = MagicMock(name="compiled_subgraph")
        leaf = build_qc_leaf(llm, fake_mcp_tools)

    assert leaf is not None
    assert mock_react.called


def test_build_qc_leaf_filters_unrelated_tools_before_passing_to_react_agent():
    """The factory passes the FILTERED tool list to create_react_agent — out-of-scope
    tools never reach the prebuilt ReAct loop."""
    llm = _mock_llm()
    fake_mcp_tools = [
        _mock_tool("clean_experiment_data"),       # QC, in-scope
        _mock_tool("detect_outliers_pca"),         # outlier, in-scope
        _mock_tool("run_pca"),                      # dimred, OUT-of-scope
        _mock_tool("plot_correlation_heatmap"),     # viz, OUT-of-scope
        _mock_tool("list_existing_analyses"),       # foundational, in-scope
    ]

    with patch("graph.leaves.qc.create_react_agent") as mock_react:
        mock_react.return_value = MagicMock()
        build_qc_leaf(llm, fake_mcp_tools)

    # Inspect the tools= kwarg passed to create_react_agent
    call_kwargs = mock_react.call_args.kwargs
    passed_tools = call_kwargs["tools"]
    passed_names = {t.name for t in passed_tools}

    assert passed_names == {
        "clean_experiment_data",
        "detect_outliers_pca",
        "list_existing_analyses",
    }
    assert "run_pca" not in passed_names
    assert "plot_correlation_heatmap" not in passed_names


def test_build_qc_leaf_handles_empty_mcp_tools_without_error():
    """Defensive: leaf still compiles even if no MCP tools are loaded."""
    llm = _mock_llm()
    with patch("graph.leaves.qc.create_react_agent") as mock_react:
        mock_react.return_value = MagicMock()
        leaf = build_qc_leaf(llm, [])
    assert leaf is not None


# ─── Tool count sanity check ─────────────────────────────────────────────────


def test_qc_leaf_tool_set_has_expected_count():
    """The proposal targets ~11 specialized tools (6 qc + 5 outlier) plus
    foundational. Today's count: 11 (foundational already overlap with qc_tools)."""
    # 6 qc_tools + 5 outlier_tools = 11 (list_existing_analyses is added once)
    assert len(QC_LEAF_TOOL_NAMES) == 12  # 11 + list_existing_analyses
