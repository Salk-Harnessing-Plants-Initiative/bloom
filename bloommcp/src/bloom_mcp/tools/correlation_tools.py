"""
MCP Tool Wrappers for Cross-Experiment Correlation Analysis.

Wraps functions from source/cross_experiment_correlations.py. Uses
source/experiment_utils.py for dynamic experiment discovery.
"""

import pandas as pd

from source import cross_experiment_correlations as corr

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


from source.experiment_utils import TRAITS_DIR, PLOTS_DIR, PLOTS_URL as PLOTS_URL_BASE

# Experiment file mapping
EXPERIMENTS = {
    "cylinder": {
        "path": "cylinder_traits.csv",
        "genotype_col": "Geno",
        "rep_col": "Rep",
    },
    "turface": {
        "path": "turface_traits.csv",
        "genotype_col": "geno",
        "rep_col": "rep",
    },
}

# Ensure plots directory exists
PLOTS_DIR.mkdir(parents=True, exist_ok=True)


def _get_trait_cols(df):
    """Get trait columns (everything except genotype/rep metadata)."""
    skip = {"genotype", "replicate", "Geno", "geno", "Rep", "rep", "Replicate"}
    return [c for c in df.columns if c not in skip]


def _save_plot(fig, name: str) -> str:
    """Save a matplotlib figure and return the URL."""
    filepath = PLOTS_DIR / f"{name}.png"
    fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return f"{PLOTS_URL_BASE}/{name}.png"


# ============================================================================
# Tool 1: List available experiments
# ============================================================================

def list_experiments() -> str:
    """List available experiments and their trait data files.

    Shows which experiments have CSV data available for cross-experiment
    correlation analysis.
    """
    lines = ["Available experiments:\n"]
    for name, info in EXPERIMENTS.items():
        csv_path = TRAITS_DIR / info["path"]
        exists = csv_path.exists()
        status = "ready" if exists else "FILE MISSING"

        if exists:
            df = pd.read_csv(csv_path)
            geno_col = info["genotype_col"]
            n_genos = df[geno_col].nunique()
            n_samples = len(df)
            n_traits = len(_get_trait_cols(df))
            lines.append(
                f"  {name}: {n_genos} genotypes, {n_samples} samples, "
                f"{n_traits} traits [{status}]"
            )
        else:
            lines.append(f"  {name}: {csv_path} [{status}]")

    return "\n".join(lines)


# ============================================================================
# Tool 2: Run cross-experiment correlation analysis
# ============================================================================

def run_cross_experiment_correlations(
    experiment_1: str = "cylinder",
    experiment_2: str = "turface",
    min_samples: int = 3,
    top_n: int = 15,
) -> str:
    """Run full cross-experiment correlation analysis between two experiments.

    Computes Pearson correlations between all trait pairs across experiments,
    identifies significant correlations (FDR-corrected), and generates a
    summary plot.

    Args:
        experiment_1: Name of first experiment (default: cylinder)
        experiment_2: Name of second experiment (default: turface)
        min_samples: Minimum replicates per genotype to include
        top_n: Number of top correlations to report
    """
    if experiment_1 not in EXPERIMENTS or experiment_2 not in EXPERIMENTS:
        avail = ", ".join(EXPERIMENTS.keys())
        return f"Unknown experiment. Available: {avail}"

    exp1_info = EXPERIMENTS[experiment_1]
    exp2_info = EXPERIMENTS[experiment_2]

    exp1_df, exp2_df, common_genos = corr.load_and_align_experiments(
        TRAITS_DIR / exp1_info["path"],
        TRAITS_DIR / exp2_info["path"],
        genotype_col1=exp1_info["genotype_col"],
        genotype_col2=exp2_info["genotype_col"],
        rep_col1=exp1_info["rep_col"],
        rep_col2=exp2_info["rep_col"],
    )

    exp1_traits = _get_trait_cols(exp1_df)
    exp2_traits = _get_trait_cols(exp2_df)

    exp1_means = corr.calculate_genotype_means(exp1_df, exp1_traits)
    exp2_means = corr.calculate_genotype_means(exp2_df, exp2_traits)

    corr_df = corr.calculate_cross_experiment_correlations(
        exp1_means, exp2_means, exp1_traits, exp2_traits, min_samples=min_samples
    )

    if len(corr_df) == 0:
        return "No valid correlations found. Check that experiments share genotypes."

    sig_df = corr.identify_significant_correlations(
        corr_df, p_threshold=0.05, r_threshold=0.3, use_fdr=True
    )

    fig = corr.create_correlation_summary_plot(corr_df)
    plot_url = _save_plot(fig, f"corr_summary_{experiment_1}_vs_{experiment_2}")

    summary = corr.summarize_correlation_results(
        corr_df, exp1_name=experiment_1, exp2_name=experiment_2
    )

    lines = [
        f"Cross-Experiment Correlation: {experiment_1} vs {experiment_2}",
        f"Common genotypes: {len(common_genos)}",
        f"Total trait pairs tested: {summary['total_correlations']}",
        f"Significant (p<0.05): {summary['significant_correlations']}",
        f"Highly significant (p<0.01): {summary['highly_significant_correlations']}",
        f"Mean |r|: {summary['mean_abs_correlation']:.3f}",
        f"\nTop {min(top_n, len(corr_df))} correlations:",
    ]

    for _, row in corr_df.head(top_n).iterrows():
        sig = "**" if row["highly_significant"] else "*" if row["significant"] else ""
        lines.append(
            f"  {row['exp1_trait']:<25} x {row['exp2_trait']:<25} "
            f"r={row['correlation']:>7.3f} p={row['p_value']:.2e} {sig}"
        )

    if len(sig_df) > 0:
        lines.append(f"\nFDR-significant correlations (|r|>=0.3): {len(sig_df)}")

    lines.append(f"\nSummary plot: {plot_url}")

    return "\n".join(lines)


# ============================================================================
# Tool 3: Scatter plot for a specific trait pair
# ============================================================================

def plot_trait_correlation(
    exp1_trait: str,
    exp2_trait: str,
    experiment_1: str = "cylinder",
    experiment_2: str = "turface",
) -> str:
    """Create a scatter plot showing correlation between a specific trait pair
    across two experiments.

    Generates a joint plot with regression line and marginal distributions.

    Args:
        exp1_trait: Trait name from experiment 1 (e.g., primary_length)
        exp2_trait: Trait name from experiment 2 (e.g., primary_length)
        experiment_1: Name of first experiment (default: cylinder)
        experiment_2: Name of second experiment (default: turface)
    """
    if experiment_1 not in EXPERIMENTS or experiment_2 not in EXPERIMENTS:
        return f"Unknown experiment. Available: {', '.join(EXPERIMENTS.keys())}"

    exp1_info = EXPERIMENTS[experiment_1]
    exp2_info = EXPERIMENTS[experiment_2]

    exp1_df, exp2_df, common_genos = corr.load_and_align_experiments(
        TRAITS_DIR / exp1_info["path"],
        TRAITS_DIR / exp2_info["path"],
        genotype_col1=exp1_info["genotype_col"],
        genotype_col2=exp2_info["genotype_col"],
        rep_col1=exp1_info["rep_col"],
        rep_col2=exp2_info["rep_col"],
    )

    exp1_traits = _get_trait_cols(exp1_df)
    exp2_traits = _get_trait_cols(exp2_df)

    if exp1_trait not in exp1_traits:
        return f"Trait '{exp1_trait}' not found in {experiment_1}. Available: {', '.join(exp1_traits[:10])}..."
    if exp2_trait not in exp2_traits:
        return f"Trait '{exp2_trait}' not found in {experiment_2}. Available: {', '.join(exp2_traits[:10])}..."

    exp1_means = corr.calculate_genotype_means(exp1_df, exp1_traits)
    exp2_means = corr.calculate_genotype_means(exp2_df, exp2_traits)

    fig = corr.create_joint_plot(
        exp1_means, exp2_means,
        exp1_trait, exp2_trait,
        exp1_name=experiment_1,
        exp2_name=experiment_2,
    )

    plot_name = f"scatter_{exp1_trait}_vs_{exp2_trait}"
    plot_url = _save_plot(fig, plot_name)

    stats = corr.calculate_per_trait_correlations(
        exp1_df, exp2_df, exp1_trait, exp2_trait
    )

    lines = [
        f"Correlation: {experiment_1}:{exp1_trait} vs {experiment_2}:{exp2_trait}",
        f"  Pearson r  = {stats['pearson_r']:.3f} (p = {stats['pearson_p']:.2e})",
        f"  Spearman rho = {stats['spearman_r']:.3f} (p = {stats['spearman_p']:.2e})",
        f"  n = {stats['n_genotypes']} genotypes",
        f"\nPlot: {plot_url}",
    ]

    return "\n".join(lines)


# ============================================================================
# Tool 4: Heatmap of all trait correlations
# ============================================================================

def plot_correlation_heatmap(
    experiment_1: str = "cylinder",
    experiment_2: str = "turface",
    top_n_traits: int = 15,
) -> str:
    """Create a heatmap of cross-experiment trait correlations.

    Shows the top N most correlated traits from each experiment as a matrix.

    Args:
        experiment_1: First experiment name
        experiment_2: Second experiment name
        top_n_traits: Number of top traits to include per axis
    """
    exp1_info = EXPERIMENTS[experiment_1]
    exp2_info = EXPERIMENTS[experiment_2]

    exp1_df, exp2_df, _ = corr.load_and_align_experiments(
        TRAITS_DIR / exp1_info["path"],
        TRAITS_DIR / exp2_info["path"],
        genotype_col1=exp1_info["genotype_col"],
        genotype_col2=exp2_info["genotype_col"],
        rep_col1=exp1_info["rep_col"],
        rep_col2=exp2_info["rep_col"],
    )

    exp1_traits = _get_trait_cols(exp1_df)
    exp2_traits = _get_trait_cols(exp2_df)
    exp1_means = corr.calculate_genotype_means(exp1_df, exp1_traits)
    exp2_means = corr.calculate_genotype_means(exp2_df, exp2_traits)

    corr_df = corr.calculate_cross_experiment_correlations(
        exp1_means, exp2_means, exp1_traits, exp2_traits
    )

    fig = corr.create_cross_experiment_heatmap(corr_df, top_n_traits=top_n_traits)
    plot_url = _save_plot(fig, f"heatmap_{experiment_1}_vs_{experiment_2}")

    return f"Correlation heatmap (top {top_n_traits} traits): {plot_url}"


# ============================================================================
# Tool 5: Compare genotypes for a trait pair (boxplots)
# ============================================================================

def plot_genotype_boxplots(
    exp1_trait: str,
    exp2_trait: str,
    experiment_1: str = "cylinder",
    experiment_2: str = "turface",
) -> str:
    """Create side-by-side boxplots showing trait distributions per genotype
    across two experiments.

    Useful for seeing which genotypes are consistent vs variable.

    Args:
        exp1_trait: Trait from experiment 1
        exp2_trait: Trait from experiment 2
        experiment_1: First experiment name
        experiment_2: Second experiment name
    """
    exp1_info = EXPERIMENTS[experiment_1]
    exp2_info = EXPERIMENTS[experiment_2]

    exp1_df, exp2_df, _ = corr.load_and_align_experiments(
        TRAITS_DIR / exp1_info["path"],
        TRAITS_DIR / exp2_info["path"],
        genotype_col1=exp1_info["genotype_col"],
        genotype_col2=exp2_info["genotype_col"],
        rep_col1=exp1_info["rep_col"],
        rep_col2=exp2_info["rep_col"],
    )

    fig = corr.create_genotype_boxplots(
        exp1_df, exp2_df, exp1_trait, exp2_trait,
        exp1_name=experiment_1, exp2_name=experiment_2,
    )

    plot_url = _save_plot(fig, f"boxplot_{exp1_trait}_vs_{exp2_trait}")
    return f"Genotype boxplots: {plot_url}"


# ============================================================================
# Tool 6: Statistical power analysis
# ============================================================================

def check_correlation_power(
    experiment_1: str = "cylinder",
    experiment_2: str = "turface",
) -> str:
    """Check statistical power for detecting correlations between experiments.

    Reports the minimum detectable correlation given the number of shared
    genotypes, and the achieved power for the top observed correlations.

    Args:
        experiment_1: First experiment name
        experiment_2: Second experiment name
    """
    exp1_info = EXPERIMENTS[experiment_1]
    exp2_info = EXPERIMENTS[experiment_2]

    exp1_df, exp2_df, common_genos = corr.load_and_align_experiments(
        TRAITS_DIR / exp1_info["path"],
        TRAITS_DIR / exp2_info["path"],
        genotype_col1=exp1_info["genotype_col"],
        genotype_col2=exp2_info["genotype_col"],
        rep_col1=exp1_info["rep_col"],
        rep_col2=exp2_info["rep_col"],
    )

    n = len(common_genos)

    mdr_80 = corr.minimum_detectable_correlation(n, alpha=0.05, power=0.80)
    mdr_90 = corr.minimum_detectable_correlation(n, alpha=0.05, power=0.90)

    lines = [
        f"Power Analysis: {experiment_1} vs {experiment_2}",
        f"  Shared genotypes: {n}",
        f"  Min detectable r (80% power): {mdr_80:.3f}",
        f"  Min detectable r (90% power): {mdr_90:.3f}",
        f"\nInterpretation:",
    ]

    if mdr_80 > 0.7:
        lines.append(f"  Low power - can only detect very strong correlations (r>{mdr_80:.2f})")
        lines.append(f"  Consider adding more genotypes to improve sensitivity.")
    elif mdr_80 > 0.4:
        lines.append(f"  Moderate power - can detect moderate-to-strong correlations (r>{mdr_80:.2f})")
    else:
        lines.append(f"  Good power - can detect moderate correlations (r>{mdr_80:.2f})")

    exp1_traits = _get_trait_cols(exp1_df)
    exp2_traits = _get_trait_cols(exp2_df)
    exp1_means = corr.calculate_genotype_means(exp1_df, exp1_traits)
    exp2_means = corr.calculate_genotype_means(exp2_df, exp2_traits)

    corr_df = corr.calculate_cross_experiment_correlations(
        exp1_means, exp2_means, exp1_traits, exp2_traits
    )

    if len(corr_df) > 0:
        lines.append(f"\nAchieved power for top 5 correlations:")
        for _, row in corr_df.head(5).iterrows():
            pwr = corr.achieved_power(row["correlation"], n)
            ci_lo, ci_hi = corr.calculate_correlation_ci(row["correlation"], n)
            lines.append(
                f"  {row['exp1_trait']:<20} x {row['exp2_trait']:<20} "
                f"r={row['correlation']:.3f}  power={pwr:.2f}  "
                f"95%CI=[{ci_lo:.3f}, {ci_hi:.3f}]"
            )

    return "\n".join(lines)


# ============================================================================
# Tool 7: Reduce redundant traits
# ============================================================================

def find_redundant_traits(
    experiment: str = "cylinder",
    correlation_threshold: float = 0.8,
) -> str:
    """Find groups of redundant (highly correlated) traits within an experiment.

    Clusters traits where |r| >= threshold and selects one representative
    per cluster. Useful for reducing dimensionality before GWAS.

    Args:
        experiment: Experiment name
        correlation_threshold: Min |r| to consider traits redundant (default: 0.8)
    """
    if experiment not in EXPERIMENTS:
        return f"Unknown experiment. Available: {', '.join(EXPERIMENTS.keys())}"

    exp_info = EXPERIMENTS[experiment]
    df = pd.read_csv(TRAITS_DIR / exp_info["path"])

    geno_col = exp_info["genotype_col"]
    trait_cols = _get_trait_cols(df)

    means = corr.calculate_genotype_means(df, trait_cols, genotype_col=geno_col)

    clusters = corr.cluster_correlated_traits(
        means[trait_cols], threshold=correlation_threshold
    )

    representatives = corr.select_cluster_representatives(means[trait_cols], clusters)

    lines = [
        f"Trait Redundancy Analysis - {experiment}",
        f"  Total traits: {len(trait_cols)}",
        f"  Clusters (|r| >= {correlation_threshold}): {len(clusters)}",
        f"  Representative traits: {len(representatives)}",
        f"  Traits eliminated: {len(trait_cols) - len(representatives)}",
        f"\nClusters:",
    ]

    for cluster_id in sorted(clusters.keys()):
        members = clusters[cluster_id]
        rep = representatives[cluster_id] if cluster_id < len(representatives) else "?"
        if len(members) == 1:
            lines.append(f"  Cluster {cluster_id}: {members[0]} (singleton)")
        else:
            lines.append(f"  Cluster {cluster_id} ({len(members)} traits, rep={rep}):")
            for m in members:
                marker = " <-rep" if m == rep else ""
                lines.append(f"    - {m}{marker}")

    lines.append(f"\nRecommended trait set ({len(representatives)} traits):")
    for r in representatives:
        lines.append(f"  {r}")

    return "\n".join(lines)


# ============================================================================
# Tool 8: Compare same trait across experiments
# ============================================================================

def compare_trait_across_experiments(
    trait_name: str,
    experiment_1: str = "cylinder",
    experiment_2: str = "turface",
) -> str:
    """Compare the same trait measured in two different experiments.

    Shows whether genotype rankings are preserved across experimental
    conditions (high cross-experiment correlation = trait is robust).

    Args:
        trait_name: Trait to compare (must exist in both experiments)
        experiment_1: First experiment name
        experiment_2: Second experiment name
    """
    exp1_info = EXPERIMENTS[experiment_1]
    exp2_info = EXPERIMENTS[experiment_2]

    exp1_df, exp2_df, common_genos = corr.load_and_align_experiments(
        TRAITS_DIR / exp1_info["path"],
        TRAITS_DIR / exp2_info["path"],
        genotype_col1=exp1_info["genotype_col"],
        genotype_col2=exp2_info["genotype_col"],
        rep_col1=exp1_info["rep_col"],
        rep_col2=exp2_info["rep_col"],
    )

    exp1_traits = _get_trait_cols(exp1_df)
    exp2_traits = _get_trait_cols(exp2_df)

    if trait_name not in exp1_traits or trait_name not in exp2_traits:
        return f"Trait '{trait_name}' not found in both experiments."

    stats = corr.calculate_per_trait_correlations(
        exp1_df, exp2_df, trait_name, trait_name
    )

    if not stats["valid"]:
        return f"Insufficient data to correlate '{trait_name}' across experiments."

    exp1_means_df = exp1_df.groupby("genotype")[trait_name].agg(["mean", "std", "count"])
    exp2_means_df = exp2_df.groupby("genotype")[trait_name].agg(["mean", "std", "count"])

    lines = [
        f"Cross-Experiment Comparison: {trait_name}",
        f"  {experiment_1} vs {experiment_2}",
        f"  Pearson r  = {stats['pearson_r']:.3f} (p = {stats['pearson_p']:.2e})",
        f"  Spearman rho = {stats['spearman_r']:.3f} (p = {stats['spearman_p']:.2e})",
        f"  n = {stats['n_genotypes']} genotypes",
    ]

    n = stats["n_genotypes"]
    pwr = corr.achieved_power(stats["spearman_r"], n)
    ci_lo, ci_hi = corr.calculate_correlation_ci(stats["spearman_r"], n)
    lines.append(f"  Power = {pwr:.2f}, 95% CI = [{ci_lo:.3f}, {ci_hi:.3f}]")

    r = abs(stats["spearman_r"])
    if r > 0.7:
        lines.append(f"\n  Strong cross-experiment consistency - genotype rankings preserved")
    elif r > 0.4:
        lines.append(f"\n  Moderate consistency - some genotype reranking across environments")
    else:
        lines.append(f"\n  Weak consistency - trait highly environment-dependent")

    exp1_m = corr.calculate_genotype_means(exp1_df, [trait_name])
    exp2_m = corr.calculate_genotype_means(exp2_df, [trait_name])

    fig = corr.create_joint_plot(
        exp1_m, exp2_m, trait_name, trait_name,
        exp1_name=experiment_1, exp2_name=experiment_2,
        correlation=stats["spearman_r"],
        p_value=stats["spearman_p"],
        n_genotypes=stats["n_genotypes"],
        pearson_r=stats["pearson_r"],
        pearson_p=stats["pearson_p"],
    )
    plot_url = _save_plot(fig, f"compare_{trait_name}_{experiment_1}_vs_{experiment_2}")
    lines.append(f"\n  Plot: {plot_url}")

    return "\n".join(lines)


# ============================================================================
# Registration
# ============================================================================

def register(mcp):
    """Register all cross-experiment correlation tools with the MCP server."""
    mcp.tool()(list_experiments)
    mcp.tool()(run_cross_experiment_correlations)
    mcp.tool()(plot_trait_correlation)
    mcp.tool()(plot_correlation_heatmap)
    mcp.tool()(plot_genotype_boxplots)
    mcp.tool()(check_correlation_power)
    mcp.tool()(find_redundant_traits)
    mcp.tool()(compare_trait_across_experiments)
