"""run_qc_workflow — apply data-cleanup filters and save a versioned cleaned CSV."""

from __future__ import annotations

import json
from typing import Optional

from bloom_mcp import data_cleanup as cleanup
from bloom_mcp.data_utils import convert_to_json_serializable
from bloom_mcp.experiment_utils import CLEANED_CSV_NAME

from ._helpers import load_frame as _load_data, start_run, store

_TOOL_NAME = "run_qc_workflow"
_TOOL_CLASS = "qc"


def run_qc_workflow(
    filename: str,
    max_zeros_per_trait: float = 0.5,
    max_nans_per_trait: float = 0.3,
    max_nans_per_sample: float = 0.2,
    min_samples_per_trait: int = 10,
    user_label: Optional[str] = None,
) -> dict:
    """Apply data-cleanup filters to a SLEAP experiment and save a versioned cleaned CSV.

    Pipeline: remove zero-inflated traits → remove NaN-heavy traits → remove
    NaN-heavy samples → drop traits with too few valid samples. Writes the
    cleaned CSV plus a JSON cleanup log into a new versioned subdirectory
    `<OUTPUT_DIR>/qc_<stem>/v<N>_<date>/`; re-running creates `v<N+1>_*`
    rather than overwriting.

    Args:
        filename: CSV filename from `list_available_experiments`.
        max_zeros_per_trait: Max fraction of zeros per trait before removal (0-1).
        max_nans_per_trait: Max fraction of NaN per trait before removal (0-1).
        max_nans_per_sample: Max fraction of NaN per sample before removal (0-1).
        min_samples_per_trait: Min valid samples required per trait.
        user_label: Optional slug appended to the version directory name.

    Returns:
        WorkflowResponse dict — `version_id`, `version_dir`, `manifest_path`,
        `summary` (row + column counts in/out + retention), `outputs` (file
        paths relative to `version_dir`). On load failure, returns
        `{"error": <message>}`.
    """
    df, trait_cols, config, source_label = _load_data(filename)
    if df is None:
        return {"error": source_label}

    original_samples = len(df)
    original_traits = len(trait_cols)

    df_clean, cleanup_log = cleanup.apply_data_cleanup_filters(
        df,
        trait_cols,
        max_zeros_per_trait=max_zeros_per_trait,
        max_nans_per_trait=max_nans_per_trait,
        max_nans_per_sample=max_nans_per_sample,
        min_samples_per_trait=min_samples_per_trait,
    )

    run = start_run(
        filename,
        _TOOL_CLASS,
        _TOOL_NAME,
        {
            "max_zeros_per_trait": max_zeros_per_trait,
            "max_nans_per_trait": max_nans_per_trait,
            "max_nans_per_sample": max_nans_per_sample,
            "min_samples_per_trait": min_samples_per_trait,
        },
        user_label=user_label,
    )
    version_dir = run.staging_dir

    cleaned_csv = version_dir / CLEANED_CSV_NAME
    df_clean.to_csv(cleaned_csv, index=False)
    log_path = version_dir / "cleanup_log.json"
    with open(log_path, "w") as f:
        json.dump(convert_to_json_serializable(cleanup_log), f, indent=2)

    stored = store().commit(
        run,
        {
            CLEANED_CSV_NAME: CLEANED_CSV_NAME,
            "cleanup_log.json": "cleanup_log.json",
        },
    )

    final_samples = cleanup_log["final_samples"]
    final_traits = cleanup_log["final_traits"]
    retention_score = (
        (final_samples * final_traits) / (original_samples * original_traits)
        if original_samples and original_traits
        else 0.0
    )

    return {
        "version_id": stored.run_ref,
        "version_dir": stored.version_dir,
        "manifest_path": run.manifest_path,
        "summary": {
            "n_rows_in": original_samples,
            "n_rows_out": final_samples,
            "n_columns_in": original_traits,
            "n_columns_out": final_traits,
            "n_rows_dropped": original_samples - final_samples,
            "n_columns_dropped": original_traits - final_traits,
            "retention_score": round(retention_score, 4),
            "source": source_label,
        },
        "outputs": {
            "cleaned_csv": "_cleaned.csv",
            "cleanup_log_json": "cleanup_log.json",
        },
    }


def register(mcp):
    """Register run_qc_workflow with the MCP server."""
    mcp.tool()(run_qc_workflow)
