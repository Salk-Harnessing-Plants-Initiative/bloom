"""
Cross-Experiment Correlation Analysis Module.

Core statistical functions for computing correlations between traits
measured across different experimental conditions (e.g., cylinder vs turface).

Used by correlation_tools.py (LangChain tool wrappers).
"""

import numpy as np
import pandas as pd
from scipy import stats as sp_stats
from scipy.cluster.hierarchy import fcluster, linkage
from pathlib import Path


def load_and_align_experiments(
    path1,
    path2,
    genotype_col1="Geno",
    genotype_col2="geno",
    rep_col1="Rep",
    rep_col2="rep",
):
    """Load two experiment CSVs and align them by shared genotypes.

    Args:
        path1: Path to first experiment CSV
        path2: Path to second experiment CSV
        genotype_col1: Genotype column name in experiment 1
        genotype_col2: Genotype column name in experiment 2
        rep_col1: Replicate column name in experiment 1
        rep_col2: Replicate column name in experiment 2

    Returns:
        (exp1_df, exp2_df, common_genotypes) with standardized column names
        'genotype' and 'replicate'.
    """
    df1 = pd.read_csv(path1)
    df2 = pd.read_csv(path2)

    # Standardize column names
    df1 = df1.rename(columns={genotype_col1: "genotype", rep_col1: "replicate"})
    df2 = df2.rename(columns={genotype_col2: "genotype", rep_col2: "replicate"})

    # Find common genotypes
    genos1 = set(df1["genotype"].dropna().unique())
    genos2 = set(df2["genotype"].dropna().unique())
    common = sorted(genos1 & genos2)

    # Filter to common genotypes
    df1 = df1[df1["genotype"].isin(common)].copy()
    df2 = df2[df2["genotype"].isin(common)].copy()

    return df1, df2, common


def calculate_genotype_means(df, trait_cols, genotype_col="genotype"):
    """Calculate mean trait values per genotype.

    Args:
        df: DataFrame with genotype column and trait columns
        trait_cols: List of trait column names to average
        genotype_col: Name of the genotype column

    Returns:
        DataFrame indexed by genotype with mean trait values.
    """
    return df.groupby(genotype_col)[trait_cols].mean()


def calculate_cross_experiment_correlations(
    exp1_means, exp2_means, exp1_traits, exp2_traits, min_samples=3
):
    """Calculate Pearson correlations between all trait pairs across experiments.

    Args:
        exp1_means: Genotype means for experiment 1 (indexed by genotype)
        exp2_means: Genotype means for experiment 2 (indexed by genotype)
        exp1_traits: List of trait names in experiment 1
        exp2_traits: List of trait names in experiment 2
        min_samples: Minimum number of shared genotypes required

    Returns:
        DataFrame with columns: exp1_trait, exp2_trait, correlation, p_value,
        n_samples, significant, highly_significant. Sorted by |correlation| descending.
    """
    common_idx = exp1_means.index.intersection(exp2_means.index)
    if len(common_idx) < min_samples:
        return pd.DataFrame()

    results = []
    for t1 in exp1_traits:
        for t2 in exp2_traits:
            x = exp1_means.loc[common_idx, t1].dropna()
            y = exp2_means.loc[common_idx, t2].dropna()
            shared = x.index.intersection(y.index)
            if len(shared) < min_samples:
                continue
            r, p = sp_stats.pearsonr(x.loc[shared], y.loc[shared])
            results.append({
                "exp1_trait": t1,
                "exp2_trait": t2,
                "correlation": r,
                "p_value": p,
                "n_samples": len(shared),
                "significant": p < 0.05,
                "highly_significant": p < 0.01,
            })

    df = pd.DataFrame(results)
    if len(df) > 0:
        df = df.sort_values("correlation", key=abs, ascending=False).reset_index(drop=True)
    return df


def identify_significant_correlations(corr_df, p_threshold=0.05, r_threshold=0.3, use_fdr=True):
    """Filter correlations to significant ones, optionally with FDR correction.

    Args:
        corr_df: Output of calculate_cross_experiment_correlations
        p_threshold: P-value threshold
        r_threshold: Minimum |r| threshold
        use_fdr: Whether to apply Benjamini-Hochberg FDR correction

    Returns:
        Filtered DataFrame of significant correlations.
    """
    if len(corr_df) == 0:
        return corr_df

    df = corr_df.copy()

    if use_fdr:
        from scipy.stats import false_discovery_control
        try:
            rejected = false_discovery_control(df["p_value"].values, alpha=p_threshold)
            df["fdr_significant"] = rejected
        except Exception:
            # Fallback: manual BH correction
            n = len(df)
            sorted_idx = np.argsort(df["p_value"].values)
            p_sorted = df["p_value"].values[sorted_idx]
            threshold = np.arange(1, n + 1) / n * p_threshold
            fdr_sig = np.zeros(n, dtype=bool)
            for i in range(n - 1, -1, -1):
                if p_sorted[i] <= threshold[i]:
                    fdr_sig[sorted_idx[:i + 1]] = True
                    break
            df["fdr_significant"] = fdr_sig

        df = df[df["fdr_significant"] & (df["correlation"].abs() >= r_threshold)]
    else:
        df = df[(df["p_value"] < p_threshold) & (df["correlation"].abs() >= r_threshold)]

    return df.reset_index(drop=True)


def summarize_correlation_results(corr_df, exp1_name="exp1", exp2_name="exp2"):
    """Generate a summary dict of correlation results.

    Args:
        corr_df: Output of calculate_cross_experiment_correlations
        exp1_name: Name of experiment 1
        exp2_name: Name of experiment 2

    Returns:
        Dict with summary statistics.
    """
    return {
        "exp1_name": exp1_name,
        "exp2_name": exp2_name,
        "total_correlations": len(corr_df),
        "significant_correlations": int(corr_df["significant"].sum()) if len(corr_df) > 0 else 0,
        "highly_significant_correlations": int(corr_df["highly_significant"].sum()) if len(corr_df) > 0 else 0,
        "mean_abs_correlation": float(corr_df["correlation"].abs().mean()) if len(corr_df) > 0 else 0,
        "max_correlation": float(corr_df["correlation"].max()) if len(corr_df) > 0 else 0,
        "min_correlation": float(corr_df["correlation"].min()) if len(corr_df) > 0 else 0,
    }


def calculate_per_trait_correlations(exp1_df, exp2_df, trait1, trait2, genotype_col="genotype"):
    """Calculate Pearson and Spearman correlations for a specific trait pair.

    Args:
        exp1_df: Experiment 1 DataFrame
        exp2_df: Experiment 2 DataFrame
        trait1: Trait name from experiment 1
        trait2: Trait name from experiment 2
        genotype_col: Genotype column name

    Returns:
        Dict with pearson_r, pearson_p, spearman_r, spearman_p, n_genotypes, valid.
    """
    means1 = exp1_df.groupby(genotype_col)[trait1].mean()
    means2 = exp2_df.groupby(genotype_col)[trait2].mean()

    common = means1.index.intersection(means2.index)
    x = means1.loc[common].dropna()
    y = means2.loc[common].dropna()
    shared = x.index.intersection(y.index)

    if len(shared) < 3:
        return {"valid": False, "n_genotypes": len(shared)}

    x_vals = x.loc[shared]
    y_vals = y.loc[shared]

    pr, pp = sp_stats.pearsonr(x_vals, y_vals)
    sr, sp = sp_stats.spearmanr(x_vals, y_vals)

    return {
        "valid": True,
        "pearson_r": float(pr),
        "pearson_p": float(pp),
        "spearman_r": float(sr),
        "spearman_p": float(sp),
        "n_genotypes": len(shared),
    }


# ============================================================================
# Plotting functions
# ============================================================================

def create_correlation_summary_plot(corr_df):
    """Create a summary plot of all cross-experiment correlations.

    Args:
        corr_df: Output of calculate_cross_experiment_correlations

    Returns:
        matplotlib Figure
    """
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Distribution of correlations
    axes[0].hist(corr_df["correlation"], bins=30, edgecolor="black", alpha=0.7, color="#4C78A8")
    axes[0].axvline(0, color="red", linestyle="--", alpha=0.5)
    axes[0].set_xlabel("Pearson r")
    axes[0].set_ylabel("Count")
    axes[0].set_title("Distribution of Cross-Experiment Correlations")

    # Volcano plot: r vs -log10(p)
    neg_log_p = -np.log10(corr_df["p_value"].clip(lower=1e-300))
    colors = ["#E45756" if s else "#72B7B2" for s in corr_df["significant"]]
    axes[1].scatter(corr_df["correlation"], neg_log_p, c=colors, alpha=0.5, s=20)
    axes[1].axhline(-np.log10(0.05), color="gray", linestyle="--", alpha=0.5, label="p=0.05")
    axes[1].set_xlabel("Pearson r")
    axes[1].set_ylabel("-log10(p-value)")
    axes[1].set_title("Volcano Plot")
    axes[1].legend()

    fig.tight_layout()
    return fig


def create_joint_plot(
    exp1_means, exp2_means, trait1, trait2,
    exp1_name="exp1", exp2_name="exp2", **kwargs
):
    """Create a scatter plot with regression line for a trait pair.

    Args:
        exp1_means: Genotype means for experiment 1
        exp2_means: Genotype means for experiment 2
        trait1: Trait from experiment 1
        trait2: Trait from experiment 2
        exp1_name: Display name for experiment 1
        exp2_name: Display name for experiment 2

    Returns:
        matplotlib Figure
    """
    import matplotlib.pyplot as plt

    common = exp1_means.index.intersection(exp2_means.index)
    x = exp1_means.loc[common, trait1].dropna()
    y = exp2_means.loc[common, trait2].dropna()
    shared = x.index.intersection(y.index)
    x = x.loc[shared]
    y = y.loc[shared]

    fig, ax = plt.subplots(figsize=(8, 6))

    ax.scatter(x, y, alpha=0.7, s=50, color="#4C78A8", edgecolor="white", linewidth=0.5)

    # Regression line
    if len(x) >= 3:
        slope, intercept, r, p, se = sp_stats.linregress(x, y)
        x_line = np.linspace(x.min(), x.max(), 100)
        ax.plot(x_line, slope * x_line + intercept, "r--", alpha=0.7,
                label=f"r={r:.3f}, p={p:.2e}")
        ax.legend()

    ax.set_xlabel(f"{exp1_name}: {trait1}")
    ax.set_ylabel(f"{exp2_name}: {trait2}")
    ax.set_title(f"{trait1} vs {trait2}\n(genotype means)")

    # Label points with genotype names
    for geno in shared:
        ax.annotate(geno, (x[geno], y[geno]), fontsize=7, alpha=0.6,
                    xytext=(3, 3), textcoords="offset points")

    fig.tight_layout()
    return fig


def create_cross_experiment_heatmap(corr_df, top_n_traits=15):
    """Create a heatmap of cross-experiment trait correlations.

    Args:
        corr_df: Output of calculate_cross_experiment_correlations
        top_n_traits: Number of top traits per axis

    Returns:
        matplotlib Figure
    """
    import matplotlib.pyplot as plt

    # Get top traits from each experiment
    top_exp1 = corr_df.groupby("exp1_trait")["correlation"].apply(
        lambda x: x.abs().max()
    ).nlargest(top_n_traits).index.tolist()

    top_exp2 = corr_df.groupby("exp2_trait")["correlation"].apply(
        lambda x: x.abs().max()
    ).nlargest(top_n_traits).index.tolist()

    # Filter and pivot
    subset = corr_df[
        corr_df["exp1_trait"].isin(top_exp1) & corr_df["exp2_trait"].isin(top_exp2)
    ]
    pivot = subset.pivot(index="exp1_trait", columns="exp2_trait", values="correlation")

    fig, ax = plt.subplots(figsize=(12, 10))
    im = ax.imshow(pivot.values, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")

    ax.set_xticks(range(len(pivot.columns)))
    ax.set_yticks(range(len(pivot.index)))
    ax.set_xticklabels(pivot.columns, rotation=45, ha="right", fontsize=8)
    ax.set_yticklabels(pivot.index, fontsize=8)

    fig.colorbar(im, ax=ax, label="Pearson r", shrink=0.8)
    ax.set_title(f"Cross-Experiment Trait Correlations (top {top_n_traits})")

    fig.tight_layout()
    return fig


def create_genotype_boxplots(
    exp1_df, exp2_df, trait1, trait2,
    exp1_name="exp1", exp2_name="exp2",
):
    """Create side-by-side boxplots showing trait distributions per genotype.

    Args:
        exp1_df: Experiment 1 DataFrame
        exp2_df: Experiment 2 DataFrame
        trait1: Trait from experiment 1
        trait2: Trait from experiment 2
        exp1_name: Display name for experiment 1
        exp2_name: Display name for experiment 2

    Returns:
        matplotlib Figure
    """
    import matplotlib.pyplot as plt

    common = sorted(set(exp1_df["genotype"]) & set(exp2_df["genotype"]))

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))

    # Experiment 1
    data1 = [exp1_df[exp1_df["genotype"] == g][trait1].dropna().values for g in common]
    bp1 = axes[0].boxplot(data1, labels=common, patch_artist=True)
    for patch in bp1["boxes"]:
        patch.set_facecolor("#4C78A8")
    axes[0].set_title(f"{exp1_name}: {trait1}")
    axes[0].set_xlabel("Genotype")
    axes[0].tick_params(axis="x", rotation=45)

    # Experiment 2
    data2 = [exp2_df[exp2_df["genotype"] == g][trait2].dropna().values for g in common]
    bp2 = axes[1].boxplot(data2, labels=common, patch_artist=True)
    for patch in bp2["boxes"]:
        patch.set_facecolor("#E45756")
    axes[1].set_title(f"{exp2_name}: {trait2}")
    axes[1].set_xlabel("Genotype")
    axes[1].tick_params(axis="x", rotation=45)

    fig.suptitle(f"Genotype Distributions: {trait1} vs {trait2}")
    fig.tight_layout()
    return fig


# ============================================================================
# Power analysis functions
# ============================================================================

def minimum_detectable_correlation(n, alpha=0.05, power=0.80):
    """Calculate the minimum detectable correlation given sample size.

    Uses the Fisher z-transform approximation.

    Args:
        n: Number of observations (genotypes)
        alpha: Significance level
        power: Desired statistical power

    Returns:
        Minimum detectable |r|
    """
    z_alpha = sp_stats.norm.ppf(1 - alpha / 2)
    z_beta = sp_stats.norm.ppf(power)

    # Fisher z needed
    z_needed = (z_alpha + z_beta) / np.sqrt(n - 3)

    # Convert back to r
    r = np.tanh(z_needed)
    return float(min(r, 1.0))


def achieved_power(r, n, alpha=0.05):
    """Calculate achieved power for an observed correlation.

    Args:
        r: Observed correlation coefficient
        n: Sample size
        alpha: Significance level

    Returns:
        Statistical power (0 to 1)
    """
    if n <= 3 or abs(r) >= 1:
        return 0.0

    z_alpha = sp_stats.norm.ppf(1 - alpha / 2)
    z_r = np.arctanh(abs(r))
    z_stat = z_r * np.sqrt(n - 3)
    power = sp_stats.norm.cdf(z_stat - z_alpha)
    return float(power)


def calculate_correlation_ci(r, n, alpha=0.05):
    """Calculate confidence interval for a correlation coefficient.

    Uses Fisher z-transform.

    Args:
        r: Correlation coefficient
        n: Sample size
        alpha: Significance level

    Returns:
        (lower_bound, upper_bound) tuple
    """
    if n <= 3:
        return (-1.0, 1.0)

    z = np.arctanh(r)
    se = 1.0 / np.sqrt(n - 3)
    z_crit = sp_stats.norm.ppf(1 - alpha / 2)

    z_lo = z - z_crit * se
    z_hi = z + z_crit * se

    return (float(np.tanh(z_lo)), float(np.tanh(z_hi)))


# ============================================================================
# Trait clustering functions
# ============================================================================

def cluster_correlated_traits(trait_means_df, threshold=0.8):
    """Cluster traits based on correlation, grouping redundant traits.

    Args:
        trait_means_df: DataFrame of genotype means (genotypes x traits)
        threshold: Correlation threshold for clustering

    Returns:
        Dict mapping cluster_id to list of trait names.
    """
    corr_matrix = trait_means_df.corr().abs()
    distance = 1 - corr_matrix

    # Ensure diagonal is 0 and values are non-negative
    np.fill_diagonal(distance.values, 0)
    distance = distance.clip(lower=0)

    # Convert to condensed form
    from scipy.spatial.distance import squareform
    condensed = squareform(distance.values)

    # Hierarchical clustering
    Z = linkage(condensed, method="average")
    labels = fcluster(Z, t=1 - threshold, criterion="distance")

    clusters = {}
    for trait, label in zip(trait_means_df.columns, labels):
        cluster_id = int(label) - 1
        if cluster_id not in clusters:
            clusters[cluster_id] = []
        clusters[cluster_id].append(trait)

    return clusters


def select_cluster_representatives(trait_means_df, clusters):
    """Select one representative trait per cluster (highest variance).

    Args:
        trait_means_df: DataFrame of genotype means
        clusters: Dict from cluster_correlated_traits

    Returns:
        List of representative trait names.
    """
    representatives = []
    for cluster_id in sorted(clusters.keys()):
        members = clusters[cluster_id]
        if len(members) == 1:
            representatives.append(members[0])
        else:
            # Pick trait with highest variance (most informative)
            variances = trait_means_df[members].var()
            representatives.append(variances.idxmax())
    return representatives
