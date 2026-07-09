"""Modular PCA analysis functions for consistent use across the codebase."""

from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from typing import Dict, Optional, Tuple, Union, List


def select_top_features_from_pca(
    loadings: np.ndarray,
    eigenvalues: np.ndarray,
    n_features_total: int,
    n_features_to_select: int,
    method: str = "extreme",
    pc_indices: Optional[List[int]] = None,
) -> List[int]:
    """Select top features based on PCA loadings using various strategies.

    Args:
        loadings: Loading matrix (n_features, n_components)
        eigenvalues: Eigenvalues for each PC
        n_features_total: Total number of features to consider
        n_features_to_select: Number of features to select (per direction for "extreme")
        method: Selection method:
            - "extreme": Top N most positive and negative loadings for specified PCs
              Returns features in order: PC1_neg, PC1_pos, PC2_neg, PC2_pos, ...
            - "top_absolute": Top N by absolute loading magnitude on specified PCs
            - "top_contribution": Top N by variance contribution to specified PCs
            - "top_variance": Top N by total variance contribution (all PCs)
            - "vector_length": Top N by Euclidean distance in PC plane (traditional biplot)
        pc_indices: Which PCs to consider (0-based). If None, uses first 2 PCs.

    Returns:
        List of selected feature indices (order depends on method)

    Raises:
        ValueError: If method is not recognized

    Examples:
        >>> loadings = np.array([[0.8, 0.1], [0.1, 0.8], [-0.7, 0.2]])
        >>> eigenvalues = np.array([3.0, 1.0])
        >>> selected = select_top_features_from_pca(
        ...     loadings, eigenvalues, 3, 1, method="extreme", pc_indices=[0]
        ... )
        >>> # Returns indices of most negative and most positive on PC1
    """
    if pc_indices is None:
        pc_indices = [0, 1]  # Default to first 2 PCs

    # Ensure we don't exceed available dimensions
    n_features = min(n_features_total, loadings.shape[0])
    pc_indices = [idx for idx in pc_indices if idx < loadings.shape[1]]

    if not pc_indices:
        raise ValueError("No valid PC indices provided")

    if method == "extreme":
        # Get top N most positive and negative loadings for each PC
        # Return in predictable order, avoiding duplicates
        selected_indices = []
        seen = set()

        for pc_idx in pc_indices:
            loadings_pc = loadings[:n_features, pc_idx]
            sorted_idx = np.argsort(loadings_pc)

            # Most negative (in order of most negative to less negative)
            for idx in sorted_idx[:n_features_to_select]:
                if idx not in seen:
                    selected_indices.append(int(idx))
                    seen.add(idx)

            # Most positive (in order of most positive to less positive)
            for idx in sorted_idx[-n_features_to_select:][::-1]:
                if idx not in seen:
                    selected_indices.append(int(idx))
                    seen.add(idx)

        return selected_indices

    elif method == "top_absolute":
        # Get features with highest absolute loading on specified PCs
        abs_loadings = np.zeros(n_features)
        for pc_idx in pc_indices:
            abs_loadings += np.abs(loadings[:n_features, pc_idx])

        return np.argsort(abs_loadings)[::-1][:n_features_to_select].tolist()

    elif method == "top_contribution":
        # Use sum of variance contributions for specified PCs
        contributions = np.zeros(n_features)
        for pc_idx in pc_indices:
            if pc_idx < len(eigenvalues):
                contributions += (
                    eigenvalues[pc_idx] * loadings[:n_features, pc_idx] ** 2
                )

        return np.argsort(contributions)[::-1][:n_features_to_select].tolist()

    elif method == "top_variance":
        # Use total variance contribution across all available PCs
        n_pcs = min(loadings.shape[1], len(eigenvalues))
        contributions = np.zeros(n_features)

        for i in range(n_pcs):
            contributions += eigenvalues[i] * loadings[:n_features, i] ** 2

        return np.argsort(contributions)[::-1][:n_features_to_select].tolist()

    elif method == "vector_length":
        # Traditional biplot: Euclidean distance (vector length) in PC plane
        # This selects features with the longest arrows in the displayed PC space
        squared_loadings = np.zeros(n_features)
        for pc_idx in pc_indices:
            squared_loadings += loadings[:n_features, pc_idx] ** 2
        distances = np.sqrt(squared_loadings)

        return np.argsort(distances)[::-1][:n_features_to_select].tolist()

    else:
        raise ValueError(f"Unknown selection method: {method}")


def select_n_components(
    X: np.ndarray,
    explained_variance_threshold: float = 0.95,
    n_components: Optional[int] = None,
    random_state: int = 42,
) -> int:
    """Determine the optimal number of PCA components.

    Args:
        X: Data array of shape (n_samples, n_features)
        explained_variance_threshold: Cumulative variance threshold for component selection
        n_components: If specified, overrides automatic selection
        random_state: Random state for reproducibility

    Returns:
        Number of components to use
    """
    n_samples, n_features = X.shape

    # Handle edge case: single sample
    if n_samples <= 1:
        raise ValueError(
            f"PCA requires at least 2 samples, got {n_samples}. "
            "Cannot compute principal components with a single sample."
        )

    if n_components is not None:
        # Use specified number, but ensure it's valid
        return max(1, min(n_components, n_features, n_samples - 1))

    # Auto-select based on explained variance threshold
    max_components = min(n_features, n_samples - 1)

    # Fit PCA with all components to find optimal number
    pca_full = PCA(n_components=max_components, random_state=random_state)
    pca_full.fit(X)

    cumulative_variance = np.cumsum(pca_full.explained_variance_ratio_)

    if cumulative_variance[-1] >= explained_variance_threshold:
        # Find the minimum number of components needed
        n_components = (
            np.argmax(cumulative_variance >= explained_variance_threshold) + 1
        )
    else:
        # Use all available components
        n_components = max_components

    return max(1, min(n_components, max_components))


def fit_pca(
    X: np.ndarray,
    n_components: int,
    random_state: int = 42,
) -> Tuple[PCA, np.ndarray]:
    """Fit PCA with specified number of components.

    Args:
        X: Data array of shape (n_samples, n_features)
        n_components: Number of components to use
        random_state: Random state for reproducibility

    Returns:
        Tuple of:
            - Fitted PCA object
            - Transformed data (n_samples, n_components)
    """
    pca = PCA(n_components=n_components, random_state=random_state)
    X_transformed = pca.fit_transform(X)
    return pca, X_transformed


def calculate_pca_metrics(
    pca: PCA,
    X_transformed: np.ndarray,
    X_fitted: Optional[np.ndarray] = None,
    ddof_for_feature_var: int = 1,
) -> Dict:
    """Calculate comprehensive PCA metrics for standardized or unstandardized data.

    Args:
        pca: Fitted PCA object
        X_transformed: Transformed data (n_samples, n_components)
        X_fitted: The data PCA was fit on (if None, assumes standardized with var=1)
        ddof_for_feature_var: ddof for per-feature variance (default=1 matches PCA)
            - Use 1 for consistency with PCA eigenvalues (recommended)
            - Use 0 if data was standardized with StandardScaler(ddof=0) and you want var=1

    Returns:
        Dictionary containing PCA metrics including:
        - pca: Fitted PCA model
        - n_components_selected: Number of components
        - transformed_data: Data in PC space
        - loadings: Component loadings (eigenvectors)
        - eigenvalues: Explained variance per component
        - explained_variance_ratio: Fraction per component (from sklearn)
        - cumulative_variance_ratio: Cumulative variance explained
        - total_variance_explained: Total fraction explained
        - explained_variance_per_feature: Variance explained per original feature
        - explained_variance_ratio_per_feature: Fraction explained per feature [0,1]
        - feature_variances: Per-feature variances of fitted data
        - feature_variance_ddof: The ddof used
    """
    # Get loadings (eigenvectors) - shape: (n_features, n_components)
    loadings = pca.components_.T

    # Get eigenvalues (explained variance)
    eigenvalues = pca.explained_variance_

    # Calculate explained variance per feature: diag(V Λ V^T)
    explained_variance_per_feature = np.sum(
        (loadings**2) * eigenvalues[np.newaxis, :], axis=1
    )

    # Calculate per-feature ratios
    if X_fitted is not None:
        # Compute actual per-feature variances
        feature_variances = np.var(X_fitted, axis=0, ddof=ddof_for_feature_var)

        # Safe division for ratios
        with np.errstate(divide="ignore", invalid="ignore"):
            explained_variance_ratio_per_feature = np.where(
                feature_variances > 0,
                explained_variance_per_feature / feature_variances,
                0.0,
            )
    else:
        # Backward compatibility: assume standardized data
        import warnings

        warnings.warn(
            "X_fitted not provided. Assuming standardized data with variance=1. "
            "Pass X_fitted for correct per-feature ratios.",
            DeprecationWarning,
            stacklevel=2,
        )
        feature_variances = np.ones_like(explained_variance_per_feature)
        explained_variance_ratio_per_feature = explained_variance_per_feature

    # Global variance explained (consistent with chosen ddof)
    if X_fitted is not None:
        total_feature_variance = np.sum(feature_variances)
        total_variance_explained_consistent = (
            np.sum(eigenvalues) / total_feature_variance
            if total_feature_variance > 0
            else 0.0
        )
    else:
        total_variance_explained_consistent = np.sum(pca.explained_variance_ratio_)

    return {
        "pca": pca,
        "n_components_selected": pca.n_components_,
        "transformed_data": X_transformed,
        "loadings": loadings,
        "eigenvalues": eigenvalues,
        "explained_variance_ratio": pca.explained_variance_ratio_,
        "cumulative_variance_ratio": np.cumsum(pca.explained_variance_ratio_),
        "total_variance_explained": np.sum(pca.explained_variance_ratio_),
        "total_variance_explained_consistent": total_variance_explained_consistent,
        "explained_variance_per_feature": explained_variance_per_feature,
        "explained_variance_ratio_per_feature": explained_variance_ratio_per_feature,
        "feature_variances": feature_variances,
        "feature_variance_ddof": ddof_for_feature_var,
    }


def build_feature_metrics_df(
    pca_result: Dict,
    ddof_feature_var: Optional[int] = None,
    include_loadings: bool = True,
    loading_prefix: str = "loading_pc",
    sort_by: str = "fraction_explained",
) -> pd.DataFrame:
    """Build a tidy DataFrame of per-feature PCA metrics.

    Creates a DataFrame with one row per feature showing variance explained,
    fraction of variance explained, and optionally the loadings for each PC.

    Args:
        pca_result: Dictionary from perform_pca_analysis
        ddof_feature_var: Override ddof for variance calculation (None uses stored value)
        include_loadings: Add columns for absolute loadings per PC
        loading_prefix: Prefix for loading column names (e.g., "loading_pc")
        sort_by: Column to sort by (default: 'fraction_explained')

    Returns:
        DataFrame with columns:
        - feature: Feature name
        - variance_total: Total variance of feature
        - variance_explained: Variance explained by retained PCs
        - fraction_explained: Ratio of explained to total variance [0,1]
        - loading_pc{k}: Absolute loading for PC k (if include_loadings=True)
    """
    feature_names = pca_result["feature_names"]
    loadings = pca_result["loadings"]
    eigenvalues = pca_result.get("eigenvalues", pca_result["pca"].explained_variance_)

    # Use stored values if available, otherwise recalculate
    if ddof_feature_var is None:
        ddof_feature_var = pca_result.get("feature_variance_ddof", 1)

    # Check if we can reuse stored calculations
    if "feature_variances" in pca_result and ddof_feature_var == pca_result.get(
        "feature_variance_ddof"
    ):
        variance_total = pca_result["feature_variances"]
        variance_explained = pca_result["explained_variance_per_feature"]
        fraction_explained = pca_result["explained_variance_ratio_per_feature"]
    else:
        # Recalculate with different ddof if needed
        X_fitted = pca_result["data_processed"]
        variance_total = np.var(X_fitted, axis=0, ddof=ddof_feature_var)
        variance_explained = np.sum((loadings**2) * eigenvalues[np.newaxis, :], axis=1)

        with np.errstate(divide="ignore", invalid="ignore"):
            fraction_explained = np.where(
                variance_total > 0, variance_explained / variance_total, 0.0
            )

    # Build base data dictionary
    data = {
        "feature": feature_names,
        "variance_total": variance_total,
        "variance_explained": variance_explained,
        "fraction_explained": fraction_explained,
    }

    # Add absolute loadings per PC
    if include_loadings:
        n_components = loadings.shape[1]
        for k in range(n_components):
            data[f"{loading_prefix}{k + 1}"] = np.abs(loadings[:, k])

    # Create DataFrame and sort
    df = pd.DataFrame(data)
    if sort_by and sort_by in df.columns:
        df = df.sort_values(sort_by, ascending=False).reset_index(drop=True)

    return df


def run_pca_and_export_artifacts(
    df_traits: pd.DataFrame,
    trait_cols: Optional[list] = None,
    *,
    analysis_dir: Union[str, Path],
    n_components: Optional[int] = None,
    explained_variance_threshold: float = 0.95,
    standardize: bool = True,
    metadata_cols: tuple = ("Barcode", "geno", "rep"),
    save_csv: bool = True,
    save_prefix: str = "",
    include_feature_metrics: bool = True,
) -> Dict[str, Union[pd.DataFrame, Dict]]:
    """Run PCA analysis and export comprehensive artifacts.

    Performs PCA via perform_pca_analysis and produces consistent artifacts:
    - Loadings matrix ({prefix}pca_loadings.csv)
    - Per-trait variance contributions ({prefix}trait_variance_contrib.csv)
    - Per-PC explained variance ratios ({prefix}pca_variance_explained.csv)
    - PC scores with metadata ({prefix}pca_transformed_data.csv)
    - Optional tidy feature metrics ({prefix}feature_metrics.csv)

    Args:
        df_traits: DataFrame containing traits and optionally metadata columns.
        trait_cols: List of trait column names. If None, uses all numeric columns.
        analysis_dir: Directory to save CSV files.
        n_components: Number of components to compute. If None, determined by
            variance threshold.
        explained_variance_threshold: Cumulative variance threshold for component
            selection.
        standardize: Whether to standardize features before PCA.
        metadata_cols: Metadata columns to include in PC scores output.
        save_csv: Whether to save CSV files to disk.
        save_prefix: Prefix for saved CSV filenames.
        include_feature_metrics: Whether to compute and return feature metrics
            DataFrame via build_feature_metrics_df.

    Returns:
        Dictionary containing:
            - "pca_results": Dict with PCA results from perform_pca_analysis
            - "scores_df": DataFrame with PC scores and metadata
            - "trait_contrib_df": DataFrame with trait variance contributions
            - "variance_df": DataFrame with variance explained per PC

    Raises:
        ValueError: If analysis_dir doesn't exist or if PCA fails.

    Example:
        >>> results = run_pca_and_export_artifacts(
        ...     df_traits=df,
        ...     trait_cols=["trait1", "trait2", "trait3"],
        ...     analysis_dir="./pca_output",
        ...     n_components=5,
        ...     save_csv=True
        ... )
        >>> pca_results = results["pca_results"]
        >>> scores_df = results["scores_df"]
    """
    analysis_dir = Path(analysis_dir)
    if save_csv:
        analysis_dir.mkdir(parents=True, exist_ok=True)

    # Select trait matrix
    if trait_cols is not None:
        X_df = df_traits[trait_cols]
    else:
        # Use all numeric columns if trait_cols not specified
        X_df = df_traits.select_dtypes(include=[np.number])

    # Main PCA call
    pca_results = perform_pca_analysis(
        data=X_df,
        standardize=standardize,
        n_components=n_components,
        explained_variance_threshold=explained_variance_threshold,
    )

    # Pull essentials
    n_used = int(pca_results["n_components_selected"])
    loadings = pca_results["loadings"][:, :n_used]  # (n_features, n_used)
    eigenvalues = pca_results["eigenvalues"][:n_used]  # (n_used,)
    evr = pca_results["explained_variance_ratio"][:n_used]
    cvr = pca_results["cumulative_variance_ratio"][:n_used]
    features = pca_results["feature_names"]

    # (1) Loadings matrix (unit vectors)
    loadings_df = pd.DataFrame(
        loadings,
        columns=[f"PC{i + 1}" for i in range(n_used)],
        index=features,
    )

    # (2) Per-trait contributions (variance units): λ_k * v_{jk}^2
    trait_pc_variance_contrib = (loadings**2) * eigenvalues  # (features x PCs)
    trait_total = trait_pc_variance_contrib.sum(axis=1)  # per-feature total
    total_variance_selected = float(eigenvalues.sum())  # scalar

    # Unitless fraction of explained variance attributable to each trait (sums to 1)
    trait_fractional_contrib = (
        trait_total / total_variance_selected if total_variance_selected > 0 else 0.0
    )

    trait_contrib_df = (
        pd.DataFrame(
            trait_pc_variance_contrib,
            columns=[f"PC{i + 1}_variance_contrib" for i in range(n_used)],
            index=features,
        )
        .assign(
            trait=lambda d: d.index,
            trait_total_variance_contrib=trait_total,
            trait_fractional_contrib=trait_fractional_contrib,
        )
        .sort_values("trait_total_variance_contrib", ascending=False)
    )

    # Reorder columns
    cols_order = (
        ["trait"]
        + [f"PC{i + 1}_variance_contrib" for i in range(n_used)]
        + ["trait_total_variance_contrib", "trait_fractional_contrib"]
    )
    trait_contrib_df = trait_contrib_df[cols_order]

    # Keep handy in pca_results for downstream code
    pca_results["trait_ev_df"] = trait_contrib_df
    pca_results["feature_importance_consistent"] = trait_contrib_df

    # (3) Per-PC variance explained (ratios → %)
    variance_df = pd.DataFrame(
        {
            "PC": [f"PC{i + 1}" for i in range(len(evr))],
            "Explained Variance (%)": evr * 100.0,
            "Cumulative Variance (%)": cvr * 100.0,
        }
    )

    # (4) PC scores + metadata
    scores = pca_results["transformed_data"][:, :n_used]
    pc_cols = [f"PC{i + 1}" for i in range(n_used)]
    scores_df = pd.DataFrame(scores, columns=pc_cols)

    # Add metadata if present
    idx_map = pca_results.get("data_indices", None)
    meta_df = df_traits if idx_map is None else df_traits.iloc[idx_map]

    for col in metadata_cols:
        if col in meta_df.columns:
            scores_df[col] = meta_df[col].values

    # Put metadata first
    ordered = [c for c in metadata_cols if c in scores_df.columns] + pc_cols
    pc_scores_df = scores_df[ordered]

    # (5) Optional tidy feature metrics
    feature_metrics_df = None
    if include_feature_metrics:
        feature_metrics_df = build_feature_metrics_df(
            pca_results,
            include_loadings=True,
            sort_by="fraction_explained",
        )

    # Save CSVs with configurable prefix
    if save_csv:
        loadings_df.to_csv(analysis_dir / f"{save_prefix}pca_loadings.csv")
        trait_contrib_df.to_csv(
            analysis_dir / f"{save_prefix}trait_variance_contrib.csv", index=False
        )
        variance_df.to_csv(
            analysis_dir / f"{save_prefix}pca_variance_explained.csv", index=False
        )
        pc_scores_df.to_csv(
            analysis_dir / f"{save_prefix}pca_transformed_data.csv", index=False
        )
        if feature_metrics_df is not None:
            feature_metrics_df.to_csv(
                analysis_dir / f"{save_prefix}feature_metrics.csv", index=False
            )

    # Return everything useful for notebooks
    out: Dict[str, Union[pd.DataFrame, Dict]] = {
        "loadings_df": loadings_df,
        "trait_contrib_df": trait_contrib_df,
        "variance_df": variance_df,
        "pc_scores_df": pc_scores_df,
        "pca_results": pca_results,
    }
    if feature_metrics_df is not None:
        out["feature_metrics_df"] = feature_metrics_df

    return out


def perform_pca_with_variance_threshold(
    X: np.ndarray,
    explained_variance_threshold: float = 0.95,
    n_components: Optional[int] = None,
    random_state: int = 42,
) -> Dict:
    """Perform PCA analysis with automatic component selection based on variance threshold.

    This is the legacy function maintained for backward compatibility.
    New code should use perform_pca_analysis() instead.

    Args:
        X: Standardized data array of shape (n_samples, n_features)
        explained_variance_threshold: Cumulative variance threshold for component selection
        n_components: If specified, overrides automatic selection
        random_state: Random state for reproducibility

    Returns:
        Dictionary containing PCA results
    """
    n_samples, n_features = X.shape

    # Select number of components
    n_components_selected = select_n_components(
        X, explained_variance_threshold, n_components, random_state
    )

    # Fit PCA
    pca, X_transformed = fit_pca(X, n_components_selected, random_state)

    # Calculate metrics
    return calculate_pca_metrics(pca, X_transformed)


def calculate_pca_reconstruction_error(
    X_scaled: np.ndarray, pca_result: Dict
) -> np.ndarray:
    """Calculate reconstruction error for each sample.

    Args:
        X_scaled: Standardized original data (n_samples, n_features)
        pca_result: Result dictionary from perform_pca_with_variance_threshold

    Returns:
        Array of reconstruction errors (n_samples,)
    """
    pca = pca_result["pca"]
    X_transformed = pca_result["transformed_data"]

    # Reconstruct the data
    X_reconstructed = pca.inverse_transform(X_transformed)

    # Calculate reconstruction errors (sum of squared errors per sample)
    reconstruction_errors = np.sum((X_scaled - X_reconstructed) ** 2, axis=1)

    return reconstruction_errors


def standardize_data(
    df: pd.DataFrame,
) -> Tuple[np.ndarray, StandardScaler, pd.DataFrame]:
    """Standardize numeric data for PCA analysis.

    Args:
        df: DataFrame with numeric features

    Returns:
        Tuple of:
            - X_scaled: Standardized data array
            - scaler: Fitted StandardScaler
            - df_clean: DataFrame with non-numeric columns removed
    """
    # Remove non-numeric columns
    df_clean = df.select_dtypes(include=[np.number])

    # Drop columns with zero variance
    # Use ddof=0 for population variance (consistent with sklearn)
    variances = df_clean.var(ddof=0)
    non_zero_var_cols = variances[variances > 0].index
    df_clean = df_clean[non_zero_var_cols]

    if df_clean.empty:
        raise ValueError("No numeric columns with non-zero variance found")

    # Standardize
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(df_clean)

    return X_scaled, scaler, df_clean


def calculate_mahalanobis_distances(
    X_transformed: np.ndarray, robust: bool = False
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Calculate Mahalanobis distances in PCA space.

    Args:
        X_transformed: PCA-transformed data (n_samples, n_components)
        robust: If True, use robust covariance estimation

    Returns:
        Tuple of:
            - distances: Mahalanobis distances (n_samples,)
            - mean: Mean of transformed data (n_components,)
            - covariance: Covariance matrix (n_components, n_components)
    """
    # Handle 1D case
    if X_transformed.ndim == 1:
        X_transformed = X_transformed.reshape(-1, 1)
    elif X_transformed.shape[1] == 1:
        # Already proper shape for 1D
        pass

    n_samples, n_features = X_transformed.shape

    if robust:
        from sklearn.covariance import MinCovDet

        robust_cov = MinCovDet(random_state=42).fit(X_transformed)
        mean = robust_cov.location_
        covariance = robust_cov.covariance_
    else:
        mean = np.mean(X_transformed, axis=0)
        if n_features == 1:
            # Special case for 1D
            covariance = np.array([[np.var(X_transformed[:, 0], ddof=1)]])
        else:
            covariance = np.cov(X_transformed, rowvar=False, ddof=1)
            # Ensure it's 2D even for single feature
            if covariance.ndim == 0:
                covariance = np.array([[covariance]])

    # Calculate Mahalanobis distances
    diff = X_transformed - mean
    try:
        if n_features == 1:
            # For 1D, just use standard deviation
            std = np.sqrt(covariance[0, 0])
            if std > 0:
                distances = np.abs(diff[:, 0]) / std
            else:
                distances = np.zeros(n_samples)
        else:
            inv_cov = np.linalg.inv(covariance)
            distances = np.sqrt(np.sum(diff @ inv_cov * diff, axis=1))
    except np.linalg.LinAlgError:
        # If singular, use pseudo-inverse
        inv_cov = np.linalg.pinv(covariance)
        distances = np.sqrt(np.sum(diff @ inv_cov * diff, axis=1))

    return distances, mean, covariance


def perform_pca_analysis(
    data: Union[pd.DataFrame, np.ndarray],
    standardize: bool = True,
    explained_variance_threshold: float = 0.95,
    n_components: Optional[int] = None,
    random_state: int = 42,
    include_feature_metrics: bool = True,
    ddof_feature_var: Optional[int] = None,
) -> Dict:
    """Perform complete PCA analysis pipeline with optional standardization.

    This is the main entry point for PCA analysis in the codebase.

    Args:
        data: Input data as DataFrame or array
        standardize: Whether to standardize the data (default: True)
        explained_variance_threshold: Cumulative variance threshold for component selection
        n_components: If specified, overrides automatic selection
        random_state: Random state for reproducibility
        include_feature_metrics: Whether to include per-feature metrics DataFrame (default: True)
        ddof_feature_var: Degrees of freedom for feature variance calculation (default: None, uses 1)

    Returns:
        Dictionary containing:
            - All metrics from calculate_pca_metrics
            - scaler: StandardScaler if standardize=True, else None
            - data_processed: Processed data (standardized or cleaned)
            - feature_names: List of feature names used
            - feature_metrics_df: DataFrame with per-feature metrics (if include_feature_metrics=True)
    """
    # Convert numpy array to DataFrame if needed
    if not isinstance(data, pd.DataFrame):
        # Assume it's a numpy array or array-like
        data = np.asarray(data)
        if data.ndim != 2:
            raise ValueError(f"Input array must be 2D, got shape {data.shape}")

        # Create feature names for the array
        feature_names = [f"Feature_{i}" for i in range(data.shape[1])]
        data = pd.DataFrame(data, columns=feature_names)

    # Now everything is a DataFrame - single logic path
    if data.empty:
        raise ValueError("Empty DataFrame provided")

    # Drop rows with any NaN values for PCA
    df_clean = data.dropna()

    if df_clean.empty:
        raise ValueError("No valid samples after removing NaN values")

    # Check if we have numeric columns
    df_numeric_check = df_clean.select_dtypes(include=[np.number])
    if df_numeric_check.empty:
        raise ValueError("No numeric columns found")

    # Check if we have at least 2 samples for meaningful PCA
    if len(df_clean) <= 1:
        # Special case for single sample
        feature_names = df_numeric_check.columns.tolist()
        X = df_numeric_check.values
        X_processed = X
        scaler = None
    else:
        if standardize:
            # Use standardize_data which handles everything
            X_processed, scaler, df_clean = standardize_data(df_clean)
            feature_names = df_clean.columns.tolist()
        else:
            # Manual cleaning without standardization
            df_numeric = df_clean.select_dtypes(include=[np.number])

            # Drop columns with zero variance
            # ddof = 0 for population variance
            variances = df_numeric.var(ddof=0)
            non_zero_var_cols = variances[variances > 0].index
            df_numeric = df_numeric[non_zero_var_cols]

            if df_numeric.empty:
                raise ValueError("No numeric columns with non-zero variance found")

            feature_names = df_numeric.columns.tolist()
            X_processed = df_numeric.values
            scaler = None

    # Validate we have enough samples after preprocessing
    if X_processed.shape[0] <= 1:
        raise ValueError(
            f"PCA requires at least 2 samples after preprocessing, got {X_processed.shape[0]}. "
            "Check your input data or preprocessing steps."
        )

    # Select number of components
    n_components_selected = select_n_components(
        X_processed, explained_variance_threshold, n_components, random_state
    )

    # Fit PCA
    pca, X_transformed = fit_pca(X_processed, n_components_selected, random_state)

    # Calculate metrics with fitted data
    # Use ddof=1 for consistency with PCA's eigenvalue calculations
    result = calculate_pca_metrics(
        pca,
        X_transformed,
        X_fitted=X_processed,
        ddof_for_feature_var=ddof_feature_var if ddof_feature_var is not None else 1,
    )

    # Add additional information
    result.update(
        {
            "scaler": scaler,
            "data_processed": X_processed,
            "feature_names": feature_names,
        }
    )

    # Build feature contributions DataFrame (always include for consistency)
    # This replaces the old "feature_importance" key
    loadings = result["loadings"]
    eigenvalues = result["eigenvalues"]
    n_components = result["n_components_selected"]

    # Calculate per-feature total contributions
    loadings_used = loadings[:, :n_components]
    eigenvalues_used = eigenvalues[:n_components]
    total_contributions = np.sum(loadings_used**2 * eigenvalues_used, axis=1)

    # Normalize to get fractional contributions
    fractional_contributions = total_contributions / np.sum(total_contributions)

    # Create DataFrame with per-PC contributions
    contrib_dict = {
        "total_contribution": total_contributions,
        "fractional_contribution": fractional_contributions,
    }

    # Add per-PC variance contributions
    for i in range(n_components):
        pc_contribution = eigenvalues[i] * loadings[:, i] ** 2
        contrib_dict[f"PC{i + 1}_variance_contrib"] = pc_contribution

    feature_contributions = pd.DataFrame(contrib_dict, index=feature_names)
    feature_contributions = feature_contributions.sort_values(
        "total_contribution", ascending=False
    )

    result["feature_contributions"] = feature_contributions

    # Build feature metrics DataFrame if requested (for backward compatibility)
    if include_feature_metrics:
        result["feature_metrics_df"] = build_feature_metrics_df(
            result,
            ddof_feature_var=ddof_feature_var,
            include_loadings=True,
            sort_by="fraction_explained",
        )

    return result
