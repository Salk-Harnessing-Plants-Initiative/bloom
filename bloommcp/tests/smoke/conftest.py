"""Shared fixtures for ``bloommcp/tests/smoke/`` -- real dev-stack MCP-transport
smoke tests (#483).

Every test in this package calls a bloommcp tool through the running container's
actual MCP transport (``fastmcp.Client``), mirroring ``live_plot_tool_smoke.py``'s
real-call approach -- never an in-process call into ``bloom_mcp``, never a mock.
That is the whole point of this package: an in-process call can catch a business-logic
regression, but only a real call through the container's actual network/MCP transport
can catch a bind-mount, permission, or container-wiring regression (the same reasoning
``live_plot_tool_smoke.py`` documents for issue #472).

Every test here is marked ``live_smoke`` (see ``bloommcp/pyproject.toml``), which
excludes it from ``python-audit``'s per-PR run (no dev stack there). The bounded-time
subset runs in CI's ``dev-stack-smoke`` job; the full set (including
``live_smoke_slow``) runs via ``/pre-merge`` against a locally-brought-up stack.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES_DIR = REPO_ROOT / "bloommcp" / "tests" / "fixtures"
TRAITS_DIR = REPO_ROOT / "bloommcp" / "data" / "SLEAP_OUT_CSV"
# Host-side mirror of the container's BLOOM_PLOTS_DIR (/app/data/PLOTS_DIR),
# bind-mounted from here per docker-compose.dev.yml -- lets a smoke test verify a
# plot tool's returned URL(s) actually correspond to real, nonempty files, not just
# a well-formed success string.
PLOTS_DIR = REPO_ROOT / "bloommcp" / "data" / "PLOTS_DIR"

# Excludes trailing "," from the character class: save_plot_or_plots's multi-page
# summary joins URLs with ", " (comma immediately after the URL, no space before it),
# so a bare \S+ would swallow the comma into the "filename".
_URL_RE = re.compile(r"https?://[^\s,]+")

# Filenames as seeded into TRAITS_DIR -- distinct from the fixtures' on-disk names in
# tests/fixtures/ so a smoke run never collides with a developer's own experiment files.
FIXTURE_FILES: dict[str, str] = {
    "turface_19": "turface_19_raw_data.csv",
    "cylinder": "cylinder_raw_data.csv",
}


def mcp_url_and_key() -> tuple[str, str]:
    """Read the running container's connection info from env (sourced from .env.dev
    by the Makefile targets / CI step that invokes pytest here)."""
    port = os.environ.get("BLOOMMCP_PORT", "8811")
    api_key = os.environ.get("BLOOMMCP_API_KEY")
    if not api_key:
        pytest.skip(
            "BLOOMMCP_API_KEY is empty -- run 'make init' (or source .env.dev) before "
            "running tests/smoke/."
        )
    return f"http://localhost:{port}/mcp", api_key


def _asdict(x: Any) -> Any:
    """Recursively normalize fastmcp's dynamic result objects to plain dicts/lists so
    tests can use ordinary ``result["key"]`` indexing regardless of fastmcp's
    structured-content wrapper type."""
    if isinstance(x, dict):
        return {k: _asdict(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_asdict(v) for v in x]
    if hasattr(x, "model_dump"):
        return _asdict(x.model_dump())
    if hasattr(x, "__dict__"):
        return {k: _asdict(v) for k, v in vars(x).items()}
    return x


def _call_tool_sync(tool_name: str, params: dict) -> Any:
    """Call a granular ``sleap_roots_*`` analysis tool (qc_clean, qc_inspect,
    remove_outliers, pca_analysis, clustering) through the real running container.

    These tools are ``as_mcp_tool``-wrapped (a single pydantic ``Params`` model), whose
    MCP-serialized input schema nests the whole payload under one ``params`` argument
    -- confirmed empirically against the running server, not assumed. Returns the
    tool's structured result normalized to a plain dict.
    """
    url, api_key = mcp_url_and_key()

    async def _call():
        async with Client(url, auth=api_key, timeout=120, init_timeout=15) as client:
            result = await client.call_tool(tool_name, {"params": params})
            return result.data

    return _asdict(asyncio.run(_call()))


def _call_plot_tool_sync(tool_name: str, **kwargs: Any) -> str:
    """Call one of the 5 plotting tools through the real running container.

    Unlike the granular analysis tools, plot tools are plain functions taking flat
    keyword arguments directly (``filename``, plus ``traits``/``threshold`` as
    applicable) -- NOT wrapped under a ``params`` argument -- and return a plain
    string summary, matching ``live_plot_tool_smoke.py``'s exact calling convention.
    """
    url, api_key = mcp_url_and_key()

    async def _call():
        async with Client(url, auth=api_key, timeout=120, init_timeout=15) as client:
            result = await client.call_tool(tool_name, kwargs)
            return result.data

    data = asyncio.run(_call())
    return data if isinstance(data, str) else str(data)


@pytest.fixture
def call_tool():
    """Injectable callable: ``call_tool("sleap_roots_qc_clean", {"experiment": ...})``.

    Synchronous on purpose (wraps ``asyncio.run`` internally) -- matches this repo's
    existing real-MCP-call test pattern (see ``test_qc_clean_appears_in_tools_list``
    and siblings in ``tests/tools/``), which never declares ``async def test_...``.
    """
    return _call_tool_sync


@pytest.fixture
def call_plot_tool():
    """Injectable callable: ``call_plot_tool("sleap_roots_plot_trait_histograms",
    filename=...)`` -- see ``_call_plot_tool_sync`` for why this is a separate helper
    from ``call_tool``."""
    return _call_plot_tool_sync


def _assert_plot_success(text: str) -> None:
    """Assert a plot tool's return text represents a real, non-empty saved plot.

    Stronger than a bare ``"Plot saved:" in text`` substring check: extracts every
    URL in the text (single-page or ``save_plot_or_plots``'s ``"N pages: url1,
    url2, ..."`` summary alike) and verifies each corresponds to a real file on the
    host-side bind-mounted PLOTS_DIR with nonzero size -- catching a tool that
    claims success while writing nothing (or an empty file) that a bare substring
    match would miss.
    """
    assert "Plot saved:" in text, f"expected a success summary, got: {text!r}"
    assert "denied" not in text.lower(), f"unexpected permission error: {text!r}"

    urls = _URL_RE.findall(text)
    assert urls, f"no URL found in the tool's success text: {text!r}"
    for url in urls:
        name = url.rsplit("/", 1)[-1]
        path = PLOTS_DIR / name
        assert path.is_file(), f"{url} claims success but {path} does not exist"
        assert path.stat().st_size > 0, f"{path} exists but is empty"


@pytest.fixture
def assert_plot_success():
    """Injectable callable: ``assert_plot_success(text)`` -- see
    ``_assert_plot_success`` for what it checks."""
    return _assert_plot_success


@pytest.fixture(params=["turface_19", "cylinder"])
def fixture_name(request) -> str:
    """Parametrizes a smoke test over both oracle fixtures (#483)."""
    return request.param


@pytest.fixture
def seeded_experiment(fixture_name: str) -> str:
    """Seed ``fixture_name``'s raw CSV into the real bind-mounted TRAITS_DIR.

    Returns the experiment filename the tool should be called with. Not cleaned up
    after the test -- matching ``live_plot_tool_smoke.py``'s convention of leaving the
    seeded fixture in place (host-side bind-mounted scratch dir, gitignored, harmless
    to leave for the next run to overwrite).
    """
    TRAITS_DIR.mkdir(parents=True, exist_ok=True)
    filename = FIXTURE_FILES[fixture_name]
    shutil.copy(FIXTURES_DIR / filename, TRAITS_DIR / filename)
    return filename
