"""
Configuration for LangChain Agent
"""
import os
from functools import lru_cache
import supabase

# Supabase/PostgREST Configuration (required)
SUPABASE_URL = os.environ.get('SUPABASE_URL')
if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL environment variable is required")
SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY')
SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ.get('SERVICE_ROLE_KEY')
if not SUPABASE_SERVICE_KEY:
    raise RuntimeError("SUPABASE_SERVICE_KEY or SERVICE_ROLE_KEY environment variable is required")

# REST API base URL
REST_API_URL = f"{SUPABASE_URL}/rest/v1"

# Frontend URL for generating clickable links
FRONTEND_URL = os.environ.get('NEXT_PUBLIC_APP_URL', 'http://localhost')


@lru_cache(maxsize=1)
def get_supabase_client():
    """Get a cached Supabase client instance.

    Uses service key for full access, falls back to anon key.
    The client is cached so it's only created once.
    """
    key = SUPABASE_SERVICE_KEY or SUPABASE_ANON_KEY or ""
    return supabase.create_client(SUPABASE_URL, key)
