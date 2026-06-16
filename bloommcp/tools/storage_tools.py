"""MCP tool: list every prior analysis on file for an experiment."""
import json
import time

import source.experiment_utils as _eu
from storage import AnalysisDir, ManifestSchemaError

TOOL_CLASSES = (
    "qc",
    "stats",
    "dimred",
    "clustering",
    "outlier",
    "viz",
    "correlation",
)

# Logical prefix inside the bloommcp-data bucket for all versioned outputs.
# Mirrors the constant in tools/workflows/_helpers.py.
_STORAGE_OUTPUT_PREFIX = "bloommcp_output"

# Tiny per-experiment manifest cache. Each list_existing_analyses call walks
# N tool classes, each doing one storage GET; in a single LLM session the
# tool gets called repeatedly with the same filename, so a 30-second TTL
# amortises the network cost without risking stale reads across sessions.
_CACHE_TTL_SECONDS = 30
_RESPONSE_CACHE: dict[str, tuple[float, str]] = {}


def _now() -> float:
    return time.monotonic()


def list_existing_analyses(experiment_filename: str) -> str:
    """List every prior analysis recorded on file for this experiment.

    Walks each tool-class prefix under `bloommcp_output/` in the
    bloommcp-data bucket and aggregates every version recorded in each
    manifest. Use this at the start of any analysis session to see
    what's already been done and avoid redundant computation.

    Results are cached per experiment for 30 seconds.

    Args:
        experiment_filename: CSV filename, e.g. "alfalfa_gwas_wave2.csv"
    """
    cached = _RESPONSE_CACHE.get(experiment_filename)
    if cached is not None and _now() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    raw_path = _eu.TRAITS_DIR / experiment_filename
    if not raw_path.exists():
        available = ", ".join(e["filename"] for e in _eu.list_experiments()) or "none"
        return json.dumps(
            {
                "error": f"Experiment '{experiment_filename}' not found in {_eu.TRAITS_DIR}",
                "available_experiments": available,
            },
            indent=2,
        )

    by_tool_class: dict[str, list[dict]] = {}
    errors: list[str] = []

    for tool_class in TOOL_CLASSES:
        analysis_dir = AnalysisDir(
            _STORAGE_OUTPUT_PREFIX, experiment_filename, tool_class
        )
        try:
            versions = analysis_dir.list_versions()
        except ManifestSchemaError as e:
            errors.append(f"{tool_class}: {e}")
            continue
        if versions:
            by_tool_class[tool_class] = [v.model_dump(mode="json") for v in versions]

    response: dict = {
        "experiment_filename": experiment_filename,
        "analyses": by_tool_class,
    }
    if not by_tool_class:
        response["message"] = (
            f"No prior analyses found for '{experiment_filename}'."
        )
    if errors:
        response["errors"] = errors

    response_str = json.dumps(response, indent=2)
    _RESPONSE_CACHE[experiment_filename] = (_now(), response_str)
    return response_str


def register(mcp):
    """Register storage introspection tools with the MCP server."""
    mcp.tool()(list_existing_analyses)
