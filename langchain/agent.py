"""
Bloom LangChain Agent - Uses PostgREST (Supabase) for data queries
Supports multiple LLM providers: OpenAI, Local (vLLM)
"""
import os
from typing import Optional, Literal

import httpx
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import trim_messages, SystemMessage, AIMessage, HumanMessage
from psycopg_pool import AsyncConnectionPool

import logging
from rich.logging import RichHandler
from rich.traceback import install
from db_url import compose_postgres_url
from graph.context_loader import make_context_loader_node
from graph.freeform import build_freeform_subgraph
from graph.state import AgentState
from tools import all_tools, generic_tools, scrna_tools, cyl_tools, context_tools

install()
logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(message)s",
    handlers=[RichHandler()]
)

#################################################### Agent Setup ####################################################

# Postgres connection URL for persistent conversation memory.
# Built from individual POSTGRES_* env vars — same code path in dev, CI,
# and prod so a bug in the composition never hides in only one environment.
# Password is percent-encoded inside compose_postgres_url so characters
# with URL-reserved meanings (@, :, /, #, %) can't corrupt the URL.
POSTGRES_URL = compose_postgres_url()


async def setup_checkpointer() -> AsyncPostgresSaver:
    """Create and initialize the PostgresSaver checkpointer.

    Opens an async connection pool to Postgres and auto-creates the
    checkpoint tables (checkpoints, checkpoint_blobs, checkpoint_writes)
    if they don't exist yet. Returns the checkpointer instance.

    Note: setup() uses CREATE INDEX CONCURRENTLY which cannot run inside
    a transaction block. We use a separate autocommit connection for the
    one-time table creation, then the pool for runtime operations.
    """
    from psycopg import AsyncConnection

    pool = AsyncConnectionPool(
        conninfo=POSTGRES_URL,
        min_size=2,
        max_size=10,
        open=False,
    )
    await pool.open()

    # Setup requires autocommit (CREATE INDEX CONCURRENTLY can't run in a transaction)
    async with await AsyncConnection.connect(POSTGRES_URL, autocommit=True) as conn:
        setup_cp = AsyncPostgresSaver(conn)
        await setup_cp.setup()

    checkpointer = AsyncPostgresSaver(pool)
    logger = logging.getLogger(__name__)
    # Read POSTGRES_HOST directly from the environment for logging. Deriving
    # the host from POSTGRES_URL would pass through a value that carries the
    # password upstream, and CodeQL's taint tracker doesn't recognize
    # urlsplit().hostname as a sanitizer for py/clear-text-logging-sensitive-data.
    host = os.environ.get("POSTGRES_HOST", "<unknown>")
    logger.info(f"PostgresSaver initialized with pool (min=2, max=10) → host={host}")
    return checkpointer


#################################################### Context Management ####################################################


def _count_tokens(messages) -> int:
    """Approximate token count (~4 chars per token)."""
    return sum(len(str(m.content)) for m in messages) // 4


SUMMARIZATION_PROMPT = """Summarize the following conversation between a user and an AI assistant on a plant biology platform.

RULES:
- Preserve ALL gene IDs (e.g., AT1G01010), dataset IDs, species names, and numeric results
- Preserve key findings (GO terms, interaction partners, expression values, trait measurements)
- Preserve user decisions and preferences
- Keep it concise — aim for 200-400 words
- Use bullet points grouped by topic

CONVERSATION:
{conversation}

SUMMARY:"""

# used for summarization when the LLM call fails
def _extract_summary(messages: list) -> str:
    """Fallback: extract key items from messages without an LLM call."""
    key_items = []
    for msg in messages:
        content = str(msg.content)
        if isinstance(msg, HumanMessage):
            key_items.append(f"User asked: {content[:500]}")
        elif isinstance(msg, AIMessage):
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    key_items.append(f"Called {tc.get('name', 'tool')}({str(tc.get('args', ''))[:100]})")
            elif content:
                key_items.append(f"Assistant: {content[:500]}")
    return "Conversation summary:\n" + "\n".join(f"- {item}" for item in key_items)


async def _llm_summarize(messages: list, llm) -> str:
    """Summarize messages using the active LLM. Falls back to extraction on failure."""
    logger = logging.getLogger(__name__)
    try:
        # Build conversation text from messages
        lines = []
        for msg in messages:
            content = str(msg.content)[:500]
            if isinstance(msg, HumanMessage):
                lines.append(f"User: {content}")
            elif isinstance(msg, AIMessage):
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        lines.append(f"Assistant [tool call]: {tc.get('name', 'tool')}({str(tc.get('args', ''))[:200]})")
                if content:
                    lines.append(f"Assistant: {content}")

        conversation_text = "\n".join(lines)
        prompt = SUMMARIZATION_PROMPT.format(conversation=conversation_text)

        response = await llm.ainvoke(prompt)
        summary = response.content.strip()
        logger.info(f"LLM summarization complete ({len(summary)} chars)")
        return f"Conversation summary:\n{summary}"
    except Exception as e:
        logger.warning(f"LLM summarization failed ({e}), falling back to extraction")
        return _extract_summary(messages)


# Token thresholds by provider
TOKEN_THRESHOLDS = {
    "local": {"summarize_at": 6000, "keep_recent": 4000},
    "openai": {"summarize_at": 100000, "keep_recent": 50000},
}


def make_pre_model_hook(provider: str = "openai", llm=None):
    """Create a pre-model hook with LLM summarization and context injection.

    Returns an async function that:
    1. Checks if get_agent_context was called; if not, injects a reminder
    2. When messages exceed the token threshold, uses the LLM to summarize old messages
    3. Returns trimmed messages for the LLM
    """
    # uses openai 100K threshold as default if not set
    thresholds = TOKEN_THRESHOLDS.get(provider, TOKEN_THRESHOLDS["openai"])

    async def pre_model_hook(state):
        messages = state["messages"]
        # Reminder injection removed — `graph.context_loader.context_loader_node`
        # injects the agent context deterministically as a SystemMessage at the
        # start of every graph invocation, so the LLM no longer needs a hint to
        # remember to call get_agent_context.
        total_tokens = _count_tokens(messages)
        if total_tokens > thresholds["summarize_at"]:
            # Find the split point: keep the last N tokens of messages
            keep_tokens = thresholds["keep_recent"]
            recent = []
            running = 0
            for msg in reversed(messages):
                msg_tokens = len(str(msg.content)) // 4
                if running + msg_tokens > keep_tokens:
                    break
                recent.insert(0, msg)
                running += msg_tokens
            # Summarize the old messages using LLM (with extraction fallback)
            old_messages = messages[:len(messages) - len(recent)]
            if old_messages:
                if llm:
                    summary_text = await _llm_summarize(old_messages, llm)
                else:
                    summary_text = _extract_summary(old_messages)
                summary_msg = SystemMessage(content=summary_text)
                messages = [summary_msg] + recent
        else:
            # Simple trim as fallback
            messages = trim_messages(
                messages,
                strategy="last",
                max_tokens=thresholds["summarize_at"],
                token_counter=lambda msgs: sum(len(str(m.content)) for m in msgs) // 4,
                include_system=True,
                allow_partial=False,
                start_on="human",
            )
        # Coerce every SystemMessage into a single merged block at the start,
        # ALWAYS led by SYSTEM_PROMPT.
        #
        # Why we own the system block here:
        #   - vLLM with Qwen's chat template only allows ONE system block at
        #     index 0; any second SystemMessage triggers "System message must
        #     be at the beginning."
        #   - create_react_agent normally prepends its own SystemMessage from
        #     the `prompt=...` argument, which collides with whatever this
        #     hook produces (resulting in two consecutive system blocks).
        #   - To keep the contract single-source, we pass `prompt=None` to
        #     create_react_agent and always emit exactly one merged
        #     SystemMessage here. Every line of SYSTEM_PROMPT plus any
        #     reminders or summaries are preserved by joining with blank
        #     lines so no instruction is silently dropped.
        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        non_system_msgs = [m for m in messages if not isinstance(m, SystemMessage)]
        parts = [SYSTEM_PROMPT]
        for m in system_msgs:
            content = str(m.content) if m.content else ""
            if content and content not in parts:  # dedupe identical blocks
                parts.append(content)
        merged_content = "\n\n".join(parts)
        messages = [SystemMessage(content=merged_content)] + non_system_msgs
        return {"llm_input_messages": messages}
    return pre_model_hook


#################################################### LLM Setup ####################################################


# Local LLM configuration (OpenAI-compatible endpoint, e.g., vLLM)
LOCAL_LLM_URL = os.getenv("LOCAL_LLM_URL")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL")


def _detect_vllm_model() -> str:
    """Auto-detect model name from vLLM /v1/models endpoint."""
    if LOCAL_LLM_MODEL:
        return LOCAL_LLM_MODEL
    if not LOCAL_LLM_URL:
        raise RuntimeError("LOCAL_LLM_URL environment variable is required")
    try:
        response = httpx.get(f"{LOCAL_LLM_URL}/models", timeout=5)
        response.raise_for_status()
        models = response.json().get("data", [])
        if models:
            model_name = models[0].get("id", "")
            logger.info(f"Auto-detected vLLM model: {model_name}")
            return model_name
    except Exception as e:
        logger.warning(f"Failed to auto-detect vLLM model: {e}")
    raise RuntimeError("Could not detect model. Set LOCAL_LLM_MODEL env var.")


_cached_model = None

def get_local_model() -> str:
    """Get local model name — from env, auto-detect, or cache."""
    global _cached_model
    if _cached_model:
        return _cached_model
    _cached_model = _detect_vllm_model()
    return _cached_model

AVAILABLE_MODELS = {
    "local": [get_local_model()],
}


def get_llm(
    provider: Literal["openai", "local"] = "openai",
    model: Optional[str] = None,
):
    """
    Factory function to create LLM instance based on provider.
    API keys are read from server-side environment variables only.

    Args:
        provider: "openai" or "local"
        model: Model name (defaults to first model in AVAILABLE_MODELS for provider)

    Returns:
        LLM instance configured for the specified provider
    """
    if not model:
        model = get_local_model()

    if provider == "local":
        if not LOCAL_LLM_URL:
            raise ValueError("LOCAL_LLM_URL environment variable required for local LLM provider")
        return ChatOpenAI(
            model=model,
            base_url=LOCAL_LLM_URL,
            api_key="not-needed",
            temperature=0.0,
            request_timeout=300,
            max_retries=1,
        )

    else:  # Default to OpenAI
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise ValueError("OPENAI_API_KEY environment variable required for OpenAI provider")
        return ChatOpenAI(
            model=model,
            api_key=key,
            temperature=0.0,
        )


#################################################### Agent Creation ####################################################


SYSTEM_PROMPT = """You are a helpful assistant for the Bloom plant phenotyping platform.
You have READ-ONLY access. You cannot create, update, or delete data.
Call get_agent_context at the start of a conversation to learn about available data sources and schema."""


def create_agent(
    provider: str = "openai",
    model: Optional[str] = None,
    tool_set: str = "all",
    mcp_tools: list = None,
    checkpointer=None,
):
    """
    Create the LangChain agent with specified LLM provider and tool set.

    Args:
        provider: "openai" or "local"
        model: Model name
        tool_set: Which tools to include:
            - "all": All tools (context + generic + scrna + cyl)
            - "scrna": Only scRNA-seq tools
            - "cyl": Only cylinder phenotyping tools
            - "generic": Only generic database tools
        mcp_tools: Tools loaded from MCP servers (bloommcp, external MCP servers)
        checkpointer: AsyncPostgresSaver instance for persistent conversation memory
    """
    llm = get_llm(provider=provider, model=model)

    # Select tools based on tool_set (context_tools always included)
    if tool_set == "scrna":
        tools = context_tools + generic_tools + scrna_tools
    elif tool_set == "cyl":
        tools = context_tools + generic_tools + cyl_tools
    elif tool_set == "generic":
        tools = context_tools + generic_tools
    else:  # "all"
        tools = context_tools + all_tools

    # Append MCP tools if provided
    if mcp_tools:
        tools = tools + mcp_tools

    # Tier 0 of the upgrade-agent-architecture roadmap: replace the opaque
    # `create_react_agent` return with an explicit StateGraph so every
    # subsequent tier (top router, domain subgraphs, analysis router, MCP
    # leaves, parallel recipes) has a real graph to attach nodes to.
    #
    # The graph has one node, `freeform`, which wraps the prebuilt ReAct
    # agent. The checkpointer lives on the OUTER graph; the inner subgraph
    # stays checkpointer=None so the parent's `messages` reducer is the
    # single source of truth for thread state.
    #
    # system_prompt=None on purpose: the pre_model_hook owns the entire
    # SystemMessage block (it merges SYSTEM_PROMPT, the get_agent_context
    # reminder, and any summary into a single SystemMessage at index 0).
    # Setting system_prompt=SYSTEM_PROMPT here would cause create_react_agent
    # to ALSO prepend its own SystemMessage, producing two consecutive
    # system blocks which Qwen's chat template rejects.
    freeform = build_freeform_subgraph(
        llm=llm,
        tools=tools,
        system_prompt=None,
        pre_model_hook=make_pre_model_hook(provider, llm=llm),
        checkpointer=None,
    )

    # Deterministic context-loader runs before the ReAct loop on every
    # invocation. Replaces the prior reminder-injection branch in
    # make_pre_model_hook — the LLM no longer needs to be told to call
    # get_agent_context because the context is already in state.messages
    # by the time freeform runs. Closure over tool_set so the same parent
    # graph shape works for any tool subset selection.
    context_loader = make_context_loader_node(tool_set=tool_set)

    builder = StateGraph(AgentState)
    builder.add_node("context_loader", context_loader)
    builder.add_node("freeform", freeform)
    builder.add_edge(START, "context_loader")
    builder.add_edge("context_loader", "freeform")
    builder.add_edge("freeform", END)
    return builder.compile(checkpointer=checkpointer)
