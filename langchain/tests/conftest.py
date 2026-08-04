"""Shared test fixtures for the langchain service.

Importing `server` transitively imports `agent.py` (module-level Postgres URL
composition + local-model auto-detection) and `config.py` (module-level
Supabase env checks) — all of which raise at import time if their env vars
are unset. None of these are actually *connected to* unless the FastAPI
lifespan runs, and `TestClient` only runs lifespan when entered as a context
manager (`with TestClient(app) as c:`); the `client` fixture below never
does that, so no real Postgres/Supabase/vLLM network call ever happens —
only the module-level presence checks need satisfying.
"""

import pytest


@pytest.fixture(autouse=True)
def _required_env(monkeypatch, tmp_path):
    """Set every env var required at import time by deps/config/agent/server,
    to a fixed test value. None of these need to point at anything real —
    see the module docstring for why. BLOOM_PLOTS_DIR must be writable:
    server.py creates it at *import* time (`os.makedirs`), and its default
    (`/app/data/PLOTS_DIR`) isn't writable outside a container."""
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("BLOOM_AGENT_KEY", "test-agent-key")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "test-local-model")
    monkeypatch.setenv("BLOOM_PLOTS_DIR", str(tmp_path / "plots"))
    for var in (
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
    ):
        monkeypatch.setenv(var, "test")


@pytest.fixture
def client(_required_env):
    """A TestClient over the real app, with auth overridden and lifespan
    (Postgres/MCP startup) never triggered — see module docstring."""
    from fastapi.testclient import TestClient

    import deps
    import server

    server.app.dependency_overrides[deps.get_current_user] = lambda: "test-user-id"
    try:
        yield TestClient(server.app)
    finally:
        server.app.dependency_overrides.clear()
