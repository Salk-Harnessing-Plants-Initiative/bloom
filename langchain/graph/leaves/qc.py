"""QC leaf — Tier 3 specialized analysis leaf for data quality + outlier detection.

Provides:
  - QC_LEAF_TOOL_NAMES: the explicit set of tool names the leaf exposes (12 total:
    6 qc_tools + 5 outlier_tools + list_existing_analyses)
  - filter_qc_tools(mcp_tools): filter the full MCP tool list down to the QC subset
  - build_qc_leaf(llm, mcp_tools, pre_model_hook): factory returning a compiled
    ReAct subgraph with the narrowed tool surface and the QC prompt

Plugged into the analysis subgraph at the "qc" destination (replaces the
placeholder edge `"qc" -> "analysis_freeform"` from PR #202).
"""
from langgraph.prebuilt import create_react_agent

from prompts.qc import QC_PROMPT


# The exact tool surface the QC leaf exposes. Hardcoded by name rather than
# heuristic-matched against bloommcp module structure so:
#   1. The leaf's surface is auditable from one place
#   2. Reordering bloommcp modules doesn't accidentally expand or contract scope
#   3. Tests can assert the set without depending on a live MCP catalog
QC_LEAF_TOOL_NAMES = frozenset(
    {
        # qc_tools (6) — discovery + inspection + cleanup
        "list_available_experiments",
        "inspect_experiment_columns",
        "load_experiment_data",
        "inspect_data_quality",
        "clean_experiment_data",
        "list_trait_columns",
        # outlier_tools (5) — detection + removal
        "detect_outliers_mahalanobis",
        "detect_outliers_isolation_forest",
        "detect_outliers_pca",
        "run_consensus_outlier_detection",
        "remove_detected_outliers",
        # storage introspection (1) — required for the prompt's
        # "check existing analyses first" rule
        "list_existing_analyses",
    }
)


def filter_qc_tools(mcp_tools):
    """Return only the MCP tools whose names are in the QC leaf's surface.

    Tools missing from the input list are silently dropped — the leaf works
    with whatever subset of QC tools the agent actually loaded. Unknown tool
    names in the input list are also dropped (defensive against drift).
    """
    if not mcp_tools:
        return []
    return [t for t in mcp_tools if getattr(t, "name", None) in QC_LEAF_TOOL_NAMES]


def build_qc_leaf(llm, mcp_tools, pre_model_hook=None):
    """Build the compiled QC leaf — a ReAct loop over the narrowed tool surface.

    Args:
        llm: Chat model instance, same as the leaf's parent (single-provider
            strategy per master Decision 4).
        mcp_tools: The full MCP tool list. Filtered to QC + outlier internally.
        pre_model_hook: Optional pre-LLM-call hook (token trim / summarize /
            single-SystemMessage merge) applied to every LLM call in the loop.

    Returns:
        A compiled subgraph (CompiledStateGraph) suitable for use as a node
        in the analysis subgraph. checkpointer=None — the parent graph in
        agent.py owns thread state.
    """
    qc_tools = filter_qc_tools(mcp_tools)
    return create_react_agent(
        model=llm,
        tools=qc_tools,
        prompt=QC_PROMPT,
        pre_model_hook=pre_model_hook,
        checkpointer=None,
    )
