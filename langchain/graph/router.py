"""Top-level router node — LLM-driven request classifier.

Calls the LLM with structured-output coercion to one of four bucket values
and writes `state["route"]`. Never wedges the request: any classification
error (LLM exception, parse failure, missing user message) degrades to the
`freeform` fallback so subsequent edges can still dispatch the request.

The router uses the same provider/model as the leaf (single-provider
strategy per the master proposal's Decision 4). The closure pattern lets
`create_agent` build the node once with its already-configured LLM, then
hand the resulting node to the StateGraph.
"""
import logging
from typing import Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from prompts.router import ROUTER_FEW_SHOTS, TOP_ROUTER_PROMPT

logger = logging.getLogger(__name__)


# The classification target. Pydantic model rather than bare Literal so
# `with_structured_output` accepts it across all backends and the LLM gets
# field-level metadata (description) to anchor on.
class TopRouteDecision(BaseModel):
    """LLM-emitted top-level route classification."""

    route: Literal["phenotyping", "scrna", "analysis", "freeform"] = Field(
        description=(
            "phenotyping = cylinder/turface scans + plant traits; "
            "scrna = single-cell RNA-seq; "
            "analysis = run a numerical analysis (QC, stats, PCA, viz); "
            "freeform = anything ambiguous or multi-domain."
        )
    )


FALLBACK_ROUTE: Literal["freeform"] = "freeform"


def _build_classifier_messages(user_text: str) -> list[dict]:
    """Compose the system prompt + few-shot examples + the new user request."""
    messages: list[dict] = [{"role": "system", "content": TOP_ROUTER_PROMPT}]
    for example_user, example_route in ROUTER_FEW_SHOTS:
        messages.append({"role": "user", "content": example_user})
        messages.append({"role": "assistant", "content": example_route})
    messages.append({"role": "user", "content": user_text})
    return messages


def _latest_user_text(messages: list) -> str | None:
    """Return the content of the most recent HumanMessage, or None."""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return None


def make_top_router_node(llm):
    """Build the top_router node bound to a specific LLM instance.

    Closure over `llm` so the node signature stays `(state) -> dict` and
    the parent graph wires it without threading config.
    """
    classifier = llm.with_structured_output(TopRouteDecision)

    async def top_router_node(state) -> dict:
        user_text = _latest_user_text(state.get("messages", []) or [])
        if user_text is None:
            logger.warning("top_router: no HumanMessage in state, falling back to %s", FALLBACK_ROUTE)
            return {"route": FALLBACK_ROUTE}

        try:
            messages = _build_classifier_messages(user_text)
            decision = await classifier.ainvoke(
                messages, config={"tags": ["router_internal"]}
            )
        except Exception as exc:
            logger.warning(
                "top_router: classifier raised %s, falling back to %s",
                type(exc).__name__,
                FALLBACK_ROUTE,
            )
            return {"route": FALLBACK_ROUTE}

        route = getattr(decision, "route", None)
        if route not in ("phenotyping", "scrna", "analysis", "freeform"):
            logger.warning(
                "top_router: classifier returned unknown route %r, falling back to %s",
                route,
                FALLBACK_ROUTE,
            )
            return {"route": FALLBACK_ROUTE}

        return {"route": route}

    return top_router_node
