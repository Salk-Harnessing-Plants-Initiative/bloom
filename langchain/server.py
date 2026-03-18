"""
FastAPI server for LangChain Agent
Supports multiple LLM providers: OpenAI, local LLM
Production-ready: PostgresSaver, JWT auth, agent caching

Endpoints:
    POST /langchain/chat         - Process a chat message (requires auth)
    GET  /langchain/models       - List available LLM providers and models
    GET  /langchain/mcp-tools    - List connected MCP tools
    POST /langchain/threads      - Create/upsert thread metadata (requires auth)
    GET  /langchain/threads      - List user's conversation threads (requires auth)
    DELETE /langchain/threads/:id - Clear a conversation thread (requires auth)
    GET  /health                 - Health check
"""
import os
import jwt
import logging
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from agent import create_agent, AVAILABLE_MODELS, setup_checkpointer
from langchain_core.messages import AIMessage
from mcp_config import MCP_SERVERS

logger = logging.getLogger(__name__)

# JWT secret for Supabase Auth token validation (required)
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET environment variable is required")

# MCP client state (populated at startup)
mcp_client = None
mcp_tools = []

# Agent cache: keyed by (provider, model, tool_set, frozenset(mcp_tool_names))
_agent_cache: dict = {}
_AGENT_CACHE_MAX = 16


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage MCP client and PostgresSaver: connect on startup, close on shutdown."""
    global mcp_client, mcp_tools

    # Initialize PostgresSaver checkpointer
    try:
        checkpointer = await setup_checkpointer()
        app.state.checkpointer = checkpointer
        logger.info("PostgresSaver checkpointer initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize PostgresSaver: {e}. Conversations will not persist.")
        app.state.checkpointer = None

    # Connect to MCP servers (with retry — bloommcp may still be starting)
    if MCP_SERVERS:
        import asyncio
        from langchain_mcp_adapters.client import MultiServerMCPClient
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                mcp_client = MultiServerMCPClient(MCP_SERVERS)
                mcp_tools = await mcp_client.get_tools()
                logger.info(f"Loaded {len(mcp_tools)} MCP tools from {len(MCP_SERVERS)} server(s)")
                break
            except Exception as e:
                if attempt < max_retries:
                    logger.info(f"MCP connection attempt {attempt}/{max_retries} failed, retrying in 3s...")
                    await asyncio.sleep(3)
                else:
                    logger.warning(f"Failed to connect to MCP servers after {max_retries} attempts: {e}. Agent will run with native tools only.")
                    mcp_client = None
                    mcp_tools = []
    else:
        logger.info("No MCP servers configured. Agent will run with native tools only.")

    yield

    # Cleanup
    mcp_client = None
    mcp_tools = []
    _agent_cache.clear()
    if hasattr(app.state, 'checkpointer') and app.state.checkpointer:
        try:
            await app.state.checkpointer.conn.close()
        except Exception:
            pass


app = FastAPI(title="Bloom LangChain Agent", version="2.0.0", lifespan=lifespan)

# Serve generated plots as static files
PLOTS_DIR = os.getenv("BLOOM_PLOTS_DIR", "/app/data/PLOTS_DIR")
os.makedirs(PLOTS_DIR, exist_ok=True)
app.mount("/plots", StaticFiles(directory=PLOTS_DIR), name="plots")

# CORS for frontend
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Auth ──────────────────────────────────────────────────────────────────────

async def get_current_user(authorization: str = Header(default=None)) -> str:
    """Extract and validate Supabase JWT from Authorization header.

    Returns the user UUID (sub claim) from the token.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    # Strip "Bearer " prefix
    token = authorization.replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Bearer token required")

    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token: missing sub claim")
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


# ─── Agent Cache ───────────────────────────────────────────────────────────────

def get_or_create_agent(
    provider: str,
    model: str,
    tool_set: str,
    filtered_mcp_tools: list,
    checkpointer,
):
    """Get a cached agent or create a new one.

    Agents are cached by (provider, model, tool_set, mcp_tool_names) key.
    LRU eviction when cache exceeds _AGENT_CACHE_MAX entries.
    """
    mcp_tool_key = frozenset(t.name for t in filtered_mcp_tools) if filtered_mcp_tools else frozenset()
    cache_key = (provider, model, tool_set, mcp_tool_key)

    if cache_key in _agent_cache:
        logger.debug(f"Agent cache hit: {cache_key}")
        return _agent_cache[cache_key]

    # Evict oldest if cache full
    if len(_agent_cache) >= _AGENT_CACHE_MAX:
        oldest_key = next(iter(_agent_cache))
        del _agent_cache[oldest_key]
        logger.debug(f"Agent cache evicted: {oldest_key}")

    agent = create_agent(
        provider=provider,
        model=model,
        tool_set=tool_set,
        mcp_tools=filtered_mcp_tools,
        checkpointer=checkpointer,
    )
    _agent_cache[cache_key] = agent
    logger.info(f"Agent cache miss — created: provider={provider}, model={model}, tool_set={tool_set}, mcp_tools={len(filtered_mcp_tools)}")
    return agent


# ─── Request/Response Models ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    prompt: str
    provider: str = "openai"  # "openai" or "local"
    model: Optional[str] = None  # Defaults to first model for provider
    tool_set: str = "all"  # "all", "scrna", "cyl", "generic"
    mcp_tool_names: list[str] = Field(default_factory=list)  # Filter MCP tools by name (empty = foundational only)
    thread_id: str = "default"  # Conversation thread ID for memory persistence


class ChatResponse(BaseModel):
    answer: str
    tools_used: list[str]
    provider: str
    model: str


class CreateThreadRequest(BaseModel):
    thread_id: str
    title: Optional[str] = None


class ModelsResponse(BaseModel):
    models: dict[str, list[str]]


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.post("/langchain/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, user_id: str = Depends(get_current_user)):
    """Process a chat message through the LangChain agent. Requires authentication."""
    try:
        provider = request.provider.lower()
        model = request.model

        # Validate provider
        if provider not in AVAILABLE_MODELS:
            raise ValueError(f"Unknown provider: {provider}. Choose from: openai, local")

        # Default model if not specified
        if not model:
            model = AVAILABLE_MODELS[provider][0]

        # Validate tool_set
        valid_tool_sets = ["all", "scrna", "cyl", "generic"]
        tool_set = request.tool_set.lower() if request.tool_set else "all"
        if tool_set not in valid_tool_sets:
            raise ValueError(f"Unknown tool_set: {tool_set}. Choose from: {valid_tool_sets}")

        # Filter MCP tools by name.
        # Foundational tools are always included so the agent can discover and load data.
        ALWAYS_INCLUDE_MCP_TOOLS = {
            "list_available_experiments",
            "load_experiment_data",
            "inspect_data_quality",
        }
        if request.mcp_tool_names:
            selected = set(request.mcp_tool_names) | ALWAYS_INCLUDE_MCP_TOOLS
            filtered_mcp_tools = [t for t in mcp_tools if t.name in selected]
        else:
            filtered_mcp_tools = [t for t in mcp_tools if t.name in ALWAYS_INCLUDE_MCP_TOOLS]

        # Get or create cached agent
        checkpointer = getattr(app.state, 'checkpointer', None)
        agent = get_or_create_agent(
            provider=provider,
            model=model,
            tool_set=tool_set,
            filtered_mcp_tools=filtered_mcp_tools,
            checkpointer=checkpointer,
        )

        # Scope thread_id to user for isolation
        scoped_thread = f"{user_id}:{request.thread_id}"

        response = await agent.ainvoke(
            {"messages": [("user", request.prompt)]},
            config={"configurable": {"thread_id": scoped_thread}},
        )
        messages = response["messages"]

        tools_used = []
        final_answer = ""

        for msg in messages:
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tool_call in msg.tool_calls:
                    tools_used.append(tool_call['name'])
            elif isinstance(msg, AIMessage) and not msg.tool_calls:
                final_answer = msg.content

        # Auto-create thread metadata and set title from first user message
        if checkpointer and request.thread_id != "default":
            try:
                title = request.prompt[:100].strip()
                if len(request.prompt) > 100:
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
                        (user_id, request.thread_id, title),
                    )
            except Exception as e:
                logger.warning(f"Failed to upsert thread metadata: {e}")

        return ChatResponse(
            answer=final_answer,
            tools_used=tools_used,
            provider=provider,
            model=model,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Chat error for user {user_id}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/langchain/models", response_model=ModelsResponse)
async def get_models():
    """Get available models for each provider."""
    return ModelsResponse(models=AVAILABLE_MODELS)


@app.get("/langchain/mcp-tools")
async def get_mcp_tools():
    """List connected MCP tools with their names and descriptions."""
    return {
        "tools": [
            {"name": t.name, "description": t.description}
            for t in mcp_tools
        ]
    }


@app.post("/langchain/threads")
async def create_thread(request: CreateThreadRequest, user_id: str = Depends(get_current_user)):
    """Create or upsert a thread metadata entry for the authenticated user."""
    checkpointer = getattr(app.state, 'checkpointer', None)
    if not checkpointer:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with checkpointer.conn.connection() as conn:
            result = await conn.execute(
                """
                INSERT INTO chat_threads (user_id, thread_id, title)
                VALUES (%s::uuid, %s, %s)
                ON CONFLICT (user_id, thread_id)
                DO UPDATE SET updated_at = now()
                RETURNING id, thread_id, title, created_at, updated_at
                """,
                (user_id, request.thread_id, request.title),
            )
            row = await result.fetchone()
            return {
                "id": str(row[0]),
                "thread_id": row[1],
                "title": row[2],
                "created_at": row[3].isoformat() if row[3] else None,
                "updated_at": row[4].isoformat() if row[4] else None,
            }
    except Exception as e:
        logger.exception(f"Error creating thread for user {user_id}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/langchain/threads")
async def list_threads(user_id: str = Depends(get_current_user)):
    """List conversation threads for the authenticated user from chat_threads table."""
    checkpointer = getattr(app.state, 'checkpointer', None)
    if not checkpointer:
        return {"threads": []}

    try:
        async with checkpointer.conn.connection() as conn:
            result = await conn.execute(
                """
                SELECT thread_id, title, created_at, updated_at
                FROM chat_threads
                WHERE user_id = %s::uuid AND deleted_at IS NULL
                ORDER BY updated_at DESC
                """,
                (user_id,),
            )
            rows = await result.fetchall()
            return {
                "threads": [
                    {
                        "thread_id": row[0],
                        "title": row[1],
                        "created_at": row[2].isoformat() if row[2] else None,
                        "updated_at": row[3].isoformat() if row[3] else None,
                    }
                    for row in rows
                ]
            }
    except Exception as e:
        logger.warning(f"Error listing threads for user {user_id}: {e}")
        return {"threads": []}


@app.delete("/langchain/threads/{thread_id}")
async def clear_thread(thread_id: str, user_id: str = Depends(get_current_user)):
    """Soft-delete a conversation thread. Marks deleted_at timestamp instead of
    permanently removing data. Checkpoint data is preserved for potential recovery."""
    checkpointer = getattr(app.state, 'checkpointer', None)
    if not checkpointer:
        raise HTTPException(status_code=503, detail="Checkpointer not available")

    try:
        async with checkpointer.conn.connection() as conn:
            await conn.execute(
                "UPDATE chat_threads SET deleted_at = now() WHERE user_id = %s::uuid AND thread_id = %s",
                (user_id, thread_id),
            )
        return {"status": "deleted", "thread_id": thread_id}
    except Exception as e:
        logger.exception(f"Error deleting thread {thread_id} for user {user_id}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    checkpointer = getattr(app.state, 'checkpointer', None)
    return {
        "status": "ok",
        "checkpointer": "postgres" if checkpointer else "none",
        "mcp_tools": len(mcp_tools),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5002)
