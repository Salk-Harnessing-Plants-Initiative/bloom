"""
MCP Tool Wrappers for SLEAP Outlier Detection.

Wraps functions from source/outlier_detection.py. Uses source/experiment_utils.py for
dynamic experiment discovery and column auto-detection. All write sites use the
versioned storage layer: each detection call lands in a new outlier_<stem>/v<N>_*/
directory and appends a manifest entry.
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path

from source.outlier_detection import (
    detect_outliers_mahalanobis as _detect_mahalanobis,
    detect_outliers_isolation_forest as _detect_isolation,
    detect_outliers_pca as _detect_pca,
    combine_outlier_methods,
    remove_outliers_from_data,
)
from source.experiment_utils import load_experiment_data as _load_data, OUTPUT_DIR, TRAITS_DIR
from storage import AnalysisDir, AnalysisWriter


def _writer_for(filename: str) -> AnalysisWriter:
    """Build an AnalysisWriter targeting the canonical outlier_<stem> directory."""
    source = TRAITS_DIR / filename
    return AnalysisWriter(
        OUTPUT_DIR,
        filename,
        "outlier",
        source_csv=source if source.exists() else None,
    )


# ============================================================================
# Tool 1: Mahalanobis Distance Outlier Detection
# ============================================================================

def detect_outliers_mahalanobis(
    filename: str,
    chi2_percentile: float = 97.5,
    user_label: str | None = None,
) -> str:
    """Detect outliers using Mahalanobis distance in PCA space.

    Measures how many standard deviations each sample is from the centroid,
    accounting for the covariance structure via PCA. Uses chi-squared
    distribution to set the threshold. Reports goodness-of-fit assessment.

    Args:
        filename: CSV filename from list_available_experiments
        chi2_percentile: Chi-squared percentile for threshold (default 97.5 = top 2.5%)
        user_label: Optional sluggified label tagged onto the version directory
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source

    stem = Path(filename).stem

    result = _detect_mahalanobis(
        data=df[trait_cols],
        chi2_percentile=chi2_percentile,
    )

    if "error" in result:
        return f"Mahalanobis detection error: {result['error']}"

    n_samples = len(result["mahalanobis_distances"])
    n_outliers = result["n_outliers"]
    pct = n_outliers / n_samples * 100 if n_samples > 0 else 0

    writer = _writer_for(filename)
    version_dir = writer.create_version(
        tool_name="detect_outliers_mahalanobis",
        params={"chi2_percentile": chi2_percentile},
        user_label=user_label,
    )
    summary = {
        "method": "Mahalanobis",
        "n_outliers": n_outliers,
        "n_samples": n_samples,
        "outlier_indices": result["outlier_indices"],
        "chi2_percentile": chi2_percentile,
    }
    (version_dir / "mahalanobis_outliers.json").write_text(json.dumps(summary, indent=2))
    entry = writer.commit({
        "mahalanobis_outliers.json": f"{version_dir.name}/mahalanobis_outliers.json",
    })

    lines = [
        f"Mahalanobis Outlier Detection: {stem} (source: {source}, version: {entry.id})",
        f"  {n_samples} samples, {len(trait_cols)} traits",
        f"  PCA components: {result['n_components']} (variance: {result['cumulative_variance_explained'] * 100:.1f}%)",
        f"  Threshold: chi2 at {chi2_percentile}th percentile = {result['threshold_value']:.2f}",
        f"  Outliers found: {n_outliers} ({pct:.1f}%)",
    ]

    gof = result.get("goodness_of_fit")
    if gof:
        lines.append(f"\n  Chi-squared Goodness-of-Fit:")
        lines.append(f"    Fit quality: {gof['fit_quality']}")
        lines.append(f"    K-S statistic: {gof['test_statistic']:.4f}")
        lines.append(f"    Assumption valid: {gof['distributional_assumption_valid']}")
        if "warning" in gof:
            lines.append(f"    Warning: {gof['warning']}")

    if n_outliers > 0:
        distances = np.array(result["mahalanobis_distances"])
        outlier_idx = result["outlier_indices"]
        outlier_dists = [(idx, distances[result["data_indices"].index(idx)]) for idx in outlier_idx]
        outlier_dists.sort(key=lambda x: x[1], reverse=True)

        lines.append(f"\n  Top outliers (by Mahalanobis distance):")
        for idx, dist in outlier_dists[:5]:
            lines.append(f"    Sample {idx}: distance = {dist:.2f}")

    lines.append(f"\n  Saved: {version_dir}/mahalanobis_outliers.json")

    return "\n".join(lines)


# ============================================================================
# Tool 2: Isolation Forest Outlier Detection
# ============================================================================

def detect_outliers_isolation_forest(
    filename: str,
    contamination: float = 0.1,
    user_label: str | None = None,
) -> str:
    """Detect outliers using Isolation Forest.

    Ensemble method that isolates anomalies by random feature splits.
    Outliers require fewer splits to isolate. Non-parametric — no distributional
    assumptions. The contamination parameter sets the expected outlier proportion.

    Args:
        filename: CSV filename from list_available_experiments
        contamination: Expected proportion of outliers (default 0.1 = 10%)
        user_label: Optional sluggified label tagged onto the version directory
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source

    stem = Path(filename).stem

    if not 0 < contamination < 0.5:
        return "contamination must be between 0 and 0.5."

    result = _detect_isolation(
        data=df[trait_cols],
        contamination=contamination,
    )

    if "error" in result:
        return f"Isolation Forest error: {result['error']}"

    n_samples = len(result["anomaly_scores"])
    n_outliers = result["n_outliers"]
    pct = n_outliers / n_samples * 100 if n_samples > 0 else 0

    writer = _writer_for(filename)
    version_dir = writer.create_version(
        tool_name="detect_outliers_isolation_forest",
        params={"contamination": contamination},
        user_label=user_label,
    )
    summary = {
        "method": "IsolationForest",
        "n_outliers": n_outliers,
        "n_samples": n_samples,
        "outlier_indices": result["outlier_indices"],
        "contamination": contamination,
    }
    (version_dir / "isolation_forest_outliers.json").write_text(json.dumps(summary, indent=2))
    entry = writer.commit({
        "isolation_forest_outliers.json": f"{version_dir.name}/isolation_forest_outliers.json",
    })

    lines = [
        f"Isolation Forest Outlier Detection: {stem} (source: {source}, version: {entry.id})",
        f"  {n_samples} samples, {len(trait_cols)} traits",
        f"  Contamination: {contamination} ({contamination * 100:.0f}% expected)",
        f"  Outliers found: {n_outliers} ({pct:.1f}%)",
    ]

    if n_outliers > 0:
        scores = np.array(result["anomaly_scores"])
        outlier_idx = result["outlier_indices"]
        outlier_scores = [(idx, scores[result["data_indices"].index(idx)]) for idx in outlier_idx]
        outlier_scores.sort(key=lambda x: x[1])

        lines.append(f"\n  Most anomalous samples (lower score = more anomalous):")
        for idx, score in outlier_scores[:5]:
            lines.append(f"    Sample {idx}: anomaly score = {score:.4f}")

    lines.append(f"\n  Saved: {version_dir}/isolation_forest_outliers.json")

    return "\n".join(lines)


# ============================================================================
# Tool 3: PCA Reconstruction Error Outlier Detection
# ============================================================================

def detect_outliers_pca(
    filename: str,
    outlier_threshold: float = 2.5,
    user_label: str | None = None,
) -> str:
    """Detect outliers using PCA reconstruction error.

    Samples that cannot be well-reconstructed from principal components have
    high reconstruction error. These are likely outliers that don't follow the
    main data patterns. Threshold is in standard deviations above the mean error.

    Args:
        filename: CSV filename from list_available_experiments
        outlier_threshold: Std deviations above mean error for outlier cutoff (default 2.5)
        user_label: Optional sluggified label tagged onto the version directory
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source

    stem = Path(filename).stem

    result = _detect_pca(
        data=df[trait_cols],
        outlier_threshold=outlier_threshold,
    )

    if "error" in result:
        return f"PCA reconstruction error: {result['error']}"

    n_samples = len(result["reconstruction_errors"])
    n_outliers = result["n_outliers"]
    pct = n_outliers / n_samples * 100 if n_samples > 0 else 0

    writer = _writer_for(filename)
    version_dir = writer.create_version(
        tool_name="detect_outliers_pca",
        params={"outlier_threshold": outlier_threshold},
        user_label=user_label,
    )
    summary = {
        "method": "PCA",
        "n_outliers": n_outliers,
        "n_samples": n_samples,
        "outlier_indices": result["outlier_indices"],
        "outlier_threshold": outlier_threshold,
    }
    (version_dir / "pca_outliers.json").write_text(json.dumps(summary, indent=2))
    entry = writer.commit({
        "pca_outliers.json": f"{version_dir.name}/pca_outliers.json",
    })

    lines = [
        f"PCA Reconstruction Outlier Detection: {stem} (source: {source}, version: {entry.id})",
        f"  {n_samples} samples, {len(trait_cols)} traits",
        f"  PCA components: {result['n_components']} (variance: {result['total_variance_explained'] * 100:.1f}%)",
        f"  Threshold: mean + {outlier_threshold} * std = {result['threshold_value']:.2f}",
        f"  Outliers found: {n_outliers} ({pct:.1f}%)",
    ]

    if n_outliers > 0:
        errors = np.array(result["reconstruction_errors"])
        outlier_idx = result["outlier_indices"]
        outlier_errs = [(idx, errors[result["data_indices"].index(idx)]) for idx in outlier_idx]
        outlier_errs.sort(key=lambda x: x[1], reverse=True)

        lines.append(f"\n  Top outliers (by reconstruction error):")
        for idx, err in outlier_errs[:5]:
            lines.append(f"    Sample {idx}: error = {err:.4f}")

    lines.append(f"\n  Saved: {version_dir}/pca_outliers.json")

    return "\n".join(lines)


# ============================================================================
# Tool 4: Consensus Outlier Detection
# ============================================================================

def run_consensus_outlier_detection(
    filename: str,
    consensus_threshold: float = 0.5,
    user_label: str | None = None,
) -> str:
    """Run all 3 outlier detection methods and report consensus results.

    Runs Mahalanobis, Isolation Forest, and PCA reconstruction error detection,
    then identifies samples flagged as outliers by multiple methods. Consensus
    outliers are more reliable than single-method results.

    Args:
        filename: CSV filename from list_available_experiments
        consensus_threshold: Fraction of methods that must agree (default 0.5 = 2 of 3)
        user_label: Optional sluggified label tagged onto the version directory
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source

    stem = Path(filename).stem

    mahal = _detect_mahalanobis(data=df[trait_cols])
    iso = _detect_isolation(data=df[trait_cols])
    pca_res = _detect_pca(data=df[trait_cols])

    combined = combine_outlier_methods(
        mahalanobis_results=mahal if "error" not in mahal else None,
        isolation_results=iso if "error" not in iso else None,
        pca_results=pca_res if "error" not in pca_res else None,
        consensus_threshold=consensus_threshold,
    )

    if "error" in combined:
        return f"Consensus detection error: {combined['error']}"

    n_consensus = combined["n_consensus_outliers"]
    methods_used = combined["agreement_summary"]["methods_compared"]

    method_counts = {
        "mahalanobis": mahal.get("n_outliers", 0) if "error" not in mahal else "failed",
        "isolation_forest": iso.get("n_outliers", 0) if "error" not in iso else "failed",
        "pca": pca_res.get("n_outliers", 0) if "error" not in pca_res else "failed",
    }

    writer = _writer_for(filename)
    version_dir = writer.create_version(
        tool_name="run_consensus_outlier_detection",
        params={"consensus_threshold": consensus_threshold},
        user_label=user_label,
    )
    summary = {
        "method": "Consensus",
        "n_consensus_outliers": n_consensus,
        "consensus_outliers": combined["consensus_outliers"],
        "consensus_threshold": consensus_threshold,
        "method_counts": {k: v for k, v in method_counts.items() if v != "failed"},
    }
    (version_dir / "consensus_outliers.json").write_text(json.dumps(summary, indent=2))
    entry = writer.commit({
        "consensus_outliers.json": f"{version_dir.name}/consensus_outliers.json",
    })

    lines = [
        f"Consensus Outlier Detection: {stem} (source: {source}, version: {entry.id})",
        f"  Methods used: {', '.join(methods_used)} ({len(methods_used)} total)",
        f"  Consensus rule: >= {consensus_threshold * 100:.0f}% agreement",
        "",
        "  Per-method results:",
    ]

    for method, count in method_counts.items():
        lines.append(f"    {method}: {count} outliers")

    lines.append(f"\n  Consensus outliers: {n_consensus}")

    agreement_count = combined.get("outlier_agreement_count", {})
    if agreement_count:
        lines.append(f"\n  Agreement distribution:")
        for n_agree in range(len(methods_used), 0, -1):
            samples_at_level = [idx for idx, cnt in agreement_count.items() if cnt == n_agree]
            if samples_at_level:
                lines.append(f"    Flagged by {n_agree}/{len(methods_used)} methods: {len(samples_at_level)} samples")

    if n_consensus > 0:
        consensus_idx = combined["consensus_outliers"]
        lines.append(f"\n  Consensus outlier indices: {consensus_idx[:20]}")
        if n_consensus > 20:
            lines.append(f"    ... ({n_consensus - 20} more)")

    lines.append(f"\n  Saved: {version_dir}/consensus_outliers.json")

    return "\n".join(lines)


# ============================================================================
# Tool 5: Remove Detected Outliers
# ============================================================================

_METHOD_TOOL_NAMES = {
    "consensus": "run_consensus_outlier_detection",
    "mahalanobis": "detect_outliers_mahalanobis",
    "isolation_forest": "detect_outliers_isolation_forest",
    "pca": "detect_outliers_pca",
}

_METHOD_OUTPUT_FILES = {
    "consensus": "consensus_outliers.json",
    "mahalanobis": "mahalanobis_outliers.json",
    "isolation_forest": "isolation_forest_outliers.json",
    "pca": "pca_outliers.json",
}


def _find_latest_method_version(filename: str, method: str):
    """Walk the outlier manifest for the latest entry produced by the named method.

    Returns (version_entry, version_dir_path) or (None, None) if no run found.
    """
    analysis_dir = AnalysisDir(OUTPUT_DIR, filename, "outlier")
    manifest = analysis_dir.read_manifest()
    if manifest is None:
        return None, None

    target_tool = _METHOD_TOOL_NAMES[method]
    expected_output = _METHOD_OUTPUT_FILES[method]
    matching = [v for v in manifest.versions if v.tool == target_tool]
    if not matching:
        return None, None

    matching.sort(key=lambda v: v.created_at, reverse=True)
    for entry in matching:
        rel = entry.outputs.get(expected_output)
        if not rel:
            continue
        full = analysis_dir.path / rel
        if full.exists():
            return entry, full
    return None, None


def remove_detected_outliers(
    filename: str,
    method: str = "consensus",
    user_label: str | None = None,
) -> str:
    """Remove outliers detected by a previous detection run and save cleaned CSV.

    Walks the outlier_<stem>/manifest.json for the latest run of the chosen
    method, reads its outlier indices, removes those samples from the dataset,
    and writes the cleaned CSV + flagged outlier rows into a new versioned
    directory. Re-running creates a new v<N>_*/.

    Args:
        filename: CSV filename from list_available_experiments
        method: Which detection result to use (consensus, mahalanobis, isolation_forest, pca)
        user_label: Optional sluggified label tagged onto the version directory
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source

    stem = Path(filename).stem

    if method not in _METHOD_OUTPUT_FILES:
        return f"Unknown method '{method}'. Choose from: {', '.join(_METHOD_OUTPUT_FILES.keys())}"

    src_entry, json_path = _find_latest_method_version(filename, method)
    if json_path is None:
        return (
            f"No saved {method} results found for '{stem}'. "
            f"Run the detection tool first (e.g., run_consensus_outlier_detection)."
        )

    saved = json.loads(json_path.read_text())
    outlier_key = "consensus_outliers" if method == "consensus" else "outlier_indices"
    outlier_indices = saved.get(outlier_key, [])

    if not outlier_indices:
        return f"No outliers to remove ({method} detection found 0 outliers for {stem})."

    cleaned_df, outlier_df = remove_outliers_from_data(
        df, outlier_indices, keep_metadata=True, return_outliers=True,
    )

    writer = _writer_for(filename)
    version_dir = writer.create_version(
        tool_name="remove_detected_outliers",
        params={"method": method, "source_version": src_entry.id},
        user_label=user_label,
    )
    cleaned_path = version_dir / f"{stem}_outliers_removed.csv"
    cleaned_df.to_csv(cleaned_path, index=False)
    outlier_path = version_dir / f"{stem}_outlier_samples.csv"
    outlier_df.to_csv(outlier_path, index=False)

    entry = writer.commit({
        f"{stem}_outliers_removed.csv": f"{version_dir.name}/{stem}_outliers_removed.csv",
        f"{stem}_outlier_samples.csv": f"{version_dir.name}/{stem}_outlier_samples.csv",
    })

    n_original = len(df)
    n_removed = len(outlier_df)
    n_remaining = len(cleaned_df)

    lines = [
        f"Outlier Removal: {stem} (method: {method}, version: {entry.id}, source: {src_entry.id})",
        f"  Original samples: {n_original}",
        f"  Outliers removed: {n_removed}",
        f"  Remaining samples: {n_remaining}",
        f"\n  Saved:",
        f"    Cleaned data: {cleaned_path}",
        f"    Outlier samples: {outlier_path}",
    ]

    return "\n".join(lines)


# ============================================================================
# Registration
# ============================================================================

def register(mcp):
    """Register all outlier detection tools with the MCP server."""
    mcp.tool()(detect_outliers_mahalanobis)
    mcp.tool()(detect_outliers_isolation_forest)
    mcp.tool()(detect_outliers_pca)
    mcp.tool()(run_consensus_outlier_detection)
    mcp.tool()(remove_detected_outliers)
