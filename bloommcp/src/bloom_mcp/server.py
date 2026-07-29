"""
Bloom MCP Server - Exposes SLEAP analysis tools via Model Context Protocol.

Transport: streamable-http on port 8811.

Surfaces:
  - Combined surface at /mcp — every tool, including each section's tools
    (namespaced). This is the endpoint the agent uses; unchanged.
  - One path per section (e.g. /sleap_roots/mcp) so a Claude Desktop client
    can load just that section. See bloom_mcp/sections/.

Every tool lives in a section (per-contributor/package sub-server; see
bloom_mcp/sections/) — there are no loose tools/*.py modules left to register
here. Sections (namespace -> tools):
  - core: cross-cutting discovery, not sleap-roots-analyze wrappers
    (list_available_experiments, load_experiment_data, list_existing_analyses)
  - sleap_roots: umbrella for the sleap-roots pipeline family. analysis/
    populated (qc_clean, qc_inspect, pca_analysis, remove_outliers, clustering,
    umap_analysis, descriptive_stats, + 5 plotting tools — histograms, boxplots,
    correlation matrix, heritability bar, variance decomposition — each
    delegating all analysis/plotting math to sleap_roots_analyze, never
    re-implementing it);
    extraction/ reserved for future sleap-roots trait-extraction tools (not
    built here).
  - phenotyping_segmentation: Lin's segmentation tools (empty scaffold today)

(The Phase-1 `run_*_workflow` tools — qc, outlier, stats, dimred, clustering —
were retired: they duplicated the granular tools and/or upstream, some were
broken, and they were the sole consumers of bloom-mcp's vendored analysis
modules. The 8 `correlation_tools` were dropped together with the vendored
`cross_experiment_correlations` module they wrapped — upstream's
`cross_experiment_analysis` has a different contract, so rewiring would have
silently changed numbers. See openspec/changes/devendor-bloommcp-analysis,
which also moved every surviving tool into the sections/ layout above.)
"""

import logging

from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Mount

# Env validation is lazy (see supabase_client / experiment_utils validate_env):
# importing this module no longer requires Supabase or the BLOOM_*_DIR env, so
# `import bloom_mcp` and the unit tests run with no env. main() calls both
# validators at startup to preserve fail-fast-at-boot for a misconfigured deploy.
from bloom_mcp.supabase_client import validate_env as validate_supabase_env
from bloom_mcp.experiment_utils import validate_env as validate_data_env

from bloom_mcp.auth import API_KEY, auth_provider

from bloom_mcp.sections import SECTIONS

logger = logging.getLogger(__name__)

# --- MCP Server (combined surface) ---

mcp = FastMCP("bloom-tools", auth=auth_provider)

# --- Sections ---
# Mount each section into the combined server so its tools appear on /mcp,
# namespaced as <section>_<tool>, for the agent. Every tool lives in a
# section — there is no per-tool server.py wiring left.
for _name, _section in SECTIONS.items():
    mcp.mount(_section, namespace=_name)


# --- Health Endpoint ---
# GET for Docker healthchecks. Bypasses MCP's SSE/JSON-RPC protocol so probes
# don't need an API key, custom Accept header, or POST body. Served at /health
# because the combined app is mounted at the app root (see build_app).
@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def build_app() -> Starlette:
    """Compose the combined surface and one path per section into one ASGI app.

    The combined surface (all tools + /health) stays at the app root, so /mcp
    and /health are unchanged for the agent and the Docker healthcheck. Each
    section is mounted at /<section> (e.g. /phenotyping_segmentation/mcp). All
    sub-app lifespans are combined so every streamable-http session manager
    starts.
    """
    combined_app = mcp.http_app(path="/mcp")
    section_apps = {
        name: section.http_app(path="/mcp") for name, section in SECTIONS.items()
    }

    # Section paths first (more specific); combined at root last so /mcp and
    # /health fall through to it.
    routes = [Mount(f"/{name}", app=app) for name, app in section_apps.items()]
    routes.append(Mount("/", app=combined_app))

    lifespans = [combined_app.lifespan, *(a.lifespan for a in section_apps.values())]
    return Starlette(routes=routes, lifespan=combine_lifespans(*lifespans))


# --- Entry Point ---


def main() -> None:
    """Validate the runtime env (backend-aware), then start the MCP server.

    The validators run before the server binds the port so a misconfigured
    deploy fails fast at container boot — preserving the fail-fast that used to
    come from importing ``supabase_client`` / ``experiment_utils``.

    Backend-aware gate: ``validate_data_env()`` runs in both modes — it validates
    the data directories AND the storage backend, so an invalid
    ``BLOOM_STORAGE_BACKEND`` value or an unusable local output root fails fast
    here. In fully-local mode (``BLOOM_STORAGE_BACKEND=local``) the Supabase
    credentials are not required and the local input root is validated instead;
    otherwise the Supabase gate runs exactly as before. prod/staging never set
    ``local``, so their fail-fast is unchanged.
    """
    from bloom_mcp.experiment_utils import validate_experiment_local_root
    from bloom_mcp.storage_backend import is_local_backend

    # Printed before validation (not after) so the active backend is visible
    # even when validate_data_env()/validate_supabase_env() fails fast below —
    # otherwise a misconfigured deploy never reveals which backend it tried.
    fully_local = is_local_backend()
    print(
        f"Bloom MCP Server storage backend: "
        f"{'local (fully-local/offline)' if fully_local else 'supabase'}"
    )

    validate_data_env()
    if fully_local:
        validate_experiment_local_root()
    else:
        validate_supabase_env()

    # Composition root: inject the persistence adapters into the tools layer.
    # Tools depend on the ports (bloom_mcp.tools._ports), never on Supabase
    # directly. The reader is coupled to the object-storage backend
    # (both local in fully-local mode) so inputs and outputs never split stores.
    # NOTE: the store is SupabaseResultStore() in *both* branches on purpose — in
    # fully-local mode its object-storage ops route through the active local backend
    # (per #389), so it makes no Supabase call; the local-ness lives in the backend
    # beneath the store, not in a separate store class.
    from bloom_mcp.result_store import SupabaseResultStore
    from bloom_mcp.tools import _ports

    if fully_local:
        from bloom_mcp.data_access import LocalReader

        _ports.configure(reader=LocalReader(), store=SupabaseResultStore())
    else:
        from bloom_mcp.data_access import SupabaseReader

        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    if API_KEY:
        print("Bloom MCP Server starting with API key authentication")
    else:
        print("Bloom MCP Server starting without authentication (dev mode)")

    import uvicorn

    uvicorn.run(build_app(), host="0.0.0.0", port=8811)


if __name__ == "__main__":
    main()
