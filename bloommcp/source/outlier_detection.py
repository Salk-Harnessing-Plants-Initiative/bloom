"""Outlier detection using Mahalanobis distance on PCA-transformed data."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import IsolationForest

from .pca import (
    perform_pca_analysis,
    calculate_mahalanobis_distances,
    calculate_pca_metrics,
    build_feature_metrics_df,
    calculate_pca_reconstruction_error,
    standardize_data,
)


def detect_outliers_mahalanobis(
    data: Union[pd.DataFrame, np.ndarray],
    standardize: bool = True,
    variance_threshold: float = 0.95,
    use_chi_squared: bool = True,
    chi2_percentile: float = 97.5,
    distance_threshold: Optional[float] = None,
    robust_covariance: bool = False,
    random_state: int = 42,
) -> Dict:
    """Detect outliers using Mahalanobis distance on PCA-transformed data.

    Mahalanobis distance measures how many standard deviations away a point is
    from the mean, accounting for the covariance structure of the data.

    Args:
        data: Input data as DataFrame or array
        standardize: Whether to standardize data before PCA
        variance_threshold: Cumulative variance threshold for PC selection (0-1)
        use_chi_squared: Use chi-squared distribution threshold
        chi2_percentile: Percentile for chi-squared threshold (0-100)
        distance_threshold: Direct Mahalanobis distance threshold (if not using chi-squared)
        robust_covariance: Use robust covariance estimation (MinCovDet)
        random_state: Random seed for reproducibility

    Returns:
        Dictionary with outlier detection results including:
        - outlier_indices: List of outlier sample indices
        - mahalanobis_distances: Distance for each sample
        - n_outliers: Number of outliers detected
        - n_components: Number of PCA components used
        - threshold_type: Type of threshold used
        - threshold_value: Threshold value used
        - feature_names: List of feature names
        - goodness_of_fit: Dict with chi-squared GOF test results (if use_chi_squared=True)
        - error: Error message if detection failed
    """
    # Convert to DataFrame to handle indices consistently
    if isinstance(data, np.ndarray):
        df = pd.DataFrame(data, columns=[f"Feature_{i}" for i in range(data.shape[1])])
    else:
        df = data.copy()

    # Track indices - PCA will drop NaN rows, we need to track valid indices
    # perform_pca_analysis handles NaN removal internally
    original_indices = df.dropna().index.tolist()

    try:
        # Perform PCA analysis with the specified variance threshold
        # This will handle NaN removal internally
        pca_result = perform_pca_analysis(
            df,
            standardize=standardize,
            explained_variance_threshold=variance_threshold,
            n_components=None,
            random_state=random_state,
        )

        # Use all selected components for Mahalanobis distance
        X_pca = pca_result["transformed_data"]
        n_components = pca_result["n_components_selected"]

        # Calculate Mahalanobis distances
        distances, mean_pca, cov_matrix = calculate_mahalanobis_distances(
            X_pca, robust=robust_covariance
        )

        # Calculate threshold
        threshold, threshold_type = calculate_outlier_threshold(
            n_components=n_components,
            use_chi_squared=use_chi_squared,
            chi2_percentile=chi2_percentile,
            distance_threshold=distance_threshold,
        )

        # Identify outliers
        outlier_result = identify_outliers_from_distances(
            distances=distances,
            threshold=threshold,
            threshold_type=threshold_type,
            indices=pd.Index(original_indices),
        )

        # Get per-feature metrics directly from PCA result
        # Since we're using all selected components, we can use the metrics as-is
        pca_metrics = calculate_pca_metrics(
            pca_result["pca"],
            X_pca,
            X_fitted=pca_result.get("data_processed"),
            ddof_for_feature_var=1,
        )

        # Extract the metrics we need (using correct keys from calculate_pca_metrics)
        feature_var_explained = pca_metrics.get(
            "explained_variance_per_feature", np.zeros(len(pca_result["feature_names"]))
        )
        feature_fraction_explained = pca_metrics.get(
            "explained_variance_ratio_per_feature", feature_var_explained
        )

        # Calculate goodness-of-fit test if using chi-squared threshold
        goodness_of_fit = None
        if use_chi_squared:
            # Validate chi-squared distribution assumption
            distances_squared = distances**2
            goodness_of_fit = validate_chi_squared_distribution(
                distances_squared, df=n_components
            )

        # Compile results
        result = {
            "method": "Mahalanobis",
            "variance_threshold": variance_threshold,
            "n_components": n_components,
            "cumulative_variance_explained": float(
                pca_result["cumulative_variance_ratio"][-1]
            ),
            "threshold_type": threshold_type,
            "threshold_value": float(threshold),
            "chi2_percentile": chi2_percentile if use_chi_squared else None,
            "distance_threshold": distance_threshold if not use_chi_squared else None,
            "mahalanobis_distances": distances.tolist(),
            "outlier_indices": outlier_result["outlier_indices"],
            "n_outliers": outlier_result["n_outliers"],
            "degrees_of_freedom": n_components,
            "explained_variance_ratio": pca_result["explained_variance_ratio"].tolist(),
            "pca_loadings": pca_result["loadings"].tolist(),
            "eigenvalues": pca_result["eigenvalues"].tolist(),
            "pca_components": X_pca.tolist(),
            "feature_names": pca_result["feature_names"],
            "data_indices": original_indices,
            "feature_variance_explained": feature_var_explained.tolist(),
            "feature_fraction_explained": feature_fraction_explained.tolist(),
            "robust_covariance": robust_covariance,
            "goodness_of_fit": goodness_of_fit,
        }

        return result

    except Exception as e:
        return {
            "method": "Mahalanobis",
            "outlier_indices": [],
            "error": f"Mahalanobis distance calculation failed: {str(e)}",
        }


def calculate_outlier_threshold(
    n_components: int,
    use_chi_squared: bool = True,
    chi2_percentile: float = 97.5,
    distance_threshold: Optional[float] = None,
) -> Tuple[float, str]:
    """Calculate threshold for outlier detection.

    Args:
        n_components: Number of PCA components (degrees of freedom)
        use_chi_squared: Whether to use chi-squared distribution
        chi2_percentile: Percentile for chi-squared threshold (0-100)
        distance_threshold: Direct distance threshold

    Returns:
        Tuple of (threshold value, threshold type string)

    Raises:
        ValueError: If parameters are invalid
    """
    # Validate inputs
    if n_components <= 0:
        raise ValueError(f"n_components must be positive, got {n_components}")

    if use_chi_squared:
        if not 0 < chi2_percentile < 100:
            raise ValueError(
                f"chi2_percentile must be between 0 and 100, got {chi2_percentile}"
            )

        # Calculate chi-squared threshold
        threshold = stats.chi2.ppf(chi2_percentile / 100, n_components)
        threshold_type = "chi_squared"
    else:
        # Use direct distance threshold
        if distance_threshold is None:
            # Default to 3 standard deviations
            distance_threshold = 3.0
        elif distance_threshold < 0:
            raise ValueError(
                f"distance_threshold must be non-negative, got {distance_threshold}"
            )

        threshold = distance_threshold
        threshold_type = "distance"

    return float(threshold), threshold_type


def identify_outliers_from_distances(
    distances: np.ndarray,
    threshold: float,
    threshold_type: str = "chi_squared",
    indices: Optional[pd.Index] = None,
) -> Dict:
    """Identify outliers from Mahalanobis distances.

    Args:
        distances: Array of Mahalanobis distances
        threshold: Threshold value for outlier detection
        threshold_type: Type of threshold ("chi_squared" or "distance")
        indices: Optional custom indices for the samples

    Returns:
        Dictionary with:
        - outlier_mask: Boolean mask of outliers
        - outlier_indices: List of outlier indices
        - n_outliers: Number of outliers
    """
    distances = np.asarray(distances)

    # Handle empty distances
    if len(distances) == 0:
        return {
            "outlier_mask": np.array([], dtype=bool),
            "outlier_indices": [],
            "n_outliers": 0,
        }

    # Determine outlier mask based on threshold type
    if threshold_type == "chi_squared":
        # For chi-squared, compare squared distances
        outlier_mask = (distances**2) > threshold
    else:
        # For direct distance, compare distances
        outlier_mask = distances > threshold

    # Get outlier indices
    if indices is not None:
        outlier_indices = indices[outlier_mask].tolist()
    else:
        outlier_indices = np.where(outlier_mask)[0].tolist()

    return {
        "outlier_mask": outlier_mask,
        "outlier_indices": outlier_indices,
        "n_outliers": int(np.sum(outlier_mask)),
    }


def validate_chi_squared_distribution(
    distances_squared: np.ndarray,
    df: int,
) -> Dict[str, Union[str, float, bool, int]]:
    """Validate chi-squared distribution assumption using multiple metrics.

    Performs both hypothesis testing (K-S test) and effect size analysis to assess
    whether squared Mahalanobis distances follow the expected chi-squared(df)
    distribution. For large samples (n > 500), the K-S test becomes hypersensitive
    and will reject even when the fit is practically excellent. Therefore, we use
    effect size metrics (K-S statistic magnitude) for large samples instead of p-values.

    Args:
        distances_squared: Squared Mahalanobis distances
        df: Degrees of freedom (number of PCA components)

    Returns:
        Dictionary containing:
        - test_type: str - 'Kolmogorov-Smirnov'
        - test_statistic: float - K-S test statistic (effect size, 0-1)
        - p_value: float - P-value for the test
        - n_samples: int - Sample size
        - fit_quality: str - 'excellent', 'good', 'acceptable', 'poor', or 'very_poor'
        - interpretation: str - Human-readable interpretation
        - distributional_assumption_valid: bool - True if fit is acceptable
        - warning: str (optional) - Warning message if assumption violated
        - effect_size_interpretation: str - Interpretation of K-S statistic magnitude
        - evaluation_strategy: str - 'p_value' or 'effect_size'

    Notes:
        **Large Sample Strategy (n > 500)**:
        Uses K-S statistic (effect size) as primary metric:
        - K-S < 0.05: Excellent fit (practically indistinguishable from χ²)
        - K-S < 0.10: Good fit (minor deviations, acceptable for outlier detection)
        - K-S < 0.15: Acceptable fit (noticeable but not severe deviations)
        - K-S < 0.20: Poor fit (consider alternative methods)
        - K-S ≥ 0.20: Very poor fit (strongly recommend alternative methods)

        **Small Sample Strategy (n ≤ 500)**:
        Uses traditional p-value thresholds:
        - p > 0.10: Excellent fit
        - 0.05 < p ≤ 0.10: Good fit
        - 0.01 < p ≤ 0.05: Poor fit
        - p ≤ 0.01: Very poor fit

    References:
        - Massey, F.J. (1951). "The Kolmogorov-Smirnov test for goodness of fit". JASA.
        - Sullivan & Feinn (2012). "Using effect size—or why the P value is not enough".
          J Grad Med Educ.
    """
    distances_squared = np.asarray(distances_squared)
    n_samples = len(distances_squared)

    # Validate inputs
    if n_samples == 0:
        return {
            "test_type": "Kolmogorov-Smirnov",
            "test_statistic": np.nan,
            "p_value": np.nan,
            "n_samples": 0,
            "fit_quality": "unknown",
            "interpretation": "No data provided for goodness-of-fit test",
            "distributional_assumption_valid": False,
            "warning": "Insufficient data for goodness-of-fit test",
            "effect_size_interpretation": "No data",
            "evaluation_strategy": "none",
        }

    if df <= 0:
        raise ValueError(f"Degrees of freedom must be positive, got {df}")

    # Perform Kolmogorov-Smirnov test
    # Compare empirical CDF of squared distances to theoretical chi-squared CDF
    ks_statistic, p_value = stats.kstest(distances_squared, stats.chi2(df).cdf)

    # Determine evaluation strategy based on sample size
    use_effect_size = n_samples > 500
    eval_strategy = "effect_size" if use_effect_size else "p_value"

    if use_effect_size:
        # Large sample strategy: Use K-S statistic (effect size) as primary metric
        # K-S statistic represents maximum deviation between empirical and theoretical CDF
        if ks_statistic < 0.05:
            fit_quality = "excellent"
            interpretation = (
                f"Excellent fit: K-S statistic = {ks_statistic:.4f} < 0.05. "
                f"Empirical distribution is practically indistinguishable from χ²({df}). "
                f"[n = {n_samples}; p = {p_value:.4g} not reliable due to large sample size]"
            )
            assumption_valid = True
            warning = None
            effect_size_interp = (
                f"Effect size is very small (K-S = {ks_statistic:.4f}), "
                "indicating excellent practical fit"
            )
        elif ks_statistic < 0.10:
            fit_quality = "good"
            interpretation = (
                f"Good fit: K-S statistic = {ks_statistic:.4f} < 0.10. "
                f"Minor deviations from χ²({df}) are present but acceptable for outlier detection. "
                f"[n = {n_samples}; p = {p_value:.4g} not reliable due to large sample size]"
            )
            assumption_valid = True
            warning = None
            effect_size_interp = (
                f"Effect size is small (K-S = {ks_statistic:.4f}), "
                "indicating good practical fit"
            )
        elif ks_statistic < 0.15:
            fit_quality = "acceptable"
            interpretation = (
                f"Acceptable fit: K-S statistic = {ks_statistic:.4f} < 0.15. "
                f"Noticeable deviations from χ²({df}) but not severe. "
                f"Mahalanobis outlier detection is still reasonable. "
                f"[n = {n_samples}; p = {p_value:.4g} not reliable due to large sample size]"
            )
            assumption_valid = True
            warning = (
                f"Note: With large sample size (n = {n_samples}), K-S test p-value is unreliable. "
                f"Assessment based on effect size (K-S statistic = {ks_statistic:.4f}) "
                "indicates acceptable fit despite low p-value."
            )
            effect_size_interp = (
                f"Effect size is moderate (K-S = {ks_statistic:.4f}), "
                "indicating acceptable practical fit despite low p-value"
            )
        elif ks_statistic < 0.20:
            fit_quality = "poor"
            interpretation = (
                f"Poor fit: K-S statistic = {ks_statistic:.4f} ≥ 0.15. "
                f"Substantial deviations from χ²({df}) distribution detected. "
                f"Consider alternative outlier detection methods. "
                f"[n = {n_samples}; p = {p_value:.4g}]"
            )
            assumption_valid = False
            warning = (
                "Effect size suggests meaningful deviation from χ² distribution. "
                "Data may have multi-modal structure. Consider cluster-aware methods "
                "(K-Means, GMM, or Hierarchical clustering)."
            )
            effect_size_interp = (
                f"Effect size is large (K-S = {ks_statistic:.4f}), "
                "indicating poor practical fit"
            )
        else:  # ks_statistic >= 0.20
            fit_quality = "very_poor"
            interpretation = (
                f"Very poor fit: K-S statistic = {ks_statistic:.4f} ≥ 0.20. "
                f"Large deviations from χ²({df}) distribution. "
                f"Mahalanobis assumptions are violated. "
                f"[n = {n_samples}; p = {p_value:.4g}]"
            )
            assumption_valid = False
            warning = (
                "Effect size indicates strong deviation from χ² distribution. "
                "Data likely has multi-cluster structure. Strongly recommend "
                "using cluster-aware outlier detection methods (K-Means, GMM, or Hierarchical)."
            )
            effect_size_interp = (
                f"Effect size is very large (K-S = {ks_statistic:.4f}), "
                "indicating very poor practical fit"
            )
    else:
        # Small sample strategy: Use traditional p-value thresholds
        if p_value > 0.10:
            fit_quality = "excellent"
            interpretation = (
                f"Excellent fit: Data is consistent with χ²({df}) distribution "
                f"(p = {p_value:.3f} > 0.10, K-S = {ks_statistic:.4f}). "
                f"Mahalanobis assumptions are valid. [n = {n_samples}]"
            )
            assumption_valid = True
            warning = None
            effect_size_interp = (
                f"K-S statistic = {ks_statistic:.4f} (small effect size)"
            )
        elif p_value > 0.05:
            fit_quality = "good"
            interpretation = (
                f"Good fit: Data is marginally consistent with χ²({df}) distribution "
                f"(p = {p_value:.3f}, K-S = {ks_statistic:.4f}). "
                f"Mahalanobis assumptions are acceptable. [n = {n_samples}]"
            )
            assumption_valid = True
            warning = None
            effect_size_interp = (
                f"K-S statistic = {ks_statistic:.4f} (small effect size)"
            )
        elif p_value > 0.01:
            fit_quality = "poor"
            interpretation = (
                f"Poor fit: Data shows deviation from χ²({df}) distribution "
                f"(p = {p_value:.3f} < 0.05, K-S = {ks_statistic:.4f}). "
                f"Consider alternative outlier detection methods. [n = {n_samples}]"
            )
            assumption_valid = False
            warning = (
                "Statistical assumption violated: Data may have multi-modal structure. "
                "Consider using cluster-aware outlier detection methods (K-Means, GMM, Hierarchical)."
            )
            effect_size_interp = (
                f"K-S statistic = {ks_statistic:.4f} (moderate effect size)"
            )
        else:  # p_value <= 0.01
            fit_quality = "very_poor"
            interpretation = (
                f"Very poor fit: Data strongly deviates from χ²({df}) distribution "
                f"(p = {p_value:.3f} ≤ 0.01, K-S = {ks_statistic:.4f}). "
                f"Mahalanobis assumptions are violated. [n = {n_samples}]"
            )
            assumption_valid = False
            warning = (
                "Statistical assumption strongly violated: Data likely has multi-cluster structure. "
                "Strongly recommend using cluster-aware outlier detection methods "
                "(K-Means, GMM, or Hierarchical clustering)."
            )
            effect_size_interp = (
                f"K-S statistic = {ks_statistic:.4f} (large effect size)"
            )

    result = {
        "test_type": "Kolmogorov-Smirnov",
        "test_statistic": float(ks_statistic),
        "p_value": float(p_value),
        "n_samples": int(n_samples),
        "fit_quality": fit_quality,
        "interpretation": interpretation,
        "distributional_assumption_valid": assumption_valid,
        "effect_size_interpretation": effect_size_interp,
        "evaluation_strategy": eval_strategy,
    }

    if warning is not None:
        result["warning"] = warning

    return result


def print_goodness_of_fit_summary(
    gof_results: Dict[str, Union[str, float, bool, int]],
    df_value: Optional[int] = None,
) -> None:
    """Print a formatted goodness-of-fit summary to console.

    Args:
        gof_results: Dictionary from validate_chi_squared_distribution()
        df_value: Degrees of freedom (PCA components), extracted from gof_results if not provided
    """
    # Extract values
    n_samples = gof_results.get("n_samples", "Unknown")
    eval_strategy = gof_results.get("evaluation_strategy", "unknown")
    ks_stat = gof_results.get("test_statistic", float("nan"))
    p_value = gof_results.get("p_value", float("nan"))
    fit_quality = gof_results.get("fit_quality", "unknown")
    valid = gof_results.get("distributional_assumption_valid", False)
    interpretation = gof_results.get("interpretation", "")
    warning = gof_results.get("warning", None)

    # Get df from results if not provided
    if df_value is None:
        # Try to extract from interpretation string
        import re

        match = re.search(r"χ²\((\d+)\)", interpretation)
        if match:
            df_value = int(match.group(1))
        else:
            df_value = "?"

    # Format strategy name
    if eval_strategy == "effect_size":
        strategy_name = "Effect Size (large sample)"
    elif eval_strategy == "p_value":
        strategy_name = "P-value (small sample)"
    else:
        strategy_name = eval_strategy.title()

    # Format p-value
    if np.isnan(p_value):
        p_val_str = "N/A"
    elif p_value < 0.001:
        p_val_str = "< 0.001"
        if eval_strategy == "effect_size":
            p_val_str += " (unreliable)"
    else:
        p_val_str = f"{p_value:.4f}"
        if eval_strategy == "effect_size" and p_value < 0.05:
            p_val_str += " (unreliable)"

    # Format fit quality with symbol
    if fit_quality in ["excellent", "good"]:
        quality_str = f"{fit_quality.upper()} ✓"
        quality_color = "green"
    elif fit_quality == "acceptable":
        quality_str = f"{fit_quality.upper()} ⚠"
        quality_color = "orange"
    else:
        quality_str = f"{fit_quality.upper()} ✗"
        quality_color = "red"

    # Format valid status
    valid_str = "Yes ✓" if valid else "No ✗"

    # Print formatted box
    box_width = 60
    print("╔" + "═" * (box_width - 2) + "╗")
    print("║" + "Mahalanobis Chi-Squared Goodness-of-Fit".center(box_width - 2) + "║")
    print("╠" + "═" * (box_width - 2) + "╣")

    # Basic info
    print(f"║ {'Sample Size:':<20} {str(n_samples) + ' samples':<{box_width - 23}} ║")
    print(
        f"║ {'PCA Components:':<20} {str(df_value) + ' components':<{box_width - 23}} ║"
    )
    print(f"║ {'Evaluation Method:':<20} {strategy_name:<{box_width - 23}} ║")

    print("╠" + "═" * (box_width - 2) + "╣")

    # Test results
    print(f"║ {'K-S Statistic:':<20} {ks_stat:.4f}{'':<{box_width - 28}} ║")
    print(f"║ {'P-value:':<20} {p_val_str:<{box_width - 23}} ║")

    print("╠" + "═" * (box_width - 2) + "╣")

    # Fit assessment
    print(f"║ {'Fit Quality:':<20} {quality_str:<{box_width - 23}} ║")
    print(f"║ {'Assumptions Valid:':<20} {valid_str:<{box_width - 23}} ║")

    # Add warning or note if present
    if warning:
        print("╠" + "═" * (box_width - 2) + "╣")
        # Wrap warning text
        import textwrap

        wrapped_lines = textwrap.wrap(warning, width=box_width - 4)
        for line in wrapped_lines:
            print(f"║ {line:<{box_width - 3}} ║")
    elif eval_strategy == "effect_size" and p_value < 0.05:
        # Add automatic note for large samples with low p-value
        print("╠" + "═" * (box_width - 2) + "╣")
        note = (
            f"Note: With large sample size (n={n_samples}), p-value is "
            "unreliable. Assessment based on effect size (K-S statistic)."
        )
        wrapped_lines = textwrap.wrap(note, width=box_width - 4)
        for line in wrapped_lines:
            print(f"║ {line:<{box_width - 3}} ║")

    print("╚" + "═" * (box_width - 2) + "╝")


def detect_outliers_pca(
    data: Union[pd.DataFrame, np.ndarray],
    n_components: Optional[int] = None,
    explained_variance_threshold: float = 0.95,
    outlier_threshold: float = 2.5,
) -> Dict:
    """Detect outliers using PCA reconstruction error.

    Principal Component Analysis (PCA) reduces data dimensionality while preserving
    variance. Outliers are detected by reconstruction error - samples that cannot
    be well-reconstructed from the principal components.

    Reconstruction Error = Σ(X_original - X_reconstructed)²

    Args:
        data: DataFrame with numeric trait data or numpy array
        n_components: Number of PCA components (auto-determined if None)
        explained_variance_threshold: Cumulative variance threshold for auto-selection (0-1)
        outlier_threshold: Threshold for outlier detection (standard deviations)

    Returns:
        Dictionary with outlier detection results including:
        - outlier_indices: List of row indices identified as outliers
        - n_components: Number of components used
        - reconstruction_errors: Per-sample reconstruction errors
        - explained_variance_ratio: Variance explained by each component
        - cumulative_variance: Cumulative variance explained
        - explained_variance_per_feature: Variance explained for each original feature
        - explained_variance_ratio_per_feature: Fraction of each feature's variance explained
        - error: Error message if detection failed
    """
    # Convert to DataFrame to handle indices consistently
    if isinstance(data, np.ndarray):
        df = pd.DataFrame(data, columns=[f"Feature_{i}" for i in range(data.shape[1])])
    else:
        df = data.copy()

    # Track indices - PCA will drop NaN rows, we need to track valid indices
    original_indices = df.dropna().index.tolist()

    try:
        # Perform PCA analysis using our simplified API
        pca_result = perform_pca_analysis(
            df,
            standardize=True,  # Always standardize for outlier detection
            explained_variance_threshold=explained_variance_threshold,
            n_components=n_components,
            random_state=42,
        )

        # Get processed data for reconstruction error calculation
        X_processed = pca_result.get("data_processed")
        if X_processed is None:
            # This shouldn't happen with our current implementation
            raise ValueError("Unable to get processed data for reconstruction")

        # Calculate reconstruction errors
        reconstruction_errors = calculate_pca_reconstruction_error(
            X_processed, pca_result
        )

        # Detect outliers using z-score of reconstruction errors
        error_mean = np.mean(reconstruction_errors)
        error_std = np.std(reconstruction_errors)

        if error_std == 0:
            # All samples have the same reconstruction error
            outlier_indices = []
            threshold_value = error_mean
        else:
            # Calculate threshold
            threshold_value = error_mean + outlier_threshold * error_std

            # Identify outliers
            outlier_mask = reconstruction_errors > threshold_value
            outlier_indices = [original_indices[i] for i in np.where(outlier_mask)[0]]

        # Get per-feature metrics using calculate_pca_metrics
        pca_metrics = calculate_pca_metrics(
            pca_result["pca"],
            pca_result["transformed_data"],
            X_fitted=X_processed,
            ddof_for_feature_var=1,
        )

        # Compile results
        result = {
            "method": "PCA",
            "n_components": pca_result["n_components_selected"],
            "explained_variance_ratio": pca_result["explained_variance_ratio"].tolist(),
            "cumulative_variance": pca_result["cumulative_variance_ratio"].tolist(),
            "total_variance_explained": float(
                pca_result["cumulative_variance_ratio"][-1]
            ),
            "explained_variance_threshold": explained_variance_threshold,
            "outlier_threshold": outlier_threshold,
            "threshold_value": float(threshold_value),
            "reconstruction_errors": reconstruction_errors.tolist(),
            "outlier_indices": outlier_indices,
            "n_outliers": len(outlier_indices),
            "pca_components": pca_result["transformed_data"].tolist(),
            "loadings": pca_result["loadings"].tolist(),
            "eigenvalues": pca_result["eigenvalues"].tolist(),
            "feature_names": pca_result["feature_names"],
            "data_indices": original_indices,
            "explained_variance_per_feature": pca_metrics.get(
                "explained_variance_per_feature",
                np.zeros(len(pca_result["feature_names"])),
            ).tolist(),
            "explained_variance_ratio_per_feature": pca_metrics.get(
                "explained_variance_ratio_per_feature",
                np.zeros(len(pca_result["feature_names"])),
            ).tolist(),
        }

        return result

    except Exception as e:
        return {
            "method": "PCA",
            "outlier_indices": [],
            "error": f"PCA reconstruction outlier detection failed: {str(e)}",
        }


def detect_outliers_isolation_forest(
    data: Union[pd.DataFrame, np.ndarray],
    contamination: float = 0.1,
    random_state: int = 42,
) -> Dict:
    """Detect outliers using Isolation Forest.

    Isolation Forest isolates anomalies by randomly selecting features and split values.
    Outliers are data points that require fewer splits to isolate, indicating they are
    different from the majority of the data.

    Anomaly Score = 2^(-E(h(x))/c(n))

    Where E(h(x)) is the average path length of sample x in isolation trees,
    and c(n) is the average path length of unsuccessful search in a BST with n points.

    Args:
        data: DataFrame with numeric trait data or numpy array
        contamination: Expected proportion of outliers (0-0.5)
        random_state: Random seed for reproducibility

    Returns:
        Dictionary with outlier detection results including:
        - outlier_indices: List of row indices identified as outliers
        - anomaly_scores: Per-sample anomaly scores (more negative = more anomalous)
        - contamination: Contamination parameter used
        - outlier_labels: -1 for outliers, 1 for inliers
        - data_indices: Original indices of the data
        - error: Error message if detection failed
    """
    # Convert to DataFrame to handle indices consistently
    if isinstance(data, np.ndarray):
        df = pd.DataFrame(data, columns=[f"Feature_{i}" for i in range(data.shape[1])])
    else:
        df = data.copy()

    # Track indices - handle NaN removal like other methods
    df_clean = df.dropna()

    # Check if we have enough data after cleaning
    if df_clean.empty or df_clean.shape[0] == 0:
        return {
            "method": "IsolationForest",
            "outlier_indices": [],
            "error": "Empty data provided or all rows contain NaN",
        }

    original_indices = df_clean.index.tolist()

    try:
        # Standardize data using our consistent approach
        X_scaled, scaler, _ = standardize_data(df_clean)

        # Fit Isolation Forest
        iso_forest = IsolationForest(
            contamination=contamination,
            random_state=random_state,
            n_estimators=100,  # Default number of trees
        )

        # Fit and predict outliers
        outlier_labels = iso_forest.fit_predict(X_scaled)

        # Get outlier indices (Isolation Forest returns -1 for outliers, 1 for inliers)
        outlier_mask = outlier_labels == -1
        outlier_indices = [original_indices[i] for i in np.where(outlier_mask)[0]]

        # Get anomaly scores (more negative = more anomalous)
        # decision_function returns the opposite of anomaly scores
        anomaly_scores = iso_forest.decision_function(X_scaled)

        return {
            "method": "IsolationForest",
            "contamination": contamination,
            "outlier_indices": outlier_indices,
            "n_outliers": len(outlier_indices),
            "anomaly_scores": anomaly_scores.tolist(),
            "outlier_labels": outlier_labels.tolist(),
            "data_indices": original_indices,
        }

    except Exception as e:
        return {
            "method": "IsolationForest",
            "outlier_indices": [],
            "error": f"Isolation Forest outlier detection failed: {str(e)}",
        }


def detect_outliers_kmeans(
    data: Union[pd.DataFrame, np.ndarray],
    n_clusters: Optional[int] = None,
    max_clusters: int = 10,
    distance_threshold: float = 2.0,
    standardize: bool = True,
    random_state: int = 42,
) -> Dict:
    """Detect outliers using K-Means clustering.

    Performs K-Means clustering and identifies samples that are far from all
    cluster centers as outliers. This method is useful for identifying samples
    that don't fit well into any natural grouping in the data.

    Outliers are defined as samples whose distance to the nearest cluster center
    exceeds: mean_distance + distance_threshold * std_distance

    Args:
        data: Input data as DataFrame or array
        n_clusters: Number of clusters (auto-selected via silhouette if None)
        max_clusters: Maximum clusters to test for auto-selection (default 10)
        distance_threshold: Threshold in standard deviations for outlier detection
        standardize: Whether to standardize data before clustering
        random_state: Random seed for reproducibility

    Returns:
        Dictionary with outlier detection results:
        - method: 'KMeans'
        - outlier_indices: List of outlier sample indices
        - n_outliers: Number of outliers detected
        - threshold_value: Distance threshold used
        - distance_threshold: Input distance threshold parameter
        - (plus all fields from perform_kmeans_clustering)

    Examples:
        >>> # With auto-optimization
        >>> result = detect_outliers_kmeans(df, n_clusters=None)
        >>> print(f"Found {result['n_outliers']} outliers in {result['n_clusters']} clusters")
        >>>
        >>> # With specified k
        >>> result = detect_outliers_kmeans(df, n_clusters=3, distance_threshold=2.0)
        >>> print(f"Found {result['n_outliers']} outliers")
        >>> cleaned_df = df.drop(index=result['outlier_indices'])
    """
    from .clustering import perform_kmeans_clustering

    try:
        # Perform core K-Means clustering
        cluster_result = perform_kmeans_clustering(
            data,
            n_clusters=n_clusters,
            max_clusters=max_clusters,
            standardize=standardize,
            random_state=random_state,
        )

        # Calculate outlier threshold based on distances
        distances = cluster_result["min_distances_to_centers"]
        mean_distance = np.mean(distances)
        std_distance = np.std(distances)

        if std_distance == 0:
            # All samples have same distance - no outliers
            outlier_indices = []
            threshold_value = mean_distance
        else:
            threshold_value = mean_distance + distance_threshold * std_distance
            outlier_mask = distances > threshold_value
            # Get indices from original data
            outlier_indices = [
                cluster_result["data_indices"][i] for i in np.where(outlier_mask)[0]
            ]

        # Return combined result
        return {
            **cluster_result,
            "outlier_indices": outlier_indices,
            "n_outliers": len(outlier_indices),
            "threshold_value": float(threshold_value),
            "distance_threshold": distance_threshold,
        }

    except Exception as e:
        return {
            "method": "KMeans",
            "outlier_indices": [],
            "error": f"K-Means outlier detection failed: {str(e)}",
        }


def detect_outliers_gmm(
    data: Union[pd.DataFrame, np.ndarray],
    n_components: Optional[int] = None,
    max_components: int = 5,
    percentile_threshold: float = 99.0,
    covariance_type: str = "full",
    standardize: bool = True,
    random_state: int = 42,
) -> Dict:
    """Detect outliers using Gaussian Mixture Model.

    Fits a GMM to the data and identifies outliers as samples with low
    log-likelihood (probability) under the fitted model. GMM can model
    complex, multi-modal distributions better than single-distribution methods.

    Outliers are defined as samples in the lowest percentile_threshold of
    log-likelihood scores.

    Args:
        data: Input data as DataFrame or array
        n_components: Number of mixture components (auto-selected via BIC if None)
        max_components: Max components to test for auto-selection
        percentile_threshold: Percentile for outlier cutoff (95-99.9)
            - 99.0 means bottom 1% are outliers
            - 95.0 means bottom 5% are outliers
        covariance_type: Covariance structure ('full', 'tied', 'diag', 'spherical')
        standardize: Whether to standardize data before clustering
        random_state: Random seed for reproducibility

    Returns:
        Dictionary with outlier detection results:
        - method: 'GMM'
        - outlier_indices: List of outlier sample indices
        - n_outliers: Number of outliers detected
        - threshold_value: Log-likelihood threshold used
        - percentile_threshold: Input percentile threshold parameter
        - (plus all fields from perform_gmm_clustering)

    Examples:
        >>> result = detect_outliers_gmm(
        ...     df,
        ...     n_components=None,  # Auto-select
        ...     percentile_threshold=99.0
        ... )
        >>> print(f"Selected {result['n_components']} components")
        >>> print(f"Found {result['n_outliers']} outliers")
    """
    from .clustering import perform_gmm_clustering

    try:
        # Perform core GMM clustering
        gmm_result = perform_gmm_clustering(
            data,
            n_components=n_components,
            max_components=max_components,
            covariance_type=covariance_type,
            standardize=standardize,
            random_state=random_state,
        )

        # Identify outliers based on log-likelihood
        log_likelihoods = gmm_result["log_likelihoods"]

        # Lower log-likelihood = lower probability = more anomalous
        # Use percentile to find threshold
        threshold = np.percentile(log_likelihoods, 100 - percentile_threshold)
        outlier_mask = log_likelihoods < threshold

        # Get indices from original data
        outlier_indices = [
            gmm_result["data_indices"][i] for i in np.where(outlier_mask)[0]
        ]

        # Return combined result
        return {
            **gmm_result,
            "outlier_indices": outlier_indices,
            "n_outliers": len(outlier_indices),
            "threshold_value": float(threshold),
            "percentile_threshold": percentile_threshold,
        }

    except Exception as e:
        return {
            "method": "GMM",
            "outlier_indices": [],
            "error": f"GMM outlier detection failed: {str(e)}",
        }


def detect_outliers_hierarchical(
    data: Union[pd.DataFrame, np.ndarray],
    n_clusters: Optional[int] = None,
    distance_threshold: float = 2.0,
    linkage_method: str = "ward",
    distance_metric: str = "euclidean",
    standardize: bool = True,
) -> Dict:
    """Detect outliers using hierarchical clustering.

    Performs hierarchical clustering, cuts dendrogram to get clusters (with optional
    automatic k optimization), then identifies samples far from their cluster centers
    as outliers. Unlike K-Means/GMM, hierarchical clustering provides a dendrogram
    showing the full hierarchy of relationships.

    Args:
        data: Input data as DataFrame or array
        n_clusters: Number of clusters (auto-optimized if None)
        distance_threshold: Threshold in standard deviations for outlier detection
        linkage_method: Hierarchical linkage method ('ward', 'complete', 'average', 'single')
        distance_metric: Distance metric for hierarchical clustering ('euclidean', etc.)
        standardize: Whether to standardize data before clustering

    Returns:
        Dictionary with outlier detection results:
        - method: 'Hierarchical'
        - outlier_indices: List of outlier sample indices
        - n_outliers: Number of outliers detected
        - threshold_value: Distance threshold used
        - distance_threshold: Input distance threshold parameter
        - linkage_matrix: Full hierarchical structure (for dendrograms)
        - cluster_labels: Labels after cutting dendrogram
        - n_clusters: Number of clusters
        - cluster_sizes: Number of samples per cluster
        - cut_height: Height where dendrogram was cut
        - cophenetic_correlation: Quality of hierarchical structure
        - distances_to_centers: Distance of each sample to its cluster center
        - (plus quality metrics from cut_dendrogram)

    Examples:
        >>> # With auto-optimization of k
        >>> result = detect_outliers_hierarchical(df, n_clusters=None)
        >>> print(f"Optimal k: {result['n_clusters']}")
        >>>
        >>> # With specified k
        >>> result = detect_outliers_hierarchical(df, n_clusters=3)
    """
    from .clustering import (
        perform_hierarchical_clustering,
        cut_dendrogram,
        calculate_optimal_clusters_hierarchical,
    )

    try:
        # 1. Perform hierarchical clustering
        hier_result = perform_hierarchical_clustering(
            data,
            method=linkage_method,
            metric=distance_metric,
            standardize=standardize,
        )

        # 2. Determine optimal k if not specified
        if n_clusters is None:
            optimal_result = calculate_optimal_clusters_hierarchical(
                hier_result, max_clusters=min(10, len(hier_result["data_indices"]) // 2)
            )
            n_clusters = optimal_result["optimal_n_clusters"]

        # 3. Cut dendrogram to get clusters
        cut_result = cut_dendrogram(hier_result, n_clusters=n_clusters)

        # 4. Calculate cluster centers and distances (for outlier detection)
        cluster_labels = cut_result["cluster_labels"]
        X_processed = hier_result["data_processed"]
        data_indices = hier_result["data_indices"]

        # Calculate centers for each cluster
        centers = np.array(
            [X_processed[cluster_labels == i].mean(axis=0) for i in range(n_clusters)]
        )

        # Calculate distance from each sample to its cluster center
        distances_to_centers = np.array(
            [
                np.linalg.norm(X_processed[i] - centers[cluster_labels[i]])
                for i in range(len(X_processed))
            ]
        )

        # 5. Identify outliers based on distances
        mean_distance = np.mean(distances_to_centers)
        std_distance = np.std(distances_to_centers)

        if std_distance == 0:
            # All samples have same distance - no outliers
            outlier_indices = []
            threshold_value = mean_distance
        else:
            threshold_value = mean_distance + distance_threshold * std_distance
            outlier_mask = distances_to_centers > threshold_value
            outlier_indices = [data_indices[i] for i in np.where(outlier_mask)[0]]

        # 6. Return combined result
        return {
            **hier_result,
            **cut_result,
            "outlier_indices": outlier_indices,
            "n_outliers": len(outlier_indices),
            "threshold_value": float(threshold_value),
            "distance_threshold": distance_threshold,
            "distances_to_centers": distances_to_centers.tolist(),
        }

    except Exception as e:
        return {
            "method": "Hierarchical",
            "outlier_indices": [],
            "error": f"Hierarchical outlier detection failed: {str(e)}",
        }


def remove_outliers_from_data(
    df: pd.DataFrame,
    outlier_indices: Union[List, np.ndarray, pd.Index],
    keep_metadata: bool = True,
    return_outliers: bool = True,
    reset_index: bool = False,
) -> Union[pd.DataFrame, Tuple[pd.DataFrame, pd.DataFrame]]:
    """Remove outlier samples from dataset and optionally return them.

    This function removes outlier samples identified by outlier detection methods
    and returns both the cleaned data and the outlier data. It properly handles
    different index types (integer, string, custom) and preserves them.

    Args:
        df: Original DataFrame with data
        outlier_indices: Indices of outliers to remove (from detection functions)
        keep_metadata: Whether to preserve all columns (True) or only numeric (False)
        return_outliers: Whether to also return the outlier DataFrame
        reset_index: Whether to reset index after removal (only for cleaned_df)

    Returns:
        If return_outliers=False: cleaned DataFrame
        If return_outliers=True: Tuple of (cleaned_df, outliers_df)

    Examples:
        >>> # Basic usage with outlier detection
        >>> result = detect_outliers_mahalanobis(df)
        >>> cleaned_df, outlier_df = remove_outliers_from_data(
        ...     df, result["outlier_indices"]
        ... )

        >>> # Only get cleaned data with reset index
        >>> cleaned = remove_outliers_from_data(
        ...     df, [1, 5, 10],
        ...     return_outliers=False,
        ...     reset_index=True
        ... )
    """
    # Make a copy to avoid modifying original
    df = df.copy()

    # Convert outlier_indices to list if needed
    if isinstance(outlier_indices, np.ndarray):
        outlier_indices = outlier_indices.tolist()
    elif isinstance(outlier_indices, pd.Index):
        outlier_indices = outlier_indices.tolist()

    # Filter to only valid indices that exist in df
    valid_outlier_indices = [idx for idx in outlier_indices if idx in df.index]

    # Create outlier DataFrame before removing them
    if len(valid_outlier_indices) > 0:
        outlier_df = df.loc[valid_outlier_indices].copy()
    else:
        # Empty DataFrame with same structure
        outlier_df = df.iloc[0:0].copy()

    # Remove outliers from main DataFrame
    cleaned_df = df.drop(index=valid_outlier_indices).copy()

    # Handle metadata filtering if requested
    if not keep_metadata:
        # Only keep numeric columns
        numeric_cols = cleaned_df.select_dtypes(include=[np.number]).columns.tolist()
        cleaned_df = cleaned_df[numeric_cols].copy()
        if len(outlier_df) > 0:
            outlier_df = outlier_df[numeric_cols].copy()

    # Reset index if requested (only for cleaned DataFrame)
    if reset_index:
        cleaned_df = cleaned_df.reset_index(drop=True)
        # Note: outlier_df keeps original indices for traceability

    # Return based on return_outliers flag
    if return_outliers:
        return cleaned_df, outlier_df
    else:
        return cleaned_df


def combine_outlier_methods(
    pca_results: Optional[Dict] = None,
    isolation_results: Optional[Dict] = None,
    mahalanobis_results: Optional[Dict] = None,
    kmeans_results: Optional[Dict] = None,
    gmm_results: Optional[Dict] = None,
    hierarchical_results: Optional[Dict] = None,
    consensus_threshold: float = 0.5,
) -> Dict:
    """Combine results from multiple outlier detection methods.

    Args:
        pca_results: Results from PCA-based detection
        isolation_results: Results from Isolation Forest detection
        mahalanobis_results: Results from Mahalanobis distance detection
        kmeans_results: Results from K-Means clustering detection
        gmm_results: Results from GMM clustering detection
        hierarchical_results: Results from hierarchical clustering detection
        consensus_threshold: Minimum fraction of methods that must agree

    Returns:
        Dictionary with combined outlier detection results

    Examples:
        >>> combined = combine_outlier_methods(
        ...     pca_results=pca_results,
        ...     isolation_results=iso_results,
        ...     mahalanobis_results=mahal_results,
        ...     kmeans_results=kmeans_results,
        ...     gmm_results=gmm_results,
        ...     hierarchical_results=hier_results,
        ...     consensus_threshold=0.5  # 50% agreement
        ... )
        >>> print(f"Consensus outliers: {combined['n_consensus_outliers']}")
    """
    # Collect outlier indices from all methods
    method_outliers = {}

    if pca_results and "error" not in pca_results:
        method_outliers["pca"] = set(pca_results.get("outlier_indices", []))

    if isolation_results and "error" not in isolation_results:
        method_outliers["isolation_forest"] = set(
            isolation_results.get("outlier_indices", [])
        )

    if mahalanobis_results and "error" not in mahalanobis_results:
        method_outliers["mahalanobis"] = set(
            mahalanobis_results.get("outlier_indices", [])
        )

    if kmeans_results and "error" not in kmeans_results:
        method_outliers["kmeans"] = set(kmeans_results.get("outlier_indices", []))

    if gmm_results and "error" not in gmm_results:
        method_outliers["gmm"] = set(gmm_results.get("outlier_indices", []))

    if hierarchical_results and "error" not in hierarchical_results:
        method_outliers["hierarchical"] = set(
            hierarchical_results.get("outlier_indices", [])
        )

    # Check that we have at least one method
    if not method_outliers:
        return {
            "method": "Combined",
            "error": "No valid outlier detection results provided",
            "consensus_outliers": [],
            "n_consensus_outliers": 0,
        }

    # Find all unique outliers
    all_outliers = set()
    for outliers in method_outliers.values():
        all_outliers.update(outliers)

    # Count agreement for each outlier and track which methods agree
    consensus_outliers = []
    outlier_agreement_count = {}
    outlier_agreement_methods = {}
    n_methods = len(method_outliers)

    for outlier_idx in all_outliers:
        methods_agreeing = []
        for method_name, outliers in method_outliers.items():
            if outlier_idx in outliers:
                methods_agreeing.append(method_name)

        agreement_count = len(methods_agreeing)
        outlier_agreement_count[outlier_idx] = agreement_count
        outlier_agreement_methods[outlier_idx] = methods_agreeing

        if agreement_count / n_methods >= consensus_threshold:
            consensus_outliers.append(outlier_idx)

    # Calculate method-specific statistics
    method_only = {}
    for method_name, outliers in method_outliers.items():
        others = set()
        for other_name, other_outliers in method_outliers.items():
            if other_name != method_name:
                others.update(other_outliers)
        method_only[f"{method_name}_only"] = list(outliers - others)

    # Find overlaps between methods
    overlaps = {}
    method_names = list(method_outliers.keys())
    for i, method1 in enumerate(method_names):
        for j, method2 in enumerate(method_names[i + 1 :], i + 1):
            overlap_key = f"{method1}_{method2}_overlap"
            overlaps[overlap_key] = list(
                method_outliers[method1].intersection(method_outliers[method2])
            )

    # Create agreement summary
    import math

    min_methods_required = math.ceil(n_methods * consensus_threshold)
    agreement_summary = {
        "methods_compared": list(method_outliers.keys()),
        "total_methods": n_methods,
        "consensus_rule": f"Agreed by at least {consensus_threshold:.0%} of methods ({min_methods_required} out of {n_methods})",
    }

    # Agreement distribution
    agreement_distribution = {}
    for count in range(1, n_methods + 1):
        samples_with_count = [
            idx for idx, c in outlier_agreement_count.items() if c == count
        ]
        if samples_with_count:
            agreement_distribution[f"agreed_by_{count}_methods"] = sorted(
                samples_with_count
            )

    return {
        "method": "Combined",
        "consensus_threshold": consensus_threshold,
        "n_methods": n_methods,
        "agreement_summary": agreement_summary,
        **{
            f"{name}_outliers": list(outliers)
            for name, outliers in method_outliers.items()
        },
        "consensus_outliers": sorted(consensus_outliers),
        "n_consensus_outliers": len(consensus_outliers),
        "outlier_agreement_count": {
            k: v for k, v in sorted(outlier_agreement_count.items())
        },
        "outlier_agreement_methods": {
            k: v for k, v in sorted(outlier_agreement_methods.items())
        },
        **agreement_distribution,
        **method_only,
        **overlaps,
    }
