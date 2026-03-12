"""Core clustering algorithms for data exploration and analysis.

This module provides standalone clustering implementations that can be used
for data exploration, genotype grouping analysis, or as building blocks for
outlier detection methods.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Union

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.mixture import GaussianMixture
from sklearn.metrics import (
    silhouette_score,
    davies_bouldin_score,
    calinski_harabasz_score,
)

from .pca import standardize_data


def perform_kmeans_clustering(
    data: Union[pd.DataFrame, np.ndarray],
    n_clusters: Optional[int] = 3,
    max_clusters: int = 10,
    standardize: bool = True,
    random_state: int = 42,
) -> Dict:
    """Perform K-Means clustering on data.

    K-Means partitions data into k clusters by minimizing within-cluster
    sum of squares (inertia). This is a distance-based clustering method
    that assigns each sample to the nearest cluster center.

    Args:
        data: Input data as DataFrame or array
        n_clusters: Number of clusters (auto-selected via silhouette if None)
        max_clusters: Maximum clusters to test for auto-selection (default 10)
        standardize: Whether to standardize data before clustering
        random_state: Random seed for reproducibility

    Returns:
        Dictionary with clustering results:
        - method: 'KMeans'
        - n_clusters: Number of clusters
        - cluster_labels: Cluster assignment for each sample (N,)
        - cluster_centers: Cluster centroids in original space (k, n_features)
        - distances_to_centers: Distance of each sample to its assigned center (N,)
        - min_distances_to_centers: Min distance to ANY center (N,)
        - cluster_sizes: Number of samples per cluster
        - inertia: Within-cluster sum of squares
        - silhouette_score: Quality metric [-1, 1], higher is better
        - davies_bouldin_score: Quality metric [0, inf), lower is better
        - calinski_harabasz_score: Quality metric [0, inf), higher is better
        - data_indices: Original data indices
        - feature_names: Feature names (if DataFrame input)
        - data_processed: Processed data used for clustering (N, n_features)

    Raises:
        ValueError: If data is empty or has insufficient samples
    """
    # Convert to DataFrame to handle indices consistently
    if isinstance(data, np.ndarray):
        df = pd.DataFrame(data, columns=[f"Feature_{i}" for i in range(data.shape[1])])
    else:
        df = data.copy()

    # Check for empty data
    if df.empty or len(df) == 0:
        raise ValueError("Empty data provided")

    # Remove NaN rows
    df_clean = df.dropna()
    if df_clean.empty:
        raise ValueError("All rows contain NaN values")

    # Auto-select optimal k if not specified
    if n_clusters is None:
        optimal_result = calculate_optimal_k_kmeans(
            df_clean,
            max_clusters=max_clusters,
            method="silhouette",
            standardize=standardize,
            random_state=random_state,
        )
        n_clusters = optimal_result["optimal_n_clusters"]

    # Check sufficient samples
    if len(df_clean) < n_clusters:
        raise ValueError(
            f"Insufficient samples ({len(df_clean)}) for {n_clusters} clusters. "
            f"Need at least {n_clusters} samples."
        )

    # Adjust n_clusters if needed (at least 10 samples per cluster recommended)
    recommended_max_clusters = max(2, len(df_clean) // 10)
    if n_clusters > recommended_max_clusters:
        n_clusters = recommended_max_clusters

    original_indices = df_clean.index.tolist()
    feature_names = df_clean.columns.tolist()

    try:
        # Standardize data if requested
        if standardize:
            X_processed, scaler, _ = standardize_data(df_clean)
        else:
            X_processed = df_clean.values

        # Fit K-Means
        kmeans = KMeans(n_clusters=n_clusters, random_state=random_state, n_init=10)
        cluster_labels = kmeans.fit_predict(X_processed)
        cluster_centers = kmeans.cluster_centers_

        # Calculate distances for each sample to its assigned center
        distances_to_centers = np.zeros(len(X_processed))
        for i, (sample, label) in enumerate(zip(X_processed, cluster_labels)):
            distances_to_centers[i] = np.linalg.norm(sample - cluster_centers[label])

        # Calculate minimum distance to ANY center (for outlier detection)
        min_distances_to_centers = np.zeros(len(X_processed))
        for i, sample in enumerate(X_processed):
            distances = [np.linalg.norm(sample - center) for center in cluster_centers]
            min_distances_to_centers[i] = min(distances)

        # Calculate cluster sizes
        cluster_sizes = [int(np.sum(cluster_labels == i)) for i in range(n_clusters)]

        # Calculate quality metrics
        silhouette = silhouette_score(X_processed, cluster_labels)
        davies_bouldin = davies_bouldin_score(X_processed, cluster_labels)
        calinski_harabasz = calinski_harabasz_score(X_processed, cluster_labels)

        return {
            "method": "KMeans",
            "n_clusters": n_clusters,
            "cluster_labels": cluster_labels,
            "cluster_centers": cluster_centers,
            "distances_to_centers": distances_to_centers,
            "min_distances_to_centers": min_distances_to_centers,
            "cluster_sizes": cluster_sizes,
            "inertia": float(kmeans.inertia_),
            "silhouette_score": float(silhouette),
            "davies_bouldin_score": float(davies_bouldin),
            "calinski_harabasz_score": float(calinski_harabasz),
            "data_indices": original_indices,
            "feature_names": feature_names,
            "data_processed": X_processed,
        }

    except Exception as e:
        raise RuntimeError(f"K-Means clustering failed: {str(e)}") from e


def calculate_optimal_k_kmeans(
    data: Union[pd.DataFrame, np.ndarray],
    max_clusters: int = 10,
    method: str = "silhouette",
    standardize: bool = True,
    random_state: int = 42,
) -> Dict:
    """Find optimal number of clusters for K-Means clustering.

    Tests different values of k from 2 to max_clusters and evaluates clustering
    quality using the specified metric. Returns the optimal k and all scores
    to help determine the best number of clusters.

    Args:
        data: Input data as DataFrame or array
        max_clusters: Maximum k to test (default 10)
        method: Metric to optimize. Options:
            - 'silhouette': Silhouette score [-1, 1], higher is better (default)
            - 'calinski': Calinski-Harabasz score [0, inf), higher is better
            - 'davies_bouldin': Davies-Bouldin score [0, inf), lower is better
        standardize: Whether to standardize data before clustering
        random_state: Random seed for reproducibility

    Returns:
        Dictionary with optimization results:
        - optimal_n_clusters: Optimal number of clusters
        - scores: Score for each k tested
        - k_values: k values tested
        - method: Metric used for optimization

    Examples:
        >>> # Auto-select optimal k
        >>> result = calculate_optimal_k_kmeans(df, max_clusters=10)
        >>> print(f"Optimal k: {result['optimal_n_clusters']}")
        >>>
        >>> # Use different metric
        >>> result = calculate_optimal_k_kmeans(df, method='calinski')

    Raises:
        ValueError: If data is insufficient for clustering
    """
    # Convert to DataFrame if needed
    if isinstance(data, np.ndarray):
        df = pd.DataFrame(data, columns=[f"Feature_{i}" for i in range(data.shape[1])])
    else:
        df = data.copy()

    # Check for empty data
    if df.empty or len(df) == 0:
        raise ValueError("Empty data provided")

    # Remove NaN rows
    df_clean = df.dropna()
    if df_clean.empty:
        raise ValueError("All rows contain NaN values")

    n_samples = len(df_clean)

    # Limit max_clusters based on sample size
    # Need at least 2 samples per cluster on average
    max_clusters = min(max_clusters, n_samples // 2)

    if max_clusters < 2:
        raise ValueError(
            f"Insufficient samples ({n_samples}) for cluster optimization. "
            f"Need at least 4 samples."
        )

    try:
        # Standardize data if requested
        if standardize:
            X_processed, _, _ = standardize_data(df_clean)
        else:
            X_processed = df_clean.values

        scores = []
        k_values = list(range(2, max_clusters + 1))

        # Test each k value
        for k in k_values:
            kmeans = KMeans(n_clusters=k, random_state=random_state, n_init=10)
            cluster_labels = kmeans.fit_predict(X_processed)

            # Calculate quality metric
            if method == "silhouette":
                score = silhouette_score(X_processed, cluster_labels)
            elif method == "calinski":
                score = calinski_harabasz_score(X_processed, cluster_labels)
            elif method == "davies_bouldin":
                score = davies_bouldin_score(X_processed, cluster_labels)
            else:
                raise ValueError(
                    f"Unknown method: {method}. "
                    f"Choose from: 'silhouette', 'calinski', 'davies_bouldin'"
                )

            scores.append(float(score))

        # Find optimal k
        if method == "davies_bouldin":
            # Lower is better for Davies-Bouldin
            optimal_idx = np.argmin(scores)
        else:
            # Higher is better for silhouette and Calinski-Harabasz
            optimal_idx = np.argmax(scores)

        optimal_k = k_values[optimal_idx]

        return {
            "optimal_n_clusters": optimal_k,
            "scores": scores,
            "k_values": k_values,
            "method": method,
        }

    except Exception as e:
        raise RuntimeError(f"K-Means optimization failed: {str(e)}") from e


def perform_gmm_clustering(
    data: Union[pd.DataFrame, np.ndarray],
    n_components: Optional[int] = None,
    max_components: int = 5,
    covariance_type: str = "full",
    standardize: bool = True,
    random_state: int = 42,
) -> Dict:
    """Perform Gaussian Mixture Model clustering.

    GMM models data as a mixture of Gaussian distributions. Provides both
    hard (cluster labels) and soft (probabilities) assignments. Automatically
    selects optimal number of components using BIC if n_components is None.

    Args:
        data: Input data as DataFrame or array
        n_components: Number of mixture components (auto-selected if None)
        max_components: Max components to test for BIC-based selection
        covariance_type: Type of covariance parameters
            - 'full': each component has own general covariance matrix
            - 'tied': all components share same covariance matrix
            - 'diag': each component has own diagonal covariance matrix
            - 'spherical': each component has own single variance
        standardize: Whether to standardize data before clustering
        random_state: Random seed for reproducibility

    Returns:
        Dictionary with clustering results:
        - method: 'GMM'
        - n_components: Number of mixture components
        - cluster_labels: Hard cluster assignments (argmax of probabilities) (N,)
        - probabilities: Soft assignments - probability for each component (N, K)
        - means: Cluster means (K, n_features)
        - covariances: Cluster covariances (depends on covariance_type)
        - weights: Mixture weights (K,)
        - log_likelihoods: Log P(x) for each sample (N,)
        - bic: Bayesian Information Criterion for selected model
        - aic: Akaike Information Criterion for selected model
        - bic_scores: BIC for all k tested (if auto-selection used)
        - aic_scores: AIC for all k tested (if auto-selection used)
        - converged: Whether EM algorithm converged
        - n_iter: Number of EM iterations performed
        - covariance_type: Type of covariance used
        - cluster_sizes: Number of samples per cluster (based on hard assignments)
        - silhouette_score: Quality metric [-1, 1], higher is better
        - davies_bouldin_score: Quality metric [0, inf), lower is better
        - calinski_harabasz_score: Quality metric [0, inf), higher is better
        - data_indices: Original data indices
        - feature_names: Feature names (if DataFrame input)
        - data_processed: Processed data used for clustering (N, n_features)

    Raises:
        ValueError: If data is empty or has insufficient samples
    """
    # Convert to DataFrame to handle indices consistently
    if isinstance(data, np.ndarray):
        df = pd.DataFrame(data, columns=[f"Feature_{i}" for i in range(data.shape[1])])
    else:
        df = data.copy()

    # Check for empty data
    if df.empty or len(df) == 0:
        raise ValueError("Empty data provided")

    # Remove NaN rows
    df_clean = df.dropna()
    if df_clean.empty:
        raise ValueError("All rows contain NaN values")

    # Check sufficient samples for GMM
    min_samples_needed = max_components if n_components is None else n_components
    if len(df_clean) < min_samples_needed * 2:
        raise ValueError(
            f"Insufficient samples ({len(df_clean)}) for GMM with {min_samples_needed} components. "
            f"Need at least {min_samples_needed * 2} samples."
        )

    original_indices = df_clean.index.tolist()
    feature_names = df_clean.columns.tolist()

    try:
        # Standardize data if requested
        if standardize:
            X_processed, scaler, _ = standardize_data(df_clean)
        else:
            X_processed = df_clean.values

        bic_scores = []
        aic_scores = []

        if n_components is None:
            # Auto-select number of components using BIC
            lowest_bic = np.inf
            best_n = 1

            # Limit max_components based on sample size
            max_test = min(max_components, len(df_clean) // 10)
            if max_test < 1:
                max_test = 1

            for n in range(1, max_test + 1):
                gmm_candidate = GaussianMixture(
                    n_components=n,
                    covariance_type=covariance_type,
                    random_state=random_state,
                )
                gmm_candidate.fit(X_processed)
                bic = gmm_candidate.bic(X_processed)
                aic = gmm_candidate.aic(X_processed)
                bic_scores.append(float(bic))
                aic_scores.append(float(aic))

                if bic < lowest_bic:
                    lowest_bic = bic
                    best_n = n

            n_components = best_n

        # Fit GMM with selected/specified number of components
        gmm = GaussianMixture(
            n_components=n_components,
            covariance_type=covariance_type,
            random_state=random_state,
        )
        gmm.fit(X_processed)

        # Get cluster assignments and probabilities
        probabilities = gmm.predict_proba(X_processed)  # Soft assignments
        cluster_labels = gmm.predict(X_processed)  # Hard assignments

        # Get log-likelihoods
        log_likelihoods = gmm.score_samples(X_processed)

        # Calculate final BIC/AIC if not already done
        if not bic_scores:
            bic_scores = [float(gmm.bic(X_processed))]
            aic_scores = [float(gmm.aic(X_processed))]

        # Calculate cluster sizes (based on hard assignments)
        cluster_sizes = [int(np.sum(cluster_labels == i)) for i in range(n_components)]

        # Calculate quality metrics (using hard assignments)
        # Note: silhouette requires 2+ clusters
        if n_components > 1:
            silhouette = silhouette_score(X_processed, cluster_labels)
            davies_bouldin = davies_bouldin_score(X_processed, cluster_labels)
            calinski_harabasz = calinski_harabasz_score(X_processed, cluster_labels)
        else:
            # Single component - quality metrics not meaningful
            silhouette = 0.0
            davies_bouldin = 0.0
            calinski_harabasz = 0.0

        return {
            "method": "GMM",
            "n_components": n_components,
            "cluster_labels": cluster_labels,
            "probabilities": probabilities,
            "means": gmm.means_,
            "covariances": gmm.covariances_,
            "weights": gmm.weights_,
            "log_likelihoods": log_likelihoods,
            "bic": bic_scores[-1],  # BIC of final model
            "aic": aic_scores[-1],  # AIC of final model
            "bic_scores": bic_scores,
            "aic_scores": aic_scores,
            "converged": gmm.converged_,
            "n_iter": int(gmm.n_iter_),
            "covariance_type": covariance_type,
            "cluster_sizes": cluster_sizes,
            "silhouette_score": float(silhouette),
            "davies_bouldin_score": float(davies_bouldin),
            "calinski_harabasz_score": float(calinski_harabasz),
            "data_indices": original_indices,
            "feature_names": feature_names,
            "data_processed": X_processed,
        }

    except Exception as e:
        raise RuntimeError(f"GMM clustering failed: {str(e)}") from e


def calculate_cluster_quality_metrics(
    data: np.ndarray, labels: np.ndarray
) -> Dict[str, float]:
    """Calculate clustering quality metrics.

    Args:
        data: Data array (N, n_features)
        labels: Cluster labels (N,)

    Returns:
        Dictionary with quality metrics:
        - silhouette_score: [-1, 1], higher is better. Measures how similar samples
          are to their own cluster compared to other clusters.
        - davies_bouldin_score: [0, inf), lower is better. Ratio of within-cluster
          to between-cluster distances.
        - calinski_harabasz_score: [0, inf), higher is better. Ratio of between-cluster
          dispersion to within-cluster dispersion.

    Raises:
        ValueError: If data/labels are invalid
    """
    if len(data) != len(labels):
        raise ValueError("Data and labels must have same length")

    if len(np.unique(labels)) < 2:
        raise ValueError("Need at least 2 clusters for quality metrics")

    try:
        silhouette = silhouette_score(data, labels)
        davies_bouldin = davies_bouldin_score(data, labels)
        calinski_harabasz = calinski_harabasz_score(data, labels)

        return {
            "silhouette_score": float(silhouette),
            "davies_bouldin_score": float(davies_bouldin),
            "calinski_harabasz_score": float(calinski_harabasz),
        }
    except Exception as e:
        raise RuntimeError(f"Failed to calculate quality metrics: {str(e)}") from e


def perform_hierarchical_clustering(
    data: Union[pd.DataFrame, np.ndarray],
    method: str = "ward",
    metric: str = "euclidean",
    standardize: bool = True,
    compute_full_tree: bool = True,
) -> Dict:
    """Perform hierarchical/agglomerative clustering.

    Creates a dendrogram showing hierarchical relationships between samples.
    Unlike K-Means/GMM, does not require specifying number of clusters upfront.

    Args:
        data: Input data as DataFrame or array
        method: Linkage method for hierarchy construction
            - 'ward': Minimizes within-cluster variance (default, good for most cases)
            - 'complete': Maximum distance between clusters (sensitive to outliers)
            - 'average': Average distance between clusters (balanced)
            - 'single': Minimum distance between clusters (can create chains)
        metric: Distance metric ('euclidean', 'manhattan', 'cosine', etc.)
            Note: 'ward' method requires 'euclidean' metric
        standardize: Whether to standardize data before clustering
        compute_full_tree: Compute full dendrogram (set False for large datasets)

    Returns:
        Dictionary with hierarchical clustering results:
        - method: 'Hierarchical'
        - linkage_matrix: Hierarchical clustering encoded as linkage matrix (n-1, 4)
        - linkage_method: Linkage method used
        - distance_metric: Distance metric used
        - cophenetic_correlation: Quality metric [0, 1], higher is better.
          Measures how faithfully the dendrogram preserves pairwise distances.
        - data_indices: Original data indices
        - feature_names: Feature names (if DataFrame input)
        - data_processed: Processed data used for clustering (N, n_features)

    Raises:
        ValueError: If data is empty or parameters are invalid

    Examples:
        >>> result = perform_hierarchical_clustering(df, method='ward')
        >>> print(f"Cophenetic correlation: {result['cophenetic_correlation']:.3f}")
        >>> # Cut at different heights to explore different k values
        >>> cut_result = cut_dendrogram(result, n_clusters=3)
    """
    from scipy.cluster.hierarchy import linkage, cophenet
    from scipy.spatial.distance import pdist

    # Convert to DataFrame to handle indices consistently
    if isinstance(data, np.ndarray):
        df = pd.DataFrame(data, columns=[f"Feature_{i}" for i in range(data.shape[1])])
    else:
        df = data.copy()

    # Check for empty data
    if df.empty or len(df) == 0:
        raise ValueError("Empty data provided")

    # Remove NaN rows
    df_clean = df.dropna()
    if df_clean.empty:
        raise ValueError("All rows contain NaN values")

    # Need at least 2 samples for hierarchical clustering
    if len(df_clean) < 2:
        raise ValueError("Need at least 2 samples for hierarchical clustering")

    # Validate method and metric combination
    if method == "ward" and metric != "euclidean":
        raise ValueError("Ward linkage requires euclidean metric")

    original_indices = df_clean.index.tolist()
    feature_names = df_clean.columns.tolist()

    try:
        # Standardize data if requested
        if standardize:
            X_processed, scaler, _ = standardize_data(df_clean)
        else:
            X_processed = df_clean.values

        # Perform hierarchical clustering
        linkage_matrix = linkage(X_processed, method=method, metric=metric)

        # Calculate cophenetic correlation (quality metric)
        # Measures how faithfully the dendrogram preserves pairwise distances
        distances = pdist(X_processed, metric=metric)
        cophenetic_corr, _ = cophenet(linkage_matrix, distances)

        return {
            "method": "Hierarchical",
            "linkage_matrix": linkage_matrix,
            "linkage_method": method,
            "distance_metric": metric,
            "cophenetic_correlation": float(cophenetic_corr),
            "data_indices": original_indices,
            "feature_names": feature_names,
            "data_processed": X_processed,
        }

    except Exception as e:
        raise RuntimeError(f"Hierarchical clustering failed: {str(e)}") from e


def cut_dendrogram(
    hierarchical_result: Dict,
    n_clusters: Optional[int] = None,
    height_threshold: Optional[float] = None,
) -> Dict:
    """Cut dendrogram to get cluster labels.

    Provides flexible cluster extraction from hierarchical clustering result.
    Can cut by number of clusters or by height threshold.

    Args:
        hierarchical_result: Result from perform_hierarchical_clustering()
        n_clusters: Number of clusters to extract (if None, use height_threshold)
        height_threshold: Height at which to cut dendrogram (if None, use n_clusters)

    Returns:
        Dictionary with cut results:
        - cluster_labels: Cluster assignment for each sample (N,)
        - n_clusters: Number of clusters
        - cluster_sizes: Number of samples per cluster
        - silhouette_score: Quality metric [-1, 1]
        - davies_bouldin_score: Quality metric [0, inf)
        - calinski_harabasz_score: Quality metric [0, inf)
        - cut_height: Height where dendrogram was cut
        - data_indices: Original data indices

    Raises:
        ValueError: If neither n_clusters nor height_threshold is provided

    Examples:
        >>> # Cut by number of clusters
        >>> cut_result = cut_dendrogram(hier_result, n_clusters=3)
        >>>
        >>> # Cut by height
        >>> cut_result = cut_dendrogram(hier_result, height_threshold=5.0)
    """
    from scipy.cluster.hierarchy import fcluster

    if n_clusters is None and height_threshold is None:
        raise ValueError("Must provide either n_clusters or height_threshold")

    linkage_matrix = hierarchical_result["linkage_matrix"]
    X_processed = hierarchical_result["data_processed"]
    data_indices = hierarchical_result["data_indices"]

    try:
        if n_clusters is not None:
            # Cut by number of clusters
            cluster_labels = fcluster(linkage_matrix, n_clusters, criterion="maxclust")
            # Calculate the height at which this cut occurs
            # The linkage matrix has heights in the 3rd column
            # We need the height that creates n_clusters
            if n_clusters == 1:
                cut_height = linkage_matrix[-1, 2]
            else:
                # Height is at the merge that creates n_clusters
                cut_height = linkage_matrix[-(n_clusters - 1), 2]
        else:
            # Cut by height threshold
            cluster_labels = fcluster(
                linkage_matrix, height_threshold, criterion="distance"
            )
            cut_height = height_threshold
            n_clusters = len(np.unique(cluster_labels))

        # Convert to 0-indexed labels
        cluster_labels = cluster_labels - 1

        # Calculate cluster sizes
        cluster_sizes = [int(np.sum(cluster_labels == i)) for i in range(n_clusters)]

        # Calculate quality metrics
        if n_clusters > 1:
            quality_metrics = calculate_cluster_quality_metrics(
                X_processed, cluster_labels
            )
        else:
            quality_metrics = {
                "silhouette_score": 0.0,
                "davies_bouldin_score": 0.0,
                "calinski_harabasz_score": 0.0,
            }

        return {
            "cluster_labels": cluster_labels,
            "n_clusters": n_clusters,
            "cluster_sizes": cluster_sizes,
            "cut_height": float(cut_height),
            "data_indices": data_indices,
            **quality_metrics,
        }

    except Exception as e:
        raise RuntimeError(f"Failed to cut dendrogram: {str(e)}") from e


def calculate_optimal_clusters_hierarchical(
    hierarchical_result: Dict,
    max_clusters: int = 10,
    method: str = "silhouette",
) -> Dict:
    """Find optimal number of clusters for hierarchical result.

    Tests different numbers of clusters and returns quality metrics to help
    determine the best k.

    Args:
        hierarchical_result: Result from perform_hierarchical_clustering()
        max_clusters: Maximum k to test
        method: Metric to optimize ('silhouette', 'calinski', 'davies_bouldin')

    Returns:
        Dictionary with optimization results:
        - optimal_n_clusters: Optimal number of clusters
        - scores: Score for each k tested
        - k_values: k values tested
        - method: Metric used for optimization

    Examples:
        >>> optimal_result = calculate_optimal_clusters_hierarchical(
        ...     hier_result, max_clusters=10, method='silhouette'
        ... )
        >>> print(f"Optimal k: {optimal_result['optimal_n_clusters']}")
    """
    linkage_matrix = hierarchical_result["linkage_matrix"]
    X_processed = hierarchical_result["data_processed"]
    n_samples = len(X_processed)

    # Limit max_clusters based on sample size
    max_clusters = min(max_clusters, n_samples - 1)

    if max_clusters < 2:
        raise ValueError("Need at least 2 clusters to optimize")

    try:
        scores = []
        k_values = list(range(2, max_clusters + 1))

        for k in k_values:
            cut_result = cut_dendrogram(hierarchical_result, n_clusters=k)

            if method == "silhouette":
                score = cut_result["silhouette_score"]
                scores.append(score)
            elif method == "calinski":
                score = cut_result["calinski_harabasz_score"]
                scores.append(score)
            elif method == "davies_bouldin":
                score = cut_result["davies_bouldin_score"]
                scores.append(score)
            else:
                raise ValueError(f"Unknown method: {method}")

        # Find optimal k
        if method == "davies_bouldin":
            # Lower is better for Davies-Bouldin
            optimal_idx = np.argmin(scores)
        else:
            # Higher is better for silhouette and Calinski-Harabasz
            optimal_idx = np.argmax(scores)

        optimal_k = k_values[optimal_idx]

        return {
            "optimal_n_clusters": optimal_k,
            "scores": scores,
            "k_values": k_values,
            "method": method,
        }

    except Exception as e:
        raise RuntimeError(f"Failed to calculate optimal clusters: {str(e)}") from e
