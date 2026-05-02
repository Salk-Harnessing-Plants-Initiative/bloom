"""Deterministic context-loader node.

Runs at the start of every graph invocation, before any LLM call. Calls the
existing `get_agent_context` tool deterministically and injects the result as a
`SystemMessage` into state via the `add_messages` reducer.

Replaces the pre-model-hook reminder hack (a string instruction telling the
LLM to *"remember"* to call `get_agent_context`) with a graph-level guarantee:
every leaf subgraph in the routed architecture inherits this property without
re-implementing it.

Idempotency is enforced by tagging the injected SystemMessage with
`CONTEXT_MARKER`. On follow-up turns within the same thread, the node detects
the prior tagged message in state and returns `{}` — no duplicate injection.
"""
from typing import Optional

from langchain_core.messages import SystemMessage

from tools.context_tools import get_agent_context

# Marker substring distinguishing context-loader output from other SystemMessages
# (e.g. conversation summaries written by the pre-model hook).
CONTEXT_MARKER = "<!-- agent-context -->"


def make_context_loader_node(tool_set: str = "all"):
    """Build a context_loader node bound to a specific tool_set.

    The closure pattern keeps the node signature `(state) -> dict` while
    letting the parent graph build different agents for different tool_set
    selections without changing how the graph wires together.
    """

    def context_loader_node(state) -> dict:
        for msg in state.get("messages", []) or []:
            if isinstance(msg, SystemMessage) and CONTEXT_MARKER in str(msg.content):
                return {}

        context_text = get_agent_context.invoke({"tool_set": tool_set})
        marked_content = f"{CONTEXT_MARKER}\n{context_text}"
        return {"messages": [SystemMessage(content=marked_content)]}

    return context_loader_node
