"""MCP tool: list every prior analysis on file for an experiment."""
import json

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


def list_existing_analyses(experiment_filename: str) -> str:
    """List every prior analysis recorded on disk for this experiment.

    Walks each tool-class directory (qc_*, stats_*, dimred_*, clustering_*,
    outlier_*, viz_*, correlation_*) under BLOOM_OUTPUT_DIR and aggregates
    every version recorded in each manifest. Use this at the start of any
    analysis session to see what's already been done and avoid redundant
    computation.

    Args:
        experiment_filename: CSV filename, e.g. "alfalfa_gwas_wave2.csv"
    """
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
        analysis_dir = AnalysisDir(_eu.OUTPUT_DIR, experiment_filename, tool_class)
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
    return json.dumps(response, indent=2)


def register(mcp):
    """Register storage introspection tools with the MCP server."""
    mcp.tool()(list_existing_analyses)
