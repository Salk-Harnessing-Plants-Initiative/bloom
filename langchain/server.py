"""
FastAPI server for LangChain Agent
Supports multiple LLM providers: OpenAI, local LLM
Production-ready: PostgresSaver, JWT auth, agent caching

Endpoints:
    POST /langchain/chat         - Process a chat message (requires auth)
    POST /langchain/chat/stream  - Stream a chat response over SSE (requires auth)
    GET  /langchain/models       - List available LLM providers and models
    GET  /langchain/mcp-tools    - List connected MCP tools
    POST /langchain/threads      - Create/upsert thread metadata (requires auth)
    GET  /langchain/threads      - List user's conversation threads (requires auth)
    DELETE /langchain/threads/:id - Clear a conversation thread (requires auth)
    GET  /health                 - Health check

Layout:
    server.py         - app, lifespan, CORS, mounts, threads/meta/health endpoints
    deps.py           - JWT auth, agent cache, MCP runtime state
    schemas.py        - Pydantic request/response models
    routes/chat.py    - /chat and /chat/stream endpoints
"""
import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import deps
from agent import AVAILABLE_MODELS, setup_checkpointer
from mcp_config import MCP_SERVERS
from routes import chat as chat_routes
from schemas import CreateThreadRequest, ModelsResponse

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage MCP client and PostgresSaver: connect on startup, close on shutdown."""
    # Initialize PostgresSaver checkpointer
    try:
        checkpointer = await setup_checkpointer()
        app.state.checkpointer = checkpointer
        logger.info("PostgresSaver checkpointer initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize PostgresSaver: {e}. Conversations will not persist.")
        app.state.checkpointer = None

    # Connect to MCP servers (with retry — bloommcp may still be starting)
    mcp_client = None
    mcp_tools: list = []
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
                    logger.warning(
                        f"Failed to connect to MCP servers after {max_retries} attempts: {e}. "
                        f"Agent will run with native tools only."
                    )
                    mcp_client = None
                    mcp_tools = []
    else:
        logger.info("No MCP servers configured. Agent will run with native tools only.")

    deps.set_mcp_state(mcp_client, mcp_tools)

    yield

    deps.clear_runtime_state()
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

# Chat + streaming endpoints live in routes/chat.py
app.include_router(chat_routes.router)


# ─── Threads ──────────────────────────────────────────────────────────────────

@app.post("/langchain/threads")
async def create_thread(request: CreateThreadRequest, user_id: str = Depends(deps.get_current_user)):
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
    except Exception:
        logger.exception(f"Error creating thread for user {user_id}")
        raise HTTPException(status_code=500, detail="An internal error has occurred.")


@app.get("/langchain/threads")
async def list_threads(user_id: str = Depends(deps.get_current_user)):
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
async def clear_thread(thread_id: str, user_id: str = Depends(deps.get_current_user)):
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
    except Exception:
        logger.exception(f"Error deleting thread {thread_id} for user {user_id}")
        raise HTTPException(status_code=500, detail="An internal error has occurred.")


# ─── Meta + health ────────────────────────────────────────────────────────────

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
            for t in deps.mcp_tools
        ]
    }


@app.get("/health")
async def health():
    checkpointer = getattr(app.state, 'checkpointer', None)
    return {
        "status": "ok",
        "checkpointer": "postgres" if checkpointer else "none",
        "mcp_tools": len(deps.mcp_tools),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5002)
