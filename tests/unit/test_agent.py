"""Unit tests for langchain/db_url.py.

Covers the two bugs the PR #144 review flagged:
  - Missing POSTGRES_* env var must raise RuntimeError (fail-fast, not
    silent fallback to localhost).
  - Password must be percent-encoded so characters with URL-reserved
    meanings (@, :, /, #, %) can't corrupt the connection URL.

The URL logic lives in langchain/db_url.py — a dedicated module so these
tests can import it without triggering the rest of agent.py (which runs
LLM model auto-detection at import time and requires LOCAL_LLM_URL).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "langchain"))

from db_url import REQUIRED_VARS, compose_postgres_url  # noqa: E402


def _set_valid_env(monkeypatch):
    """Set the 5 POSTGRES_* vars to a known-good baseline so each test
    can mutate one value at a time."""
    monkeypatch.setenv("POSTGRES_USER", "admin")
    monkeypatch.setenv("POSTGRES_PASSWORD", "simplepassword")
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "postgres")


@pytest.mark.parametrize("missing_var", REQUIRED_VARS)
def test_agent_raises_on_missing_postgres_vars(monkeypatch, missing_var):
    """Every required POSTGRES_* var must fail-fast when unset. Guards
    against silently falling back to a default like localhost."""
    _set_valid_env(monkeypatch)
    monkeypatch.delenv(missing_var, raising=False)
    with pytest.raises(RuntimeError, match=missing_var):
        compose_postgres_url()


def test_postgres_url_encodes_special_chars_in_password(monkeypatch):
    """Password with URL-reserved characters must be percent-encoded so
    the composed URL is parseable. The specific characters below would
    otherwise break postgresql:// URL parsing:
      @  → would split user/host section
      :  → would split user/password section
      /  → would split host/database section
      #  → would introduce a fragment
      %  → would require escaping anyway (reserved in percent-encoding)"""
    _set_valid_env(monkeypatch)
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss/w:rd#01%")
    url = compose_postgres_url()
    assert url == "postgresql://admin:p%40ss%2Fw%3Ard%2301%25@db:5432/postgres"
