"""UMAP dimensionality reduction for root trait analysis."""

from __future__ import annotations

from typing import Dict, List
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

# Check if UMAP is available
try:
    import umap

    UMAP_AVAILABLE = True
except ImportError:
    UMAP_AVAILABLE = False


def perform_umap_analysis(
    df: pd.DataFrame,
    feature_cols: List[str],
    n_neighbors: int = 15,
    min_dist: float = 0.1,
    n_components: int = 2,
    random_state: int = 42,
) -> Dict:
    """Perform UMAP dimensionality reduction.

    Args:
        df: DataFrame containing the samples.
        feature_cols: List of feature column names to use for UMAP.
        n_neighbors: Size of local neighborhood (affects local vs global structure).
            Larger values result in more global structure being preserved.
        min_dist: Minimum distance between points in embedding.
            Controls how tightly UMAP packs points together.
        n_components: Number of dimensions for embedding.
        random_state: Random seed for reproducibility.

    Returns:
        Dictionary containing:
            - embedding: UMAP-transformed data (n_samples x n_components)
            - reducer: Fitted UMAP object for transforming new data
            - scaler: Fitted StandardScaler for preprocessing
            - n_neighbors: Number of neighbors used
            - min_dist: Minimum distance used

    Raises:
        ImportError: If umap-learn is not installed.
        ValueError: If parameters are invalid or data contains NaN values.
        KeyError: If specified feature columns don't exist in DataFrame.
    """
    if not UMAP_AVAILABLE:
        raise ImportError("UMAP is not installed. Install with: pip install umap-learn")

    # Validate parameters
    if n_neighbors <= 0:
        raise ValueError(f"n_neighbors must be positive, got {n_neighbors}")

    if min_dist < 0:
        raise ValueError(f"min_dist must be non-negative, got {min_dist}")

    if n_components <= 0:
        raise ValueError(f"n_components must be positive, got {n_components}")

    # Check for minimum samples
    if len(df) < 2:
        raise ValueError(f"UMAP requires at least 2 samples, got {len(df)}")

    # Prepare data
    X = df[feature_cols].values

    # Check for NaN values
    if np.any(np.isnan(X)):
        raise ValueError(
            "Input data contains NaN values. "
            "Please handle missing values before performing UMAP."
        )

    # Adjust n_neighbors if needed (can't be larger than n_samples)
    n_samples = X.shape[0]
    if n_neighbors > n_samples - 1:
        n_neighbors = n_samples - 1

    # Standardize features
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Perform UMAP
    reducer = umap.UMAP(
        n_neighbors=n_neighbors,
        min_dist=min_dist,
        n_components=n_components,
        random_state=random_state,
    )

    embedding = reducer.fit_transform(X_scaled)

    return {
        "embedding": embedding,
        "reducer": reducer,
        "scaler": scaler,
        "n_neighbors": n_neighbors,
        "min_dist": min_dist,
    }
