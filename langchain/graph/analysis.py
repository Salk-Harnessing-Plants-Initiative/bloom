"""Analysis subgraph: second-level router + analysis_freeform fallback leaf.

Plugged in at the parent graph's "analysis" route destination. Mirrors the
top-router pattern at the second level: an LLM classifier writes
state["analysis_route"] into one of 6 sub-buckets and a conditional edge
dispatches to the matching leaf.

Until Tier 3 sub-proposals land specialized leaves (qc, stats, dimred_cluster,
viz, correlation), every analysis classification dispatches to
`analysis_freeform`. Wiring only — zero behavior change vs. PR #201's pre-this
state, since the parent graph routed "analysis" → "freeform" before, and now
routes "analysis" → "analysis_subgraph" → ... → analysis_freeform leaf, which
is functionally the same scope (all MCP tools) under a different prompt.

When Tier 3 leaves land, each replaces one destination value in the conditional
edge here. The router's contract never changes.
"""
import logging
from typing import Literal

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from graph.leaves.qc import build_qc_leaf
from graph.state import AgentState
from prompts.analysis_freeform import ANALYSIS_FREEFORM_PROMPT
from prompts.analysis_router import (
    ANALYSIS_ROUTER_FEW_SHOTS,
    ANALYSIS_ROUTER_PROMPT,
)

logger = logging.getLogger(__name__)


class AnalysisRouteDecision(BaseModel):
    """LLM-emitted second-level analysis classification."""

    route: Literal[
        "qc",
        "stats",
        "dimred_cluster",
        "viz",
        "correlation",
        "analysis_freeform",
    ] = Field(
        description=(
            "qc = data quality + cleanup + outlier detection; "
            "stats = descriptive stats, ANOVA, heritability; "
            "dimred_cluster = PCA, clustering, dimensionality reduction; "
            "viz = plots and visualizations; "
            "correlation = cross-experiment correlation; "
            "analysis_freeform = ambiguous or multi-bucket analysis requests."
        )
    )


ANALYSIS_FALLBACK_ROUTE: Literal["analysis_freeform"] = "analysis_freeform"

_VALID_ANALYSIS_ROUTES = (
    "qc",
    "stats",
    "dimred_cluster",
    "viz",
    "correlation",
    "analysis_freeform",
)


def _build_classifier_messages(user_text: str) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": ANALYSIS_ROUTER_PROMPT}]
    for example_user, example_route in ANALYSIS_ROUTER_FEW_SHOTS:
        messages.append({"role": "user", "content": example_user})
        messages.append({"role": "assistant", "content": example_route})
    messages.append({"role": "user", "content": user_text})
    return messages


def _latest_user_text(messages: list) -> str | None:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return None


def make_analysis_router_node(llm):
    """Build the analysis_router node bound to a specific LLM instance.

    Same closure pattern as `make_top_router_node` — keeps the node signature
    `(state) -> dict` while the LLM is captured at construction time. Three-way
    fallback to `analysis_freeform` on any error path.
    """
    classifier = llm.with_structured_output(AnalysisRouteDecision)

    async def analysis_router_node(state) -> dict:
        user_text = _latest_user_text(state.get("messages", []) or [])
        if user_text is None:
            logger.warning(
                "analysis_router: no HumanMessage in state, falling back to %s",
                ANALYSIS_FALLBACK_ROUTE,
            )
            return {"analysis_route": ANALYSIS_FALLBACK_ROUTE}

        try:
            messages = _build_classifier_messages(user_text)
            decision = await classifier.ainvoke(messages)
        except Exception as exc:
            logger.warning(
                "analysis_router: classifier raised %s, falling back to %s",
                type(exc).__name__,
                ANALYSIS_FALLBACK_ROUTE,
            )
            return {"analysis_route": ANALYSIS_FALLBACK_ROUTE}

        route = getattr(decision, "route", None)
        if route not in _VALID_ANALYSIS_ROUTES:
            logger.warning(
                "analysis_router: classifier returned unknown route %r, falling back to %s",
                route,
                ANALYSIS_FALLBACK_ROUTE,
            )
            return {"analysis_route": ANALYSIS_FALLBACK_ROUTE}

        return {"analysis_route": route}

    return analysis_router_node


def build_analysis_subgraph(llm, mcp_tools: list, pre_model_hook=None, post_model_hook=None):
    """Build the compiled analysis subgraph.

    Args:
        llm: Chat model used by both the analysis_router and the inner
            analysis_freeform leaf (single-provider strategy per master).
        mcp_tools: MCP tools the analysis_freeform leaf can invoke. The leaf
            sees only MCP tools (no native scrna/cyl/generic tools) — the top
            level `freeform` leaf is the catch-all for those.
        pre_model_hook: Optional pre-LLM-call hook (token trim / summarize /
            single-SystemMessage merge) applied to the inner ReAct loop.
        post_model_hook: Optional post-LLM-call hook. PR #208 wires the
            empty-AIMessage safety net here — same hook the freeform leaf
            uses, applied to every analysis leaf so silent termination is
            architecturally impossible regardless of which route runs.

    Topology inside the subgraph:

        START
          ↓
        analysis_router  (LLM call, writes state["analysis_route"])
          ↓
        conditional edge: state["analysis_route"] → leaf name
          ↓ (every value currently → "analysis_freeform")
        analysis_freeform  (ReAct loop, MCP tools only)
          ↓
        END

    The compiled subgraph is plugged in as a node in the parent graph; the
    parent's checkpointer owns thread state, so this subgraph runs with
    checkpointer=None (set implicitly by the caller in agent.py).
    """
    analysis_router = make_analysis_router_node(llm)

    # Inner ReAct loop with MCP-only tools and the analysis-focused prompt.
    # Catch-all for analysis requests that don't fit one of the specialized
    # Tier 3 leaves (and the destination for analysis_router's
    # `analysis_freeform` classification).
    analysis_freeform = create_react_agent(
        model=llm,
        tools=mcp_tools or [],
        prompt=ANALYSIS_FREEFORM_PROMPT,
        pre_model_hook=pre_model_hook,
        post_model_hook=post_model_hook,
        checkpointer=None,
    )

    # qc leaf — Tier 3 specialized leaf for data quality + outlier detection.
    # Sees only the 11 QC + outlier tools (plus list_existing_analyses) under
    # the QC_PROMPT, so the LLM reasons over a tight surface and the leaf's
    # workflow rules (check existing analyses first, surface source labels,
    # pick the right outlier method) anchor the decision-making.
    qc_leaf = build_qc_leaf(
        llm=llm,
        mcp_tools=mcp_tools or [],
        pre_model_hook=pre_model_hook,
        post_model_hook=post_model_hook,
    )

    builder = StateGraph(AgentState)
    builder.add_node("analysis_router", analysis_router)
    builder.add_node("analysis_freeform", analysis_freeform)
    builder.add_node("qc_leaf", qc_leaf)
    builder.add_edge(START, "analysis_router")
    builder.add_conditional_edges(
        "analysis_router",
        lambda state: state["analysis_route"],
        {
            "qc": "qc_leaf",
            "stats": "analysis_freeform",
            "dimred_cluster": "analysis_freeform",
            "viz": "analysis_freeform",
            "correlation": "analysis_freeform",
            "analysis_freeform": "analysis_freeform",
        },
    )
    builder.add_edge("qc_leaf", END)
    builder.add_edge("analysis_freeform", END)
    return builder.compile()
