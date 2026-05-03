"""
Bloom LangChain Agent - Uses PostgREST (Supabase) for data queries
Supports multiple LLM providers: OpenAI, Local (vLLM)
"""
import os
import uuid
from typing import Optional, Literal

import httpx
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import (
    trim_messages,
    SystemMessage,
    AIMessage,
    HumanMessage,
    RemoveMessage,
)
from psycopg_pool import AsyncConnectionPool

import logging
from rich.logging import RichHandler
from rich.traceback import install
from db_url import compose_postgres_url
from graph.analysis import build_analysis_subgraph
from graph.context_loader import make_context_loader_node
from graph.freeform import build_freeform_subgraph
from graph.router import make_top_router_node
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
        return {"llm_input_messages": messages}
    return pre_model_hook


#################################################### Runtime Safety Net (Empty AIMessage Detection) ####################################################


# Recovery question templates for the runtime safety net. The post_model_hook
# uses these deterministic templates indexed by consecutive forced-clarification
# count. Three rounds before bail. We do NOT call an LLM to generate recovery
# text — recovery is the failure boundary; predictable text is more useful
# than generated text.
RECOVERY_QUESTIONS = (
    "I'm not sure how to proceed with that question. Could you tell me more "
    "about what you're looking for?",
    "I'm still having trouble. Could you give me more specifics — like the "
    "experiment name, trait name, or what kind of answer you're after?",
    "I'm not making progress on this. Could you rephrase the question, or "
    "break it into a smaller part?",
)

# Final terminal message after 3 forced clarifications without progress.
# The graph terminates normally with this as a content-only AIMessage.
FAILURE_MESSAGE = (
    "I'm having trouble processing this request. Could you try rephrasing it, "
    "or start a new conversation?"
)

# Bound on consecutive forced clarifications before the runtime gives up.
# Tunable via env if the production data suggests a different value.
MAX_FORCED_CLARIFICATIONS = int(os.getenv("MAX_FORCED_CLARIFICATIONS", "3"))

# Tool-call id prefix used to mark forced asks. Walk-back logic distinguishes
# forced asks (auto-emitted by the runtime) from LLM-driven asks (the LLM
# explicitly chose to ask). Only forced asks count toward the bound.
FORCED_ASK_ID_PREFIX = "forced-"


def _count_consecutive_forced_asks(messages) -> int:
    """Walk back through messages and count consecutive forced ask_user
    tool_calls since the last non-forced AIMessage.

    The chain breaks on:
      - any AIMessage with content (a normal final answer)
      - any AIMessage with tool_calls that are NOT all forced asks (the LLM
        chose to call a real tool, indicating recovery)

    The chain extends through forced-ask tool_calls and their corresponding
    ToolMessage results (the user's reply) — we count one bump per AIMessage
    that emitted a forced ask.

    Returns the count of consecutive forced asks. Used to index
    RECOVERY_QUESTIONS and to decide whether to bail.
    """
    count = 0
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            # Any normal AIMessage breaks the chain
            if msg.content:
                return count
            if not msg.tool_calls:
                # An empty AIMessage (the very one we're inspecting on the
                # current turn) doesn't extend the chain by itself; we extend
                # only when a forced ask was actually emitted.
                continue
            # Check if all tool_calls are forced (forced-* prefix)
            ids = [str(tc.get("id", "")) for tc in msg.tool_calls]
            all_forced = all(i.startswith(FORCED_ASK_ID_PREFIX) for i in ids)
            if not all_forced:
                # The LLM emitted a real tool_call → chain breaks
                return count
            count += 1
        # HumanMessage / ToolMessage / SystemMessage are part of the chain
        # only when they sit between forced-ask AIMessages; they don't bump
        # the count themselves. Continue walking back.
    return count


def make_post_model_hook():
    """Create a post-model hook that prevents silent termination.

    Guarantees: no leaf or react agent can produce a terminal AIMessage
    with empty content AND empty tool_calls. Every empty AIMessage gets
    replaced with one of two things:
      (a) a forced ask_user tool_call, if we haven't bailed yet, OR
      (b) a final AIMessage with FAILURE_MESSAGE, if we've already forced
          MAX_FORCED_CLARIFICATIONS consecutive asks.

    Forced asks are marked with tool_call.id prefix "forced-N-<uuid>" so
    the walk-back counter can distinguish them from LLM-driven asks. Only
    forced asks count toward the bound.

    The hook returns a partial state update with a RemoveMessage for the
    empty AIMessage and the replacement message. The add_messages reducer
    handles RemoveMessage by removing the matching id from state.
    """
    async def post_model_hook(state):
        messages = state.get("messages") or []
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage):
            return None
        if last.content or last.tool_calls:
            return None  # normal turn, leave alone

        # Empty AIMessage detected — apply the safety net.
        # Walk back over the message list to count consecutive forced asks
        # already in the history (excluding the empty one we just produced).
        forced_so_far = _count_consecutive_forced_asks(messages[:-1])

        if forced_so_far >= MAX_FORCED_CLARIFICATIONS:
            # Bound hit. Replace empty with a real failure message and let
            # the graph terminate normally (no tool_calls = END).
            replacement = AIMessage(content=FAILURE_MESSAGE)
        else:
            # Force a clarification with the next-indexed recovery question.
            question = RECOVERY_QUESTIONS[forced_so_far]
            forced_id = f"{FORCED_ASK_ID_PREFIX}{forced_so_far + 1}-{uuid.uuid4().hex[:8]}"
            replacement = AIMessage(
                content="",
                tool_calls=[{
                    "name": "ask_user",
                    "args": {"question": question},
                    "id": forced_id,
                }],
            )
        return {"messages": [RemoveMessage(id=last.id), replacement]}

    return post_model_hook


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
Call get_agent_context at the start of a conversation to learn about available data sources and schema.

# Handling uncertainty

When the user references something by everyday name (a trait, an
experiment, an accession, a species), and the schema-side identifier is
ambiguous or unknown, FIRST call the corresponding discovery tool to
translate or disambiguate:
  - For traits, call list_traits_tool to see schema names like
    'primary_length' before using them in compare_waves_trait_tool or
    similar.
  - For experiments, call list_experiments_tool when the user names an
    experiment and you don't know its id.
  - For species, call list_species_tool similarly.

Only after discovery has been attempted and ambiguity remains
unresolvable should you call ask_user. Discovery first, ask second.

# No silent termination

NEVER respond with empty content. If you cannot answer a question — for
any reason — call the ask_user tool with a specific, focused question
explaining what you need. The user prefers a visible follow-up question
to a blank reply.

If you've already asked for clarification 1-2 times without progress, the
runtime will surface a graceful failure message — but you should not rely
on that fallback. Make every clarification question count: be specific,
list the options you found via discovery, and ask the smallest possible
follow-up that lets you proceed."""


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
    # The freeform leaf wraps the prebuilt ReAct agent — same prompt, same
    # tools, same pre-model hook PLUS the post_model_hook from PR #208 which
    # detects empty AIMessages and forces an ask_user clarification (bounded
    # to MAX_FORCED_CLARIFICATIONS retries before a graceful failure). The
    # checkpointer lives on the OUTER graph; the inner subgraph stays
    # checkpointer=None so the parent's `messages` reducer is the single
    # source of truth for thread state.
    freeform = build_freeform_subgraph(
        llm=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        pre_model_hook=make_pre_model_hook(provider, llm=llm),
        post_model_hook=make_post_model_hook(),
        checkpointer=None,
    )

    # Deterministic context-loader runs before the ReAct loop on every
    # invocation. Replaces the prior reminder-injection branch in
    # make_pre_model_hook — the LLM no longer needs to be told to call
    # get_agent_context because the context is already in state.messages
    # by the time freeform runs. Closure over tool_set so the same parent
    # graph shape works for any tool subset selection.
    context_loader = make_context_loader_node(tool_set=tool_set)

    # Top-level router: classifies every request into one of four buckets
    # and writes state["route"]. Until Tier 2 subgraphs land, every route
    # value dispatches to the existing freeform leaf — wiring only, no
    # behaviour change for end users. The router uses the same LLM as the
    # leaf (single-provider strategy per master Decision 4).
    top_router = make_top_router_node(llm)

    # Analysis subgraph: second-level router + analysis_freeform fallback leaf.
    # Plugged in at the parent's "analysis" route destination. Inside the
    # subgraph, an LLM sub-classifier writes state["analysis_route"] into one
    # of 6 sub-buckets (qc, stats, dimred_cluster, viz, correlation,
    # analysis_freeform); every value currently dispatches to analysis_freeform
    # until Tier 3 sub-proposals land specialized leaves. The leaf sees only
    # MCP tools — native scrna/cyl/generic stay in the top-level freeform.
    analysis_subgraph = build_analysis_subgraph(
        llm=llm,
        mcp_tools=mcp_tools or [],
        pre_model_hook=make_pre_model_hook(provider, llm=llm),
        post_model_hook=make_post_model_hook(),
    )

    builder = StateGraph(AgentState)
    builder.add_node("context_loader", context_loader)
    builder.add_node("top_router", top_router)
    builder.add_node("analysis_subgraph", analysis_subgraph)
    builder.add_node("freeform", freeform)
    builder.add_edge(START, "context_loader")
    builder.add_edge("context_loader", "top_router")
    builder.add_conditional_edges(
        "top_router",
        lambda state: state["route"],
        {
            "phenotyping": "freeform",
            "scrna": "freeform",
            "analysis": "analysis_subgraph",
            "freeform": "freeform",
        },
    )
    builder.add_edge("analysis_subgraph", END)
    builder.add_edge("freeform", END)
    return builder.compile(checkpointer=checkpointer)
