"""
MCP Tool Wrappers for SLEAP Outlier Detection.

Wraps functions from source/outlier_detection.py. Uses source/experiment_utils.py for
dynamic experiment discovery and column auto-detection.
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
from source.experiment_utils import load_experiment_data as _load_data, OUTPUT_DIR


# ============================================================================
# Tool 1: Mahalanobis Distance Outlier Detection
# ============================================================================

def detect_outliers_mahalanobis(
    filename: str,
    chi2_percentile: float = 97.5,
) -> str:
    """Detect outliers using Mahalanobis distance in PCA space.

    Measures how many standard deviations each sample is from the centroid,
    accounting for the covariance structure via PCA. Uses chi-squared
    distribution to set the threshold. Reports goodness-of-fit assessment.

    Args:
        filename: CSV filename from list_available_experiments
        chi2_percentile: Chi-squared percentile for threshold (default 97.5 = top 2.5%)
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

    lines = [
        f"Mahalanobis Outlier Detection: {stem} (source: {source})",
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

    out_dir = OUTPUT_DIR / f"outliers_{stem}"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "method": "Mahalanobis",
        "n_outliers": n_outliers,
        "n_samples": n_samples,
        "outlier_indices": result["outlier_indices"],
        "chi2_percentile": chi2_percentile,
    }
    (out_dir / "mahalanobis_outliers.json").write_text(json.dumps(summary, indent=2))

    return "\n".join(lines)


# ============================================================================
# Tool 2: Isolation Forest Outlier Detection
# ============================================================================

def detect_outliers_isolation_forest(
    filename: str,
    contamination: float = 0.1,
) -> str:
    """Detect outliers using Isolation Forest.

    Ensemble method that isolates anomalies by random feature splits.
    Outliers require fewer splits to isolate. Non-parametric — no distributional
    assumptions. The contamination parameter sets the expected outlier proportion.

    Args:
        filename: CSV filename from list_available_experiments
        contamination: Expected proportion of outliers (default 0.1 = 10%)
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

    lines = [
        f"Isolation Forest Outlier Detection: {stem} (source: {source})",
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

    out_dir = OUTPUT_DIR / f"outliers_{stem}"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "method": "IsolationForest",
        "n_outliers": n_outliers,
        "n_samples": n_samples,
        "outlier_indices": result["outlier_indices"],
        "contamination": contamination,
    }
    (out_dir / "isolation_forest_outliers.json").write_text(json.dumps(summary, indent=2))

    return "\n".join(lines)


# ============================================================================
# Tool 3: PCA Reconstruction Error Outlier Detection
# ============================================================================

def detect_outliers_pca(
    filename: str,
    outlier_threshold: float = 2.5,
) -> str:
    """Detect outliers using PCA reconstruction error.

    Samples that cannot be well-reconstructed from principal components have
    high reconstruction error. These are likely outliers that don't follow the
    main data patterns. Threshold is in standard deviations above the mean error.

    Args:
        filename: CSV filename from list_available_experiments
        outlier_threshold: Std deviations above mean error for outlier cutoff (default 2.5)
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

    lines = [
        f"PCA Reconstruction Outlier Detection: {stem} (source: {source})",
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

    out_dir = OUTPUT_DIR / f"outliers_{stem}"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "method": "PCA",
        "n_outliers": n_outliers,
        "n_samples": n_samples,
        "outlier_indices": result["outlier_indices"],
        "outlier_threshold": outlier_threshold,
    }
    (out_dir / "pca_outliers.json").write_text(json.dumps(summary, indent=2))

    return "\n".join(lines)


# ============================================================================
# Tool 4: Consensus Outlier Detection
# ============================================================================

def run_consensus_outlier_detection(
    filename: str,
    consensus_threshold: float = 0.5,
) -> str:
    """Run all 3 outlier detection methods and report consensus results.

    Runs Mahalanobis, Isolation Forest, and PCA reconstruction error detection,
    then identifies samples flagged as outliers by multiple methods. Consensus
    outliers are more reliable than single-method results.

    Args:
        filename: CSV filename from list_available_experiments
        consensus_threshold: Fraction of methods that must agree (default 0.5 = 2 of 3)
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

    lines = [
        f"Consensus Outlier Detection: {stem} (source: {source})",
        f"  Methods used: {', '.join(methods_used)} ({len(methods_used)} total)",
        f"  Consensus rule: >= {consensus_threshold * 100:.0f}% agreement",
        "",
        "  Per-method results:",
    ]

    method_counts = {
        "mahalanobis": mahal.get("n_outliers", 0) if "error" not in mahal else "failed",
        "isolation_forest": iso.get("n_outliers", 0) if "error" not in iso else "failed",
        "pca": pca_res.get("n_outliers", 0) if "error" not in pca_res else "failed",
    }

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

    out_dir = OUTPUT_DIR / f"outliers_{stem}"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "method": "Consensus",
        "n_consensus_outliers": n_consensus,
        "consensus_outliers": combined["consensus_outliers"],
        "consensus_threshold": consensus_threshold,
        "method_counts": {k: v for k, v in method_counts.items() if v != "failed"},
    }
    (out_dir / "consensus_outliers.json").write_text(json.dumps(summary, indent=2))

    return "\n".join(lines)


# ============================================================================
# Tool 5: Remove Detected Outliers
# ============================================================================

def remove_detected_outliers(
    filename: str,
    method: str = "consensus",
) -> str:
    """Remove outliers detected by a previous detection run and save cleaned CSV.

    Reads outlier indices from a saved JSON result file and removes those
    samples from the dataset. Saves cleaned CSV to BLOOM_OUTPUT_DIR.

    Args:
        filename: CSV filename from list_available_experiments
        method: Which detection result to use (consensus, mahalanobis, isolation_forest, pca)
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source

    stem = Path(filename).stem

    method_files = {
        "consensus": "consensus_outliers.json",
        "mahalanobis": "mahalanobis_outliers.json",
        "isolation_forest": "isolation_forest_outliers.json",
        "pca": "pca_outliers.json",
    }

    if method not in method_files:
        return f"Unknown method '{method}'. Choose from: {', '.join(method_files.keys())}"

    out_dir = OUTPUT_DIR / f"outliers_{stem}"
    json_path = out_dir / method_files[method]

    if not json_path.exists():
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

    cleaned_dir = OUTPUT_DIR / f"outliers_{stem}"
    cleaned_dir.mkdir(parents=True, exist_ok=True)
    cleaned_path = cleaned_dir / f"{stem}_outliers_removed.csv"
    cleaned_df.to_csv(cleaned_path, index=False)

    outlier_path = cleaned_dir / f"{stem}_outlier_samples.csv"
    outlier_df.to_csv(outlier_path, index=False)

    n_original = len(df)
    n_removed = len(outlier_df)
    n_remaining = len(cleaned_df)

    lines = [
        f"Outlier Removal: {stem} (method: {method})",
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
