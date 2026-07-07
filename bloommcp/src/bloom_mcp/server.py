"""
Bloom MCP Server - Exposes SLEAP analysis tools via Model Context Protocol.

Transport: streamable-http on port 8811.

Surfaces:
  - Combined surface at /mcp — every tool, including each section's tools
    (namespaced). This is the endpoint the agent uses; unchanged.
  - One path per section (e.g. /phenotyping_segmentation/mcp) so a Claude
    Desktop client can load just that section. See bloom_mcp/sections/.

Workflow tools (one MCP call runs the full analysis):
  - run_qc_workflow
  - run_outlier_workflow
  - run_descriptive_stats_workflow
  - run_dimensionality_reduction_workflow
  - run_clustering_workflow

Discovery tools (always-on):
  - list_available_experiments
  - load_experiment_data
  - inspect_data_quality
  - list_existing_analyses

Direct tools (granular, available for ad-hoc use):
  - qc_clean:          clean a raw trait table for analysis (delegates to
                       sleap_roots_analyze.clean_traits_for_analysis)
  - pca_analysis:      PCA on a cleaned experiment (require_clean; delegates to
                       sleap_roots_analyze.perform_pca_analysis)
  - correlation_tools: 8 cross-experiment correlation tools
  - viz_tools:         7 plotting tools

Sections (per-package sub-servers, see bloom_mcp/sections/):
  - phenotyping_segmentation: Lin's segmentation tools (empty scaffold today)
"""

import hmac
import logging

from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount

from bloom_mcp import input_formats, uploads

# Env validation is lazy (see supabase_client / experiment_utils validate_env):
# importing this module no longer requires Supabase or the BLOOM_*_DIR env, so
# `import bloom_mcp` and the unit tests run with no env. main() calls both
# validators at startup to preserve fail-fast-at-boot for a misconfigured deploy.
from bloom_mcp.supabase_client import validate_env as validate_supabase_env
from bloom_mcp.experiment_utils import validate_env as validate_data_env

from bloom_mcp.auth import API_KEY, auth_provider

from bloom_mcp.tools import (
    qc_tools,
    viz_tools,
    correlation_tools,
    storage_tools,
    qc_clean_tool,
    pca_analysis_tool,
)
from bloom_mcp.tools.workflows import (
    clustering as clustering_workflow,
    dimred as dimred_workflow,
    outlier as outlier_workflow,
    qc as qc_workflow,
    stats as stats_workflow,
)
from bloom_mcp.sections import SECTIONS

logger = logging.getLogger(__name__)

# --- MCP Server (combined surface) ---

mcp = FastMCP("bloom-tools", auth=auth_provider)

# --- Register All Tool Modules ---

# Discovery tools (always-on)
qc_tools.register(mcp)
storage_tools.register(mcp)

# Workflow tools
qc_workflow.register(mcp)
outlier_workflow.register(mcp)
stats_workflow.register(mcp)
dimred_workflow.register(mcp)
clustering_workflow.register(mcp)

# Direct tools (granular)
qc_clean_tool.register(mcp)
pca_analysis_tool.register(mcp)
correlation_tools.register(mcp)
viz_tools.register(mcp)

# --- Sections ---
# Mount each section into the combined server so its tools appear on /mcp,
# namespaced as <section>_<tool>, for the agent.
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


# --- File upload surface ---
# Plain HTTP routes (not MCP tools — tool calls can't carry file bytes), gated by
# the same BLOOMMCP_API_KEY bearer as the MCP transport. Files land flat in
# bloommcp_input/ under bloom_agent; per-user identity/namespacing is deferred (#406).


def _authorized(request: Request) -> bool:
    """Constant-time Bearer check against BLOOMMCP_API_KEY.

    Mirrors the MCP transport's ApiKeyVerifier. When no API key is configured
    (dev mode) the routes are open, matching the server's existing dev behavior.
    """
    if not API_KEY:
        return True
    header = request.headers.get("authorization", "")
    prefix = "bearer "
    token = header[len(prefix):] if header.lower().startswith(prefix) else ""
    return bool(token) and hmac.compare_digest(token, API_KEY)


@mcp.custom_route("/uploads", methods=["POST"])
async def upload_input(request: Request) -> JSONResponse:
    """Receive a small/moderate input file (multipart `file`), validate it, and
    store it in bloommcp_input/. Large files should use `/uploads/sign` instead."""
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    # Reject an oversized upload from its Content-Length before buffering the body
    # into memory; direct it to the signed direct-to-Storage route instead.
    oversized = uploads.buffered_limit_exceeded(request.headers.get("content-length"))
    if oversized is not None:
        return JSONResponse(
            {
                "error": (
                    f"upload of {oversized} bytes exceeds the "
                    f"{input_formats.MAX_BUFFERED_UPLOAD_SIZE}-byte limit for this "
                    f"endpoint; use POST /uploads/sign for large files"
                ),
                "use": "/uploads/sign",
            },
            status_code=413,
        )
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "filename"):
        return JSONResponse({"error": "missing file"}, status_code=400)
    data = await upload.read()
    try:
        result = uploads.receive_upload(upload.filename, data)
    except input_formats.FileTooLargeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=413)
    except input_formats.FormatError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception:  # noqa: BLE001 - never leak internals to the caller
        logger.exception("input upload failed")
        return JSONResponse({"error": "internal error"}, status_code=500)
    return JSONResponse(result, status_code=201)


@mcp.custom_route("/uploads/sign", methods=["POST"])
async def sign_input_upload(request: Request) -> JSONResponse:
    """Mint a scoped signed upload URL for a large input so the client streams
    directly to Storage. Body: `{"filename": "<name.ext>"}`."""
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - malformed body
        body = {}
    filename = (body or {}).get("filename", "")
    try:
        result = uploads.signed_input_upload(filename)
    except input_formats.FormatError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception:  # noqa: BLE001 - never leak internals to the caller
        logger.exception("signed upload url failed")
        return JSONResponse({"error": "internal error"}, status_code=500)
    return JSONResponse(result, status_code=200)


# --- Entry Point ---


def main() -> None:
    """Validate env, inject persistence adapters, then serve the ASGI app.

    The validators run before the server binds the port so a misconfigured
    deploy fails fast at container boot — preserving the fail-fast that used to
    come from importing ``supabase_client`` / ``experiment_utils``.
    """
    validate_supabase_env()
    validate_data_env()

    # Composition root: inject the production persistence adapters into the
    # tools layer. Tools depend on the ports (bloom_mcp.tools._ports), never on
    # Supabase / AnalysisWriter directly, so swapping a backend is a change here.
    from bloom_mcp.data_access import SupabaseReader
    from bloom_mcp.result_store import SupabaseResultStore
    from bloom_mcp.tools import _ports

    _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    if API_KEY:
        print("Bloom MCP Server starting with API key authentication")
    else:
        print("Bloom MCP Server starting without authentication (dev mode)")

    import uvicorn

    uvicorn.run(build_app(), host="0.0.0.0", port=8811)


if __name__ == "__main__":
    main()
