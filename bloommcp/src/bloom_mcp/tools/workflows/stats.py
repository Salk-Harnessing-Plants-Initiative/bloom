"""run_descriptive_stats_workflow — per-trait descriptive statistics + versioned CSV.

Clubs together the descriptive-statistics surface that used to be the
`get_trait_statistics` MCP tool, and ADDS disk-write integration that the
old tool lacked. Each call writes `stats.csv` (the full per-trait table)
into a new versioned directory `stats_<stem>/v<N>_<date>/` via AnalysisWriter.

ANOVA, heritability, and heritability-diagnostic logic from the original
`stats_tools.py` move to dedicated workflows (heritability + group-comparison)
in later phases — this workflow is descriptive stats only.
"""

from __future__ import annotations

from typing import Optional

from bloom_mcp import trait_statistics as _stats_module

from ._helpers import load_frame as _load_data, start_run, store

_TOOL_NAME = "run_descriptive_stats_workflow"
_TOOL_CLASS = "stats"
_SUMMARY_TRAIT_CAP = 50


def run_descriptive_stats_workflow(
    filename: str,
    traits: str = "",
    user_label: Optional[str] = None,
) -> dict:
    """Compute per-trait descriptive statistics and save a versioned CSV.

    Reports n, mean, std, median, q25, q75, min, max, CV, skewness, and
    kurtosis for each numeric trait. The full table is written to
    `stats.csv` inside the version dir; the response's `summary` is capped
    to the first `_SUMMARY_TRAIT_CAP` (= 50) traits to keep the payload
    bounded for very wide experiments.

    Args:
        filename: CSV filename from `list_available_experiments`.
        traits: Comma-separated list of trait names to include. Empty (default)
            means all numeric traits.
        user_label: Optional slug appended to the version directory name.

    Returns:
        WorkflowResponse dict — `version_id`, `version_dir`, `manifest_path`,
        `summary` (n_traits, n_failed, stats_per_trait[:50], source),
        `outputs` (stats_csv path). On load failure or no matching traits,
        returns `{"error": <message>}` with no version created.
    """
    df, trait_cols, config, source_label = _load_data(filename)
    if df is None:
        return {"error": source_label}

    if traits.strip():
        requested = [t.strip() for t in traits.split(",")]
        selected = [t for t in requested if t in trait_cols]
        if not selected:
            return {
                "error": (
                    f"None of the requested traits found. "
                    f"Available (first 10): {', '.join(trait_cols[:10])}"
                ),
            }
        trait_cols = selected

    results = _stats_module.calculate_trait_statistics(df, trait_cols)

    rows: list[dict] = []
    failed: list[str] = []
    for trait in trait_cols:
        r = results.get(trait, {})
        if "error" in r:
            failed.append(trait)
            continue
        rows.append(
            {
                "trait": trait,
                "n": r.get("count"),
                "mean": r.get("mean"),
                "std": r.get("std"),
                "median": r.get("median"),
                "q25": r.get("q25"),
                "q75": r.get("q75"),
                "min": r.get("min"),
                "max": r.get("max"),
                "cv": r.get("cv"),
                "skewness": r.get("skewness"),
                "kurtosis": r.get("kurtosis"),
            }
        )

    import pandas as pd

    stats_df = pd.DataFrame(rows)

    run = start_run(
        filename,
        _TOOL_CLASS,
        _TOOL_NAME,
        {"traits": traits if traits.strip() else "all"},
        user_label=user_label,
    )
    version_dir = run.staging_dir

    stats_csv = version_dir / "stats.csv"
    stats_df.to_csv(stats_csv, index=False)

    stored = store().commit(
        run,
        {
            "stats.csv": "stats.csv",
        },
    )

    return {
        "version_id": stored.run_ref,
        "version_dir": str(version_dir),
        "manifest_path": run.manifest_path,
        "summary": {
            "n_traits": len(rows),
            "n_failed": len(failed),
            "failed_traits": failed[:10],
            "stats_per_trait": rows[:_SUMMARY_TRAIT_CAP],
            "truncated_in_summary": len(rows) > _SUMMARY_TRAIT_CAP,
            "source": source_label,
        },
        "outputs": {
            "stats_csv": "stats.csv",
        },
    }


def register(mcp):
    """Register run_descriptive_stats_workflow with the MCP server."""
    mcp.tool()(run_descriptive_stats_workflow)
