"""run_outlier_workflow — outlier detection pipeline as a single MCP call.

Clubs together the 5 outlier-related primitives that used to be separate
MCP tools (detect_outliers_mahalanobis, detect_outliers_isolation_forest,
detect_outliers_pca, run_consensus_outlier_detection, remove_detected_outliers)
into one workflow with a `method` parameter and a `remove_outliers` flag.

Each call writes a single versioned directory `outlier_<stem>/v<N>_<date>/`
via AnalysisWriter. See `method` parameter docs for the 5 dispatch modes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from bloom_mcp.data_utils import convert_to_json_serializable
from bloom_mcp.outlier_detection import (
    combine_outlier_methods,
    detect_outliers_isolation_forest as _detect_isolation,
    detect_outliers_mahalanobis as _detect_mahalanobis,
    detect_outliers_pca as _detect_pca,
    remove_outliers_from_data,
)

from ._helpers import load_frame as _load_data, start_run, store

_TOOL_NAME = "run_outlier_workflow"
_TOOL_CLASS = "outlier"

VALID_METHODS = (
    "mahalanobis",
    "isolation_forest",
    "pca",
    "consensus",
    "all_then_consensus",
)


def run_outlier_workflow(
    filename: str,
    method: str = "consensus",
    chi2_percentile: float = 97.5,
    contamination: float = 0.1,
    pca_threshold_percentile: float = 97.5,
    consensus_threshold: float = 0.5,
    remove_outliers: bool = True,
    user_label: Optional[str] = None,
) -> dict:
    """Detect (and optionally remove) outliers, writing one versioned dir per call.

    Args:
        filename: CSV filename from `list_available_experiments`.
        method: One of `mahalanobis`, `isolation_forest`, `pca`, `consensus`,
            or `all_then_consensus`. `consensus` runs all three detectors
            and reports samples flagged by at least `consensus_threshold`
            fraction of them. `all_then_consensus` does the same but inlines
            every per-detector outlier set in the response under
            `summary.per_detector`.
        chi2_percentile: Threshold for Mahalanobis (default 97.5).
        contamination: Expected outlier fraction for Isolation Forest (default 0.1).
        pca_threshold_percentile: Threshold for PCA reconstruction error (default 97.5).
        consensus_threshold: Fraction of detectors that must agree (default 0.5).
        remove_outliers: If True (default), drop the flagged samples and write
            `<stem>_cleaned.csv` + `<stem>_outlier_samples.csv` alongside the
            outliers JSON. If False, only the detection JSON is written.
        user_label: Optional slug appended to the version directory name.

    Returns:
        WorkflowResponse dict — `version_id`, `version_dir`, `manifest_path`,
        `summary` (method, n_outliers, optional n_removed/n_remaining, optional
        per_detector counts), `outputs` (file paths relative to `version_dir`).
        On load failure or invalid method, returns `{"error": <message>}` and
        no version is created.
    """
    if method not in VALID_METHODS:
        return {
            "error": f"Unknown method '{method}'. Valid: {', '.join(VALID_METHODS)}",
        }

    df, trait_cols, config, source_label = _load_data(filename)
    if df is None:
        return {"error": source_label}

    stem = Path(filename).stem
    data = df[trait_cols]

    outlier_indices: list[int] = []
    summary: dict = {"method": method, "source": source_label}
    detection_outputs: dict[str, dict] = {}

    if method == "mahalanobis":
        res = _detect_mahalanobis(data=data, chi2_percentile=chi2_percentile)
        if "error" in res:
            return {"error": f"Mahalanobis detection failed: {res['error']}"}
        outlier_indices = res.get("outlier_indices", [])
        summary["n_outliers"] = len(outlier_indices)
        summary["chi2_percentile"] = chi2_percentile
        detection_outputs["mahalanobis_outliers.json"] = res

    elif method == "isolation_forest":
        res = _detect_isolation(data=data, contamination=contamination)
        if "error" in res:
            return {"error": f"Isolation Forest detection failed: {res['error']}"}
        outlier_indices = res.get("outlier_indices", [])
        summary["n_outliers"] = len(outlier_indices)
        summary["contamination"] = contamination
        detection_outputs["isolation_forest_outliers.json"] = res

    elif method == "pca":
        res = _detect_pca(data=data, threshold_percentile=pca_threshold_percentile)
        if "error" in res:
            return {"error": f"PCA detection failed: {res['error']}"}
        outlier_indices = res.get("outlier_indices", [])
        summary["n_outliers"] = len(outlier_indices)
        summary["pca_threshold_percentile"] = pca_threshold_percentile
        detection_outputs["pca_outliers.json"] = res

    else:  # consensus or all_then_consensus
        mahal = _detect_mahalanobis(data=data, chi2_percentile=chi2_percentile)
        iso = _detect_isolation(data=data, contamination=contamination)
        pca_res = _detect_pca(data=data, threshold_percentile=pca_threshold_percentile)
        combined = combine_outlier_methods(
            mahalanobis_results=mahal if "error" not in mahal else None,
            isolation_results=iso if "error" not in iso else None,
            pca_results=pca_res if "error" not in pca_res else None,
            consensus_threshold=consensus_threshold,
        )
        if "error" in combined:
            return {"error": f"Consensus detection failed: {combined['error']}"}

        outlier_indices = combined.get("consensus_outliers", [])
        summary["n_consensus_outliers"] = len(outlier_indices)
        summary["consensus_threshold"] = consensus_threshold
        per_detector_counts = {
            "mahalanobis": mahal.get("n_outliers", 0) if "error" not in mahal else None,
            "isolation_forest": (
                iso.get("n_outliers", 0) if "error" not in iso else None
            ),
            "pca": pca_res.get("n_outliers", 0) if "error" not in pca_res else None,
        }
        summary["per_detector"] = per_detector_counts
        detection_outputs["consensus_outliers.json"] = combined
        if method == "all_then_consensus":
            if "error" not in mahal:
                detection_outputs["mahalanobis_outliers.json"] = mahal
            if "error" not in iso:
                detection_outputs["isolation_forest_outliers.json"] = iso
            if "error" not in pca_res:
                detection_outputs["pca_outliers.json"] = pca_res

    run = start_run(
        filename,
        _TOOL_CLASS,
        _TOOL_NAME,
        {
            "method": method,
            "chi2_percentile": chi2_percentile,
            "contamination": contamination,
            "pca_threshold_percentile": pca_threshold_percentile,
            "consensus_threshold": consensus_threshold,
            "remove_outliers": remove_outliers,
        },
        user_label=user_label,
    )
    version_dir = run.staging_dir

    outputs: dict[str, str] = {}
    for fname, payload in detection_outputs.items():
        (version_dir / fname).write_text(
            json.dumps(convert_to_json_serializable(payload), indent=2)
        )
        outputs[fname] = fname

    if remove_outliers and outlier_indices:
        cleaned_df, outlier_df = remove_outliers_from_data(
            df,
            outlier_indices,
            keep_metadata=True,
            return_outliers=True,
        )
        cleaned_name = f"{stem}_cleaned.csv"
        outlier_name = f"{stem}_outlier_samples.csv"
        cleaned_df.to_csv(version_dir / cleaned_name, index=False)
        outlier_df.to_csv(version_dir / outlier_name, index=False)
        outputs[cleaned_name] = cleaned_name
        outputs[outlier_name] = outlier_name
        summary["n_original_samples"] = len(df)
        summary["n_removed"] = len(outlier_df)
        summary["n_remaining"] = len(cleaned_df)

    stored = store().commit(run, outputs)

    return {
        "version_id": stored.run_ref,
        "version_dir": str(version_dir),
        "manifest_path": run.manifest_path,
        "summary": summary,
        "outputs": outputs,
    }


def register(mcp):
    """Register run_outlier_workflow with the MCP server."""
    mcp.tool()(run_outlier_workflow)
