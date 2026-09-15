"""Shared fixtures for ``bloommcp/tests/smoke/`` -- real dev-stack MCP-transport
smoke tests (#483).

Every test in this package calls a bloommcp tool through the running container's
actual MCP transport (``fastmcp.Client``) -- never an in-process call into ``bloom_mcp``,
never a mock. That is the whole point of this package: an in-process call can catch a
business-logic regression, but only a real call through the container's actual
network/MCP transport can catch a container-wiring regression (the reasoning issue #472
first applied to the since-retired ``live_plot_tool_smoke.py``, whose subject -- a tool
writing straight to the bind-mounted ``PLOTS_DIR`` -- no longer exists after #462).

Every tool here is a granular ``@as_mcp_tool`` consumer reading through the
``ExperimentReader`` port, so one harness serves all of them: ``call_tool`` +
``db_experiment_id``. The filename-based ``seeded_experiment``/``call_plot_tool`` harness
that served the bare-``mcp.tool()`` plot tools was removed with the last two of those.

Every test here is marked ``live_smoke`` (see ``bloommcp/pyproject.toml``), which
excludes it from ``python-audit``'s per-PR run (no dev stack there). The bounded-time
subset runs in CI's ``dev-stack-smoke`` job; the full set (including
``live_smoke_slow``) runs via ``/pre-merge`` against a locally-brought-up stack.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

REPO_ROOT = Path(__file__).resolve().parents[3]
# The 12 granular analysis tools (qc_clean, qc_inspect, remove_outliers, pca_analysis,
# clustering, descriptive_stats, umap_analysis, cross_experiment_correlations,
# heritability_analysis, and -- since #466 -- plot_trait_histograms, plot_trait_boxplots,
# plot_correlation_matrix) all route through SupabaseReader, whose raw
# tier is DB-only (bloom#551) -- a tool call needs a numeric experiment id, not a
# filename. Each oracle fixture instead needs a REAL numeric experiment id that already
# has trait rows in whatever Postgres this smoke run points at; seeding that data is not
# automated by this package (no tracking issue filed yet for a smoke DB seeder). Set the
# matching env var before running these tests.
EXPERIMENT_ID_ENV_VARS: dict[str, str] = {
    "turface_19": "BLOOM_SMOKE_EXPERIMENT_ID_TURFACE_19",
    "cylinder": "BLOOM_SMOKE_EXPERIMENT_ID_CYLINDER",
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

    Reads ``result.structured_content`` (the raw JSON the server sent), NOT
    ``result.data`` (fastmcp's client-side reconstruction of that JSON into a dynamic
    type derived from the tool's output schema). Found via #489's cross-experiment-
    correlations smoke test failing in CI with every ``RunLinks.outputs`` (a
    ``dict[str, str]`` field) field coming back an empty ``{}``: fastmcp's
    ``json_schema_to_type`` reconstructs a nested ``object``-typed schema with no
    declared ``properties`` (a plain ``dict[str, str]`` field like ``outputs`` has none
    -- only ``additionalProperties``) into a fieldless placeholder type rather than a
    real ``dict[str, str]``, so the client-side object silently loses every key -- the
    exact underlying schema-routing path within ``json_schema_to_type`` wasn't traced
    further than that; treat "loses nested dict keys on reconstruction" as the confirmed
    symptom, not a fully pinned root cause -- confirmed directly against the live
    container for the long-shipped ``pca_analysis`` tool too, so this was a latent bug in
    every ``RunLinks``-based tool's smoke coverage, not something introduced by #489.
    ``structured_content`` is the server's actual JSON payload with no such
    reconstruction step, so it does not carry this risk for any field shape.
    """
    url, api_key = mcp_url_and_key()

    async def _call():
        async with Client(url, auth=api_key, timeout=120, init_timeout=15) as client:
            result = await client.call_tool(tool_name, {"params": params})
            return result.structured_content

    return _asdict(asyncio.run(_call()))


@pytest.fixture
def call_tool():
    """Injectable callable: ``call_tool("sleap_roots_qc_clean", {"experiment": ...})``.

    Synchronous on purpose (wraps ``asyncio.run`` internally) -- matches this repo's
    existing real-MCP-call test pattern (see ``test_qc_clean_appears_in_tools_list``
    and siblings in ``tests/tools/``), which never declares ``async def test_...``.
    """
    return _call_tool_sync


@pytest.fixture(params=["turface_19", "cylinder"])
def fixture_name(request) -> str:
    """Parametrizes a smoke test over both oracle fixtures (#483)."""
    return request.param


@pytest.fixture
def db_experiment_id(fixture_name: str) -> str:
    """Resolve ``fixture_name`` to the numeric experiment id a SupabaseReader-backed
    tool call should be called with.

    SupabaseReader's raw tier is DB-only (bloom#551): there is no local-CSV upload path
    matching env var is unset, since not every dev/CI environment has that DB seeding
    done yet.
    """
    env_var = EXPERIMENT_ID_ENV_VARS[fixture_name]
    experiment_id = os.environ.get(env_var, "")
    if not experiment_id:
        pytest.skip(
            f"{env_var} is unset -- set it to a numeric experiment id already seeded "
            f"with trait rows in Postgres for the {fixture_name!r} oracle fixture "
            "(SupabaseReader's raw tier is DB-only; there is no local-CSV upload path "
            "to fall back to)."
        )
    return experiment_id
