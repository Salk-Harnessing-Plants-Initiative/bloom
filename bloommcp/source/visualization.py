"""Visualization utilities for trait analysis.

This module provides basic static visualization functions including:
- Trait distribution plots (histograms, boxplots)
- Correlation analysis
- PCA and UMAP visualization functions
- Extreme phenotype identification
- Publication-ready figure generation
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import seaborn as sns

from datetime import datetime
import logging

# Import PCA functions
from .pca import select_top_features_from_pca

try:
    import plotly.graph_objects as go
except ImportError:
    go = None

logger = logging.getLogger(__name__)

from .data_cleanup import (
    apply_data_cleanup_filters,
)


def create_trait_histograms(
    df: pd.DataFrame,
    trait_cols: List[str],
    n_cols: int = 3,
    figsize: Tuple[int, int] = (15, 10),
) -> plt.Figure:
    """Create histogram plots for all traits.

    Args:
        df: DataFrame with trait data
        trait_cols: List of trait column names
        n_cols: Number of columns in subplot grid
        figsize: Figure size

    Returns:
        Matplotlib figure object
    """
    n_traits = len(trait_cols)
    if n_traits == 0:
        # Handle empty case
        fig, ax = plt.subplots(1, 1, figsize=figsize)
        ax.text(
            0.5,
            0.5,
            "No traits to plot",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
        return fig

    n_rows = (n_traits + n_cols - 1) // n_cols
    fig, axes = plt.subplots(n_rows, n_cols, figsize=figsize)

    # Handle single row case
    if n_rows == 1:
        axes = [axes] if n_cols == 1 else axes
    else:
        axes = axes.flatten()

    for i, trait in enumerate(trait_cols):
        if trait in df.columns:
            data = df[trait].dropna()

            if len(data) > 0:
                axes[i].hist(data, bins=30, alpha=0.7, edgecolor="black")
                axes[i].set_title(f"{trait}\n(n={len(data)})", fontsize=10)
                axes[i].set_xlabel("Value")
                axes[i].set_ylabel("Frequency")
            else:
                axes[i].text(
                    0.5,
                    0.5,
                    "No data",
                    ha="center",
                    va="center",
                    transform=axes[i].transAxes,
                )
                axes[i].set_title(trait)

    # Hide empty subplots
    for i in range(n_traits, len(axes)):
        axes[i].set_visible(False)

    plt.tight_layout()
    return fig


def create_trait_boxplots_by_genotype(
    df: pd.DataFrame,
    trait_cols: List[str],
    genotype_col: str = "geno",
    n_cols: int = 3,
    figsize: Tuple[int, int] = (15, 10),
    adaptive_config: Optional[Any] = None,
) -> plt.Figure:
    """Create boxplots for traits grouped by genotype.

    Args:
        df: DataFrame with trait and genotype data
        trait_cols: List of trait column names
        genotype_col: Name of genotype column
        n_cols: Number of columns in subplot grid
        figsize: Figure size (only used if adaptive_config is None)
        adaptive_config: Optional adaptive sizing configuration

    Returns:
        Matplotlib figure object
    """
    n_traits = len(trait_cols)
    if n_traits == 0:
        # Handle empty case
        fig, ax = plt.subplots(1, 1, figsize=figsize)
        ax.text(
            0.5,
            0.5,
            "No traits to plot",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
        return fig

    # Calculate adaptive size based on genotype count
    if adaptive_config is not None and genotype_col in df.columns:
        from sleap_roots_analyze.viz_utils import calculate_barplot_size

        n_genotypes = df[genotype_col].nunique()
        n_rows = (n_traits + n_cols - 1) // n_cols

        # Each subplot needs width based on genotype count
        subplot_width, subplot_height = calculate_barplot_size(
            n_items=n_genotypes,
            config=adaptive_config,
            orientation="vertical",  # Genotypes on X-axis
            as_subplot=True,
            n_subplots=n_traits,
        )

        # Total figure size based on grid layout
        fig_width = subplot_width * n_cols
        fig_height = subplot_height * n_rows

        # Apply bounds
        fig_width = max(
            adaptive_config.min_width, min(adaptive_config.max_width, fig_width)
        )
        fig_height = max(
            adaptive_config.min_height, min(adaptive_config.max_height, fig_height)
        )

        figsize = (fig_width, fig_height)

    n_rows = (n_traits + n_cols - 1) // n_cols
    fig, axes = plt.subplots(n_rows, n_cols, figsize=figsize)

    # Handle single row case
    if n_rows == 1:
        axes = [axes] if n_cols == 1 else axes
    else:
        axes = axes.flatten()

    for i, trait in enumerate(trait_cols):
        if trait in df.columns and genotype_col in df.columns:
            # Create boxplot
            df_plot = df[[trait, genotype_col]].dropna()

            if len(df_plot) > 0:
                df_plot.boxplot(column=trait, by=genotype_col, ax=axes[i])
                axes[i].set_title(f"{trait}")
                axes[i].set_xlabel("Genotype")
                axes[i].set_ylabel(trait)
                plt.setp(axes[i].xaxis.get_majorticklabels(), rotation=90)
            else:
                axes[i].text(
                    0.5,
                    0.5,
                    "No data",
                    ha="center",
                    va="center",
                    transform=axes[i].transAxes,
                )
                axes[i].set_title(trait)

    # Hide empty subplots
    for i in range(n_traits, len(axes)):
        axes[i].set_visible(False)

    plt.tight_layout()
    return fig


def create_trait_histograms_batched(
    df: pd.DataFrame,
    trait_cols: List[str],
    batch_size: int = 16,
    n_cols: int = 4,
    figsize: Tuple[int, int] = (16, 16),
) -> List[plt.Figure]:
    """Create batched histogram plots for traits (multiple figures for many traits).

    Args:
        df: DataFrame with trait data
        trait_cols: List of trait column names
        batch_size: Number of traits per figure (default: 16)
        n_cols: Number of columns in subplot grid
        figsize: Figure size for FULL batches (default: (16, 16))

    Returns:
        List of matplotlib figure objects (one per batch)
    """
    n_traits = len(trait_cols)
    if n_traits == 0:
        return []

    figures = []
    for batch_start in range(0, n_traits, batch_size):
        batch_end = min(batch_start + batch_size, n_traits)
        batch_traits = trait_cols[batch_start:batch_end]

        # Calculate adaptive figsize for this batch
        n_traits_in_batch = len(batch_traits)
        n_rows = (n_traits_in_batch + n_cols - 1) // n_cols

        # Scale figsize proportionally for partial batches
        if n_traits_in_batch < batch_size:
            # Calculate full batch dimensions
            full_n_rows = (batch_size + n_cols - 1) // n_cols
            # Scale height proportionally
            batch_figsize = (figsize[0], figsize[1] * (n_rows / full_n_rows))
        else:
            batch_figsize = figsize

        # Create figure for this batch
        fig = create_trait_histograms(
            df, batch_traits, n_cols=n_cols, figsize=batch_figsize
        )
        fig.suptitle(
            f"Trait Histograms (Traits {batch_start + 1}-{batch_end} of {n_traits})",
            fontsize=14,
            y=0.995,
        )
        figures.append(fig)

    return figures


def create_trait_boxplots_by_genotype_batched(
    df: pd.DataFrame,
    trait_cols: List[str],
    genotype_col: str = "geno",
    batch_size: int = 16,
    n_cols: int = 4,
    figsize: Tuple[int, int] = (16, 16),
) -> List[plt.Figure]:
    """Create batched boxplot plots by genotype (multiple figures for many traits).

    Args:
        df: DataFrame with trait data
        trait_cols: List of trait column names
        genotype_col: Column name for genotype grouping
        batch_size: Number of traits per figure (default: 16)
        n_cols: Number of columns in subplot grid
        figsize: Figure size for FULL batches (default: (16, 16))

    Returns:
        List of matplotlib figure objects (one per batch)
    """
    n_traits = len(trait_cols)
    if n_traits == 0:
        return []

    figures = []
    for batch_start in range(0, n_traits, batch_size):
        batch_end = min(batch_start + batch_size, n_traits)
        batch_traits = trait_cols[batch_start:batch_end]

        # Calculate adaptive figsize for this batch
        n_traits_in_batch = len(batch_traits)
        n_rows = (n_traits_in_batch + n_cols - 1) // n_cols

        # Scale figsize proportionally for partial batches
        if n_traits_in_batch < batch_size:
            # Calculate full batch dimensions
            full_n_rows = (batch_size + n_cols - 1) // n_cols
            # Scale height proportionally
            batch_figsize = (figsize[0], figsize[1] * (n_rows / full_n_rows))
        else:
            batch_figsize = figsize

        # Create figure for this batch
        fig = create_trait_boxplots_by_genotype(
            df,
            batch_traits,
            genotype_col=genotype_col,
            n_cols=n_cols,
            figsize=batch_figsize,
        )
        fig.suptitle(
            f"Trait Boxplots by Genotype (Traits {batch_start + 1}-{batch_end} of {n_traits})",
            fontsize=14,
            y=0.995,
        )
        figures.append(fig)

    return figures


def create_correlation_heatmap(
    df: pd.DataFrame, trait_cols: List[str], figsize: Tuple[int, int] = (12, 12)
) -> plt.Figure:
    """Create correlation heatmap for traits.

    Args:
        df: DataFrame with trait data
        trait_cols: List of trait column names
        figsize: Figure size (will be made square using the larger dimension)

    Returns:
        Matplotlib figure object
    """
    # Calculate correlation matrix
    trait_data = df[trait_cols]
    corr_matrix = trait_data.corr()

    # Create mask for upper triangle
    mask = np.triu(np.ones_like(corr_matrix, dtype=bool))

    # Ensure square figure by using the larger dimension
    square_size = max(figsize[0], figsize[1])
    square_figsize = (square_size, square_size)

    # Create heatmap
    fig, ax = plt.subplots(figsize=square_figsize)

    sns.heatmap(
        corr_matrix,
        mask=mask,
        annot=True,
        cmap="coolwarm",
        center=0,
        square=True,
        ax=ax,
        fmt=".2f",
        cbar_kws={"shrink": 0.8},
    )

    ax.set_title("Trait Correlation Matrix")
    plt.xticks(rotation=90, ha="center")
    plt.yticks(rotation=0)
    plt.tight_layout()

    return fig


def save_figure_with_unique_name(
    fig: plt.Figure, run_dir: Path, base_name: str, dpi: int = 300, format: str = "png"
) -> Path:
    """Save figure with unique timestamped name to prevent overwrites.

    Args:
        fig: Matplotlib figure object
        run_dir: Directory to save the figure
        base_name: Base name for the file
        dpi: Resolution for saved plot
        format: File format (png, pdf, svg)

    Returns:
        Path to saved figure
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    # Create unique filename with timestamp
    timestamp = datetime.now().strftime("%H%M%S")
    filename = f"{base_name}_{timestamp}.{format}"
    plot_path = run_dir / filename

    # Ensure uniqueness even with same timestamp
    counter = 1
    while plot_path.exists():
        filename = f"{base_name}_{timestamp}_{counter:02d}.{format}"
        plot_path = run_dir / filename
        counter += 1

    fig.savefig(plot_path, dpi=dpi, bbox_inches="tight")
    return plot_path


def create_exploratory_summary_plots(
    df: pd.DataFrame,
    trait_cols: List[str],
    genotype_col: str = "geno",
    adaptive_config: Optional[Any] = None,
) -> Dict[str, plt.Figure]:
    """Create comprehensive exploratory data analysis plots.

    Args:
        df: DataFrame with trait data
        trait_cols: List of trait column names
        genotype_col: Name of genotype column
        adaptive_config: Optional adaptive sizing configuration

    Returns:
        Dictionary of plot names to figure objects
    """
    figures = {}

    # 1. Trait distribution summary
    if len(trait_cols) > 0:
        n_traits_to_show = min(16, len(trait_cols))
        fig = create_trait_histograms(df, trait_cols[:n_traits_to_show], n_cols=4)
        figures["trait_distributions"] = fig

    # 2. Missing data heatmap
    if len(trait_cols) > 0:
        fig, ax = plt.subplots(figsize=(12, 8))
        missing_data = df[trait_cols].isna()
        sns.heatmap(missing_data.T, cbar=True, ax=ax, cmap="RdYlBu_r")
        ax.set_title("Missing Data Pattern")
        ax.set_xlabel("Sample Index")
        ax.set_ylabel("Traits")
        figures["missing_data_pattern"] = fig

    # 3. Trait value ranges (box plots)
    if len(trait_cols) > 0:
        n_traits_box = min(12, len(trait_cols))
        fig = create_trait_boxplots_by_genotype(
            df, trait_cols[:n_traits_box], genotype_col, adaptive_config=adaptive_config
        )
        figures["trait_ranges_by_genotype"] = fig

    # 4. Sample size per genotype
    if genotype_col in df.columns:
        genotype_counts = df[genotype_col].value_counts()
        if len(genotype_counts) > 0:
            # Calculate adaptive size based on genotype count
            if adaptive_config is not None:
                from sleap_roots_analyze.viz_utils import calculate_barplot_size

                sample_count_figsize = calculate_barplot_size(
                    n_items=len(genotype_counts),
                    config=adaptive_config,
                    orientation="vertical",
                )
            else:
                sample_count_figsize = (10, 6)

            fig, ax = plt.subplots(figsize=sample_count_figsize)
            genotype_counts.plot(kind="bar", ax=ax)
            ax.set_title("Sample Size per Genotype")
            ax.set_xlabel("Genotype")
            ax.set_ylabel("Number of Samples")
            ax.tick_params(axis="x", rotation=90)
            plt.tight_layout()
            figures["samples_per_genotype"] = fig

    # 5. Trait correlation overview (for subset of traits)
    if len(trait_cols) > 1:
        n_traits_corr = min(25, len(trait_cols))
        fig = create_correlation_heatmap(df, trait_cols[:n_traits_corr])
        figures["trait_correlations"] = fig

    return figures


def create_trait_eda_plots(
    df: pd.DataFrame,
    trait_cols: List[str],
    thresholds: Dict[str, float],
    cleanup_log: Optional[Dict] = None,
    min_samples_per_trait: int = 10,
) -> Dict[str, plt.Figure]:
    """Create comprehensive trait EDA plots using apply_data_cleanup_filters for consistency.

    Args:
        df: DataFrame with trait data
        trait_cols: List of trait column names
        thresholds: Dictionary with nan and zero thresholds (outlier ignored as it's not used for trait removal)
        cleanup_log: Optional cleanup log from apply_data_cleanup_filters with actual removed traits
        min_samples_per_trait: Minimum number of valid samples required per trait

    Returns:
        Dictionary of plot names to figure objects
    """
    figures = {}

    # Calculate EDA metrics
    eda_metrics = {
        "Trait": [],
        "Num_NaNs": [],
        "Num_Zeros": [],
        "Num_Outliers": [],
        "Variance": [],
        "Fraction_NaNs": [],
        "Fraction_Zeros": [],
        "Fraction_Outliers": [],
    }

    for col in trait_cols:
        if col in df.columns:
            # Count NaNs and zeros
            n_nans = df[col].isna().sum()
            n_zeros = (df[col] == 0).sum()

            # Count outliers using IQR
            Q1 = df[col].quantile(0.25)
            Q3 = df[col].quantile(0.75)
            IQR = Q3 - Q1
            lower_bound = Q1 - 1.5 * IQR
            upper_bound = Q3 + 1.5 * IQR
            n_outliers = ((df[col] < lower_bound) | (df[col] > upper_bound)).sum()

            # Calculate variance
            variance = df[col].var()

            # Store metrics
            eda_metrics["Trait"].append(col)
            eda_metrics["Num_NaNs"].append(n_nans)
            eda_metrics["Num_Zeros"].append(n_zeros)
            eda_metrics["Num_Outliers"].append(n_outliers)
            eda_metrics["Variance"].append(variance)
            eda_metrics["Fraction_NaNs"].append(n_nans / len(df))
            eda_metrics["Fraction_Zeros"].append(n_zeros / len(df))
            eda_metrics["Fraction_Outliers"].append(n_outliers / len(df))

    eda_df = pd.DataFrame(eda_metrics)

    # Add trait prefix for grouping
    eda_df["Prefix"] = eda_df["Trait"].apply(
        lambda x: x.split("_")[0] if "_" in x else "NoPrefix"
    )

    # 1. Trait overview plot (similar to plot_eda_summary)
    fig, axes = plt.subplots(3, 1, figsize=(18, 14), sharex=True)

    # NaN fraction
    sns.barplot(x="Trait", y="Fraction_NaNs", hue="Prefix", data=eda_df, ax=axes[0])
    axes[0].axhline(
        y=thresholds.get("nan", 0.3),
        color="red",
        linestyle="--",
        alpha=0.7,
        label=f"Threshold ({thresholds.get('nan', 0.3)})",
    )
    axes[0].set_title("Fraction of NaN Values per Trait")
    axes[0].tick_params(labelbottom=False)
    axes[0].legend()

    # Zero fraction
    sns.barplot(x="Trait", y="Fraction_Zeros", hue="Prefix", data=eda_df, ax=axes[1])
    axes[1].axhline(
        y=thresholds.get("zero", 0.5),
        color="red",
        linestyle="--",
        alpha=0.7,
        label=f"Threshold ({thresholds.get('zero', 0.5)})",
    )
    axes[1].set_title("Fraction of Zero Values per Trait")
    axes[1].tick_params(labelbottom=False)
    axes[1].legend()

    # Outlier fraction
    sns.barplot(x="Trait", y="Fraction_Outliers", hue="Prefix", data=eda_df, ax=axes[2])
    axes[2].axhline(
        y=thresholds.get("outlier", 0.1),
        color="red",
        linestyle="--",
        alpha=0.7,
        label=f"Threshold ({thresholds.get('outlier', 0.1)})",
    )
    axes[2].set_title("Fraction of IQR Outliers per Trait")
    axes[2].set_xlabel("Trait")
    axes[2].tick_params(axis="x", rotation=90)
    axes[2].legend(title="Prefix", bbox_to_anchor=(1.05, 1), loc="upper left")

    plt.tight_layout()
    figures["trait_eda_overview"] = fig

    # 2. Traits Actually Removed (if cleanup_log provided)
    # Use actual removed traits from cleanup log if available
    actual_removed_traits = []
    removal_reasons_dict = {}

    if cleanup_log and "removed_traits" in cleanup_log:
        for trait_info in cleanup_log["removed_traits"]:
            if isinstance(trait_info, dict):
                trait_name = trait_info.get("trait", "")
                if trait_name:
                    actual_removed_traits.append(trait_name)
                    # Get the actual removal reason from the cleanup log
                    reason = trait_info.get("reason", "Unknown")
                    if reason == "too_many_zeros":
                        removal_reasons_dict[trait_name] = (
                            f"High Zeros ({trait_info.get('zero_fraction', 0):.2%})"
                        )
                    elif reason == "too_many_nans":
                        removal_reasons_dict[trait_name] = (
                            f"High NaNs ({trait_info.get('nan_fraction', 0):.2%})"
                        )
                    elif reason == "insufficient_samples":
                        removal_reasons_dict[trait_name] = (
                            f"Insufficient samples ({trait_info.get('valid_samples', 0)})"
                        )
                    else:
                        removal_reasons_dict[trait_name] = reason

    # If we have actually removed traits, show them
    if actual_removed_traits:
        fig, ax = plt.subplots(figsize=(12, max(8, len(actual_removed_traits) * 0.4)))

        # Filter eda_df to only include actually removed traits
        removed_df = eda_df[eda_df["Trait"].isin(actual_removed_traits)].copy()

        # Add the actual removal reasons
        removed_df["Removal_Reason"] = removed_df["Trait"].map(removal_reasons_dict)

        # Plot (only NaN and Zero fractions since outliers don't affect trait removal)
        y_pos = np.arange(len(removed_df))
        ax.barh(y_pos, removed_df["Fraction_NaNs"], label="NaN Fraction", alpha=0.7)
        ax.barh(
            y_pos,
            removed_df["Fraction_Zeros"],
            left=removed_df["Fraction_NaNs"],
            label="Zero Fraction",
            alpha=0.7,
        )

        ax.set_yticks(y_pos)
        ax.set_yticklabels(removed_df["Trait"])
        ax.set_xlabel("Fraction")
        ax.set_title(f"Traits Actually Removed ({len(actual_removed_traits)} traits)")
        ax.legend()

        # Add actual removal reasons as text
        for i, (idx, row) in enumerate(removed_df.iterrows()):
            ax.text(1.02, i, row["Removal_Reason"], va="center", fontsize=8)

        plt.tight_layout()
        figures["traits_actually_removed"] = fig

    # If no cleanup_log provided, use apply_data_cleanup_filters to determine what WOULD be removed
    # This ensures consistency with the actual pipeline behavior
    if not cleanup_log:
        # Run apply_data_cleanup_filters to see what would be removed
        _, simulated_log = apply_data_cleanup_filters(
            df.copy(),
            trait_cols,
            max_zeros_per_trait=thresholds.get("zero", 0.5),
            max_nans_per_trait=thresholds.get("nan", 0.3),
            min_samples_per_trait=min_samples_per_trait,
        )

        # Extract traits that would be removed
        hypothetical_removals = []
        hypothetical_reasons = {}
        for trait_info in simulated_log.get("removed_traits", []):
            if isinstance(trait_info, dict):
                trait_name = trait_info.get("trait", "")
                if trait_name:
                    hypothetical_removals.append(trait_name)
                    reason = trait_info.get("reason", "Unknown")
                    if reason == "too_many_zeros":
                        hypothetical_reasons[trait_name] = (
                            f"High Zeros ({trait_info.get('zero_fraction', 0):.2%})"
                        )
                    elif reason == "too_many_nans":
                        hypothetical_reasons[trait_name] = (
                            f"High NaNs ({trait_info.get('nan_fraction', 0):.2%})"
                        )
                    elif reason == "insufficient_samples":
                        hypothetical_reasons[trait_name] = (
                            f"Insufficient samples ({trait_info.get('valid_samples', 0)})"
                        )
                    else:
                        hypothetical_reasons[trait_name] = reason
    else:
        hypothetical_removals = []
        hypothetical_reasons = {}

    # Show traits that would be removed but weren't (shouldn't happen if cleanup_log is from same parameters)
    traits_exceeding_thresholds = [
        t for t in hypothetical_removals if t not in actual_removed_traits
    ]

    if traits_exceeding_thresholds:
        fig, ax = plt.subplots(
            figsize=(12, max(8, len(traits_exceeding_thresholds) * 0.4))
        )
        exceed_df = eda_df[eda_df["Trait"].isin(traits_exceeding_thresholds)].copy()

        # Add the simulated removal reasons
        exceed_df["Threshold_Exceeded"] = exceed_df["Trait"].map(hypothetical_reasons)

        # Plot (only NaN and Zero fractions since outliers don't affect trait removal)
        y_pos = np.arange(len(exceed_df))
        ax.barh(y_pos, exceed_df["Fraction_NaNs"], label="NaN Fraction", alpha=0.7)
        ax.barh(
            y_pos,
            exceed_df["Fraction_Zeros"],
            left=exceed_df["Fraction_NaNs"],
            label="Zero Fraction",
            alpha=0.7,
        )

        ax.set_yticks(y_pos)
        ax.set_yticklabels(exceed_df["Trait"])
        ax.set_xlabel("Fraction")
        ax.set_title(
            f"Traits Exceeding Cleanup Thresholds But Not Removed ({len(traits_exceeding_thresholds)} traits)"
        )
        ax.legend()

        # Add threshold info as text
        for i, (idx, row) in enumerate(exceed_df.iterrows()):
            ax.text(1.02, i, row["Threshold_Exceeded"], va="center", fontsize=8)

        plt.tight_layout()
        figures["traits_exceeding_thresholds"] = fig

    # 3. Variance distribution plot
    fig, ax = plt.subplots(figsize=(10, 6))
    valid_vars = eda_df[eda_df["Variance"] > 0]["Variance"]
    if len(valid_vars) > 0:
        ax.hist(np.log10(valid_vars + 1e-10), bins=30, alpha=0.7, edgecolor="black")
        ax.set_xlabel("Log10(Variance)")
        ax.set_ylabel("Number of Traits")
        ax.set_title("Distribution of Trait Variances (log scale)")
    plt.tight_layout()
    figures["variance_distribution"] = fig

    return figures


def create_heritability_plot(
    heritability_results: Dict,
    threshold: float = 0.5,
    figsize: Tuple[int, int] = (12, 6),
) -> plt.Figure:
    """Create bar plot of heritability estimates.

    Args:
        heritability_results: Results from heritability analysis
        threshold: Threshold line for high heritability
        figsize: Figure size

    Returns:
        Matplotlib figure object
    """
    # Extract valid heritability values
    traits = []
    h2_values = []

    for trait, results in heritability_results.items():
        if isinstance(results, dict) and "heritability" in results:
            h2_value = results["heritability"]
            # Skip None or invalid values
            if (
                h2_value is not None
                and isinstance(h2_value, (int, float))
                and 0 <= h2_value <= 1
            ):
                traits.append(trait)
                h2_values.append(h2_value)

    if not traits:
        fig, ax = plt.subplots(figsize=figsize)
        ax.text(0.5, 0.5, "No heritability data available", ha="center", va="center")
        ax.set_title("Heritability Estimates")
        return fig

    # Create plot
    fig, ax = plt.subplots(figsize=figsize)

    # Color bars based on threshold
    colors = ["green" if h2 >= threshold else "orange" for h2 in h2_values]

    bars = ax.bar(range(len(traits)), h2_values, color=colors, alpha=0.7)

    # Add threshold line
    ax.axhline(
        y=threshold,
        color="red",
        linestyle="--",
        alpha=0.7,
        label=f"Threshold ({threshold})",
    )

    # Customize plot
    ax.set_xlabel("Traits")
    ax.set_ylabel("Heritability (H²)")
    ax.set_title("Broad-sense Heritability Estimates")
    ax.set_xticks(range(len(traits)))
    ax.set_xticklabels(traits, rotation=90, ha="center")
    ax.set_ylim(0, 1)
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")

    # Add value labels on bars
    for bar, h2 in zip(bars, h2_values):
        height = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            height + 0.01,
            f"{h2:.3f}",
            ha="center",
            va="bottom",
            fontsize=8,
        )

    plt.tight_layout()
    return fig


def create_heritability_threshold_plot(
    threshold_analysis: Dict[str, np.ndarray],
    current_threshold: Optional[float] = None,
    figsize: Tuple[float, float] = (10, 6),
) -> plt.Figure:
    """Create plot showing trait retention vs heritability threshold.

    Args:
        threshold_analysis: Results from analyze_heritability_thresholds
        current_threshold: Current threshold to highlight (optional)
        figsize: Figure size

    Returns:
        Figure with threshold analysis plot
    """
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=figsize, height_ratios=[2, 1])

    thresholds = threshold_analysis["thresholds"]
    traits_retained = threshold_analysis["traits_retained"]
    fraction_retained = threshold_analysis["fraction_retained"]
    total_traits = threshold_analysis["total_traits"]

    # Top plot: Number of traits retained
    ax1.plot(thresholds, traits_retained, "b-", linewidth=2, label="Traits retained")
    ax1.fill_between(thresholds, 0, traits_retained, alpha=0.3, color="blue")

    # Add reference lines
    ax1.axhline(
        y=total_traits * 0.5,
        color="gray",
        linestyle="--",
        alpha=0.5,
        label="50% of traits",
    )
    ax1.axhline(
        y=total_traits * 0.75,
        color="gray",
        linestyle=":",
        alpha=0.5,
        label="75% of traits",
    )

    # Highlight current threshold
    if current_threshold is not None:
        idx = np.argmin(np.abs(thresholds - current_threshold))
        ax1.axvline(
            x=current_threshold,
            color="red",
            linestyle="--",
            alpha=0.7,
            label=f"Current: {current_threshold}",
        )
        ax1.plot(current_threshold, traits_retained[idx], "ro", markersize=8)
        # Position text above the point to avoid overlap with the line
        y_offset = total_traits * 0.03  # 3% of total range as offset
        ax1.text(
            current_threshold + 0.02,
            traits_retained[idx] + y_offset,
            f"{int(traits_retained[idx])} traits",
            verticalalignment="bottom",
        )

    ax1.set_ylabel("Number of Traits Retained", fontsize=12)
    ax1.set_title("Trait Retention vs Heritability Threshold", fontsize=14)
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="upper right")
    ax1.set_xlim(0, 1)
    ax1.set_ylim(0, total_traits * 1.05)

    # Bottom plot: Fraction retained
    ax2.plot(thresholds, fraction_retained * 100, "g-", linewidth=2)
    ax2.fill_between(thresholds, 0, fraction_retained * 100, alpha=0.3, color="green")

    if current_threshold is not None:
        ax2.axvline(x=current_threshold, color="red", linestyle="--", alpha=0.7)
        idx = np.argmin(np.abs(thresholds - current_threshold))
        ax2.plot(current_threshold, fraction_retained[idx] * 100, "ro", markersize=8)
        # Position text above the point to avoid overlap with the line
        y_offset = 3  # 3% as offset
        ax2.text(
            current_threshold + 0.02,
            fraction_retained[idx] * 100 + y_offset,
            f"{fraction_retained[idx] * 100:.1f}%",
            verticalalignment="bottom",
        )

    ax2.set_xlabel("Heritability Threshold (H²)", fontsize=12)
    ax2.set_ylabel("Traits Retained (%)", fontsize=12)
    ax2.grid(True, alpha=0.3)
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 105)

    plt.tight_layout()
    return fig


def create_variance_decomposition_plot(
    comparison_df: pd.DataFrame,
    figsize: tuple = (14, 8),
    output_path: Optional[Path] = None,
    threshold: float = 0.3,
) -> plt.Figure:
    """Create 3-panel variance decomposition plot for heritability diagnostics.

    Displays heritability estimates, variance components, and sample statistics.
    Uses Linear Mixed Model (LMM) with genotype as random effect for estimation.

    Args:
        comparison_df: DataFrame from compare_trait_heritabilities()
        figsize: Figure size (width, height) in inches
        output_path: Optional path to save figure
        threshold: Heritability threshold for reference lines (default: 0.3)

    Returns:
        matplotlib Figure object

    Example:
        >>> comparison = compare_trait_heritabilities(df, traits, h2_results)
        >>> fig = create_variance_decomposition_plot(comparison)
        >>> plt.show()
    """
    fig, axes = plt.subplots(1, 3, figsize=figsize)

    if len(comparison_df) == 0:
        # Handle empty data
        for ax in axes:
            ax.text(
                0.5,
                0.5,
                "No data available",
                ha="center",
                va="center",
                transform=ax.transAxes,
            )
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(output_path, dpi=300, bbox_inches="tight")
        return fig

    # Panel 1: Heritability bar chart
    ax = axes[0]
    x_pos = range(len(comparison_df))
    ax.bar(x_pos, comparison_df["heritability"], color="steelblue", alpha=0.7)
    ax.set_ylabel("Heritability (H²)")
    ax.set_title("Heritability Estimates")
    ax.axhline(
        y=threshold,
        color="r",
        linestyle="--",
        alpha=0.5,
        label=f"Threshold ({threshold})",
    )
    ax.legend()
    ax.set_xticks(x_pos)
    ax.set_xticklabels(comparison_df["trait"], rotation=90, ha="center")
    ax.set_xlabel("")

    # Panel 2: Variance components (stacked bar)
    ax = axes[1]
    x_pos = range(len(comparison_df))
    ax.bar(
        x_pos,
        comparison_df["var_genetic"],
        label="Genetic (σ²_G)",
        color="teal",
        alpha=0.7,
    )
    ax.bar(
        x_pos,
        comparison_df["var_residual"],
        bottom=comparison_df["var_genetic"],
        label="Residual (σ²_E)",
        color="coral",
        alpha=0.7,
    )
    ax.set_ylabel("Variance")
    ax.set_title("Genetic vs Residual Variance")
    ax.legend()
    ax.set_xticks(x_pos)
    ax.set_xticklabels(comparison_df["trait"], rotation=90, ha="center")
    ax.set_xlabel("")

    # Panel 3: Sample size and CV
    ax = axes[2]
    ax2 = ax.twinx()

    # Bar plot for sample size
    x_pos = range(len(comparison_df))
    ax.bar(
        x_pos,
        comparison_df["n_observations"],
        color="goldenrod",
        alpha=0.7,
        label="N observations",
    )

    # Line plot for CV
    ax2.plot(
        x_pos,
        comparison_df["trait_cv"],
        color="purple",
        marker="o",
        linewidth=2,
        label="CV (%)",
    )

    ax.set_ylabel("N Observations", color="goldenrod")
    ax2.set_ylabel("Coefficient of Variation (%)", color="purple")
    ax.set_title("Sample Size and Coefficient of Variation")
    ax.set_xticks(x_pos)
    ax.set_xticklabels(comparison_df["trait"], rotation=90, ha="center")
    ax.tick_params(axis="y", labelcolor="goldenrod")
    ax2.tick_params(axis="y", labelcolor="purple")
    ax.set_xlabel("")

    # Add model information to figure
    fig.text(
        0.5,
        0.02,
        "Model: Linear Mixed Model with Genotype as Random Effect (LMM: Trait ~ 1 + (1|Genotype))",
        ha="center",
        fontsize=10,
        style="italic",
        color="gray",
    )

    plt.tight_layout(rect=[0, 0.04, 1, 1])  # Leave space for model text

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=300, bbox_inches="tight")

    return fig


def create_trait_by_genotype_boxplots(
    df: pd.DataFrame,
    traits: List[str],
    heritability_results: Dict[str, Dict],
    genotype_col: str = "geno",
    ncols: int = 2,
    figsize: Optional[tuple] = None,
    output_path: Optional[Path] = None,
    adaptive_config: Optional[Any] = None,
) -> plt.Figure:
    """Create boxplots showing trait distributions by genotype with H² annotations.

    Args:
        df: DataFrame with trait data
        traits: List of trait names to plot
        heritability_results: Dictionary mapping traits to heritability results
        genotype_col: Name of genotype column
        ncols: Number of columns in subplot grid
        figsize: Optional figure size (auto-sized if None)
        output_path: Optional path to save figure
        adaptive_config: Optional adaptive sizing configuration

    Returns:
        matplotlib Figure object

    Example:
        >>> h2_results = calculate_heritability_estimates(df, traits)
        >>> fig = create_trait_by_genotype_boxplots(df, traits, h2_results)
        >>> plt.show()
    """
    n_traits = len(traits)
    nrows = (n_traits + ncols - 1) // ncols

    if figsize is None:
        # Calculate adaptive size based on genotype count
        if adaptive_config is not None and genotype_col in df.columns:
            from sleap_roots_analyze.viz_utils import calculate_barplot_size

            n_genotypes = df[genotype_col].nunique()

            # Each subplot needs width based on genotype count
            subplot_width, subplot_height = calculate_barplot_size(
                n_items=n_genotypes,
                config=adaptive_config,
                orientation="vertical",  # Genotypes on X-axis
                as_subplot=True,
                n_subplots=n_traits,
            )

            # Total figure size based on grid layout
            fig_width = subplot_width * ncols
            fig_height = subplot_height * nrows

            # Apply bounds
            fig_width = max(
                adaptive_config.min_width, min(adaptive_config.max_width, fig_width)
            )
            fig_height = max(
                adaptive_config.min_height,
                min(adaptive_config.max_height, fig_height),
            )

            figsize = (fig_width, fig_height)
        else:
            figsize = (6 * ncols, 5 * nrows)

    # Handle single subplot case - must create figure differently for pandas boxplot
    if n_traits == 1:
        fig, ax = plt.subplots(1, 1, figsize=figsize)
        axes = [ax]
    else:
        fig, axes = plt.subplots(nrows, ncols, figsize=figsize)
        if nrows == 1:
            axes = list(axes)
        else:
            axes = axes.flatten()

    for idx, trait in enumerate(traits):
        ax = axes[idx]

        # Get data
        subset = df[[trait, genotype_col]].dropna()

        if len(subset) == 0:
            ax.text(
                0.5,
                0.5,
                "No data available",
                ha="center",
                va="center",
                transform=ax.transAxes,
            )
            ax.set_title(f"{trait}\n(No data)")
            continue

        # Create boxplot
        subset.boxplot(column=trait, by=genotype_col, ax=ax)

        # Get heritability info
        h2_result = heritability_results.get(trait, {})
        h2 = h2_result.get("heritability", np.nan)
        var_g = h2_result.get("var_genetic", np.nan)
        var_r = h2_result.get("var_residual", np.nan)

        # Build title
        title = f"{trait}\nH² = {h2:.3f}"
        if not np.isnan(var_g) and not np.isnan(var_r):
            title += f"\nσ²_G = {var_g:.4f}, σ²_E = {var_r:.4f}"
        ax.set_title(title)

        # Remove pandas default title
        plt.sca(ax)
        plt.xlabel(genotype_col)
        plt.ylabel(trait)

        # Rotate labels if many genotypes
        if subset[genotype_col].nunique() > 10:
            ax.tick_params(axis="x", rotation=90)

    # Hide extra subplots
    for idx in range(n_traits, len(axes)):
        axes[idx].set_visible(False)

    plt.suptitle("")  # Remove default suptitle
    plt.tight_layout()

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=300, bbox_inches="tight")

    return fig


def create_heritability_diagnostic_dashboard(
    df: pd.DataFrame,
    traits: List[str],
    heritability_results: Dict[str, Dict],
    comparison_df: pd.DataFrame,
    layout: str = "vertical",
    output_path: Optional[Path] = None,
) -> plt.Figure:
    """Create heritability diagnostic visualization with variance decomposition.

    Creates variance decomposition and trait-by-genotype boxplot figures.
    Note: Currently returns only the variance decomposition plot; the boxplot
    is generated but not included in the returned figure.

    Args:
        df: DataFrame with trait data
        traits: List of trait names to analyze
        heritability_results: Dictionary with heritability results
        comparison_df: DataFrame from compare_trait_heritabilities()
        layout: "vertical" (default) or "horizontal" (currently unused)
        output_path: Optional path to save variance decomposition figure

    Returns:
        matplotlib Figure object containing variance decomposition plot

    Example:
        >>> h2_results = calculate_heritability_estimates(df, traits)
        >>> comparison = compare_trait_heritabilities(df, traits, h2_results)
        >>> fig = create_heritability_diagnostic_dashboard(
        ...     df, traits, h2_results, comparison
        ... )
        >>> plt.show()
    """
    # Create variance decomposition
    var_fig = create_variance_decomposition_plot(comparison_df, figsize=(14, 8))

    # Create boxplots
    box_fig = create_trait_by_genotype_boxplots(
        df, traits, heritability_results, figsize=(12, 4 * len(traits))
    )

    # For now, return the variance decomposition figure
    # Full dashboard integration would require more complex subplot management
    plt.close(box_fig)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        var_fig.savefig(output_path, dpi=300, bbox_inches="tight")

    return var_fig


def identify_extreme_samples_in_pc_space(
    pca_results: Dict,
    df: pd.DataFrame,
    n_components: int = 3,
    n_std: float = 2.0,
    sample_id_col: str = "Barcode",
) -> pd.DataFrame:
    """Identify samples with extreme values in PC space.

    Args:
        pca_results: Results from perform_pca_analysis.
        df: Original dataframe with sample metadata.
        n_components: Number of PC components to consider.
        n_std: Number of standard deviations to consider extreme.
        sample_id_col: Column name containing sample IDs.

    Returns:
        DataFrame with extreme samples and their PC scores.
    """
    X_pca = pca_results["transformed_data"]
    n_components = min(n_components, X_pca.shape[1])

    extreme_samples = []

    # Check each PC individually
    for pc_idx in range(n_components):
        pc_scores = X_pca[:, pc_idx]
        pc_mean = np.mean(pc_scores)
        pc_std = np.std(pc_scores, ddof=1)

        # Guard against zero/near-zero std
        if pc_std <= 0:
            continue

        # Find extreme samples (both high and low)
        extreme_mask = np.abs(pc_scores - pc_mean) > n_std * pc_std
        extreme_indices = np.where(extreme_mask)[0]

        for idx in extreme_indices:
            sample_id = df.iloc[idx][sample_id_col]
            z_score = (pc_scores[idx] - pc_mean) / pc_std
            extreme_type = "high" if pc_scores[idx] > pc_mean else "low"

            extreme_samples.append(
                {
                    sample_id_col: sample_id,
                    "pc_component": f"PC{pc_idx + 1}",
                    "pc_score": pc_scores[idx],
                    "z_score": z_score,
                    "extreme_type": extreme_type,
                    "explained_variance_ratio": pca_results["explained_variance_ratio"][
                        pc_idx
                    ],
                }
            )

    # Optional: Check for samples extreme in multiple PCs using Hotelling's T²
    # This is less interpretable but provides a global outlier statistic
    if n_components > 1 and "eigenvalues" in pca_results:
        # Calculate Hotelling's T² statistic (variance-standardized radius)
        # T² = sum((score_k)² / eigenvalue_k)
        eigenvalues = np.asarray(pca_results["eigenvalues"][:n_components])
        eigenvalues = np.maximum(eigenvalues, 1e-12)  # Protect against tiny eigenvalues
        t_squared = np.sum((X_pca[:, :n_components] ** 2) / eigenvalues, axis=1)

        # For multivariate normal, T² follows chi-square distribution
        # Use chi-square critical value for threshold
        from scipy.stats import chi2

        chi2_threshold = chi2.ppf(1 - 0.05, df=n_components)  # 95% confidence

        extreme_t2_mask = t_squared > chi2_threshold
        extreme_t2_indices = np.where(extreme_t2_mask)[0]

        # Calculate empirical z-score on radius for better interpretability
        r = np.sqrt(t_squared)
        r_mean = float(np.mean(r))
        r_std = float(np.std(r, ddof=1) or 1e-12)  # Guard against zero std

        for idx in extreme_t2_indices:
            sample_id = df.iloc[idx][sample_id_col]
            # Use empirical z-score on radius
            z_score = (r[idx] - r_mean) / r_std

            extreme_samples.append(
                {
                    sample_id_col: sample_id,
                    "pc_component": "Hotelling T²",
                    "pc_score": t_squared[idx],
                    "z_score": z_score,
                    "extreme_type": "multi-pc",
                    "explained_variance_ratio": np.sum(
                        pca_results["explained_variance_ratio"][:n_components]
                    ),
                }
            )

    extreme_df = pd.DataFrame(extreme_samples)

    # Remove duplicates, keeping the most extreme case (by absolute z-score)
    if not extreme_df.empty:
        extreme_df["abs_z_score"] = extreme_df["z_score"].abs()
        extreme_df = extreme_df.sort_values("abs_z_score", ascending=False)
        extreme_df = extreme_df.drop_duplicates(subset=[sample_id_col], keep="first")
        extreme_df = extreme_df.drop(columns=["abs_z_score"])

    return extreme_df


def create_pca_scree_plot(
    pca_results: Dict,
    variance_threshold: float = 0.95,
    figsize: Tuple[float, float] = (10, 6),
) -> plt.Figure:
    """Create a combined scree plot with variance threshold indicator.

    Args:
        pca_results: Results from perform_pca_analysis.
        variance_threshold: Cumulative variance threshold to highlight.
        figsize: Figure size.

    Returns:
        Enhanced scree plot with combined individual and cumulative variance.
    """
    fig, ax = plt.subplots(figsize=figsize)

    explained_var = pca_results["explained_variance_ratio"]
    cumulative_var = pca_results["cumulative_variance_ratio"]
    n_components = len(explained_var)

    # Find the number of components for threshold
    n_threshold = np.argmax(cumulative_var >= variance_threshold) + 1

    # Individual variance bars with color coding
    colors = [
        "darkblue" if i < n_threshold else "lightblue" for i in range(n_components)
    ]
    bars = ax.bar(
        range(1, n_components + 1),
        explained_var * 100,
        color=colors,
        alpha=0.7,
        label="Individual variance",
    )

    # Add percentage labels on bars
    for i, (bar, var) in enumerate(zip(bars, explained_var)):
        if i < 10:  # Only label first 10 for readability
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.5,
                f"{var * 100:.1f}",
                ha="center",
                va="bottom",
                fontsize=8,
            )

    # Cumulative variance line on same axis
    ax.plot(
        range(1, n_components + 1),
        cumulative_var * 100,
        color="darkred",
        marker="o",
        markersize=4,
        linewidth=2,
        label="Cumulative variance",
    )

    # Add threshold line
    ax.axhline(
        y=variance_threshold * 100,
        color="green",
        linestyle="--",
        linewidth=2,
        alpha=0.7,
        label=f"{variance_threshold * 100:.0f}% threshold",
    )

    # Find and mark threshold crossing point
    if n_threshold <= n_components:
        ax.plot(
            n_threshold,
            cumulative_var[n_threshold - 1] * 100,
            "o",
            color="green",
            markersize=8,
            zorder=5,
        )

        # Add vertical line at threshold
        ax.axvline(
            x=n_threshold,
            color="green",
            linestyle=":",
            alpha=0.5,
        )

        # Add simple text annotation with exact variance
        ax.text(
            n_threshold,
            cumulative_var[n_threshold - 1] * 100 + 2,
            f"{cumulative_var[n_threshold - 1] * 100:.1f}%",
            ha="center",
            color="green",
            fontsize=10,
            fontweight="bold",
        )

    ax.set_xlabel("Principal Component")
    ax.set_ylabel("Explained Variance (%)")
    ax.set_xticks(range(1, min(21, n_components + 1)))
    ax.set_ylim([0, 105])
    ax.grid(True, alpha=0.3)

    # Custom legend explaining the color coding
    from matplotlib.patches import Patch

    legend_elements = [
        Patch(
            facecolor="darkblue",
            alpha=0.7,
            label=f"First {n_threshold} PCs (meets threshold)",
        ),
        Line2D(
            [0], [0], color="darkred", lw=2, marker="o", label="Cumulative variance"
        ),
        Line2D(
            [0],
            [0],
            color="green",
            lw=2,
            linestyle="--",
            label=f"{variance_threshold * 100:.0f}% threshold",
        ),
    ]
    # Only add light blue legend if there are actually visible bars beyond threshold
    if n_threshold < n_components and any(
        explained_var[n_threshold:] * 100 > 1
    ):  # Only if some remaining PCs > 1%
        legend_elements.insert(
            1,
            Patch(
                facecolor="lightblue", alpha=0.7, label=f"Remaining PCs (low variance)"
            ),
        )

    ax.legend(handles=legend_elements, loc="center right", framealpha=0.9)

    # Title with feature count
    n_features = pca_results.get(
        "n_features", len(pca_results.get("feature_names", []))
    )
    title = f"PCA Scree Plot"
    if n_features:
        title += f" (Total features: {n_features})"
    plt.title(title, fontsize=14, pad=20)

    plt.tight_layout()
    return fig


def create_feature_contribution_plot(
    pca_results: Dict,
    trait_names: List[str],
    n_components: Optional[int] = None,
    variance_threshold: float = 0.95,
    top_n: int = 20,
    feature_selection: str = "top_variance",  # New parameter
    figsize: Tuple[float, float] = (12, 8),
) -> plt.Figure:
    """Create a plot showing feature contributions across selected PCs.

    This function can use pre-calculated contributions from run_pca_and_export_artifacts
    (if available in pca_results["trait_contrib_df"]) or calculate them on the fly.

    Args:
        pca_results: Results from perform_pca_analysis or run_pca_and_export_artifacts.
            If it contains "trait_contrib_df", those pre-calculated contributions will be used.
        trait_names: Names of traits/features.
        n_components: Number of PCs to consider. If None, use variance threshold.
        variance_threshold: Cumulative variance threshold for PC selection.
        top_n: Number of top contributing features to show.
        feature_selection: Method for selecting features:
            - "top_variance": Top N by total variance contribution (default)
            - "extreme": Top N most positive and negative for displayed PCs
            - "top_absolute": Top N by absolute loading magnitude
            - "top_contribution": Top N by contribution to displayed PCs
        figsize: Figure size.

    Returns:
        Feature contribution plot.
    """
    # Check if pre-calculated contributions are available
    if "feature_contributions" in pca_results or "trait_contrib_df" in pca_results:
        # Use pre-calculated contributions
        trait_contrib_df = pca_results.get(
            "feature_contributions", pca_results.get("trait_contrib_df")
        )

        # Determine number of components
        if n_components is None:
            cumulative_var = pca_results["cumulative_variance_ratio"]
            n_components = np.argmax(cumulative_var >= variance_threshold) + 1

        # Check what type of contribution data we have
        pc_contrib_cols = [
            col
            for col in trait_contrib_df.columns
            if col.startswith("PC") and col.endswith("_variance_contrib")
        ]

        if pc_contrib_cols:
            # We have per-PC contributions (from run_pca_and_export_artifacts)
            available_pcs = min(len(pc_contrib_cols), n_components)
            pc_cols_to_use = [
                f"PC{i + 1}_variance_contrib" for i in range(available_pcs)
            ]

            # Get top contributors
            top_features_df = trait_contrib_df.head(min(top_n, len(trait_contrib_df)))

            # Extract traits
            if "trait" in top_features_df.columns:
                top_traits = top_features_df["trait"].tolist()
            else:
                top_traits = top_features_df.index.tolist()

            # Get contributions per PC
            contributions = top_features_df[pc_cols_to_use].values

            # Get total contributions
            if "trait_total_variance_contrib" in top_features_df.columns:
                total_contributions = top_features_df[
                    "trait_total_variance_contrib"
                ].values
            else:
                total_contributions = contributions.sum(axis=1)

        else:
            # We only have total contributions (from perform_pca_analysis)
            # Need to calculate per-PC contributions
            n_components = min(
                n_components,
                pca_results.get(
                    "n_components_selected",
                    len(pca_results["explained_variance_ratio"]),
                ),
            )

            # Get top features
            top_features_df = trait_contrib_df.head(min(top_n, len(trait_contrib_df)))

            # Extract trait names from index
            top_traits = top_features_df.index.tolist()

            # Get total contributions
            if "total_contribution" in top_features_df.columns:
                total_contributions = top_features_df["total_contribution"].values
            else:
                total_contributions = top_features_df[
                    "trait_total_variance_contrib"
                ].values

            # Calculate per-PC contributions for these features
            loadings = pca_results["loadings"][:, :n_components]
            eigenvalues = pca_results["eigenvalues"][:n_components]
            feature_names = pca_results["feature_names"]

            # Find indices of top features
            top_indices = [feature_names.index(trait) for trait in top_traits]

            # Calculate contributions
            contributions = np.zeros((len(top_traits), n_components))
            for i in range(n_components):
                for j, idx in enumerate(top_indices):
                    contributions[j, i] = eigenvalues[i] * loadings[idx, i] ** 2

            available_pcs = n_components

    else:
        # Calculate contributions on the fly (backward compatibility)
        # Determine number of components
        if n_components is None:
            cumulative_var = pca_results["cumulative_variance_ratio"]
            n_components = np.argmax(cumulative_var >= variance_threshold) + 1

        n_components = min(
            n_components,
            pca_results.get(
                "n_components_selected", len(pca_results["explained_variance_ratio"])
            ),
        )

        # Calculate variance-weighted contributions
        loadings = pca_results["loadings"][:, :n_components]
        eigenvalues = pca_results["eigenvalues"][:n_components]

        # Calculate contribution of each feature to each PC
        contributions = np.zeros((len(trait_names), n_components))
        for i in range(n_components):
            contributions[:, i] = eigenvalues[i] * loadings[:, i] ** 2

        # Sum across selected PCs
        total_contributions = np.sum(contributions, axis=1)

        # Sort features by total contribution
        sorted_indices = np.argsort(total_contributions)[::-1]

        # Limit to available features
        actual_top_n = min(top_n, len(trait_names))
        top_indices = sorted_indices[:actual_top_n]

        # Get top features data
        top_traits = [trait_names[i] for i in top_indices]
        contributions = contributions[top_indices]
        total_contributions = total_contributions[top_indices]
        available_pcs = n_components

    # Create figure
    fig, ax = plt.subplots(figsize=figsize)

    # Create stacked bar chart
    # Reverse the order so highest contribution is at top
    top_traits = top_traits[::-1]
    contributions = contributions[::-1]
    total_contributions = total_contributions[::-1]
    y_pos = np.arange(len(top_traits))

    # Colors for different PCs
    colors = plt.cm.tab20(np.linspace(0, 1, available_pcs))

    # Plot stacked bars
    left = np.zeros(len(top_traits))
    for i in range(available_pcs):
        ax.barh(
            y_pos,
            contributions[:, i],
            left=left,
            label=f"PC{i + 1}",
            color=colors[i],
            edgecolor="black",
            linewidth=0.5,
        )
        left += contributions[:, i]

    # Customize plot
    ax.set_yticks(y_pos)
    ax.set_yticklabels(top_traits)
    ax.set_xlabel("Variance Contribution", fontsize=12)
    ax.set_ylabel("Features", fontsize=12)
    ax.set_title(
        f"Top {len(top_traits)} Feature Contributions to First {available_pcs} PCs",
        fontsize=14,
    )

    # Add legend
    ax.legend(
        loc="lower right",
        bbox_to_anchor=(1.0, 0.0),
        ncol=min(available_pcs, 5),
        fontsize=10,
    )

    # Add grid
    ax.grid(True, axis="x", alpha=0.3)

    # Adjust layout
    plt.tight_layout()

    return fig


def create_pca_biplot(
    pca_results: Dict,
    df: pd.DataFrame,
    trait_names: List[str],
    color_by: Optional[str] = None,
    pc_x: int = 1,
    pc_y: int = 2,
    top_n_features: int = 10,
    feature_selection: str = "vector_length",  # "extreme", "top_absolute", "top_contribution", or "vector_length"
    figsize: Tuple[float, float] = (10, 8),
    alpha: float = 0.6,
    arrow_scale: Optional[float] = None,  # Auto-scale if None
    genotypes_to_color: Optional[List[str]] = None,
    highlight_genotypes: Optional[List[str]] = None,
) -> plt.Figure:
    """Create a PCA biplot showing samples and feature loadings.

    Args:
        pca_results: Results from perform_pca_analysis.
        df: Original dataframe with metadata for coloring.
        trait_names: Names of traits/features.
        color_by: Column name to color points by.
        pc_x: PC for x-axis (1-indexed).
        pc_y: PC for y-axis (1-indexed).
        top_n_features: Number of features to show per direction (if extreme) or total.
        feature_selection: Method for selecting features to display:
            - "vector_length": Top N by Euclidean distance in PC plane (traditional, default)
            - "extreme": Top N most positive and negative for each PC
            - "top_absolute": Top N by absolute loading magnitude
            - "top_contribution": Top N by variance contribution to displayed PCs
        figsize: Figure size.
        alpha: Transparency for scatter points.
        arrow_scale: Scaling factor for feature arrows (auto-calculated if None).
        genotypes_to_color: Optional list of specific categories to color when using
            categorical color_by. If provided, only these categories will be colored
            with distinct colors and shown in the legend. All other categories will be
            plotted in gray as "Other". If None (default), all categories are colored.
            Only applies when color_by is categorical.
        highlight_genotypes: Optional list of specific genotypes to highlight with
            larger points and edge colors. Works independently of genotypes_to_color.

    Returns:
        PCA biplot figure.
    """
    fig, ax = plt.subplots(figsize=figsize)

    X_pca = pca_results["transformed_data"]
    loadings = pca_results["loadings"]
    explained_var = pca_results["explained_variance_ratio"]

    # Get PC indices (0-based)
    pc_x_idx = pc_x - 1
    pc_y_idx = pc_y - 1

    # Ensure we handle the correct number of features
    n_features = min(len(trait_names), loadings.shape[0])

    # Get eigenvalues if available for variance-based selection
    eigenvalues = pca_results.get("eigenvalues", np.ones(loadings.shape[1]))

    # Map feature_selection parameter to method
    if feature_selection == "extreme":
        method = "extreme"
    elif feature_selection == "top_absolute":
        method = "top_absolute"
    elif feature_selection == "top_contribution":
        method = "top_contribution"
    elif feature_selection == "vector_length":
        method = "vector_length"
    else:
        method = "vector_length"  # Default to traditional biplot convention

    # Select features using modular function
    top_indices = select_top_features_from_pca(
        loadings=loadings,
        eigenvalues=eigenvalues,
        n_features_total=n_features,
        n_features_to_select=top_n_features,
        method=method,
        pc_indices=[pc_x_idx, pc_y_idx],
    )

    # Plot samples
    if color_by and color_by in df.columns:
        # Handle data indices if PCA removed NaN samples
        if "data_indices" in pca_results:
            # PCA was run on a subset of samples
            data_indices = pca_results["data_indices"]
            df_pca = (
                df.iloc[data_indices]
                if isinstance(data_indices[0], int)
                else df.loc[data_indices]
            )
        else:
            # Assume PCA was run on all samples in df order
            # But check if sizes match
            if len(X_pca) != len(df):
                # Try to match by dropping NaN rows
                numeric_cols = (
                    df[trait_names].select_dtypes(include=[np.number]).columns
                )
                mask_complete = ~df[numeric_cols].isna().any(axis=1)
                df_pca = df[mask_complete]
                if len(df_pca) != len(X_pca):
                    raise ValueError(
                        f"Cannot match PCA samples ({len(X_pca)}) to dataframe ({len(df)})"
                    )
            else:
                df_pca = df

        # Handle categorical coloring
        if df_pca[color_by].dtype == "object" or isinstance(
            df_pca[color_by].dtype, pd.CategoricalDtype
        ):
            # Get unique categories
            all_categories = df_pca[color_by].unique()

            # Filter categories if genotypes_to_color is provided
            if genotypes_to_color is not None:
                # Only color specified genotypes
                categories_to_plot = [
                    cat for cat in all_categories if cat in genotypes_to_color
                ]
                other_categories = [
                    cat for cat in all_categories if cat not in genotypes_to_color
                ]

                # Generate colors for selected categories
                # Use tab10 but exclude gray (index 7) which is RGB(0.5, 0.5, 0.5)
                tab10_colors = plt.cm.tab10(range(10))
                # Exclude index 7 (gray) from tab10
                non_gray_colors = [
                    tab10_colors[i] for i in range(10) if i != 7
                ]  # Indices: 0-6, 8-9

                # Smart color assignment: highlighted genotypes get maximally distinct colors
                color_map = {}  # Map genotype -> color

                if highlight_genotypes:
                    # Separate highlighted and non-highlighted genotypes
                    highlighted_in_plot = [
                        cat for cat in categories_to_plot if cat in highlight_genotypes
                    ]
                    non_highlighted_in_plot = [
                        cat
                        for cat in categories_to_plot
                        if cat not in highlight_genotypes
                    ]

                    # Assign evenly-spaced colors to highlighted genotypes
                    n_highlighted = len(highlighted_in_plot)
                    if n_highlighted > 0:
                        # Calculate evenly-spaced indices
                        step = len(non_gray_colors) / n_highlighted
                        highlight_indices = [
                            int(i * step) for i in range(n_highlighted)
                        ]

                        for cat, idx in zip(highlighted_in_plot, highlight_indices):
                            color_map[cat] = non_gray_colors[idx]

                    # Assign remaining colors to non-highlighted genotypes
                    used_indices = (
                        set(highlight_indices) if n_highlighted > 0 else set()
                    )
                    remaining_colors = [
                        non_gray_colors[i]
                        for i in range(len(non_gray_colors))
                        if i not in used_indices
                    ]

                    # Cycle through remaining colors for non-highlighted
                    for i, cat in enumerate(non_highlighted_in_plot):
                        color_map[cat] = (
                            remaining_colors[i % len(remaining_colors)]
                            if remaining_colors
                            else non_gray_colors[i % len(non_gray_colors)]
                        )
                else:
                    # Default: assign colors sequentially
                    if len(categories_to_plot) > len(non_gray_colors):
                        colors = [
                            non_gray_colors[i % len(non_gray_colors)]
                            for i in range(len(categories_to_plot))
                        ]
                    else:
                        colors = non_gray_colors[: len(categories_to_plot)]

                    color_map = {
                        cat: colors[i] for i, cat in enumerate(categories_to_plot)
                    }

                # Plot selected categories with distinct colors
                # Plot in two passes: non-highlighted first, then highlighted on top

                # Pass 1: Plot non-highlighted genotypes
                for cat in categories_to_plot:
                    if highlight_genotypes and cat in highlight_genotypes:
                        continue  # Skip highlighted ones for now

                    mask = df_pca[color_by] == cat
                    ax.scatter(
                        X_pca[mask, pc_x_idx],
                        X_pca[mask, pc_y_idx],
                        c=[color_map[cat]],
                        label=cat,
                        alpha=alpha,
                        s=50,
                        edgecolors="none",
                    )

                # Pass 2: Plot highlighted genotypes on top
                if highlight_genotypes:
                    for cat in categories_to_plot:
                        if cat not in highlight_genotypes:
                            continue  # Only plot highlighted ones

                        mask = df_pca[color_by] == cat
                        ax.scatter(
                            X_pca[mask, pc_x_idx],
                            X_pca[mask, pc_y_idx],
                            c=[color_map[cat]],
                            label=cat,
                            alpha=alpha,
                            s=150,
                            edgecolors="black",
                            linewidths=1.5,
                            zorder=10,  # Higher zorder = on top
                        )

                # Plot other categories in gray as "Other"
                if len(other_categories) > 0:
                    # Check if any "other" categories should be highlighted
                    if highlight_genotypes:
                        highlighted_others = [
                            cat
                            for cat in other_categories
                            if cat in highlight_genotypes
                        ]
                        non_highlighted_others = [
                            cat
                            for cat in other_categories
                            if cat not in highlight_genotypes
                        ]

                        # Plot non-highlighted others first (below)
                        if non_highlighted_others:
                            other_mask = df_pca[color_by].isin(non_highlighted_others)
                            ax.scatter(
                                X_pca[other_mask, pc_x_idx],
                                X_pca[other_mask, pc_y_idx],
                                c="gray",
                                label="Other",
                                alpha=alpha,
                                s=50,
                                edgecolors="none",
                            )

                        # Plot highlighted "others" on top
                        if highlighted_others:
                            highlight_mask = df_pca[color_by].isin(highlighted_others)
                            ax.scatter(
                                X_pca[highlight_mask, pc_x_idx],
                                X_pca[highlight_mask, pc_y_idx],
                                c="gray",
                                label="Other (highlighted)",
                                alpha=alpha,
                                s=150,
                                edgecolors="black",
                                linewidths=1.5,
                                zorder=10,  # Higher zorder = on top
                            )
                    else:
                        other_mask = df_pca[color_by].isin(other_categories)
                        ax.scatter(
                            X_pca[other_mask, pc_x_idx],
                            X_pca[other_mask, pc_y_idx],
                            c="gray",
                            label="Other",
                            alpha=alpha,
                            s=50,
                            edgecolors="none",
                        )
            else:
                # Default behavior: color all categories
                categories = all_categories
                tab10_colors_default = plt.cm.tab10(range(10))

                # Smart color assignment for default case too
                color_map_default = {}

                if highlight_genotypes:
                    # Separate highlighted and non-highlighted genotypes
                    highlighted_cats = [
                        cat for cat in categories if cat in highlight_genotypes
                    ]
                    non_highlighted_cats = [
                        cat for cat in categories if cat not in highlight_genotypes
                    ]

                    # Assign evenly-spaced colors to highlighted genotypes
                    n_highlighted = len(highlighted_cats)
                    if n_highlighted > 0:
                        step = 10 / n_highlighted
                        highlight_indices = [
                            int(i * step) for i in range(n_highlighted)
                        ]

                        for cat, idx in zip(highlighted_cats, highlight_indices):
                            color_map_default[cat] = tab10_colors_default[idx]

                    # Assign remaining colors to non-highlighted
                    used_indices = (
                        set(highlight_indices) if n_highlighted > 0 else set()
                    )
                    remaining_colors = [
                        tab10_colors_default[i]
                        for i in range(10)
                        if i not in used_indices
                    ]

                    for i, cat in enumerate(non_highlighted_cats):
                        color_map_default[cat] = (
                            remaining_colors[i % len(remaining_colors)]
                            if remaining_colors
                            else tab10_colors_default[i % 10]
                        )
                else:
                    # Sequential assignment
                    colors = plt.cm.tab10(np.linspace(0, 1, len(categories)))
                    color_map_default = {
                        cat: colors[i] for i, cat in enumerate(categories)
                    }

                # Plot in two passes: non-highlighted first, then highlighted on top

                # Pass 1: Plot non-highlighted genotypes
                for cat in categories:
                    if highlight_genotypes and cat in highlight_genotypes:
                        continue  # Skip highlighted ones for now

                    mask = df_pca[color_by] == cat
                    ax.scatter(
                        X_pca[mask, pc_x_idx],
                        X_pca[mask, pc_y_idx],
                        c=[color_map_default[cat]],
                        label=cat,
                        alpha=alpha,
                        s=50,
                        edgecolors="none",
                    )

                # Pass 2: Plot highlighted genotypes on top
                if highlight_genotypes:
                    for cat in categories:
                        if cat not in highlight_genotypes:
                            continue  # Only plot highlighted ones

                        mask = df_pca[color_by] == cat
                        ax.scatter(
                            X_pca[mask, pc_x_idx],
                            X_pca[mask, pc_y_idx],
                            c=[color_map_default[cat]],
                            label=cat,
                            alpha=alpha,
                            s=150,
                            edgecolors="black",
                            linewidths=1.5,
                            zorder=10,  # Higher zorder = on top
                        )

            ax.legend(title=color_by, bbox_to_anchor=(1.05, 1), loc="upper left")
        else:
            # Numeric coloring
            scatter = ax.scatter(
                X_pca[:, pc_x_idx],
                X_pca[:, pc_y_idx],
                c=df_pca[color_by],
                alpha=alpha,
                s=50,
                edgecolors="none",
                cmap="viridis",
            )
            plt.colorbar(scatter, ax=ax, label=color_by)
    else:
        # No coloring
        ax.scatter(
            X_pca[:, pc_x_idx],
            X_pca[:, pc_y_idx],
            c="blue",
            alpha=alpha,
            s=50,
            edgecolors="none",
        )

    # Auto-scale arrows if not specified
    if arrow_scale is None:
        # Scale arrows to be visible relative to the data spread
        data_range_x = np.ptp(X_pca[:, pc_x_idx])
        data_range_y = np.ptp(X_pca[:, pc_y_idx])
        max_loading = np.max(np.abs(loadings[:, [pc_x_idx, pc_y_idx]]))
        if max_loading > 0:
            arrow_scale = min(data_range_x, data_range_y) / (4 * max_loading)
        else:
            arrow_scale = 1.0

    # Plot feature vectors (only selected features)
    for idx in top_indices:
        # Skip if index is out of bounds for trait_names
        if idx >= len(trait_names):
            continue
        # Use raw loadings (these are the eigenvector components)
        x_load = loadings[idx, pc_x_idx] * arrow_scale
        y_load = loadings[idx, pc_y_idx] * arrow_scale

        # Draw arrow
        ax.arrow(
            0,
            0,
            x_load,
            y_load,
            head_width=0.05,
            head_length=0.05,
            fc="red",
            ec="red",
            alpha=0.8,
            linewidth=1.5,
        )

        # Add label with smart positioning to avoid overlaps
        angle = np.arctan2(y_load, x_load)

        # Create a pattern of offsets to spread out labels
        idx_pos = list(top_indices).index(idx)
        offsets = [-0.3, 0, 0.3, -0.2, 0.2, -0.4, 0.1, 0.4, -0.1, 0.35]
        angle_offset = offsets[idx_pos % len(offsets)]

        # Vary radius to create layers
        radius_mult = 1.15 + (idx_pos % 4) * 0.1
        radius = np.sqrt(x_load**2 + y_load**2) * radius_mult

        label_x = radius * np.cos(angle + angle_offset)
        label_y = radius * np.sin(angle + angle_offset)

        # Adjust text position to avoid overlap with arrow
        ha = "left" if label_x > 0 else "right"
        va = "bottom" if label_y > 0 else "top"

        ax.text(
            label_x,
            label_y,
            trait_names[idx],
            fontsize=14,
            ha=ha,
            va=va,
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.7),
        )

    # Set axis labels and title
    ax.set_xlabel(f"PC{pc_x} ({explained_var[pc_x - 1] * 100:.1f}% variance)")
    ax.set_ylabel(f"PC{pc_y} ({explained_var[pc_y - 1] * 100:.1f}% variance)")
    ax.set_title(f"PCA Biplot", fontsize=14)

    # Add grid
    ax.grid(True, alpha=0.3)
    ax.axhline(y=0, color="k", linestyle="-", alpha=0.3)
    ax.axvline(x=0, color="k", linestyle="-", alpha=0.3)

    # Set equal aspect ratio for better interpretation
    ax.set_aspect("equal", adjustable="datalim")

    plt.tight_layout()
    return fig


def create_umap_colored_by_top_traits(
    umap_results: Union[np.ndarray, Dict],
    df: pd.DataFrame,
    trait_columns: List[str],
    trait_names: List[str],
    pca_results: Dict,
    n_traits: int = 6,
    feature_selection: str = "top_variance",  # New parameter
    variance_threshold: Optional[float] = None,
    figsize: Tuple[float, float] = (15, 10),
) -> plt.Figure:
    """Create UMAP plots colored by top contributing traits.

    Args:
        umap_results: 2D UMAP embedding array or dictionary with 'embedding' key.
        df: Original dataframe with trait values.
        trait_columns: Column names of traits.
        trait_names: Display names of traits.
        pca_results: PCA results for determining trait importance.
        n_traits: Number of top traits to plot.
        feature_selection: Method for selecting features:
            - "top_variance": Top N by total variance contribution (default)
            - "extreme": Top N most positive and negative for first 2 PCs
            - "top_absolute": Top N by absolute loading magnitude
            - "top_contribution": Top N by contribution to first 2 PCs
        variance_threshold: Cumulative variance threshold for PC selection.
            If None, use the same threshold as perform_pca_analysis.
        figsize: Figure size.

    Returns:
        UMAP plots colored by traits.
    """
    # Extract embedding if umap_results is a dictionary
    if isinstance(umap_results, dict):
        umap_embedding = umap_results["embedding"]
    else:
        umap_embedding = umap_results

    # Get necessary data from PCA results
    loadings = pca_results["loadings"]
    eigenvalues = pca_results["eigenvalues"]

    # Determine which PCs to use for variance calculation
    if feature_selection == "top_variance":
        # Use the same PCA threshold/components as perform_pca_analysis by default
        cumulative_var = pca_results["cumulative_variance_ratio"]

        # Check if pca_results has the selected components info
        if "n_components_selected" in pca_results:
            n_pcs = pca_results["n_components_selected"]
        elif variance_threshold is not None:
            # Use provided threshold
            n_pcs = np.argmax(cumulative_var >= variance_threshold) + 1
        else:
            # Default to 95% variance if not specified
            n_pcs = np.argmax(cumulative_var >= 0.95) + 1

        # Clamp n_pcs to available data
        n_pcs = min(n_pcs, loadings.shape[1], len(eigenvalues))
        pc_indices = list(range(n_pcs))
    else:
        # For other methods, use first 2 PCs by default
        pc_indices = [0, 1]

    # Ensure we handle the correct number of features
    n_features = min(len(trait_columns), loadings.shape[0])

    # Select top features using modular function
    top_indices = select_top_features_from_pca(
        loadings=loadings,
        eigenvalues=eigenvalues,
        n_features_total=n_features,
        n_features_to_select=n_traits,
        method=feature_selection,
        pc_indices=pc_indices if feature_selection != "top_variance" else None,
    )

    # Calculate contributions for display (always use variance contribution for labels)
    contributions = np.zeros(n_features)
    for i in range(min(loadings.shape[1], len(eigenvalues))):
        contributions += eigenvalues[i] * loadings[:n_features, i] ** 2

    # Create subplots
    n_cols = 3
    n_rows = (n_traits + n_cols - 1) // n_cols
    fig, axes = plt.subplots(n_rows, n_cols, figsize=figsize)
    axes = axes.flatten() if n_rows > 1 else axes

    for i, trait_idx in enumerate(top_indices[:n_traits]):
        ax = axes[i]
        # Skip if index is out of bounds
        if trait_idx >= len(trait_columns) or trait_idx >= len(trait_names):
            continue
        trait_col = trait_columns[trait_idx]
        trait_name = trait_names[trait_idx]
        trait_values = df[trait_col].values

        # Create scatter plot
        scatter = ax.scatter(
            umap_embedding[:, 0],
            umap_embedding[:, 1],
            c=trait_values,
            cmap="viridis",
            s=30,
            alpha=0.7,
            edgecolors="none",
        )

        # Add colorbar
        cbar = plt.colorbar(scatter, ax=ax)
        cbar.set_label(trait_name, fontsize=9)
        cbar.ax.tick_params(labelsize=8)

        # Set labels and title
        ax.set_xlabel("UMAP 1", fontsize=9)
        ax.set_ylabel("UMAP 2", fontsize=9)

        # Add selection method to subtitle if not default
        subtitle = f"(Contribution: {contributions[trait_idx]:.3f})"
        if feature_selection == "extreme":
            # Check if this was positive or negative loading
            pc1_loading = loadings[trait_idx, 0] if loadings.shape[1] > 0 else 0
            direction = "+" if pc1_loading > 0 else "-"
            subtitle = f"(PC1{direction}, Contrib: {contributions[trait_idx]:.3f})"

        ax.set_title(
            f"{trait_name}\n{subtitle}",
            fontsize=10,
        )
        ax.tick_params(labelsize=8)

    # Remove empty subplots
    for i in range(len(top_indices), len(axes)):
        fig.delaxes(axes[i])

    # Update title based on selection method
    method_desc = {
        "top_variance": "Contributing",
        "extreme": "Extreme Loading",
        "top_absolute": "Highest Absolute Loading",
        "top_contribution": "Contributing to PC1-PC2",
    }
    title = f"UMAP Colored by Top {n_traits} {method_desc.get(feature_selection, 'Contributing')} Traits"
    fig.suptitle(title, fontsize=14)
    plt.tight_layout()

    return fig


def create_umap_single_trait(
    umap_results: Union[np.ndarray, Dict],
    df: pd.DataFrame,
    trait_col: str,
    trait_name: Optional[str] = None,
    color_by: Optional[str] = None,
    figsize: Tuple[float, float] = (8, 6),
    cmap: str = "viridis",
    point_size: int = 30,
    alpha: float = 0.7,
    title: Optional[str] = None,
) -> plt.Figure:
    """Create a single UMAP plot colored by a specific trait.

    This is a simple helper function for plotting UMAP embeddings colored by
    individual traits, useful for inspecting specific trait distributions.

    Args:
        umap_results: 2D UMAP embedding array or dictionary with 'embedding' key.
        df: Original dataframe with trait values.
        trait_col: Column name of the trait to color by.
        trait_name: Display name of trait. If None, uses trait_col.
        color_by: Optional column for categorical coloring (e.g., genotype).
            If provided, creates two subplots: one colored by trait, one by category.
        figsize: Figure size (width, height).
        cmap: Colormap for continuous trait values.
        point_size: Size of scatter plot points.
        alpha: Transparency of points (0-1).
        title: Optional custom title. If None, auto-generates title.

    Returns:
        Matplotlib figure with UMAP plot(s).

    Raises:
        ValueError: If trait_col not in df or if umap_results is invalid.

    Examples:
        >>> # Simple plot colored by single trait
        >>> fig = create_umap_single_trait(umap_results, df, "primary_root_length")
        >>>
        >>> # Plot with genotype overlay
        >>> fig = create_umap_single_trait(
        ...     umap_results, df, "primary_root_length",
        ...     color_by="geno", figsize=(14, 6)
        ... )
    """
    # Extract embedding if umap_results is a dictionary
    if isinstance(umap_results, dict):
        umap_embedding = umap_results["embedding"]
    else:
        umap_embedding = umap_results

    # Validate inputs
    if trait_col not in df.columns:
        raise ValueError(f"Trait column '{trait_col}' not found in dataframe")

    if umap_embedding.shape[0] != len(df):
        raise ValueError(
            f"UMAP embedding has {umap_embedding.shape[0]} samples "
            f"but dataframe has {len(df)} rows"
        )

    # Use trait_col as default display name
    if trait_name is None:
        trait_name = trait_col

    # Determine subplot layout
    if color_by is not None:
        if color_by not in df.columns:
            raise ValueError(f"color_by column '{color_by}' not found in dataframe")
        fig, axes = plt.subplots(1, 2, figsize=figsize)
    else:
        fig, ax = plt.subplots(figsize=figsize)
        axes = [ax]

    # Plot 1: Colored by trait
    ax = axes[0] if len(axes) > 1 else axes[0]
    trait_values = df[trait_col].values

    scatter = ax.scatter(
        umap_embedding[:, 0],
        umap_embedding[:, 1],
        c=trait_values,
        cmap=cmap,
        s=point_size,
        alpha=alpha,
        edgecolors="none",
    )

    # Add colorbar
    cbar = plt.colorbar(scatter, ax=ax)
    cbar.set_label(trait_name, fontsize=10)

    ax.set_xlabel("UMAP 1", fontsize=10)
    ax.set_ylabel("UMAP 2", fontsize=10)

    if title:
        ax.set_title(title, fontsize=12)
    else:
        ax.set_title(f"UMAP colored by {trait_name}", fontsize=12)

    # Plot 2: Colored by category (if requested)
    if color_by is not None:
        ax = axes[1]
        category_values = df[color_by].values

        # Use categorical colors
        unique_cats = np.unique(category_values)
        colors = plt.cm.tab10(np.linspace(0, 1, len(unique_cats)))
        cat_to_color = {cat: colors[i] for i, cat in enumerate(unique_cats)}
        point_colors = [cat_to_color[cat] for cat in category_values]

        ax.scatter(
            umap_embedding[:, 0],
            umap_embedding[:, 1],
            c=point_colors,
            s=point_size,
            alpha=alpha,
            edgecolors="none",
        )

        # Add legend
        handles = [
            plt.Line2D(
                [0],
                [0],
                marker="o",
                color="w",
                markerfacecolor=cat_to_color[cat],
                markersize=8,
                label=cat,
            )
            for cat in unique_cats
        ]
        ax.legend(
            handles=handles, title=color_by, bbox_to_anchor=(1.05, 1), loc="upper left"
        )

        ax.set_xlabel("UMAP 1", fontsize=10)
        ax.set_ylabel("UMAP 2", fontsize=10)
        ax.set_title(f"UMAP colored by {color_by}", fontsize=12)

    plt.tight_layout()
    return fig


def identify_extreme_genotypes_by_pc(
    pca_results: Dict,
    df: pd.DataFrame,
    genotype_col: str = "geno",
    n_components: Optional[int] = None,
    variance_threshold: float = 0.95,
    n_extreme: int = 3,
) -> pd.DataFrame:
    """Identify extreme genotypes based on their median PC scores.

    Args:
        pca_results: Results from perform_pca_analysis.
        df: Original dataframe with genotype information.
        genotype_col: Column name containing genotype information.
        n_components: Number of PCs to consider. If None, use variance threshold.
        variance_threshold: Cumulative variance threshold for PC selection.
        n_extreme: Number of extreme genotypes to identify per PC (both high and low).

    Returns:
        DataFrame with extreme genotypes, their median PC scores, and rankings.
    """
    if genotype_col not in df.columns:
        raise ValueError(f"Genotype column '{genotype_col}' not found in dataframe")

    X_pca = pca_results["transformed_data"]

    # Determine number of components
    if n_components is None:
        cumulative_var = pca_results["cumulative_variance_ratio"]
        n_components = np.argmax(cumulative_var >= variance_threshold) + 1

    n_components = min(n_components, X_pca.shape[1])

    # Create dataframe with PC scores and genotypes
    pc_df = pd.DataFrame()
    pc_df[genotype_col] = df[genotype_col]

    for i in range(n_components):
        pc_df[f"PC{i + 1}"] = X_pca[:, i]

    # Calculate median PC scores by genotype
    median_scores = pc_df.groupby(genotype_col).median()
    counts = pc_df.groupby(genotype_col).size()

    extreme_genotypes = []

    # For each PC, identify extreme genotypes
    for i in range(n_components):
        pc_col = f"PC{i + 1}"
        pc_medians = median_scores[pc_col].sort_values()

        # Get n_extreme lowest and highest genotypes
        low_genotypes = pc_medians.head(n_extreme)
        high_genotypes = pc_medians.tail(n_extreme)

        # Add low extremes
        for rank, (geno, median_score) in enumerate(low_genotypes.items(), 1):
            extreme_genotypes.append(
                {
                    genotype_col: geno,
                    "pc_component": pc_col,
                    "median_pc_score": median_score,
                    "direction": "low",
                    "rank": rank,
                    "n_samples": counts[geno],
                    "explained_variance_ratio": pca_results["explained_variance_ratio"][
                        i
                    ],
                }
            )

        # Add high extremes
        for rank, (geno, median_score) in enumerate(
            high_genotypes.iloc[::-1].items(), 1
        ):
            extreme_genotypes.append(
                {
                    genotype_col: geno,
                    "pc_component": pc_col,
                    "median_pc_score": median_score,
                    "direction": "high",
                    "rank": rank,
                    "n_samples": counts[geno],
                    "explained_variance_ratio": pca_results["explained_variance_ratio"][
                        i
                    ],
                }
            )

    return pd.DataFrame(extreme_genotypes)


def create_pc_genotype_boxplots(
    pca_results: Dict,
    df: pd.DataFrame,
    genotype_col: str = "geno",
    n_components: Optional[int] = None,
    variance_threshold: float = 0.95,
    highlight_extreme: int = 3,
    figsize: Tuple[float, float] = (20, 6),
    title_fontsize: int = 14,
    highlight_genotypes: Optional[list] = None,
) -> plt.Figure:
    """Create boxplots showing PC score distributions by genotype.

    Args:
        pca_results: Results from perform_pca_analysis.
        df: Original dataframe with genotype information.
        genotype_col: Column name containing genotype information.
        n_components: Number of PCs to plot. If None, use variance threshold.
        variance_threshold: Cumulative variance threshold for PC selection.
        highlight_extreme: Number of extreme genotypes to highlight per PC.
        figsize: Figure size.
        title_fontsize: Font size for the main title.
        highlight_genotypes: Optional list of genotype names to highlight in gold.

    Returns:
        Boxplot figure.
    """
    if genotype_col not in df.columns:
        raise ValueError(f"Genotype column '{genotype_col}' not found in dataframe")

    X_pca = pca_results["transformed_data"]

    # Determine number of components
    if n_components is None:
        cumulative_var = pca_results["cumulative_variance_ratio"]
        n_components = np.argmax(cumulative_var >= variance_threshold) + 1

    n_components = min(
        n_components, X_pca.shape[1]
    )  # Use exact number from variance threshold

    # Create dataframe with PC scores and genotypes
    pc_df = pd.DataFrame()
    pc_df[genotype_col] = df[genotype_col]

    for i in range(n_components):
        pc_df[f"PC{i + 1}"] = X_pca[:, i]

    # Create subplots - stack vertically
    n_cols = 1
    n_rows = n_components

    # Make figure height adaptive based on number of components
    # Use provided figsize width, but scale height by number of PCs
    adaptive_height = max(figsize[1], n_components * 3)  # Minimum 3 inches per PC
    adaptive_figsize = (figsize[0], adaptive_height)

    fig, axes = plt.subplots(n_rows, n_cols, figsize=adaptive_figsize)
    if n_components == 1:
        axes = [axes]
    else:
        axes = axes.flatten() if n_rows > 1 else axes

    # Get extreme genotypes
    extreme_df = identify_extreme_genotypes_by_pc(
        pca_results,
        df,
        genotype_col,
        n_components,
        variance_threshold,
        highlight_extreme,
    )

    for i in range(n_components):
        ax = axes[i] if n_components > 1 else axes[0]
        pc_col = f"PC{i + 1}"

        # Get data for this PC
        pc_data = []
        labels = []
        colors = []

        # Get extreme genotypes for this PC
        pc_extremes = extreme_df[extreme_df["pc_component"] == pc_col]
        extreme_genos_high = set(
            pc_extremes[pc_extremes["direction"] == "high"][genotype_col]
        )
        extreme_genos_low = set(
            pc_extremes[pc_extremes["direction"] == "low"][genotype_col]
        )

        # Sort genotypes by median PC score
        genotype_medians = pc_df.groupby(genotype_col)[pc_col].median().sort_values()

        for geno in genotype_medians.index:
            geno_data = pc_df[pc_df[genotype_col] == geno][pc_col].values
            pc_data.append(geno_data)
            labels.append(geno)

            # Color extreme genotypes (takes priority)
            if geno in extreme_genos_high:
                colors.append("darkred")
            elif geno in extreme_genos_low:
                colors.append("darkblue")
            # Color highlighted genotypes
            elif highlight_genotypes and geno in highlight_genotypes:
                colors.append("gold")
            else:
                colors.append("gray")

        # Create boxplot (use tick_labels for newer matplotlib)
        try:
            bp = ax.boxplot(pc_data, tick_labels=labels, patch_artist=True)
        except TypeError:
            # Fallback for older matplotlib versions
            bp = ax.boxplot(pc_data, labels=labels, patch_artist=True)

        # Color the boxes
        for patch, color in zip(bp["boxes"], colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.6)

        # Styling
        ax.set_ylabel(f"{pc_col} Score")
        ax.set_xlabel("Genotype")
        ax.set_title(
            f"{pc_col} ({pca_results['explained_variance_ratio'][i]:.1%} variance)",
            fontsize=12,
        )
        ax.tick_params(axis="x", rotation=90)
        ax.grid(axis="y", alpha=0.3)

        # Make highlighted genotype labels bold and colored
        if highlight_genotypes:
            for tick_label, geno in zip(ax.get_xticklabels(), labels):
                if geno in highlight_genotypes:
                    tick_label.set_fontweight("bold")
                    tick_label.set_color("darkgoldenrod")

    # Remove empty subplots
    for i in range(n_components, len(axes)):
        fig.delaxes(axes[i])

    # Main title
    fig.suptitle(
        f"PC Score Distributions by Genotype "
        + f"(Using {n_components} PCs explaining {pca_results['cumulative_variance_ratio'][n_components - 1]:.1%} variance)",
        fontsize=title_fontsize,
    )

    # Add legend at figure level, positioned at top right
    legend_elements = [
        Patch(facecolor="darkred", alpha=0.6, label=f"Top {highlight_extreme}"),
        Patch(facecolor="darkblue", alpha=0.6, label=f"Bottom {highlight_extreme}"),
        Patch(facecolor="gray", alpha=0.6, label="Other"),
    ]
    if highlight_genotypes:
        legend_elements.insert(
            2, Patch(facecolor="gold", alpha=0.6, label="Highlighted")
        )

    fig.legend(
        handles=legend_elements,
        loc="upper right",
        ncol=1,
        bbox_to_anchor=(0.98, 0.98),
        frameon=True,
        fancybox=False,
        shadow=False,
    )

    plt.tight_layout(rect=[0, 0, 1, 0.96])  # Reduced whitespace at top
    return fig


def create_feature_contribution_heatmap(
    pca_results: Dict,
    n_components: int = 5,
    n_features: int = 20,
    figsize: Tuple[float, float] = (10, 8),
    plot_type: str = "both",  # "variance", "loadings", or "both"
) -> Union[plt.Figure, Tuple[plt.Figure, plt.Figure]]:
    """Create heatmaps showing feature loadings and/or variance contributions to PCs.

    Args:
        pca_results: Results from perform_pca_analysis containing loadings and eigenvalues.
        n_components: Number of components to show.
        n_features: Number of top features to show.
        figsize: Figure size for each plot.
        plot_type: Type of plot to create:
            - "variance": Only variance contribution heatmap
            - "loadings": Only raw loadings heatmap
            - "both": Return tuple of (variance_fig, loadings_fig)

    Returns:
        Single figure if plot_type is "variance" or "loadings",
        tuple of (variance_fig, loadings_fig) if plot_type is "both".
    """
    # Get necessary data from pca_results
    if "loadings" not in pca_results or "eigenvalues" not in pca_results:
        raise ValueError("pca_results must contain 'loadings' and 'eigenvalues' keys")

    loadings = pca_results["loadings"]
    eigenvalues = pca_results["eigenvalues"]
    feature_names = pca_results.get(
        "feature_names", [f"Feature_{i}" for i in range(loadings.shape[0])]
    )

    # Determine number of components to show
    n_comp_available = min(loadings.shape[1], len(eigenvalues))
    n_comp_to_show = min(n_components, n_comp_available)

    # Calculate total variance contributions for feature selection
    variance_contributions = np.zeros(loadings.shape[0])
    for i in range(n_comp_to_show):
        variance_contributions += eigenvalues[i] * loadings[:, i] ** 2

    # Get top features by total variance contribution
    top_indices = np.argsort(variance_contributions)[::-1][:n_features]
    top_feature_names = [feature_names[i] for i in top_indices]
    n_features_actual = len(top_feature_names)  # Actual number of features plotted

    # Helper function to create a heatmap
    def create_heatmap(data, title, cbar_label):
        fig, ax = plt.subplots(figsize=figsize)

        sns.heatmap(
            data,
            cmap="RdBu_r",
            center=0,
            fmt=".3f",
            cbar_kws={"label": cbar_label},
            ax=ax,
            annot=True,
            annot_kws={"size": 8},
        )

        ax.set_title(title)
        ax.set_xlabel("Principal Component")
        ax.set_ylabel("Feature")

        plt.tight_layout()
        return fig

    # Create variance contribution heatmap
    if plot_type in ["variance", "both"]:
        # Calculate variance contributions for selected features
        variance_data = {}
        for i in range(n_comp_to_show):
            contributions = [
                eigenvalues[i] * loadings[idx, i] ** 2 for idx in top_indices
            ]
            variance_data[f"PC{i + 1}"] = contributions

        variance_df = pd.DataFrame(variance_data, index=top_feature_names)

        variance_fig = create_heatmap(
            variance_df,
            f"Top {n_features_actual} Feature Variance Contributions to First {n_comp_to_show} PCs",
            "Variance Contribution",
        )

    # Create raw loadings heatmap
    if plot_type in ["loadings", "both"]:
        # Extract raw loadings for selected features
        loadings_data = {}
        for i in range(n_comp_to_show):
            # Use raw loadings (eigenvectors) - these show correlations
            loadings_data[f"PC{i + 1}"] = [loadings[idx, i] for idx in top_indices]

        loadings_df = pd.DataFrame(loadings_data, index=top_feature_names)

        loadings_fig = create_heatmap(
            loadings_df,
            f"Top {n_features_actual} Feature Loadings (Correlations) for First {n_comp_to_show} PCs",
            "Loading (Correlation)",
        )

    # Return appropriate figure(s)
    if plot_type == "variance":
        return variance_fig
    elif plot_type == "loadings":
        return loadings_fig
    else:  # both
        return variance_fig, loadings_fig


def create_publication_figure(
    fig: Union[plt.Figure, "go.Figure"],
    output_path: Union[str, Path],
    dpi: int = 300,
    format: str = "pdf",
    transparent: bool = False,
    bbox_inches: str = "tight",
) -> None:
    """Save a figure in publication-ready format.

    Args:
        fig: Figure to save (matplotlib or plotly).
        output_path: Output file path.
        dpi: Resolution for raster formats.
        format: Output format ('pdf', 'eps', 'png', 'svg').
        transparent: Whether to use transparent background.
        bbox_inches: Bbox setting for matplotlib figures.

    Raises:
        ValueError: If figure type is not supported.
    """
    output_path = Path(output_path)

    if isinstance(fig, plt.Figure):
        # Save matplotlib figure
        fig.savefig(
            output_path,
            dpi=dpi,
            format=format,
            transparent=transparent,
            bbox_inches=bbox_inches,
        )
    elif go is not None and hasattr(fig, "write_image"):
        # Save plotly figure if plotly is available
        if format == "pdf":
            fig.write_image(str(output_path), format="pdf")
        elif format == "png":
            fig.write_image(str(output_path), format="png", scale=dpi / 100)
        elif format == "svg":
            fig.write_image(str(output_path), format="svg")
        else:
            fig.write_html(str(output_path.with_suffix(".html")))
    else:
        raise ValueError("Unsupported figure type")


def identify_extreme_phenotypes(
    df: pd.DataFrame,
    trait_cols: List[str],
    group_col: str = "geno",
    n_std: float = 2.0,
    min_samples_per_group: int = 3,
) -> Dict[str, pd.DataFrame]:
    """Identify genotypes with extreme phenotypes for each trait.

    Args:
        df: DataFrame containing trait data.
        trait_cols: List of trait columns to analyze.
        group_col: Column to group by (e.g., 'geno').
        n_std: Number of standard deviations to consider extreme.
        min_samples_per_group: Minimum samples required per group.

    Returns:
        Dictionary mapping trait names to DataFrames of extreme genotypes.
        Each DataFrame contains columns: mean, std, count, deviation, direction.
    """
    extreme_phenotypes = {}

    # Return empty dict for empty DataFrame
    if df.empty or not trait_cols:
        return extreme_phenotypes

    for trait in trait_cols:
        if trait not in df.columns:
            continue

        # Calculate group means
        group_stats = df.groupby(group_col)[trait].agg(["mean", "std", "count"])

        # Filter groups with enough samples
        valid_groups = group_stats[group_stats["count"] >= min_samples_per_group]

        if len(valid_groups) > 0:
            # Calculate overall mean and std
            overall_mean = df[trait].mean()
            overall_std = df[trait].std()

            if pd.notna(overall_std) and overall_std > 0:
                # Identify extreme groups
                high_threshold = overall_mean + n_std * overall_std
                low_threshold = overall_mean - n_std * overall_std

                extreme_groups = valid_groups[
                    (valid_groups["mean"] > high_threshold)
                    | (valid_groups["mean"] < low_threshold)
                ].copy()

                if len(extreme_groups) > 0:
                    extreme_groups["deviation"] = (
                        extreme_groups["mean"] - overall_mean
                    ) / overall_std
                    extreme_groups["direction"] = extreme_groups["deviation"].apply(
                        lambda x: "high" if x > 0 else "low"
                    )
                    extreme_groups = extreme_groups.sort_values(
                        "deviation", key=lambda x: abs(x), ascending=False
                    )
                    extreme_phenotypes[trait] = extreme_groups

    return extreme_phenotypes


def create_phenotype_variation_plot(
    df: pd.DataFrame,
    trait: str,
    group_col: str = "geno",
    highlight_extreme: bool = True,
    n_std: float = 2.0,
    point_size: float = 50,
    figsize: Tuple[float, float] = (12, 8),
    output_csv_path: Optional[Path] = None,
) -> Tuple[plt.Figure, pd.DataFrame]:
    """Create a box plot with jittered points showing phenotypic variation across groups.

    Args:
        df: DataFrame containing trait data.
        trait: Trait column name.
        group_col: Column to group by.
        highlight_extreme: Whether to highlight extreme phenotypes.
        n_std: Number of standard deviations for extreme threshold.
        point_size: Size of the jittered points.
        figsize: Figure size.
        output_csv_path: If provided, save plot data to this CSV file.

    Returns:
        Tuple of (Figure, DataFrame with plot data).
    """
    fig, ax = plt.subplots(1, 1, figsize=figsize)

    # Prepare data
    df_plot = df[[group_col, trait]].dropna()

    # Calculate group statistics for ordering
    group_stats = df_plot.groupby(group_col)[trait].agg(["mean", "std", "count"])
    group_stats = group_stats.sort_values("mean")
    group_order = group_stats.index.tolist()

    # Create position mapping
    positions = {g: i for i, g in enumerate(group_order)}

    # Prepare data for box plot
    plot_data = [df_plot[df_plot[group_col] == g][trait].values for g in group_order]

    # Create box plot
    bp = ax.boxplot(
        plot_data,
        positions=list(range(len(group_order))),
        widths=0.6,
        patch_artist=True,
        showfliers=False,  # Don't show outliers as we'll plot all points
    )

    # Style the box plot
    for patch in bp["boxes"]:
        patch.set_facecolor("lightblue")
        patch.set_alpha(0.7)

    # Add jittered points
    np.random.seed(42)  # For reproducibility
    for i, group in enumerate(group_order):
        group_data = df_plot[df_plot[group_col] == group][trait].values
        n_points = len(group_data)

        # Create jitter
        jitter = np.random.uniform(-0.2, 0.2, n_points)
        x_positions = np.full(n_points, i) + jitter

        # Plot points
        ax.scatter(
            x_positions,
            group_data,
            alpha=0.6,
            s=point_size,
            color="darkblue",
            edgecolors="black",
            linewidth=0.5,
            zorder=10,
        )

    # Add mean ± std lines
    for i, (group, stats) in enumerate(group_stats.iterrows()):
        mean = stats["mean"]
        std = stats["std"]

        # Plot mean line
        ax.plot([i - 0.25, i + 0.25], [mean, mean], "r-", linewidth=2, zorder=15)

        # Plot std range if not NaN
        if pd.notna(std):
            ax.plot([i, i], [mean - std, mean + std], "r-", linewidth=1.5, zorder=15)
            ax.plot(
                [i - 0.1, i + 0.1],
                [mean + std, mean + std],
                "r-",
                linewidth=1.5,
                zorder=15,
            )
            ax.plot(
                [i - 0.1, i + 0.1],
                [mean - std, mean - std],
                "r-",
                linewidth=1.5,
                zorder=15,
            )

    # Calculate overall statistics
    overall_mean = df_plot[trait].mean()
    overall_std = df_plot[trait].std()

    # Highlight extreme phenotypes
    if highlight_extreme and pd.notna(overall_std) and overall_std > 0:
        high_threshold = overall_mean + n_std * overall_std
        low_threshold = overall_mean - n_std * overall_std

        # Color boxes based on mean values
        for i, (group, stats) in enumerate(group_stats.iterrows()):
            if stats["mean"] > high_threshold:
                bp["boxes"][i].set_facecolor("red")
                bp["boxes"][i].set_alpha(0.6)
            elif stats["mean"] < low_threshold:
                bp["boxes"][i].set_facecolor("blue")
                bp["boxes"][i].set_alpha(0.6)

        # Add threshold lines
        ax.axhline(
            y=high_threshold,
            color="red",
            linestyle="--",
            alpha=0.5,
            label=f"+{n_std} SD",
        )
        ax.axhline(
            y=low_threshold,
            color="blue",
            linestyle="--",
            alpha=0.5,
            label=f"-{n_std} SD",
        )
        ax.axhline(
            y=overall_mean,
            color="black",
            linestyle="-",
            alpha=0.3,
            label="Overall mean",
        )

    # Set labels and title
    ax.set_xticks(list(range(len(group_order))))
    ax.set_xticklabels(group_order, rotation=90, ha="center")
    ax.set_xlabel(group_col.capitalize())
    ax.set_ylabel(trait)

    # Add custom legend
    legend_elements = [
        Line2D([0], [0], color="r", linewidth=2, label="Mean ± SD"),
        Line2D(
            [0],
            [0],
            marker="o",
            color="w",
            markerfacecolor="darkblue",
            markersize=8,
            alpha=0.6,
            label="Individual observations",
        ),
    ]

    if highlight_extreme and pd.notna(overall_std) and overall_std > 0:
        legend_elements.extend(
            [
                Line2D(
                    [0],
                    [0],
                    color="red",
                    linestyle="--",
                    alpha=0.5,
                    label=f"+{n_std} SD",
                ),
                Line2D(
                    [0],
                    [0],
                    color="blue",
                    linestyle="--",
                    alpha=0.5,
                    label=f"-{n_std} SD",
                ),
                Line2D(
                    [0],
                    [0],
                    color="black",
                    linestyle="-",
                    alpha=0.3,
                    label="Overall mean",
                ),
            ]
        )

    ax.legend(handles=legend_elements, loc="best")
    ax.grid(True, alpha=0.3, axis="y")

    # Leave space at top for suptitle
    plt.tight_layout(rect=[0, 0, 1, 0.96])

    # Create DataFrame with plot data
    plot_data_list = []
    for group in group_order:
        group_data = df_plot[df_plot[group_col] == group][trait].values
        for value in group_data:
            plot_data_list.append(
                {
                    group_col: group,
                    trait: value,
                    f"{trait}_mean": group_stats.loc[group, "mean"],
                    f"{trait}_std": group_stats.loc[group, "std"],
                    f"{trait}_count": group_stats.loc[group, "count"],
                }
            )

    plot_df = pd.DataFrame(plot_data_list)

    # Add overall statistics
    plot_df[f"{trait}_overall_mean"] = overall_mean
    plot_df[f"{trait}_overall_std"] = overall_std

    if highlight_extreme and pd.notna(overall_std) and overall_std > 0:
        plot_df[f"{trait}_high_threshold"] = overall_mean + n_std * overall_std
        plot_df[f"{trait}_low_threshold"] = overall_mean - n_std * overall_std

    # Save to CSV if path provided
    if output_csv_path:
        output_csv_path = Path(output_csv_path)
        plot_df.to_csv(output_csv_path, index=False)

    return fig, plot_df


def create_genotype_image_grid(
    df: pd.DataFrame,
    image_links: Dict[str, Dict[str, Path]],
    genotype: str,
    genotype_col: str = "geno",
    barcode_col: str = "Barcode",
    image_type: str = "features.png",
    n_cols: int = 4,
    figsize: Optional[Tuple[float, float]] = None,
    show_labels: bool = True,
    label_fontsize: int = 10,
    title_fontsize: int = 14,
    show_stats: bool = True,
    trait_cols: Optional[List[str]] = None,
    max_images: Optional[int] = None,
) -> plt.Figure:
    """Create a publication-ready grid of plant images for a specific genotype.

    This function displays all plant images for samples matching a specified genotype
    in a clean matplotlib grid layout, suitable for publications and presentations.

    Args:
        df: DataFrame with trait data and sample metadata.
        image_links: Dictionary mapping barcode to image paths, typically from
            link_rhizovision_images_to_samples().
        genotype: Name of the genotype to display.
        genotype_col: Column name containing genotype identifiers (default: "geno").
        barcode_col: Column name containing sample barcodes (default: "Barcode").
        image_type: Type of image to display (default: "features.png").
            Options: "features.png", "seg.png", or other types in image_links.
        n_cols: Number of columns in the grid (default: 4).
        figsize: Figure size as (width, height). If None, calculated automatically.
        show_labels: Whether to show barcode labels below images (default: True).
        label_fontsize: Font size for image labels (default: 10).
        title_fontsize: Font size for figure title (default: 14).
        show_stats: Whether to show trait statistics in title (default: True).
        trait_cols: List of trait columns to compute statistics for (default: None).
            If None and show_stats=True, uses first 3 numeric columns.
        max_images: Maximum number of images to display (default: None = all).

    Returns:
        matplotlib Figure object containing the image grid.

    Raises:
        ValueError: If genotype not found in dataframe or required columns missing.
        FileNotFoundError: If no valid images found for the genotype.

    Example:
        >>> from sleap_roots_analyze.data_utils import link_rhizovision_images_to_samples
        >>> image_links = link_rhizovision_images_to_samples(df, "path/to/images")
        >>> fig = create_genotype_image_grid(
        ...     df=df_traits,
        ...     image_links=image_links,
        ...     genotype="Genotype_A",
        ...     n_cols=4,
        ...     show_labels=True,
        ...     trait_cols=["primary_root_length", "lateral_root_count"]
        ... )
        >>> fig.savefig("genotype_A_samples.pdf", dpi=300, bbox_inches='tight')
    """
    # Import PIL here to avoid requiring it as a hard dependency
    try:
        from PIL import Image
    except ImportError:
        raise ImportError(
            "PIL (Pillow) is required for image display. "
            "Install with: pip install Pillow"
        )

    # Validate required columns
    if genotype_col not in df.columns:
        raise ValueError(
            f"Genotype column '{genotype_col}' not found. "
            f"Available: {df.columns.tolist()}"
        )
    if barcode_col not in df.columns:
        raise ValueError(
            f"Barcode column '{barcode_col}' not found. "
            f"Available: {df.columns.tolist()}"
        )

    # Filter dataframe for specified genotype
    df_geno = df[df[genotype_col] == genotype].copy()

    if len(df_geno) == 0:
        raise ValueError(
            f"No samples found for genotype '{genotype}'. "
            f"Available genotypes: {df[genotype_col].unique().tolist()}"
        )

    # Collect valid image paths and corresponding barcodes
    valid_images = []
    valid_barcodes = []

    for idx, row in df_geno.iterrows():
        barcode = row[barcode_col]

        # Check if barcode exists in image_links
        if barcode not in image_links:
            continue

        # Check if image type exists for this barcode
        if image_type not in image_links[barcode]:
            continue

        img_path = image_links[barcode][image_type]

        # Check if path is valid and file exists
        if img_path is not None and Path(img_path).exists():
            valid_images.append(img_path)
            valid_barcodes.append(barcode)

            # Stop if we've reached max_images
            if max_images is not None and len(valid_images) >= max_images:
                break

    if len(valid_images) == 0:
        raise FileNotFoundError(
            f"No valid images found for genotype '{genotype}'. "
            f"Checked {len(df_geno)} samples for image type '{image_type}'."
        )

    # Limit to max_images if specified
    n_images = len(valid_images)

    # Calculate grid dimensions
    n_rows = int(np.ceil(n_images / n_cols))

    # Calculate figure size if not provided
    if figsize is None:
        # Base size per image (width, height) in inches
        img_width = 2.5
        img_height = 2.5
        figsize = (n_cols * img_width, n_rows * img_height + 1)  # +1 for title

    # Create figure and axes
    fig, axes = plt.subplots(n_rows, n_cols, figsize=figsize)

    # Handle single row/col cases
    if n_rows == 1 and n_cols == 1:
        axes = np.array([[axes]])
    elif n_rows == 1:
        axes = axes.reshape(1, -1)
    elif n_cols == 1:
        axes = axes.reshape(-1, 1)

    # Flatten axes for easier iteration
    axes_flat = axes.flatten()

    # Load and display images
    for i, (img_path, barcode) in enumerate(zip(valid_images, valid_barcodes)):
        ax = axes_flat[i]

        try:
            # Load image
            img = Image.open(img_path)

            # Display image
            ax.imshow(img)

            # Add label if requested
            if show_labels:
                ax.set_xlabel(barcode, fontsize=label_fontsize)

            # Remove ticks
            ax.set_xticks([])
            ax.set_yticks([])

            # Keep box for clean look
            for spine in ax.spines.values():
                spine.set_edgecolor("gray")
                spine.set_linewidth(0.5)

        except Exception as e:
            # Handle image loading errors
            ax.text(
                0.5,
                0.5,
                f"Error loading\n{barcode}",
                ha="center",
                va="center",
                fontsize=8,
                color="red",
            )
            ax.set_xticks([])
            ax.set_yticks([])

    # Hide unused subplots
    for i in range(n_images, len(axes_flat)):
        axes_flat[i].axis("off")

    # Create title
    title_parts = [f"Genotype: {genotype} (n={n_images})"]

    # Add trait statistics if requested
    if show_stats and len(df_geno) > 0:
        # Determine which traits to show
        if trait_cols is None:
            # Use first 3 numeric columns (excluding barcode/genotype)
            numeric_cols = df_geno.select_dtypes(include=[np.number]).columns
            exclude_cols = [genotype_col, barcode_col, "rep", "replicate"]
            trait_cols = [col for col in numeric_cols if col not in exclude_cols][:3]

        if trait_cols:
            stats_parts = []
            for trait in trait_cols:
                if trait in df_geno.columns:
                    trait_vals = df_geno[trait].dropna()
                    if len(trait_vals) > 0:
                        mean_val = trait_vals.mean()
                        std_val = trait_vals.std()
                        stats_parts.append(f"{trait}: {mean_val:.2f} ± {std_val:.2f}")

            if stats_parts:
                title_parts.append(" | ".join(stats_parts))

    fig.suptitle("\n".join(title_parts), fontsize=title_fontsize, y=0.98)

    plt.tight_layout(rect=[0, 0, 1, 0.98])

    return fig


def create_regression_plot(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    color_by: Optional[str] = None,
    figsize: Tuple[int, int] = (8, 8),
    title: Optional[str] = None,
    scatter_kws: Optional[Dict] = None,
    line_kws: Optional[Dict] = None,
) -> plt.Figure:
    """Create publication-quality linear regression plot with statistical annotations.

    Generates a scatter plot with linear regression line, confidence interval,
    and statistical annotations including R², p-value, Pearson correlation,
    and regression equation.

    Args:
        df: DataFrame containing the data
        x_col: Column name for x-axis (independent variable)
        y_col: Column name for y-axis (dependent variable)
        color_by: Optional column name for coloring points by category.
            If provided, points are colored by group but a single regression
            line is fitted to all data.
        figsize: Figure size as (width, height) in inches. Default (8, 8).
        title: Optional custom title. If None, auto-generates from column names.
        scatter_kws: Optional dict of kwargs passed to scatter plot
            (e.g., {'s': 50, 'alpha': 0.6})
        line_kws: Optional dict of kwargs passed to regression line
            (e.g., {'color': 'red', 'linewidth': 2})

    Returns:
        matplotlib Figure object with regression plot

    Raises:
        ValueError: If columns don't exist, are non-numeric, have zero variance,
            or insufficient samples (<3) after NaN removal

    Examples:
        >>> # Simple regression plot
        >>> fig = create_regression_plot(
        ...     df,
        ...     x_col='Surface Area (mm²)',
        ...     y_col='Root Biomass (mg)'
        ... )
        >>> fig.savefig('regression_biomass_surface.png', dpi=300)
        >>>
        >>> # Regression with color by genotype
        >>> fig = create_regression_plot(
        ...     df,
        ...     x_col='Shoot Biomass (mg)',
        ...     y_col='Root Biomass (mg)',
        ...     color_by='Genotype',
        ...     title='Root vs Shoot Biomass'
        ... )
    """
    from scipy import stats as scipy_stats
    import warnings

    # Input validation: check columns exist
    missing_cols = []
    for col in [x_col, y_col]:
        if col not in df.columns:
            missing_cols.append(col)
    if color_by and color_by not in df.columns:
        missing_cols.append(color_by)

    if missing_cols:
        raise ValueError(f"Column(s) not found in DataFrame: {', '.join(missing_cols)}")

    # Check numeric types
    if not pd.api.types.is_numeric_dtype(df[x_col]):
        raise ValueError(
            f"Column '{x_col}' must be numeric. "
            f"For categorical variables, use the color_by parameter."
        )
    if not pd.api.types.is_numeric_dtype(df[y_col]):
        raise ValueError(
            f"Column '{y_col}' must be numeric. "
            f"For categorical variables, use the color_by parameter."
        )

    # Handle NaN values
    plot_df = df[[x_col, y_col]].copy()
    if color_by:
        plot_df[color_by] = df[color_by]

    initial_count = len(plot_df)
    plot_df = plot_df.dropna(subset=[x_col, y_col])
    final_count = len(plot_df)

    if final_count < 3:
        raise ValueError(
            f"Insufficient samples for regression analysis. "
            f"Need at least 3 valid samples, got {final_count} "
            f"(after removing {initial_count - final_count} NaN values)"
        )

    # Warn if >20% data dropped
    pct_dropped = (initial_count - final_count) / initial_count * 100
    if pct_dropped > 20:
        warnings.warn(
            f"Dropped {pct_dropped:.1f}% of data ({initial_count - final_count}/{initial_count} samples) "
            f"due to NaN values in '{x_col}' or '{y_col}'",
            UserWarning,
        )

    # Check for zero variance
    x_vals = plot_df[x_col].values
    y_vals = plot_df[y_col].values

    if np.std(x_vals) == 0:
        raise ValueError(
            f"Column '{x_col}' has zero variance (all values are {x_vals[0]}). "
            f"Cannot perform regression analysis."
        )
    if np.std(y_vals) == 0:
        raise ValueError(
            f"Column '{y_col}' has zero variance (all values are {y_vals[0]}). "
            f"Cannot perform regression analysis."
        )

    # Calculate statistics
    pearson_r, pearson_p = scipy_stats.pearsonr(x_vals, y_vals)
    r_squared = pearson_r**2
    slope, intercept, _, _, _ = scipy_stats.linregress(x_vals, y_vals)

    # Create figure
    fig, ax = plt.subplots(figsize=figsize)

    # Default scatter/line kwargs
    default_scatter_kws = {"s": 50, "alpha": 0.7}
    default_line_kws = {"color": "#2E6E73", "linewidth": 2}

    if scatter_kws:
        default_scatter_kws.update(scatter_kws)
    if line_kws:
        default_line_kws.update(line_kws)

    # Plot scatter points with optional coloring
    if color_by:
        # Color by groups but fit single regression
        unique_groups = plot_df[color_by].unique()

        # Warn if too many categories
        if len(unique_groups) > 20:
            warnings.warn(
                f"Color_by column '{color_by}' has {len(unique_groups)} unique values. "
                f"Legend may be difficult to read.",
                UserWarning,
            )

        # Use seaborn color palette for consistency
        palette = sns.color_palette("tab10", n_colors=len(unique_groups))
        color_map = dict(zip(unique_groups, palette))

        for group in unique_groups:
            mask = plot_df[color_by] == group
            ax.scatter(
                x_vals[mask],
                y_vals[mask],
                label=str(group),
                color=color_map[group],
                s=default_scatter_kws["s"],
                alpha=default_scatter_kws["alpha"],
            )
        ax.legend(title=color_by, bbox_to_anchor=(1.05, 1), loc="upper left")
    else:
        # Single color scatter
        scatter_color = default_scatter_kws.pop("color", "#4CB391")
        ax.scatter(x_vals, y_vals, color=scatter_color, **default_scatter_kws)

    # Add regression line with confidence interval using seaborn
    sns.regplot(
        x=x_vals,
        y=y_vals,
        ax=ax,
        scatter=False,  # Already plotted scatter
        line_kws=default_line_kws,
        ci=95,  # 95% confidence interval
    )

    # Add statistical annotations
    # Format p-value
    if pearson_p < 0.001:
        p_text = "p < 0.001"
    else:
        p_text = f"p = {pearson_p:.3f}"

    # Format regression equation
    if intercept >= 0:
        equation = f"y = {slope:.3f}x + {intercept:.3f}"
    else:
        equation = f"y = {slope:.3f}x - {abs(intercept):.3f}"

    # Create annotation text box
    stats_text = (
        f"R = {pearson_r:.3f}\n"
        f"R² = {r_squared:.3f}\n"
        f"{p_text}\n"
        f"{equation}\n"
        f"n = {final_count}"
    )

    # Position text box in upper left corner
    ax.text(
        0.05,
        0.95,
        stats_text,
        transform=ax.transAxes,
        fontsize=10,
        verticalalignment="top",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.8, edgecolor="gray"),
    )

    # Labels and title
    ax.set_xlabel(x_col, fontsize=12)
    ax.set_ylabel(y_col, fontsize=12)

    if title:
        ax.set_title(title, fontsize=14)
    else:
        ax.set_title(f"{y_col} vs {x_col}", fontsize=14)

    plt.tight_layout()

    return fig
