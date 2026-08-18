"""list_existing_analyses — discovery tool: list every prior analysis on file.

Not a ``sleap-roots-analyze`` wrapper — reads through the injected
``ResultStore`` port. Always-included in the agent's tool set.
"""

import dataclasses
import json
import time
from pathlib import Path

# `trim_staleness` reads manifests directly through `AnalysisDir`/the storage
# backend, not through the injected `ResultStore`/`ExperimentReader` ports this
# file otherwise depends on exclusively (see test_persistence_import_guard.py).
# Disclosed, narrow exception (design.md Decision 2, bloom#585): it is an
# ambient, advisory-only hint layered on top of the analyses payload below, not
# a replacement data path.
from bloom_mcp.experiment_utils import (
    OUTLIERS_TOOL_CLASS,
    QC_TOOL_CLASS,
    safe_error_text,
    trim_staleness,
)
from bloom_mcp.tools import _ports

# Kept intact across tool retirements (devendor-bloommcp-analysis) so historical
# runs persisted under a retired tool class (stats, dimred, outlier, correlation)
# still read back via list_existing_analyses. Do NOT prune retired classes.
# `qc`/`outliers` reference the single-sourced constants in `experiment_utils`
# (the producers, `qc_clean.py`/`remove_outliers.py`, do too) rather than
# re-typing the literal — a typo here would silently hide trimmed runs from
# this tool's output, exactly the drift class #420 is about.
TOOL_CLASSES = (
    QC_TOOL_CLASS,
    "stats",
    "dimred",
    "clustering",
    "outlier",
    OUTLIERS_TOOL_CLASS,
    "viz",
    "correlation",
)

# Public MCP tool name for each tool_class this loop iterates that maps to a
# current tool (bloom#664 item 3) — so a `list_runs` failure names the tool an
# agent actually invoked, not its internal storage-namespacing string. The 3
# retired/legacy entries above (`dimred`, `outlier`, `viz`) have no current
# tool and are deliberately absent here; `_TOOL_CLASS_TO_PUBLIC_NAME.get(...)`
# falls back to the raw tool_class for those.
_TOOL_CLASS_TO_PUBLIC_NAME: dict[str, str] = {
    QC_TOOL_CLASS: "qc_clean",
    "stats": "descriptive_stats",
    "clustering": "clustering",
    OUTLIERS_TOOL_CLASS: "remove_outliers",
    "correlation": "cross_experiment_correlations",
}

# Tiny per-experiment response cache. Each list_existing_analyses call walks
# N tool classes, each doing one storage GET; in a single LLM session the
# tool gets called repeatedly with the same filename, so a 30-second TTL
# amortises the network cost without risking stale reads across sessions.
_CACHE_TTL_SECONDS = 30
_RESPONSE_CACHE: dict[str, tuple[float, str]] = {}


def _now() -> float:
    return time.monotonic()


def list_existing_analyses(experiment: str) -> str:
    """List every prior analysis recorded on file for this experiment.

    Aggregates every recorded run across each tool class via the injected
    ``ResultStore``. Use this at the start of any analysis session to see
    what's already been done and avoid redundant computation.

    Results are cached per experiment for 30 seconds — including ``trim_is_stale``
    below, with no invalidation hook on a ``qc_clean``/``remove_outliers`` commit:
    calling this, then running the action that makes a trim stale, then
    re-checking within the cache window can return the pre-action, now-stale
    cached value. Call again after the 30-second window (or treat a
    just-completed commit as invalidating your own cached assumption about
    this experiment) rather than trusting an immediate re-check.

    The response includes a top-level ``trim_is_stale`` boolean whenever this
    experiment has an ``outliers``-class (``remove_outliers``) version, so a
    stale trim (a ``qc_clean`` has run since it was made) is visible without a
    separate ``require_clean=True`` read. This field is advisory only: it is
    omitted both when the experiment has never been trimmed and when the check
    itself fails — if it is absent, check ``errors`` for a ``trim_staleness``
    entry before concluding the experiment was never trimmed. When present,
    ``trim_based_on_qc_version`` names the ``qc``-class version the trim was
    made from, and ``trim_current_qc_version`` names the ``qc``-class version
    that is current now (``None`` when no ``qc``-class version exists for this
    experiment at all — a corruption-adjacent state, not ordinary staleness;
    see ``experiment_utils.trim_staleness``) — the same distinction the
    server-side staleness log makes, now visible to whoever is calling this
    tool rather than only in a log line they can't see.

    Args:
        experiment: experiment identifier, e.g. "alfalfa_gwas_wave2.csv"
    """
    cached = _RESPONSE_CACHE.get(experiment)
    if cached is not None and _now() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    known = {exp.filename for exp in _ports.reader().list_experiments()}
    if known and experiment not in known:
        return json.dumps(
            {
                "error": f"Experiment '{experiment}' not found",
                "available_experiments": ", ".join(sorted(known)),
            },
            indent=2,
        )

    by_tool_class: dict[str, list[dict]] = {}
    errors: list[str] = []
    store = _ports.store()

    for tool_class in TOOL_CLASSES:
        try:
            runs = store.list_runs(experiment, tool_class)
        except Exception as exc:  # noqa: BLE001 - aggregate, never fail the whole call
            public_name = _TOOL_CLASS_TO_PUBLIC_NAME.get(tool_class, tool_class)
            errors.append(f"{public_name}: {safe_error_text(exc)}")
            continue
        if runs:
            by_tool_class[tool_class] = [dataclasses.asdict(r) for r in runs]

    staleness = None
    try:
        staleness = trim_staleness(Path(experiment).stem)
    except Exception as exc:  # noqa: BLE001 - advisory-only; never fail the whole call
        errors.append(f"trim_staleness: {safe_error_text(exc)}")

    response: dict = {
        "experiment": experiment,
        "analyses": by_tool_class,
    }
    if not by_tool_class:
        response["message"] = f"No prior analyses found for '{experiment}'."
    if staleness is not None:
        response["trim_is_stale"] = staleness.is_stale
        response["trim_based_on_qc_version"] = staleness.outliers_based_on_version
        response["trim_current_qc_version"] = staleness.current_qc_label
    if errors:
        response["errors"] = errors

    response_str = json.dumps(response, indent=2)
    _RESPONSE_CACHE[experiment] = (_now(), response_str)
    return response_str
