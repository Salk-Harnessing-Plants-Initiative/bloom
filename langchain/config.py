"""
Configuration for LangChain Agent
"""
import os
import logging
from functools import lru_cache
import supabase

logger = logging.getLogger(__name__)

# Supabase/PostgREST Configuration (required)
SUPABASE_URL = os.environ.get('SUPABASE_URL')
if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL environment variable is required")

# Agent key — must use bloom_agent role (read-only)
# Do NOT use service_role — the agent should never have write access
BLOOM_AGENT_KEY = os.environ.get('BLOOM_AGENT_KEY')
if not BLOOM_AGENT_KEY:
    raise RuntimeError("BLOOM_AGENT_KEY environment variable is required. Generate a JWT with role=bloom_agent.")

# Service key — only for server-side operations (auth verification, not data queries)
SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ.get('SERVICE_ROLE_KEY')

# REST API base URL
REST_API_URL = f"{SUPABASE_URL}/rest/v1"

# Frontend URL for generating clickable links
FRONTEND_URL = os.environ.get('NEXT_PUBLIC_APP_URL', 'http://localhost')


@lru_cache(maxsize=1)
def get_supabase_client():
    """Get a cached Supabase client for the agent.

    Uses BLOOM_AGENT_KEY (bloom_agent role, read-only) for data queries.
    The agent should never have write access to the database.
    """
    return supabase.create_client(SUPABASE_URL, BLOOM_AGENT_KEY)
