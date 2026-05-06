"""
Shared runtime dependencies for the LangChain agent server.

Owns the JWT auth dependency, the MCP runtime state (populated by the FastAPI
lifespan in server.py), and the LRU agent cache used by every chat handler.
Route modules import this directly so they don't need to reach into server.py.
"""
import os
import logging

import jwt
from fastapi import Header, HTTPException

from agent import create_agent

logger = logging.getLogger(__name__)


# ─── Auth ─────────────────────────────────────────────────────────────────────

JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET environment variable is required")


async def get_current_user(authorization: str = Header(default=None)) -> str:
    """Extract and validate Supabase JWT from Authorization header.

    Returns the user UUID (sub claim) from the token.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

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


# ─── MCP Runtime State ────────────────────────────────────────────────────────

# Populated by server.py's lifespan via set_mcp_state(). Route handlers read
# `deps.mcp_tools` (always via attribute access on the module, never via
# `from deps import mcp_tools`) so reassignments are visible.
mcp_client = None
mcp_tools: list = []


def set_mcp_state(client, tools: list) -> None:
    """Install the connected MCP client and tool list into module state."""
    global mcp_client, mcp_tools
    mcp_client = client
    mcp_tools = tools


def clear_runtime_state() -> None:
    """Reset MCP state and the agent cache. Called on lifespan shutdown."""
    global mcp_client, mcp_tools
    mcp_client = None
    mcp_tools = []
    _agent_cache.clear()


# ─── Agent Cache ──────────────────────────────────────────────────────────────

# LRU cache keyed by (provider, model, tool_set, frozenset(mcp_tool_names)).
_agent_cache: dict = {}
_AGENT_CACHE_MAX = 16


def get_or_create_agent(
    provider: str,
    model: str,
    tool_set: str,
    filtered_mcp_tools: list,
    checkpointer,
):
    """Get a cached agent or create a new one.

    LRU eviction when cache exceeds _AGENT_CACHE_MAX entries.
    """
    mcp_tool_key = (
        frozenset(t.name for t in filtered_mcp_tools) if filtered_mcp_tools else frozenset()
    )
    cache_key = (provider, model, tool_set, mcp_tool_key)

    if cache_key in _agent_cache:
        logger.debug(f"Agent cache hit: {cache_key}")
        return _agent_cache[cache_key]

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
    logger.info(
        f"Agent cache miss — created: provider={provider}, model={model}, "
        f"tool_set={tool_set}, mcp_tools={len(filtered_mcp_tools)}"
    )
    return agent
