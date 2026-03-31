"""Additional outlier detection visualization methods."""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from scipy.stats import chi2
from typing import Dict, List, Optional, Tuple
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
from pathlib import Path
from datetime import datetime


def create_isolation_forest_plots(
    df: pd.DataFrame, iso_results: Dict
) -> Dict[str, plt.Figure]:
    """Create Isolation Forest outlier detection visualization plots.

    Args:
        df: DataFrame with trait data
        iso_results: Results from Isolation Forest outlier detection

    Returns:
        Dictionary of plot names to figure objects
    """
    figures = {}

    if "error" in iso_results:
        return figures

    # Main visualization
    if iso_results.get("anomaly_scores"):
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

        scores = np.array(iso_results["anomaly_scores"])
        outlier_indices = set(iso_results.get("outlier_indices", []))

        # Get data indices
        if "data_indices" in iso_results:
            data_indices = iso_results["data_indices"]
        else:
            data_indices = list(range(len(scores)))

        # Histogram of anomaly scores
        ax1.hist(scores, bins=30, alpha=0.7, edgecolor="black")
        ax1.set_xlabel("Anomaly Score")
        ax1.set_ylabel("Frequency")
        ax1.set_title("Distribution of Isolation Forest Anomaly Scores")
        ax1.set_xlim(scores.min() - 0.1, scores.max() + 0.1)

        # Scatter plot of scores
        colors = ["red" if idx in outlier_indices else "blue" for idx in data_indices]

        # Sort for better visualization
        sorted_positions = np.argsort(scores)
        sorted_scores = scores[sorted_positions]
        sorted_colors = [colors[i] for i in sorted_positions]

        ax2.scatter(
            range(len(sorted_scores)), sorted_scores, c=sorted_colors, alpha=0.6
        )
        ax2.set_xlabel("Sample Index (sorted by score)")
        ax2.set_ylabel("Anomaly Score")
        ax2.set_title(
            f"Isolation Forest Anomaly Scores\n({len(outlier_indices)} outliers)"
        )
        ax2.grid(True, alpha=0.3)

        # Add legend
        legend_elements = [
            Patch(facecolor="blue", alpha=0.6, label="Normal"),
            Patch(facecolor="red", alpha=0.6, label="Outlier"),
        ]
        ax2.legend(handles=legend_elements)

        plt.tight_layout()
        figures["isolation_forest_analysis"] = fig

    return figures


def create_outlier_overlap_heatmap(all_outlier_results: Dict) -> plt.Figure:
    """Create a heatmap showing overlap between outlier detection methods.

    Args:
        all_outlier_results: Dictionary with results from all outlier methods

    Returns:
        Matplotlib figure object
    """
    # Extract outlier sets from each method
    method_outliers = {}
    for method, results in all_outlier_results.items():
        if (
            isinstance(results, dict)
            and "outlier_indices" in results
            and method != "combined"
        ):
            method_outliers[method] = set(results["outlier_indices"])

    method_names = list(method_outliers.keys())
    n_methods = len(method_names)

    # Handle case with no methods
    if n_methods == 0:
        fig, ax = plt.subplots(figsize=(10, 8))
        ax.text(
            0.5,
            0.5,
            "No outlier detection methods found",
            ha="center",
            va="center",
            transform=ax.transAxes,
            fontsize=12,
        )
        ax.set_title("Outlier Method Overlap", fontsize=14, fontweight="bold")
        ax.axis("off")
        return fig

    # Create overlap matrix
    overlap_matrix = np.zeros((n_methods, n_methods))

    for i, method1 in enumerate(method_names):
        for j, method2 in enumerate(method_names):
            if i == j:
                # Diagonal: number of outliers detected by this method
                overlap_matrix[i, j] = len(method_outliers[method1])
            else:
                # Off-diagonal: number of overlapping outliers
                overlap = method_outliers[method1].intersection(
                    method_outliers[method2]
                )
                overlap_matrix[i, j] = len(overlap)

    # Create figure
    fig, ax = plt.subplots(figsize=(10, 8))

    # Create mask for upper triangle
    mask = np.triu(np.ones_like(overlap_matrix, dtype=bool), k=1)

    # Create heatmap
    sns.heatmap(
        overlap_matrix,
        mask=mask,
        annot=True,
        fmt="g",
        cmap="Blues",
        square=True,
        xticklabels=method_names,
        yticklabels=method_names,
        cbar_kws={"label": "Number of Outliers"},
        ax=ax,
    )

    ax.set_title(
        "Outlier Detection Method Overlap\n(Diagonal: Total outliers per method)"
    )
    plt.tight_layout()

    return fig


def create_outliers_per_genotype_plot(
    df: pd.DataFrame, all_outlier_results: Dict, genotype_col: str = "geno"
) -> plt.Figure:
    """Create visualization of outliers per genotype for each method.

    Args:
        df: DataFrame with trait and genotype data
        all_outlier_results: Dictionary with results from all outlier methods
        genotype_col: Name of genotype column

    Returns:
        Matplotlib figure object
    """
    # Collect outlier counts per genotype per method
    genotypes = sorted(df[genotype_col].unique())
    methods = [m for m in all_outlier_results.keys() if m != "combined"]

    outlier_counts = {method: {geno: 0 for geno in genotypes} for method in methods}
    total_counts = {geno: 0 for geno in genotypes}

    # Count samples per genotype
    for geno in genotypes:
        total_counts[geno] = len(df[df[genotype_col] == geno])

    # Count outliers per genotype per method
    for method, results in all_outlier_results.items():
        if method != "combined" and "outlier_indices" in results:
            for idx in results["outlier_indices"]:
                if idx in df.index:
                    geno = df.loc[idx, genotype_col]
                    if geno in genotypes:
                        outlier_counts[method][geno] += 1

    # Create figure
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))

    # Plot 1: Absolute counts
    x = np.arange(len(genotypes))
    width = 0.8 / len(methods)

    for i, method in enumerate(methods):
        counts = [outlier_counts[method][geno] for geno in genotypes]
        offset = (i - len(methods) / 2 + 0.5) * width
        ax1.bar(x + offset, counts, width, label=method, alpha=0.8)

    ax1.set_xlabel("Genotype")
    ax1.set_ylabel("Number of Outliers")
    ax1.set_title("Outliers per Genotype by Detection Method")
    ax1.set_xticks(x)
    ax1.set_xticklabels(genotypes, rotation=90, ha="center")
    ax1.legend(bbox_to_anchor=(1.05, 1), loc="upper left")
    ax1.grid(True, alpha=0.3, axis="y")

    # Plot 2: Proportions
    for i, method in enumerate(methods):
        proportions = [
            (
                outlier_counts[method][geno] / total_counts[geno] * 100
                if total_counts[geno] > 0
                else 0
            )
            for geno in genotypes
        ]
        offset = (i - len(methods) / 2 + 0.5) * width
        ax2.bar(x + offset, proportions, width, label=method, alpha=0.8)

    ax2.set_xlabel("Genotype")
    ax2.set_ylabel("Outlier Percentage (%)")
    ax2.set_title("Outlier Proportion per Genotype by Detection Method")
    ax2.set_xticks(x)
    ax2.set_xticklabels(genotypes, rotation=90, ha="center")
    ax2.legend(bbox_to_anchor=(1.05, 1), loc="upper left")
    ax2.grid(True, alpha=0.3, axis="y")

    # Add sample sizes as text
    for i, geno in enumerate(genotypes):
        ax2.text(i, -2, f"n={total_counts[geno]}", ha="center", va="top", fontsize=8)

    plt.tight_layout()
    return fig


def create_mahalanobis_outlier_plots(
    df: pd.DataFrame, mahal_results: Dict
) -> Dict[str, plt.Figure]:
    """Create Mahalanobis-specific outlier detection plots.

    Args:
        df: DataFrame with trait data
        mahal_results: Results from Mahalanobis outlier detection

    Returns:
        Dictionary of plot names to figure objects
    """
    figures = {}

    if "error" in mahal_results:
        return figures

    # 1. Main outlier detection figure based on threshold type
    if mahal_results.get("mahalanobis_distances"):
        threshold_type = mahal_results.get("threshold_type", "distance")

        # Create figure with 3 subplots
        fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(18, 6))

        distances = np.array(mahal_results["mahalanobis_distances"])

        # Handle empty distances
        if len(distances) == 0:
            for ax in [ax1, ax2, ax3]:
                ax.text(
                    0.5,
                    0.5,
                    "No data available",
                    ha="center",
                    va="center",
                    transform=ax.transAxes,
                )
                ax.set_title("No data")
            fig.suptitle(
                "Mahalanobis Distance Outlier Detection - No Data",
                fontsize=14,
                fontweight="bold",
            )
            figures["mahalanobis_outlier_detection"] = fig
            return figures

        outlier_indices = set(mahal_results.get("outlier_indices", []))
        threshold_value = mahal_results.get("threshold_value", 0)
        n_components = mahal_results.get("n_components", 1)

        # Get data indices
        if "data_indices" in mahal_results:
            data_indices = mahal_results["data_indices"]
        else:
            data_indices = list(range(len(distances)))

        # Determine what to plot based on threshold type
        if threshold_type == "chi_squared":
            # For chi-squared, plot squared distances
            plot_distances = distances**2
            ylabel = "Mahalanobis Distance²"
            dist_label = "Distance²"
            # Chi-squared threshold is already squared
            plot_threshold = threshold_value
            chi2_percentile = mahal_results.get("chi2_percentile", 95.0)
            threshold_label = rf"$\chi^2_{{{chi2_percentile / 100:.2f}}}({n_components}) = {threshold_value:.2f}$"
        else:
            # For distance threshold, plot raw distances
            plot_distances = distances
            ylabel = "Mahalanobis Distance"
            dist_label = "Distance"
            plot_threshold = threshold_value
            threshold_label = f"Threshold = {threshold_value:.2f}"

        # Plot 1: Distribution with goodness of fit (moved from plot 2)
        ax1.hist(
            plot_distances,
            bins=50,
            alpha=0.7,
            edgecolor="black",
            density=True,
            label="Observed data",
        )
        ax1.axvline(
            x=plot_threshold,
            color="red",
            linestyle="--",
            linewidth=2,
            alpha=0.8,
            label=threshold_label,
        )

        # Add chi-squared distribution overlay
        if threshold_type == "chi_squared" and len(plot_distances) > 0:
            # Overlay theoretical chi-squared distribution
            x_range = np.linspace(0, max(plot_distances) * 1.1, 1000)
            chi2_pdf = chi2.pdf(x_range, df=n_components)
            ax1.plot(
                x_range,
                chi2_pdf,
                "g-",
                linewidth=2,
                label=rf"$\chi^2({n_components})$ theoretical",
            )

        ax1.set_xlabel(dist_label)
        ax1.set_ylabel("Density")
        ax1.set_title(f"Distribution of Mahalanobis {dist_label}", fontsize=12)
        ax1.legend(loc="upper right")
        ax1.grid(True, alpha=0.3)

        # NOTE: Goodness-of-fit display removed from figure (too crowded)
        # Use print_goodness_of_fit_summary() in console instead
        # See: sleap_roots_analyze.outlier_detection.print_goodness_of_fit_summary()

        # Goodness-of-fit results are still available in:
        # - mahal_results['goodness_of_fit'] dictionary
        # - Saved in 03_outlier_detection_results.json
        pass  # Placeholder to maintain code structure

        # Plot 2: Outlier detection plot (sorted distances)
        sorted_indices = np.argsort(plot_distances)
        sorted_distances = plot_distances[sorted_indices]
        sorted_data_indices = [data_indices[i] for i in sorted_indices]
        colors = [
            "red" if idx in outlier_indices else "blue" for idx in sorted_data_indices
        ]

        ax2.scatter(
            range(len(sorted_distances)), sorted_distances, c=colors, alpha=0.6, s=30
        )
        ax2.axhline(
            y=plot_threshold, color="red", linestyle="--", linewidth=2, alpha=0.8
        )

        # Add expected mean and standard deviation bands for chi-squared
        if threshold_type == "chi_squared":
            # For chi-squared distribution with k degrees of freedom:
            # Mean = k, Variance = 2k, SD = sqrt(2k)
            chi2_mean = n_components
            chi2_std = np.sqrt(2 * n_components)

            # Add mean line
            ax2.axhline(
                y=chi2_mean,
                color="green",
                linestyle="-",
                linewidth=1.5,
                alpha=0.7,
                label=f"Expected mean = {chi2_mean:.1f}",
            )

            # Add ±1 SD bands
            ax2.axhline(
                y=chi2_mean + chi2_std,
                color="green",
                linestyle=":",
                linewidth=1,
                alpha=0.5,
                label=f"±1 SD",
            )
            ax2.axhline(
                y=chi2_mean - chi2_std,
                color="green",
                linestyle=":",
                linewidth=1,
                alpha=0.5,
            )

            # Add ±2 SD bands
            ax2.axhline(
                y=chi2_mean + 2 * chi2_std,
                color="orange",
                linestyle=":",
                linewidth=1,
                alpha=0.5,
                label=f"±2 SD",
            )
            ax2.axhline(
                y=chi2_mean - 2 * chi2_std,
                color="orange",
                linestyle=":",
                linewidth=1,
                alpha=0.5,
            )
        ax2.set_xlabel("Sample Index (sorted by distance)")
        ax2.set_ylabel(ylabel)
        ax2.set_title("Outlier Detection", fontsize=12)

        # Add legend with cleaner labels
        legend_elements = [
            Patch(facecolor="blue", alpha=0.6, label="Normal"),
            Patch(
                facecolor="red", alpha=0.6, label=f"Outlier (n={len(outlier_indices)})"
            ),
            Line2D(
                [0],
                [0],
                color="red",
                linestyle="--",
                linewidth=2,
                label=threshold_label,
            ),
        ]
        # Add legend with distribution info if chi-squared
        if threshold_type == "chi_squared":
            # Add distribution lines to legend
            dist_elements = [
                Line2D(
                    [0],
                    [0],
                    color="green",
                    linestyle="-",
                    linewidth=1.5,
                    label=f"Expected mean = {chi2_mean:.1f}",
                ),
                Line2D(
                    [0], [0], color="green", linestyle=":", linewidth=1, label="±1 SD"
                ),
                Line2D(
                    [0], [0], color="orange", linestyle=":", linewidth=1, label="±2 SD"
                ),
            ]
            all_elements = legend_elements + dist_elements
            ax2.legend(handles=all_elements, loc="upper left")

            # Add text box with threshold interpretation
            chi2_mean = n_components
            chi2_std = np.sqrt(2 * n_components)
            threshold_z = (plot_threshold - chi2_mean) / chi2_std

            textstr = f"χ² Distribution Info:\n"
            textstr += f"Expected mean = {chi2_mean:.1f}\n"
            textstr += f"Expected SD = {chi2_std:.2f}\n"
            textstr += f"Threshold = {plot_threshold:.2f}\n"
            textstr += f"  = {threshold_z:.2f} SDs from mean"

            props = dict(boxstyle="round", facecolor="wheat", alpha=0.5)
            # Position below the legend
            ax2.text(
                0.02,
                0.65,
                textstr,
                transform=ax2.transAxes,
                fontsize=9,
                verticalalignment="top",
                horizontalalignment="left",
                bbox=props,
            )
        else:
            ax2.legend(handles=legend_elements, loc="upper left")
        ax2.grid(True, alpha=0.3)

        # Plot 3: PCA scatter plot with outliers
        if (
            "pca_components" in mahal_results
            and mahal_results["pca_components"] is not None
        ):
            pca_components = np.array(mahal_results["pca_components"])
            if pca_components.shape[1] >= 2:
                # Use same data indices mapping
                colors = [
                    "red" if idx in outlier_indices else "blue" for idx in data_indices
                ]
                ax3.scatter(
                    pca_components[:, 0],
                    pca_components[:, 1],
                    c=colors,
                    alpha=0.6,
                    s=50,
                )

                # Add cleaner legend
                legend_elements = [
                    Patch(facecolor="blue", alpha=0.6, label="Normal"),
                    Patch(
                        facecolor="red",
                        alpha=0.6,
                        label=f"Outlier (n={len(outlier_indices)})",
                    ),
                ]
                ax3.legend(handles=legend_elements, loc="best")
                ax3.set_xlabel("PC1")
                ax3.set_ylabel("PC2")
                ax3.set_title("PCA Projection", fontsize=12)
                ax3.grid(True, alpha=0.3)
        else:
            ax3.text(
                0.5,
                0.5,
                "PCA components not available",
                ha="center",
                va="center",
                transform=ax3.transAxes,
            )
            ax3.set_title("PCA Projection", fontsize=12)

        # Set main title based on threshold type
        if threshold_type == "chi_squared":
            chi2_percentile = mahal_results.get("chi2_percentile", 95.0)
            fig.suptitle(
                rf"Mahalanobis Outlier Detection ($\chi^2_{{{chi2_percentile / 100:.2f}}}$ threshold with {n_components} PCs)",
                fontsize=14,
            )
        else:
            fig.suptitle(
                f"Mahalanobis Outlier Detection (Distance threshold = {threshold_value:.2f} with {n_components} PCs)",
                fontsize=14,
            )

        plt.tight_layout()
        figures["mahalanobis_outlier_detection"] = fig

    # 2. PC selection analysis (separate figure)
    if mahal_results.get("explained_variance_ratio"):
        fig2, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

        # Variance explanation
        var_ratio = mahal_results["explained_variance_ratio"]
        cum_var = np.cumsum(var_ratio)
        n_components = mahal_results.get("n_components", 1)

        x = range(1, len(var_ratio) + 1)
        ax1.bar(x, var_ratio, alpha=0.7, label="Individual")
        ax1.plot(x, cum_var, "ro-", label="Cumulative")
        ax1.axvline(
            x=n_components,
            color="green",
            linestyle="--",
            alpha=0.7,
            label=f"Selected ({n_components} PCs)",
        )
        ax1.axhline(
            y=mahal_results.get("variance_threshold", 0.75),
            color="red",
            linestyle="--",
            alpha=0.7,
            label=f"Threshold ({mahal_results.get('variance_threshold', 0.75):.0%})",
        )
        ax1.set_xlabel("Principal Component")
        ax1.set_ylabel("Explained Variance Ratio")
        ax1.set_title("PC Selection for Mahalanobis Distance", fontsize=12)
        ax1.legend()
        ax1.grid(True, alpha=0.3)

        # Feature importance - use pre-calculated explained variance ratio per feature
        if (
            "explained_variance_ratio_per_feature" in mahal_results
            and "feature_names" in mahal_results
        ):
            explained_var_ratio = np.array(
                mahal_results["explained_variance_ratio_per_feature"]
            )
            feature_names = mahal_results["feature_names"]

            top_indices = np.argsort(explained_var_ratio)[-10:]

            # Create bar plot with colors based on values
            bars = ax2.barh(range(len(top_indices)), explained_var_ratio[top_indices])
            ax2.set_yticks(range(len(top_indices)))
            ax2.set_yticklabels([feature_names[i] for i in top_indices])

            # Color code bars
            norm = plt.Normalize(vmin=0, vmax=1)
            sm = plt.cm.ScalarMappable(cmap="RdYlBu_r", norm=norm)
            for bar, val in zip(bars, explained_var_ratio[top_indices]):
                bar.set_color(sm.to_rgba(val))

            ax2.set_xlabel(f"Explained Variance Ratio (using {n_components} PCs)")
            ax2.set_title("Feature Variance Explained", fontsize=12)
            ax2.set_xlim([0, 1.05])
            ax2.axvline(x=1.0, color="black", linestyle="--", alpha=0.3, linewidth=0.5)
            ax2.grid(True, alpha=0.3, axis="x")
        elif "pca_loadings" in mahal_results and "feature_names" in mahal_results:
            # Fallback to old calculation
            loadings = np.array(mahal_results["pca_loadings"])
            feature_names = mahal_results["feature_names"]

            # Calculate overall importance as explained variance-weighted sum of squared loadings
            eigenvalues = mahal_results.get("eigenvalues", None)

            if eigenvalues is not None and len(eigenvalues) >= n_components:
                eigenvalues = eigenvalues[:n_components]
                importance = np.sum(
                    (loadings[:, :n_components] ** 2) * eigenvalues, axis=1
                )
            else:
                importance = np.sum(loadings[:, :n_components] ** 2, axis=1)
            top_indices = np.argsort(importance)[-10:]

            ax2.barh(range(len(top_indices)), importance[top_indices])
            ax2.set_yticks(range(len(top_indices)))
            ax2.set_yticklabels([feature_names[i] for i in top_indices])
            ax2.set_xlabel(
                rf"Trait Variance Contribution ($\sum_{{{n_components} \text{{ PCs}}}}\lambda_k \cdot loading_k^2$)"
            )
            ax2.set_title("Top 10 Contributing Features", fontsize=12)
            ax2.grid(True, alpha=0.3, axis="x")

        fig2.suptitle("Mahalanobis Distance - PC Selection Analysis", fontsize=14)
        plt.tight_layout()
        figures["mahalanobis_pc_analysis"] = fig2

    # 3. Threshold analysis (separate figure, without goodness of fit)
    if mahal_results.get("mahalanobis_distances"):
        fig3 = plt.figure(figsize=(12, 6))
        ax1 = fig3.add_subplot(111)

        distances = np.array(mahal_results["mahalanobis_distances"])
        squared_distances = distances**2
        n_components = mahal_results.get("n_components", 1)
        current_threshold = mahal_results.get("threshold_value", 0)
        threshold_type = mahal_results.get("threshold_type", "distance")

        # Create main axis and twin for dual scale
        ax1_twin = ax1.twinx()

        # Show effect of both chi-squared and distance-based thresholds
        # Chi-squared based thresholds
        percentiles = np.arange(90, 99.9, 0.5)
        chi2_thresholds = []
        chi2_outlier_counts = []

        for p in percentiles:
            threshold = chi2.ppf(p / 100, df=n_components)
            chi2_thresholds.append(np.sqrt(threshold))  # Convert to distance
            chi2_outlier_counts.append(np.sum(squared_distances > threshold))

        # Chi-squared distribution parameters
        # For chi-squared distribution: mean = k, std = sqrt(2k)
        chi2_mean = n_components
        chi2_std = np.sqrt(2 * n_components)

        # Plot chi-squared based
        line1 = ax1.plot(
            percentiles,
            chi2_outlier_counts,
            "b-",
            linewidth=2,
            label="Chi-squared percentile",
        )
        ax1.set_xlabel("Chi-squared Percentile")
        ax1.set_ylabel("Number of Outliers", color="b")
        ax1.tick_params(axis="y", labelcolor="b")

        # Plot distance based on secondary x-axis
        ax1_top = ax1.twiny()

        # To align the axes, we need to convert percentiles to SDs
        # For each percentile, calculate how many SDs it represents
        percentiles_as_sds = []
        for p in percentiles:
            chi2_val = chi2.ppf(p / 100, df=n_components)
            sds_from_mean = (chi2_val - chi2_mean) / chi2_std
            percentiles_as_sds.append(sds_from_mean)

        # Set the secondary axis to match the primary axis range
        ax1_top.set_xlim(ax1.get_xlim())

        # Create custom tick locations that correspond to nice SD values
        sd_ticks = [0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
        # Convert SD values to percentile positions for placement
        sd_tick_positions = []
        for sd in sd_ticks:
            chi2_val = chi2_mean + sd * chi2_std
            pct = chi2.cdf(chi2_val, df=n_components) * 100
            if 90 <= pct <= 99.9:  # Only show if in range
                sd_tick_positions.append(pct)
            else:
                sd_tick_positions.append(np.nan)

        # Set the ticks and labels
        valid_ticks = [
            (pos, sd)
            for pos, sd in zip(sd_tick_positions, sd_ticks)
            if not np.isnan(pos)
        ]
        if valid_ticks:
            tick_positions, tick_labels = zip(*valid_ticks)
            ax1_top.set_xticks(tick_positions)
            ax1_top.set_xticklabels([f"{sd:.1f}" for sd in tick_labels])

        ax1_top.set_xlabel("Standard deviations from χ² mean", color="g")
        ax1_top.tick_params(axis="x", labelcolor="g")

        # Add standard chi-squared percentiles
        percentiles_to_mark = [95, 97.5, 99]
        for p in percentiles_to_mark:
            threshold_p = chi2.ppf(p / 100, df=n_components)
            ax1.axvline(x=p, linestyle=":", alpha=0.5, color="gray")
            # Add text label
            y_pos = ax1.get_ylim()[1] * 0.9
            ax1.text(
                p,
                y_pos,
                rf"$\chi^2_{{{p / 100:.2f}}}({n_components})$"
                + f"\n= {threshold_p:.2f}",
                ha="center",
                va="top",
                fontsize=9,
                rotation=0,
            )

        # Mark current threshold
        if threshold_type == "chi_squared":
            current_percentile = mahal_results.get("chi2_percentile", 95.0)
            ax1.axvline(
                x=current_percentile,
                color="red",
                linestyle="-",
                linewidth=3,
                label=f"Current: {current_percentile}th percentile",
            )
            n_outliers = np.sum(squared_distances > current_threshold)
            ax1.axhline(y=n_outliers, color="red", linestyle=":", alpha=0.5)

            # No need to draw on top axis since it's aligned with bottom axis
            # The vertical line on bottom axis already shows the position
        else:
            # For distance threshold
            # current_threshold is in distance units, need to convert to chi2 scale
            current_chi2 = current_threshold**2
            current_k = (current_chi2 - chi2_mean) / chi2_std
            # Convert to percentile for display
            current_percentile = chi2.cdf(current_chi2, df=n_components) * 100
            if 90 <= current_percentile <= 99.9:
                ax1.axvline(
                    x=current_percentile,
                    color="red",
                    linestyle="-",
                    linewidth=3,
                    label=f"Current: {current_percentile:.1f}th percentile ({current_k:.2f} SDs)",
                )
            n_outliers = np.sum(distances > current_threshold)
            ax1.axhline(y=n_outliers, color="red", linestyle=":", alpha=0.5)

        # Add percentage scale on right
        total_samples = len(distances)
        ax1_twin.set_ylim(ax1.get_ylim())
        ax1_twin.set_ylabel("Outlier Percentage (%)")
        ax1_twin.set_yticks(ax1.get_yticks())
        ax1_twin.set_yticklabels(
            [f"{(y / total_samples * 100):.1f}" for y in ax1.get_yticks()]
        )

        # Add legend
        ax1.legend(loc="upper right")

        ax1.set_title(
            "Mahalanobis Distance Threshold Analysis\nEffect of Different Thresholds on Outlier Detection",
            fontsize=14,
        )
        ax1.grid(True, alpha=0.3)
        ax1.set_xlabel("Chi-squared Percentile")

        # Add info box showing threshold interpretation
        if threshold_type == "chi_squared":
            current_k = (current_threshold - chi2_mean) / chi2_std
            info_text = f"χ² Distribution Info:\n"
            info_text += f"df = {n_components}\n"
            info_text += f"Mean = {chi2_mean:.1f}\n"
            info_text += f"SD = {chi2_std:.2f}\n\n"
            info_text += f"Current threshold:\n"
            info_text += f"χ²({n_components}) = {current_threshold:.2f}\n"
            info_text += f"= {current_k:.2f} SDs from mean"

            props = dict(boxstyle="round", facecolor="wheat", alpha=0.5)
            ax1.text(
                0.02,
                0.95,
                info_text,
                transform=ax1.transAxes,
                fontsize=9,
                verticalalignment="top",
                horizontalalignment="left",
                bbox=props,
            )

        plt.tight_layout()
        figures["mahalanobis_threshold_analysis"] = fig3

    return figures


def create_pca_outlier_plot(
    df: pd.DataFrame, pca_results: Dict, figsize: Tuple[int, int] = (18, 12)
) -> plt.Figure:
    """Create PCA-based outlier detection visualization.

    Args:
        df: Original dataframe for sample labels
        pca_results: Results dictionary from PCA outlier detection
        figsize: Figure size

    Returns:
        Matplotlib figure object
    """
    fig = plt.figure(figsize=figsize)
    # Increase hspace to prevent y-axis label overlap between rows
    # Increase wspace to give more room between PCA space and feature contribution plots
    gs = fig.add_gridspec(2, 3, hspace=0.5, wspace=0.5)

    # Plot 1: Explained variance
    ax1 = fig.add_subplot(gs[0, 0])
    if "explained_variance_ratio" in pca_results:
        var_ratios = pca_results["explained_variance_ratio"]
        cum_var = pca_results["cumulative_variance"]
        n_components = pca_results.get("n_components", len(var_ratios))
        variance_threshold = pca_results.get("explained_variance_threshold", 0.95)

        x = range(1, len(var_ratios) + 1)
        ax1.bar(x, var_ratios, alpha=0.7, label="Individual")
        ax1.plot(x, cum_var, "ro-", label="Cumulative")

        # Add threshold line
        ax1.axhline(
            y=variance_threshold,
            color="red",
            linestyle="--",
            alpha=0.7,
            label=f"Threshold ({variance_threshold:.0%})",
        )

        # Mark selected components
        ax1.axvline(
            x=n_components,
            color="green",
            linestyle="--",
            alpha=0.7,
            label=f"Selected ({n_components} PCs)",
        )

        # Add text showing total variance explained
        total_var = (
            cum_var[n_components - 1] if n_components <= len(cum_var) else cum_var[-1]
        )
        ax1.text(
            0.95,
            0.05,
            f"Using {n_components} PCs\n{total_var:.1%} variance",
            transform=ax1.transAxes,
            ha="right",
            va="bottom",
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.8),
        )

        ax1.set_xlabel("Principal Component")
        ax1.set_ylabel("Explained Variance Ratio")
        ax1.set_title("PC Selection for Reconstruction")
        ax1.legend(loc="center right")
        ax1.grid(True, alpha=0.3)

    # Plot 2: Reconstruction errors with correct threshold
    ax2 = fig.add_subplot(gs[0, 1:])
    if "reconstruction_errors" in pca_results:
        errors = np.array(pca_results["reconstruction_errors"])
        outlier_indices = set(pca_results.get("outlier_indices", []))

        # Get the actual threshold value used
        threshold_value = pca_results.get("threshold_value", 0)

        # Get the DataFrame indices that correspond to the errors
        if "data_indices" in pca_results:
            data_indices = pca_results["data_indices"]
        else:
            # Fallback - assume sequential if not provided
            data_indices = list(range(len(errors)))

        # Create colors based on whether DataFrame indices are outliers
        colors = ["red" if idx in outlier_indices else "blue" for idx in data_indices]

        # Sort by error for better visualization
        sorted_indices = np.argsort(errors)
        sorted_errors = errors[sorted_indices]
        sorted_colors = [colors[i] for i in sorted_indices]

        ax2.scatter(
            range(len(sorted_errors)), sorted_errors, c=sorted_colors, alpha=0.6
        )
        ax2.set_xlabel("Sample Index (sorted by error)")
        ax2.set_ylabel("Reconstruction Error")
        ax2.set_title(f"PCA Reconstruction Errors\n({len(outlier_indices)} outliers)")
        ax2.grid(True, alpha=0.3)

        # Add threshold line
        ax2.axhline(
            y=threshold_value,
            color="red",
            linestyle="--",
            alpha=0.7,
            label=f"Threshold ({threshold_value:.2f})",
        )

        # Add legend to explain colors
        from matplotlib.patches import Patch

        legend_elements = [
            Patch(facecolor="blue", alpha=0.6, label="Normal"),
            Patch(facecolor="red", alpha=0.6, label="Outlier"),
        ]
        ax2.legend(handles=legend_elements, loc="upper left")

    # Plot 3: PCA space visualization (PC1 vs PC2)
    # Add more space between rows to prevent overlap
    ax3 = fig.add_subplot(gs[1, 0:2])
    if "pca_components" in pca_results:
        pc_data = np.array(pca_results["pca_components"])
        outlier_indices = set(pca_results.get("outlier_indices", []))

        if pc_data.shape[1] >= 2:
            # Get data indices
            if "data_indices" in pca_results:
                data_indices = pca_results["data_indices"]
            else:
                data_indices = list(range(len(pc_data)))

            colors = [
                "red" if idx in outlier_indices else "blue" for idx in data_indices
            ]
            ax3.scatter(pc_data[:, 0], pc_data[:, 1], c=colors, alpha=0.6)
            ax3.set_xlabel(
                f"PC1 ({pca_results['explained_variance_ratio'][0]:.1%} var)"
            )
            ax3.set_ylabel(
                f"PC2 ({pca_results['explained_variance_ratio'][1]:.1%} var)"
            )
            ax3.set_title("PCA Space (PC1 vs PC2)")
            ax3.grid(True, alpha=0.3)

            # Add legend
            from matplotlib.patches import Patch

            legend_elements = [
                Patch(facecolor="blue", alpha=0.6, label="Normal"),
                Patch(facecolor="red", alpha=0.6, label="Outlier"),
            ]
            ax3.legend(handles=legend_elements)

    # Plot 4: Explained Variance Ratio per Feature
    ax4 = fig.add_subplot(gs[1, 2])
    if (
        "explained_variance_ratio_per_feature" in pca_results
        and "feature_names" in pca_results
    ):
        # Use the pre-calculated explained variance ratio per feature
        explained_var_ratio = np.array(
            pca_results["explained_variance_ratio_per_feature"]
        )
        feature_names = pca_results["feature_names"]
        n_components = pca_results.get(
            "n_components", len(pca_results.get("eigenvalues", []))
        )

        # Get top 10 features by explained variance
        top_indices = np.argsort(explained_var_ratio)[-10:]

        # Create bar plot
        bars = ax4.barh(range(len(top_indices)), explained_var_ratio[top_indices])
        ax4.set_yticks(range(len(top_indices)))
        ax4.set_yticklabels([feature_names[i] for i in top_indices], fontsize=9)

        # Color bars based on value
        # If all values are ~1.0 (using all components), use gray to indicate it's uninformative
        if np.all(explained_var_ratio > 0.95):
            for bar in bars:
                bar.set_color("gray")
            title_note = " (All components used)"
        else:
            # Use a gradient from blue (low) to red (high)
            norm = plt.Normalize(vmin=0, vmax=1)
            sm = plt.cm.ScalarMappable(cmap="RdYlBu_r", norm=norm)
            for bar, val in zip(bars, explained_var_ratio[top_indices]):
                bar.set_color(sm.to_rgba(val))
            title_note = ""

        # Set labels
        ax4.set_xlabel(f"Explained Variance Ratio (using {n_components} PCs)")
        ax4.set_title(f"Feature Variance Explained{title_note}", fontsize=12)
        ax4.set_xlim([0, 1.05])  # Set x-axis from 0 to 1
        ax4.grid(True, alpha=0.3, axis="x")

        # Add vertical line at 1.0 to show maximum possible
        ax4.axvline(x=1.0, color="black", linestyle="--", alpha=0.3, linewidth=0.5)
    elif "loadings" in pca_results and "feature_names" in pca_results:
        # Fallback: calculate from loadings and eigenvalues if not provided
        loadings = np.array(pca_results["loadings"])
        feature_names = pca_results["feature_names"]
        eigenvalues = pca_results.get("eigenvalues", None)

        if eigenvalues is not None:
            eigenvalues = np.array(eigenvalues)  # Ensure numpy array
            n_components = len(eigenvalues)
            # Calculate explained variance ratio per feature
            explained_var_ratio = np.sum(
                (loadings[:, :n_components] ** 2) * eigenvalues[np.newaxis, :], axis=1
            )
        else:
            # Without eigenvalues, can't calculate properly
            explained_var_ratio = np.sum(loadings**2, axis=1)

        top_indices = np.argsort(explained_var_ratio)[-10:]

        ax4.barh(range(len(top_indices)), explained_var_ratio[top_indices])
        ax4.set_yticks(range(len(top_indices)))
        ax4.set_yticklabels([feature_names[i] for i in top_indices], fontsize=9)
        ax4.set_xlabel("Explained Variance Ratio")
        ax4.set_title("Feature Variance Explained", fontsize=12)
        ax4.grid(True, alpha=0.3, axis="x")

    plt.suptitle("PCA Outlier Detection Analysis", fontsize=14)
    return fig


def create_comprehensive_outlier_comparison(outlier_results: Dict) -> plt.Figure:
    """Create comprehensive comparison of all outlier detection methods.

    Args:
        outlier_results: Dictionary containing results from all methods

    Returns:
        Matplotlib figure object
    """
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(15, 12))
    fig.suptitle("Comprehensive Outlier Detection Comparison", fontsize=16)

    # Handle empty results
    if not outlier_results:
        for ax in [ax1, ax2, ax3, ax4]:
            ax.text(
                0.5,
                0.5,
                "No outlier detection results available",
                ha="center",
                va="center",
                transform=ax.transAxes,
                fontsize=12,
            )
            ax.set_title("No data")
            ax.axis("off")
        return fig

    # Extract method names and counts
    method_names = []
    outlier_counts = []

    for method, results in outlier_results.items():
        if isinstance(results, dict) and "outlier_indices" in results:
            method_names.append(method.replace("_", " ").title())
            outlier_counts.append(len(results["outlier_indices"]))

    # 1. Bar plot of outlier counts by method
    ax1.bar(method_names, outlier_counts, alpha=0.7)
    ax1.set_xlabel("Detection Method")
    ax1.set_ylabel("Number of Outliers")
    ax1.set_title("Outliers Detected by Each Method")
    ax1.tick_params(axis="x", rotation=90)

    # 2. Venn diagram (simplified overlap visualization)
    # Dynamically detect available methods
    methods = []
    method_outliers = {}

    if "combined" in outlier_results:
        # Get methods from combined results
        combined = outlier_results["combined"]
        for key in combined.keys():
            if key.endswith("_outliers") and isinstance(combined[key], list):
                method_name = key.replace("_outliers", "")
                # Skip special keys like consensus_outliers
                if method_name != "consensus":
                    methods.append(method_name)
                    method_outliers[method_name] = set(combined[key])
    else:
        # Get methods from individual results
        for method, results in outlier_results.items():
            if (
                isinstance(results, dict)
                and "outlier_indices" in results
                and method != "combined"
            ):
                methods.append(method)
                method_outliers[method] = set(results["outlier_indices"])

    # Sort methods for consistent ordering
    methods = sorted(methods)

    if methods:
        # Create overlap matrix
        overlap_data = []
        for i, method1 in enumerate(methods):
            row = []
            for j, method2 in enumerate(methods):
                if method1 in method_outliers and method2 in method_outliers:
                    set1 = method_outliers[method1]
                    set2 = method_outliers[method2]
                    overlap = len(set1.intersection(set2))
                    row.append(overlap)
                else:
                    row.append(0)
            overlap_data.append(row)

        # Only create heatmap if we have data
        if overlap_data and methods:
            # Format method names for display
            display_names = []
            for m in methods:
                # Handle common method name variations
                if m == "isolation_forest":
                    display_names.append("Isolation Forest")
                elif m == "pca":
                    display_names.append("PCA")
                elif m == "mahalanobis":
                    display_names.append("Mahalanobis")
                elif m == "iqr_per_trait":
                    display_names.append("IQR per Trait")
                elif m == "gmm":
                    display_names.append("GMM")
                elif m == "kmeans":
                    display_names.append("K-Means")
                elif m == "mincovdet":
                    display_names.append("MinCovDet")
                else:
                    # Default: replace underscores and title case
                    display_names.append(m.replace("_", " ").title())

            sns.heatmap(
                overlap_data,
                annot=True,
                fmt="d",
                cmap="YlOrRd",
                xticklabels=display_names,
                yticklabels=display_names,
                ax=ax2,
            )
            ax2.set_title("Outlier Overlap Between Methods")
        else:
            ax2.text(
                0.5,
                0.5,
                "No overlap data available",
                ha="center",
                va="center",
                transform=ax2.transAxes,
            )
            ax2.set_title("Outlier Overlap Between Methods")
    else:
        # No methods found at all
        ax2.text(
            0.5,
            0.5,
            "No outlier methods found",
            ha="center",
            va="center",
            transform=ax2.transAxes,
        )
        ax2.set_title("Outlier Overlap Between Methods")
        ax2.set_xticks([])
        ax2.set_yticks([])

    # 3. Consensus analysis
    if "combined" in outlier_results:
        combined = outlier_results["combined"]
        consensus_outliers = combined.get("consensus_outliers", [])
        n_methods = combined.get("n_methods", 2)

        # Count how many methods agree for each outlier
        all_outliers = set()
        for key, value in combined.items():
            if key.endswith("_outliers") and isinstance(value, list):
                all_outliers.update(value)

        agreement_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 7: 0}

        for outlier in all_outliers:
            count = sum(
                1
                for key, value in combined.items()
                if key.endswith("_outliers")
                and isinstance(value, list)
                and outlier in value
            )
            if count in agreement_counts:
                agreement_counts[count] += 1

        ax3.bar(agreement_counts.keys(), agreement_counts.values(), alpha=0.7)
        ax3.set_xlabel("Number of Methods in Agreement")
        ax3.set_ylabel("Number of Outliers")
        ax3.set_title("Outlier Detection Consensus Analysis")
        ax3.set_xticks(list(agreement_counts.keys()))

    # 4. Summary statistics
    summary_text = []
    summary_text.append(f"Total methods compared: {len(method_names)}")

    if "combined" in outlier_results:
        summary_text.append(
            f"Consensus outliers: {len(outlier_results['combined'].get('consensus_outliers', []))}"
        )
        summary_text.append(
            f"Consensus threshold: {outlier_results['combined'].get('consensus_threshold', 0.5):.1%}"
        )

    # Add summary of each method
    for method, count in zip(method_names, outlier_counts):
        summary_text.append(f"{method}: {count} outliers")

    ax4.text(
        0.1,
        0.9,
        "\n".join(summary_text),
        transform=ax4.transAxes,
        verticalalignment="top",
        fontsize=12,
        family="monospace",
    )
    ax4.set_title("Summary Statistics")
    ax4.axis("off")

    plt.tight_layout()
    return fig


def create_kmeans_outlier_plots(
    df: pd.DataFrame,
    kmeans_results: Dict,
    pca_result: Optional[Dict] = None,
) -> Dict[str, plt.Figure]:
    """Create K-Means clustering outlier detection plots.

    Generates comprehensive visualizations for K-Means-based outlier detection:
    1. PCA scatter with cluster coloring + highlighted outliers
    2. Distance distribution with threshold
    3. Cluster sizes bar chart
    4. Silhouette plot

    Args:
        df: Original DataFrame with trait data
        kmeans_results: Results from detect_outliers_kmeans()
        pca_result: Optional PCA results from perform_pca_analysis().
            If provided, reuses these PCA components for efficiency.

    Returns:
        Dictionary mapping plot names to matplotlib Figures

    Examples:
        >>> # Option 1: With existing PCA (efficient for notebooks)
        >>> pca_result = perform_pca_analysis(numeric_traits)
        >>> kmeans_results = detect_outliers_kmeans(numeric_traits)
        >>> figs = create_kmeans_outlier_plots(df, kmeans_results, pca_result)
        >>>
        >>> # Option 2: Without PCA (computes for visualization)
        >>> figs = create_kmeans_outlier_plots(df, kmeans_results)
    """
    from sleap_roots_analyze.cluster_visualization import (
        create_cluster_scatter_pca,
        create_distance_distribution_plot,
        create_cluster_size_barplot,
        create_silhouette_plot,
    )

    figures = {}

    if "error" in kmeans_results:
        return figures

    # 1. PCA scatter with clusters and outliers highlighted
    try:
        fig_scatter = create_cluster_scatter_pca(
            kmeans_results,
            pca_result=pca_result,
            highlight_indices=kmeans_results.get("outlier_indices", []),
            title=f"K-Means Clustering (k={kmeans_results['n_clusters']})\n"
            f"{kmeans_results['n_outliers']} outliers detected",
        )
        figures["kmeans_pca_scatter"] = fig_scatter
    except Exception as e:
        print(f"Warning: Could not create PCA scatter plot: {e}")

    # 2. Distance distribution
    if "min_distances_to_centers" in kmeans_results:
        try:
            distances = np.array(kmeans_results["min_distances_to_centers"])
            threshold = kmeans_results["threshold_value"]

            fig_dist = create_distance_distribution_plot(
                distances, threshold, "K-Means"
            )
            figures["kmeans_distance_distribution"] = fig_dist
        except Exception as e:
            print(f"Warning: Could not create distance distribution plot: {e}")

    # 3. Cluster sizes
    if "cluster_labels" in kmeans_results:
        try:
            fig_sizes = create_cluster_size_barplot(
                kmeans_results["cluster_labels"], kmeans_results["n_clusters"]
            )
            figures["kmeans_cluster_sizes"] = fig_sizes
        except Exception as e:
            print(f"Warning: Could not create cluster size plot: {e}")

    # 4. Silhouette plot
    if "silhouette_score" in kmeans_results:
        try:
            fig_silhouette = create_silhouette_plot(kmeans_results)
            figures["kmeans_silhouette"] = fig_silhouette
        except Exception as e:
            print(f"Warning: Could not create silhouette plot: {e}")

    return figures


def create_gmm_outlier_plots(
    df: pd.DataFrame,
    gmm_results: Dict,
    pca_result: Optional[Dict] = None,
) -> Dict[str, plt.Figure]:
    """Create GMM (Gaussian Mixture Model) outlier detection plots.

    Generates comprehensive visualizations for GMM-based outlier detection:
    1. PCA scatter with cluster coloring + highlighted outliers
    2. Log-likelihood distribution with threshold
    3. BIC/AIC scores for component selection (if auto-selection was used)
    4. Cluster sizes bar chart
    5. Silhouette plot
    6. Probability heatmap showing soft cluster assignments

    Args:
        df: Original DataFrame with trait data
        gmm_results: Results from detect_outliers_gmm()
        pca_result: Optional PCA results from perform_pca_analysis().
            If provided, reuses these PCA components for efficiency.

    Returns:
        Dictionary mapping plot names to matplotlib Figures

    Examples:
        >>> # Option 1: With existing PCA (efficient for notebooks)
        >>> pca_result = perform_pca_analysis(numeric_traits)
        >>> gmm_results = detect_outliers_gmm(numeric_traits, n_components=None)
        >>> figs = create_gmm_outlier_plots(df, gmm_results, pca_result)
        >>>
        >>> # Option 2: Without PCA (computes for visualization)
        >>> figs = create_gmm_outlier_plots(df, gmm_results)
    """
    from sleap_roots_analyze.cluster_visualization import (
        create_cluster_scatter_pca,
        create_distance_distribution_plot,
        create_cluster_size_barplot,
        create_bic_aic_comparison_plot,
        create_silhouette_plot,
    )

    figures = {}

    if "error" in gmm_results:
        return figures

    # 1. PCA scatter with clusters and outliers highlighted
    try:
        fig_scatter = create_cluster_scatter_pca(
            gmm_results,
            pca_result=pca_result,
            highlight_indices=gmm_results.get("outlier_indices", []),
            title=f"GMM Clustering (k={gmm_results['n_components']})\n"
            f"{gmm_results['n_outliers']} outliers detected",
        )
        figures["gmm_pca_scatter"] = fig_scatter
    except Exception as e:
        print(f"Warning: Could not create PCA scatter plot: {e}")

    # 2. Log-likelihood distribution
    if "log_likelihoods" in gmm_results:
        try:
            log_likelihoods = np.array(gmm_results["log_likelihoods"])
            threshold = gmm_results["threshold_value"]

            # Note: For GMM, lower log-likelihood = more anomalous
            # So we flip the comparison for visualization
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 6))

            # Histogram with threshold line
            ax1.hist(
                log_likelihoods, bins=30, alpha=0.7, edgecolor="black", color="skyblue"
            )
            ax1.axvline(
                threshold,
                color="red",
                linestyle="--",
                linewidth=2,
                label=f"Threshold: {threshold:.3f}",
            )
            ax1.set_xlabel("Log-Likelihood")
            ax1.set_ylabel("Frequency")
            ax1.set_title("GMM Log-Likelihood Distribution")
            ax1.legend()
            ax1.grid(True, alpha=0.3)

            # Sorted scatter plot
            sorted_idx = np.argsort(log_likelihoods)
            sorted_ll = log_likelihoods[sorted_idx]
            colors = ["red" if ll < threshold else "blue" for ll in sorted_ll]

            ax2.scatter(range(len(sorted_ll)), sorted_ll, c=colors, alpha=0.6, s=30)
            ax2.axhline(
                threshold,
                color="red",
                linestyle="--",
                linewidth=2,
                label=f"Threshold: {threshold:.3f}",
            )
            ax2.set_xlabel("Sample Index (sorted by log-likelihood)")
            ax2.set_ylabel("Log-Likelihood")
            ax2.set_title("GMM Log-Likelihoods (Sorted)")
            ax2.grid(True, alpha=0.3)

            # Add legend
            legend_elements = [
                Patch(facecolor="blue", alpha=0.6, label="Normal"),
                Patch(facecolor="red", alpha=0.6, label="Below Threshold (Outlier)"),
            ]
            ax2.legend(handles=legend_elements)

            plt.tight_layout()
            figures["gmm_loglikelihood_distribution"] = fig
        except Exception as e:
            print(f"Warning: Could not create log-likelihood distribution plot: {e}")

    # 3. BIC/AIC comparison (if auto-selection was used)
    if len(gmm_results.get("bic_scores", [])) > 1:
        try:
            fig_bic_aic = create_bic_aic_comparison_plot(
                gmm_results["bic_scores"], gmm_results["aic_scores"]
            )
            figures["gmm_bic_aic_comparison"] = fig_bic_aic
        except Exception as e:
            print(f"Warning: Could not create BIC/AIC comparison plot: {e}")

    # 4. Cluster sizes
    if "cluster_labels" in gmm_results:
        try:
            fig_sizes = create_cluster_size_barplot(
                gmm_results["cluster_labels"], gmm_results["n_components"]
            )
            figures["gmm_cluster_sizes"] = fig_sizes
        except Exception as e:
            print(f"Warning: Could not create cluster size plot: {e}")

    # 5. Silhouette plot
    if "silhouette_score" in gmm_results:
        try:
            fig_silhouette = create_silhouette_plot(gmm_results)
            figures["gmm_silhouette"] = fig_silhouette
        except Exception as e:
            print(f"Warning: Could not create silhouette plot: {e}")

    # 6. Probability heatmap (soft assignments)
    if "probabilities" in gmm_results:
        try:
            probabilities = np.array(gmm_results["probabilities"])
            n_components = gmm_results["n_components"]

            fig, ax = plt.subplots(figsize=(12, 8))

            # Sort samples by their primary cluster for better visualization
            cluster_labels = gmm_results["cluster_labels"]
            sort_idx = np.lexsort((probabilities.max(axis=1), cluster_labels))

            # Create heatmap
            im = ax.imshow(
                probabilities[sort_idx].T,
                aspect="auto",
                cmap="YlOrRd",
                interpolation="nearest",
            )

            ax.set_xlabel("Sample Index (sorted by cluster)")
            ax.set_ylabel("Component")
            ax.set_title("GMM Soft Cluster Assignments\n(Probability Heatmap)")
            ax.set_yticks(range(n_components))
            ax.set_yticklabels([f"Component {i + 1}" for i in range(n_components)])

            # Add colorbar
            cbar = plt.colorbar(im, ax=ax)
            cbar.set_label("Probability")

            plt.tight_layout()
            figures["gmm_probability_heatmap"] = fig
        except Exception as e:
            print(f"Warning: Could not create probability heatmap: {e}")

    return figures


def create_hierarchical_outlier_plots(
    df: pd.DataFrame,
    hierarchical_results: Dict,
    pca_result: Optional[Dict] = None,
) -> Dict[str, plt.Figure]:
    """Create hierarchical clustering outlier detection plots.

    Generates comprehensive visualizations for hierarchical clustering-based outlier detection:
    1. Dendrogram with cut line
    2. PCA scatter with cluster coloring + highlighted outliers
    3. Distance distribution with threshold
    4. Cluster sizes bar chart
    5. Silhouette plot

    Args:
        df: Original DataFrame with trait data
        hierarchical_results: Results from detect_outliers_hierarchical()
        pca_result: Optional PCA results from perform_pca_analysis().
            If provided, reuses these PCA components for efficiency.

    Returns:
        Dictionary mapping plot names to matplotlib Figures

    Examples:
        >>> # Option 1: With existing PCA (efficient for notebooks)
        >>> pca_result = perform_pca_analysis(numeric_traits)
        >>> hier_results = detect_outliers_hierarchical(numeric_traits)
        >>> figs = create_hierarchical_outlier_plots(df, hier_results, pca_result)
        >>>
        >>> # Option 2: Without PCA (computes for visualization)
        >>> figs = create_hierarchical_outlier_plots(df, hier_results)
    """
    from sleap_roots_analyze.cluster_visualization import (
        create_dendrogram,
        create_cluster_scatter_pca,
        create_distance_distribution_plot,
        create_cluster_size_barplot,
        create_silhouette_plot,
    )

    figures = {}

    if "error" in hierarchical_results:
        return figures

    # 1. Dendrogram with cut line
    if "linkage_matrix" in hierarchical_results:
        try:
            n_clusters = hierarchical_results.get("n_clusters")
            cut_height = hierarchical_results.get("cut_height")

            fig_dendro = create_dendrogram(
                hierarchical_results,
                n_clusters=n_clusters,
                cut_height=cut_height,
                title=f"Hierarchical Clustering Dendrogram\n"
                f"Cophenetic correlation: {hierarchical_results.get('cophenetic_correlation', 0):.3f}",
            )
            figures["hierarchical_dendrogram"] = fig_dendro
        except Exception as e:
            print(f"Warning: Could not create dendrogram: {e}")

    # 2. PCA scatter with clusters and outliers highlighted
    try:
        fig_scatter = create_cluster_scatter_pca(
            hierarchical_results,
            pca_result=pca_result,
            highlight_indices=hierarchical_results.get("outlier_indices", []),
            title=f"Hierarchical Clustering (k={hierarchical_results.get('n_clusters', 'auto')})\n"
            f"{hierarchical_results.get('n_outliers', 0)} outliers detected",
        )
        figures["hierarchical_pca_scatter"] = fig_scatter
    except Exception as e:
        print(f"Warning: Could not create PCA scatter plot: {e}")

    # 3. Distance distribution
    if "distances_to_centers" in hierarchical_results:
        try:
            distances = np.array(hierarchical_results["distances_to_centers"])
            threshold = hierarchical_results["threshold_value"]

            fig_dist = create_distance_distribution_plot(
                distances, threshold, "Hierarchical"
            )
            figures["hierarchical_distance_distribution"] = fig_dist
        except Exception as e:
            print(f"Warning: Could not create distance distribution plot: {e}")

    # 4. Cluster sizes
    if "cluster_labels" in hierarchical_results:
        try:
            n_clusters = hierarchical_results.get(
                "n_clusters", len(np.unique(hierarchical_results["cluster_labels"]))
            )
            fig_sizes = create_cluster_size_barplot(
                hierarchical_results["cluster_labels"], n_clusters
            )
            figures["hierarchical_cluster_sizes"] = fig_sizes
        except Exception as e:
            print(f"Warning: Could not create cluster size plot: {e}")

    # 5. Silhouette plot
    if "silhouette_score" in hierarchical_results:
        try:
            fig_silhouette = create_silhouette_plot(hierarchical_results)
            figures["hierarchical_silhouette"] = fig_silhouette
        except Exception as e:
            print(f"Warning: Could not create silhouette plot: {e}")

    # 6. Optimal k analysis (if auto-optimization was performed)
    if "optimal_k_analysis" in hierarchical_results:
        try:
            analysis = hierarchical_results["optimal_k_analysis"]
            scores = analysis["scores"]
            k_values = analysis["k_values"]
            method = analysis["method"]
            optimal_k = analysis["optimal_n_clusters"]

            fig, ax = plt.subplots(figsize=(10, 6))

            ax.plot(k_values, scores, marker="o", linewidth=2, markersize=8)
            ax.axvline(
                optimal_k,
                color="red",
                linestyle="--",
                linewidth=2,
                label=f"Optimal k={optimal_k}",
            )
            ax.scatter(
                [optimal_k],
                [scores[k_values.index(optimal_k)]],
                s=200,
                c="red",
                marker="*",
                zorder=5,
                edgecolors="black",
                linewidths=2,
            )

            ax.set_xlabel("Number of Clusters (k)")
            ax.set_ylabel(f"{method.replace('_', ' ').title()} Score")
            ax.set_title(
                f"Optimal Cluster Selection\nMethod: {method.replace('_', ' ').title()}"
            )
            ax.legend()
            ax.grid(True, alpha=0.3)
            ax.set_xticks(k_values)

            plt.tight_layout()
            figures["hierarchical_optimal_k"] = fig
        except Exception as e:
            print(f"Warning: Could not create optimal k plot: {e}")

    return figures
