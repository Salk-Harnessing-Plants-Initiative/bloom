"""Shared fixtures + Supabase-free env for the bloom_mcp unit suite.

The whole point of Tier 0 is that this suite runs with **no live Supabase**:
``SUPABASE_URL`` / ``BLOOM_AGENT_KEY`` are explicitly removed so the lazy
validation in ``bloom_mcp.supabase_client`` is exercised, and the non-secret
data directories that ``bloom_mcp.experiment_utils`` requires at import are
pointed at a throwaway temp dir (mirrors ``tests/unit/test_workflow_scaffolding``
in the repo root).
"""

from __future__ import annotations

import os
import tempfile

# --- Guarantee Supabase is absent before any bloom_mcp import ---
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("BLOOM_AGENT_KEY", None)

# --- Non-secret data dirs experiment_utils validates at import time ---
_TMP = tempfile.mkdtemp(prefix="bloom_mcp_tests_")
os.environ.setdefault("BLOOM_TRAITS_DIR", _TMP)
os.environ.setdefault("BLOOM_OUTPUT_DIR", _TMP)
os.environ.setdefault("BLOOM_PLOTS_DIR", _TMP)
os.environ.setdefault("BLOOM_PLOTS_URL", "http://localhost/plots")
