"""Tier 0 acceptance tests for the installable ``bloom_mcp`` package.

These map 1:1 to the ``bloommcp-packaging`` spec scenarios: Supabase-free
import, lazy env validation (incl. partial env), boot fail-fast, no stale
prototype imports, and a FastMCP ``Client`` smoke over the registered tools.
All run with ``SUPABASE_URL`` / ``BLOOM_AGENT_KEY`` unset (see conftest).
"""

from __future__ import annotations

import ast
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

_PKG_ROOT = Path(__file__).resolve().parents[1] / "src" / "bloom_mcp"

# Every bloom-specific env var the package reads. A fresh interpreter must import
# the server with none of these set.
_BLOOM_ENV_VARS = (
    "SUPABASE_URL",
    "BLOOM_AGENT_KEY",
    "BLOOM_TRAITS_DIR",
    "BLOOM_OUTPUT_DIR",
    "BLOOM_PLOTS_DIR",
    "BLOOM_PLOTS_URL",
)


# ── Installable Package Layout ──────────────────────────────────────────────


def test_import_bloom_mcp_with_no_supabase_env():
    """`import bloom_mcp` (and the server) succeed with Supabase unset."""
    assert "SUPABASE_URL" not in os.environ
    assert "BLOOM_AGENT_KEY" not in os.environ
    import bloom_mcp  # noqa: F401
    import bloom_mcp.server as server

    assert server.mcp.name == "bloom-tools"


def test_fresh_interpreter_imports_server_with_no_bloom_env():
    """A clean interpreter imports bloom_mcp.server with NO bloom env at all.

    Guards against any module-load env gate (Supabase *or* the BLOOM_*_DIR check
    in experiment_utils) re-crashing the import. Run in a subprocess so it is
    immune to env already set by conftest in this process.
    """
    env = {k: v for k, v in os.environ.items() if k not in _BLOOM_ENV_VARS}
    result = subprocess.run(
        [sys.executable, "-c", "import bloom_mcp.server"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_no_stale_prototype_imports():
    """No module under src/bloom_mcp imports a bare source/tools/storage root."""
    offenders: list[str] = []
    for py in _PKG_ROOT.rglob("*.py"):
        tree = ast.parse(py.read_text(), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in {"source", "tools", "storage"}:
                        offenders.append(f"{py.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                # level > 0 is a relative import (from . import x) — never stale.
                if node.level == 0 and node.module:
                    if node.module.split(".")[0] in {"source", "tools", "storage"}:
                        offenders.append(f"{py.name}: from {node.module}")
    assert not offenders, "stale prototype imports remain:\n" + "\n".join(offenders)


# ── Lazy Supabase Environment Validation ────────────────────────────────────


@pytest.mark.parametrize(
    "present",
    [
        {},  # both unset
        {"SUPABASE_URL": "http://kong:8000"},  # only URL → KEY missing
        {"BLOOM_AGENT_KEY": "fake-jwt"},  # only KEY → URL missing
    ],
)
def test_accessor_validates_lazily_and_names_missing_var(present, monkeypatch):
    """A Supabase accessor with incomplete env raises naming the missing var."""
    import bloom_mcp.supabase_client as sc

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("BLOOM_AGENT_KEY", raising=False)
    for k, v in present.items():
        monkeypatch.setenv(k, v)

    with pytest.raises(RuntimeError) as exc:
        sc.get_postgrest_client()

    msg = str(exc.value)
    for var in ("SUPABASE_URL", "BLOOM_AGENT_KEY"):
        if var not in present:
            assert var in msg
        else:
            assert var not in msg


def test_validate_env_passes_when_both_present(monkeypatch):
    """validate_env() returns cleanly once both vars are set."""
    import bloom_mcp.supabase_client as sc

    monkeypatch.setenv("SUPABASE_URL", "http://kong:8000")
    monkeypatch.setenv("BLOOM_AGENT_KEY", "fake-jwt")
    sc.validate_env()  # must not raise


def test_experiment_utils_validate_env_raises_when_dirs_unset(monkeypatch):
    """experiment_utils.validate_env() raises naming the missing BLOOM_*_DIR vars."""
    import bloom_mcp.experiment_utils as eu

    for var in (
        "BLOOM_TRAITS_DIR",
        "BLOOM_OUTPUT_DIR",
        "BLOOM_PLOTS_DIR",
        "BLOOM_PLOTS_URL",
    ):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(RuntimeError, match="BLOOM_TRAITS_DIR"):
        eu.validate_env()


# ── Server Boot Fail-Fast ───────────────────────────────────────────────────


def test_boot_validation_fails_fast_without_server_io(monkeypatch):
    """server.main()'s validate_env() raises before any network/server I/O."""
    import bloom_mcp.server as server

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("BLOOM_AGENT_KEY", raising=False)

    # Spy: mcp.run must never be reached when env is missing.
    called = {"run": False}
    monkeypatch.setattr(
        server.mcp, "run", lambda *a, **k: called.__setitem__("run", True)
    )

    with pytest.raises(RuntimeError):
        server.main()
    assert called["run"] is False


def test_boot_validation_fails_fast_on_missing_data_dirs(monkeypatch):
    """main() also fails fast when BLOOM_*_DIR is missing (Supabase present)."""
    import bloom_mcp.server as server

    # Supabase satisfied so the failure must come from the data-dir validator.
    monkeypatch.setenv("SUPABASE_URL", "http://kong:8000")
    monkeypatch.setenv("BLOOM_AGENT_KEY", "fake-jwt")
    monkeypatch.delenv("BLOOM_TRAITS_DIR", raising=False)

    called = {"run": False}
    monkeypatch.setattr(
        server.mcp, "run", lambda *a, **k: called.__setitem__("run", True)
    )

    with pytest.raises(RuntimeError, match="BLOOM_TRAITS_DIR"):
        server.main()
    assert called["run"] is False


# ── FastMCP Client smoke (no live Supabase) ─────────────────────────────────


def test_fastmcp_client_lists_registered_tools():
    """A FastMCP Client can connect to the in-process server and see tools."""
    from fastmcp import Client

    import bloom_mcp.server as server

    async def _list():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = asyncio.run(_list())
    names = {t.name for t in tools}
    assert "list_available_experiments" in names
    assert len(names) >= 5


# ── _validate_name property (keeps the mandated hypothesis dep load-bearing) ─


@given(st.text(), st.text())
def test_validate_name_rejects_any_string_with_slash(left, right):
    """Any object name containing '/' is rejected — the bucket-escape guard."""
    import bloom_mcp.supabase_client as sc

    name = f"{left}/{right}"  # guaranteed to contain a slash
    with pytest.raises(ValueError):
        sc._validate_name(name)
