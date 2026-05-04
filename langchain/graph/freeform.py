"""Freeform leaf subgraph factory.

The freeform leaf is the safety-valve subgraph that preserves today's
single-agent behavior. Every router (top-level and analysis-level)
can fall through to a freeform variant when classification is uncertain,
so a misclassification can never wedge the agent.

For Tier 0 (`add-stategraph-foundation`) the freeform leaf is the ONLY
node in the parent graph — every request flows `START → freeform → END`
and behaves byte-for-byte like the pre-refactor `create_react_agent` call.

The factory is a thin wrapper around LangGraph's prebuilt `create_react_agent`
so we keep the existing pre-model hook, system prompt, and tool list intact.
The compiled subgraph it returns is suitable for use as a node in a parent
StateGraph — see `langchain/agent.py::create_agent`.
"""
from typing import Optional

from langgraph.prebuilt import create_react_agent


def build_freeform_subgraph(
    llm,
    tools: list,
    system_prompt: str,
    pre_model_hook=None,
    post_model_hook=None,
    checkpointer=None,
):
    """Build the compiled freeform subgraph.

    Args:
        llm: Chat model (already provider/temperature-configured).
        tools: Tool callables the LLM can invoke, including any MCP tools.
        system_prompt: Top-level instructions string passed as `prompt=`.
        pre_model_hook: Optional pre-LLM-call hook (token trim / summarize).
        post_model_hook: Optional post-LLM-call hook. PR #208 wires the
            empty-AIMessage safety net here — detects empty content + no
            tool_calls, replaces with a forced `ask_user` clarification or a
            bounded-retries failure message. Without this hook, the leaf can
            silently terminate.
        checkpointer: Pass `None` here when the subgraph is used as a node
            inside a parent graph — the parent owns checkpointing in that
            case so the parent's `messages` field is the canonical history.
            Pass an `AsyncPostgresSaver` only if calling this subgraph
            standalone (not via the parent graph).

    Returns:
        A compiled subgraph (CompiledStateGraph) that exposes `ainvoke` and
        `astream_events` — drop-in for the previous `create_react_agent`
        return value.
    """
    # prompt=None on purpose. The pre_model_hook upstream owns the entire
    # SystemMessage block — it merges SYSTEM_PROMPT with any prior system
    # messages (e.g., from the context_loader node) into a single block at
    # index 0. Setting prompt=system_prompt here would cause
    # create_react_agent to ALSO prepend its own SystemMessage, producing
    # two consecutive system blocks which Qwen's chat template rejects.
    # `system_prompt` arg is preserved in the signature for backward
    # compatibility but is intentionally not forwarded to create_react_agent.
    _ = system_prompt  # noqa: F841 - retained for signature stability
    return create_react_agent(
        model=llm,
        tools=tools,
        prompt=None,
        pre_model_hook=pre_model_hook,
        post_model_hook=post_model_hook,
        checkpointer=checkpointer,
    )
