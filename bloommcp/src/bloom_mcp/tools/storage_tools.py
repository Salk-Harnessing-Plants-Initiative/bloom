"""MCP tool: list every prior analysis on file for an experiment."""

import dataclasses
import json
import time

from bloom_mcp.tools import _ports

TOOL_CLASSES = (
    "qc",
    "stats",
    "dimred",
    "clustering",
    "outlier",
    "viz",
    "correlation",
)

# Tiny per-experiment response cache. Each list_existing_analyses call walks
# N tool classes, each doing one storage GET; in a single LLM session the
# tool gets called repeatedly with the same filename, so a 30-second TTL
# amortises the network cost without risking stale reads across sessions.
_CACHE_TTL_SECONDS = 30
_RESPONSE_CACHE: dict[str, tuple[float, str]] = {}


def _now() -> float:
    return time.monotonic()


def list_existing_analyses(experiment_filename: str) -> str:
    """List every prior analysis recorded on file for this experiment.

    Aggregates every recorded run across each tool class via the injected
    ``ResultStore``. Use this at the start of any analysis session to see
    what's already been done and avoid redundant computation.

    Results are cached per experiment for 30 seconds.

    Args:
        experiment_filename: CSV filename, e.g. "alfalfa_gwas_wave2.csv"
    """
    cached = _RESPONSE_CACHE.get(experiment_filename)
    if cached is not None and _now() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    known = {exp.filename for exp in _ports.reader().list_experiments()}
    if known and experiment_filename not in known:
        return json.dumps(
            {
                "error": f"Experiment '{experiment_filename}' not found",
                "available_experiments": ", ".join(sorted(known)),
            },
            indent=2,
        )

    by_tool_class: dict[str, list[dict]] = {}
    errors: list[str] = []
    store = _ports.store()

    for tool_class in TOOL_CLASSES:
        try:
            runs = store.list_runs(experiment_filename, tool_class)
        except Exception as exc:  # noqa: BLE001 - aggregate, never fail the whole call
            errors.append(f"{tool_class}: {exc}")
            continue
        if runs:
            by_tool_class[tool_class] = [dataclasses.asdict(r) for r in runs]

    response: dict = {
        "experiment_filename": experiment_filename,
        "analyses": by_tool_class,
    }
    if not by_tool_class:
        response["message"] = f"No prior analyses found for '{experiment_filename}'."
    if errors:
        response["errors"] = errors

    response_str = json.dumps(response, indent=2)
    _RESPONSE_CACHE[experiment_filename] = (_now(), response_str)
    return response_str


def register(mcp):
    """Register storage introspection tools with the MCP server."""
    mcp.tool()(list_existing_analyses)
