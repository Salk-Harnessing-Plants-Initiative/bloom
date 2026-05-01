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
    checkpointer=None,
):
    """Build the compiled freeform subgraph.

    Args:
        llm: Chat model (already provider/temperature-configured).
        tools: Tool callables the LLM can invoke, including any MCP tools.
        system_prompt: Top-level instructions string passed as `prompt=`.
        pre_model_hook: Optional pre-LLM-call hook (token trim / summarize).
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
    # IMPORTANT: prompt=None on purpose, even though `system_prompt` is in our
    # function signature. The pre_model_hook (in langchain/agent.py) owns the
    # entire SystemMessage block — it merges SYSTEM_PROMPT plus any reminders
    # and summaries into exactly one SystemMessage at index 0. Passing
    # prompt=system_prompt here would cause create_react_agent to ALSO prepend
    # its own SystemMessage, producing two consecutive system blocks which
    # Qwen's chat template rejects ("System message must be at the beginning").
    # We keep `system_prompt` in the signature so the caller's intent is
    # documented at the call site, but it's intentionally unused here.
    _ = system_prompt
    return create_react_agent(
        model=llm,
        tools=tools,
        prompt=None,
        pre_model_hook=pre_model_hook,
        checkpointer=checkpointer,
    )
