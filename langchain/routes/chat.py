"""Chat endpoints: synchronous and SSE-streaming variants share the same agent
factory, validation, and thread-metadata upsert."""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage

import deps
from agent import AVAILABLE_MODELS
from schemas import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# Foundational MCP tools are always exposed so the agent can discover and load data.
ALWAYS_INCLUDE_MCP_TOOLS = {
    "list_available_experiments",
    "load_experiment_data",
    "inspect_data_quality",
}

VALID_TOOL_SETS = ["all", "scrna", "cyl", "generic"]


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _resolve_agent(body: ChatRequest, checkpointer) -> tuple[object, str, str]:
    """Validate the request and resolve a cached agent.

    Returns (agent, provider, model). Raises ValueError on invalid input so the
    sync endpoint can map it to HTTP 400 and the streaming endpoint can map it
    to a yielded `error` event.
    """
    provider = body.provider.lower()
    model = body.model
    if provider not in AVAILABLE_MODELS:
        raise ValueError(f"Unknown provider: {provider}. Choose from: openai, local")
    if not model:
        model = AVAILABLE_MODELS[provider][0]

    tool_set = body.tool_set.lower() if body.tool_set else "all"
    if tool_set not in VALID_TOOL_SETS:
        raise ValueError(f"Unknown tool_set: {tool_set}. Choose from: {VALID_TOOL_SETS}")

    if body.mcp_tool_names:
        selected = set(body.mcp_tool_names) | ALWAYS_INCLUDE_MCP_TOOLS
        filtered = [t for t in deps.mcp_tools if t.name in selected]
    else:
        filtered = [t for t in deps.mcp_tools if t.name in ALWAYS_INCLUDE_MCP_TOOLS]

    agent = deps.get_or_create_agent(
        provider=provider,
        model=model,
        tool_set=tool_set,
        filtered_mcp_tools=filtered,
        checkpointer=checkpointer,
    )
    return agent, provider, model


async def _upsert_thread_metadata(checkpointer, user_id: str, thread_id: str, prompt: str) -> None:
    """Best-effort write to chat_threads. Logs warnings, never raises."""
    if not checkpointer or thread_id == "default":
        return
    try:
        title = prompt[:100].strip()
        if len(prompt) > 100:
            title += "..."
        async with checkpointer.conn.connection() as conn:
            await conn.execute(
                """
                INSERT INTO chat_threads (user_id, thread_id, title)
                VALUES (%s::uuid, %s, %s)
                ON CONFLICT (user_id, thread_id)
                DO UPDATE SET
                    updated_at = now(),
                    title = COALESCE(chat_threads.title, EXCLUDED.title)
                """,
                (user_id, thread_id, title),
            )
    except Exception as e:
        logger.warning(f"Failed to upsert thread metadata: {e}")


# ─── Synchronous chat ─────────────────────────────────────────────────────────

@router.post("/langchain/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    request: Request,
    user_id: str = Depends(deps.get_current_user),
):
    """Process a chat message through the LangChain agent. Requires authentication."""
    try:
        checkpointer = getattr(request.app.state, 'checkpointer', None)
        agent, provider, model = _resolve_agent(body, checkpointer)
        scoped_thread = f"{user_id}:{body.thread_id}"

        response = await agent.ainvoke(
            {"messages": [("user", body.prompt)]},
            config={"configurable": {"thread_id": scoped_thread}},
        )
        messages = response["messages"]

        tools_used: list[str] = []
        final_answer = ""
        for msg in messages:
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tool_call in msg.tool_calls:
                    tools_used.append(tool_call['name'])
            elif isinstance(msg, AIMessage) and not msg.tool_calls:
                final_answer = msg.content

        await _upsert_thread_metadata(checkpointer, user_id, body.thread_id, body.prompt)

        return ChatResponse(
            answer=final_answer,
            tools_used=tools_used,
            provider=provider,
            model=model,
        )
    except ValueError as e:
        logger.warning("Invalid chat request for user %s: %s", user_id, e)
        raise HTTPException(status_code=400, detail="Invalid request.")
    except Exception:
        logger.exception(f"Chat error for user {user_id}")
        raise HTTPException(status_code=500, detail="An internal error has occurred.")


# ─── Streaming chat ───────────────────────────────────────────────────────────

@router.post("/langchain/chat/stream")
async def chat_stream(
    body: ChatRequest,
    request: Request,
    user_id: str = Depends(deps.get_current_user),
):
    """Stream a chat response over Server-Sent Events.

    Emits the same vocabulary the frontend already consumes:
        status     - lifecycle hint ("Thinking...")
        tool       - tool call started (content = tool name)
        tool_done  - tool call completed (content = tool name)
        token      - incremental LLM output chunk (content = token text)
        done       - stream completed (tools_used = list of tool names)
        error      - terminal error (content = error message)

    The HTTP status is always 200; mid-stream failures are delivered as an
    `error` event because headers have already been flushed by the time the
    agent runs.
    """
    async def event_stream():
        try:
            checkpointer = getattr(request.app.state, 'checkpointer', None)
            try:
                agent, _provider, _model = _resolve_agent(body, checkpointer)
            except ValueError as e:
                logger.warning("Invalid chat stream request for user %s: %s", user_id, e)
                yield f"data: {json.dumps({'type': 'error', 'content': 'Invalid request.'})}\n\n"
                return

            scoped_thread = f"{user_id}:{body.thread_id}"
            tools_used: list[str] = []

            yield f"data: {json.dumps({'type': 'status', 'content': 'Thinking...'})}\n\n"

            async for event in agent.astream_events(
                {"messages": [("user", body.prompt)]},
                config={"configurable": {"thread_id": scoped_thread}},
                version="v2",
            ):
                kind = event.get("event", "")

                if kind == "on_tool_start":
                    tool_name = event.get("name", "")
                    tools_used.append(tool_name)
                    yield f"data: {json.dumps({'type': 'tool', 'content': tool_name})}\n\n"

                elif kind == "on_tool_end":
                    tool_name = event.get("name", "")
                    yield f"data: {json.dumps({'type': 'tool_done', 'content': tool_name})}\n\n"

                elif kind == "on_chat_model_stream":
                    if "router_internal" in (event.get("tags") or []):
                        continue
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and isinstance(getattr(chunk, "content", None), str) and chunk.content:
                        yield f"data: {json.dumps({'type': 'token', 'content': chunk.content})}\n\n"

            yield f"data: {json.dumps({'type': 'done', 'tools_used': tools_used})}\n\n"

            await _upsert_thread_metadata(checkpointer, user_id, body.thread_id, body.prompt)

        except Exception:
            logger.exception(f"Stream error for user {user_id}")
            yield f"data: {json.dumps({'type': 'error', 'content': 'An internal error has occurred.'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
