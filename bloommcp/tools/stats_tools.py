"""
MCP Tool Wrappers for SLEAP Statistics & Heritability.

Wraps functions from source/trait_statistics.py. Uses source/experiment_utils.py for
dynamic experiment discovery and column auto-detection.
"""

from pathlib import Path

from source import trait_statistics as stats_module
from source.experiment_utils import load_experiment_data as _load_data


# ============================================================================
# Tool 1: Descriptive statistics
# ============================================================================

def get_trait_statistics(filename: str, traits: str = "") -> str:
    """Get descriptive statistics for traits in a SLEAP experiment.

    Reports mean, std, CV, min, max, median, skewness, and kurtosis.
    If no specific traits are provided, summarizes all traits.

    Args:
        filename: CSV filename from list_available_experiments
        traits: Comma-separated trait names to analyze (empty = all traits)
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return config

    if traits.strip():
        requested = [t.strip() for t in traits.split(",")]
        selected = [t for t in requested if t in trait_cols]
        if not selected:
            return f"None of the requested traits found. Available: {', '.join(trait_cols[:10])}..."
        trait_cols = selected

    results = stats_module.calculate_trait_statistics(df, trait_cols)

    lines = [f"Descriptive Statistics: {filename} (source: {source})", f"  {len(trait_cols)} traits\n"]

    for trait_name in trait_cols:
        r = results.get(trait_name, {})
        if "error" in r:
            lines.append(f"  {trait_name}: {r['error']}")
            continue
        lines.append(
            f"  {trait_name}:"
            f"  mean={r['mean']:.3f}, std={r['std']:.3f}, CV={r['cv']:.3f},"
            f"  range=[{r['min']:.3f}, {r['max']:.3f}],"
            f"  skew={r['skewness']:.3f}, kurtosis={r['kurtosis']:.3f},"
            f"  n={r['count']}"
        )

    return "\n".join(lines)


# ============================================================================
# Tool 2: ANOVA by genotype
# ============================================================================

def run_anova_by_genotype(filename: str, traits: str = "", alpha: float = 0.05) -> str:
    """Run one-way ANOVA for traits grouped by genotype.

    Tests whether genotype means differ significantly for each trait.
    Reports F-statistic, p-value, eta-squared (effect size), and significance.

    Args:
        filename: CSV filename from list_available_experiments
        traits: Comma-separated trait names (empty = all traits)
        alpha: Significance level (default 0.05)
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return config

    genotype_col = config["genotype_col"]
    if not genotype_col or genotype_col not in df.columns:
        return f"No genotype column detected in {filename}. Cannot run ANOVA."

    if traits.strip():
        requested = [t.strip() for t in traits.split(",")]
        selected = [t for t in requested if t in trait_cols]
        if not selected:
            return "None of the requested traits found in dataset."
        trait_cols = selected

    results = stats_module.perform_anova_by_genotype(
        df, trait_cols, genotype_col=genotype_col, alpha=alpha,
    )

    if "error" in results:
        return f"ANOVA error: {results['error']}"

    lines = [f"ANOVA by Genotype: {filename} (source: {source})", f"  alpha={alpha}\n"]

    sig_count = 0
    for trait_name in trait_cols:
        r = results.get(trait_name, {})
        if "error" in r:
            lines.append(f"  {trait_name}: {r['error']}")
            continue
        sig = "***" if r["significant"] else "   "
        sig_count += 1 if r["significant"] else 0
        lines.append(
            f"  {sig} {trait_name}:"
            f"  F={r['f_statistic']:.2f}, p={r['p_value']:.4f},"
            f"  eta2={r['eta_squared']:.3f}, n={r['total_n']},"
            f"  groups={r['n_groups']}"
        )

    lines.append(f"\nSummary: {sig_count}/{len(trait_cols)} traits significant at alpha={alpha}")

    return "\n".join(lines)


# ============================================================================
# Tool 3: Heritability estimation
# ============================================================================

def calculate_heritability(filename: str) -> str:
    """Calculate broad-sense heritability (H2) for all traits using mixed models.

    Uses linear mixed model with genotype as random effect.
    H2 = sigma2_G / (sigma2_G + sigma2_E / mean_n_reps)

    Reports H2 for each trait, sorted from highest to lowest.

    Args:
        filename: CSV filename from list_available_experiments
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return config

    genotype_col = config["genotype_col"]
    replicate_col = config["replicate_col"]
    if not genotype_col or not replicate_col:
        return f"Cannot calculate heritability: genotype or replicate column not detected in {filename}."

    results = stats_module.calculate_heritability_estimates(
        df, trait_cols,
        genotype_col=genotype_col,
        replicate_col=replicate_col,
    )

    if "error" in results:
        return f"Heritability error: {results['error']}"

    h2_list = []
    for trait_name in trait_cols:
        r = results.get(trait_name, {})
        if "heritability" in r:
            h2_list.append((trait_name, r))

    h2_list.sort(key=lambda x: x[1]["heritability"], reverse=True)

    lines = [
        f"Heritability (H2): {filename} (source: {source})",
        f"  {len(h2_list)} traits analyzed, method: mixed_model\n",
        f"  {'Trait':<40s} {'H2':>6s} {'var_G':>10s} {'var_E':>10s} {'n':>5s}",
        f"  {'---' * 25}",
    ]

    high_count = 0
    for trait_name, r in h2_list:
        h2 = r["heritability"]
        marker = "***" if h2 >= 0.5 else "   "
        high_count += 1 if h2 >= 0.5 else 0
        lines.append(
            f"  {marker} {trait_name:<36s}"
            f"  {h2:>6.3f}"
            f"  {r['var_genetic']:>10.4f}"
            f"  {r['var_residual']:>10.4f}"
            f"  {r['n_observations']:>5d}"
        )

    error_traits = [t for t in trait_cols if "error" in results.get(t, {})]
    if error_traits:
        lines.append(f"\n  Failed ({len(error_traits)}):")
        for t in error_traits[:5]:
            lines.append(f"    {t}: {results[t]['error']}")

    lines.append(f"\nSummary: {high_count}/{len(h2_list)} traits with H2 >= 0.5 (*** = high)")

    return "\n".join(lines)


# ============================================================================
# Tool 4: Find high heritability traits
# ============================================================================

def find_high_heritability_traits(filename: str, threshold: float = 0.5) -> str:
    """Find traits with heritability above a threshold.

    Args:
        filename: CSV filename from list_available_experiments
        threshold: Minimum H2 value (default 0.5)
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return config

    genotype_col = config["genotype_col"]
    replicate_col = config["replicate_col"]
    if not genotype_col or not replicate_col:
        return f"Cannot calculate heritability: genotype or replicate column not detected in {filename}."

    results = stats_module.calculate_heritability_estimates(
        df, trait_cols,
        genotype_col=genotype_col,
        replicate_col=replicate_col,
    )

    if "error" in results:
        return f"Error: {results['error']}"

    high_traits = stats_module.identify_high_heritability_traits(results, threshold=threshold)

    if not high_traits:
        return f"No traits found with H2 >= {threshold} in {filename}."

    high_with_h2 = [(t, results[t]["heritability"]) for t in high_traits]
    high_with_h2.sort(key=lambda x: x[1], reverse=True)

    lines = [
        f"High Heritability Traits: {filename} (H2 >= {threshold})",
        f"  {len(high_with_h2)} / {len(trait_cols)} traits qualify\n",
    ]

    for trait_name, h2 in high_with_h2:
        lines.append(f"  {trait_name}: H2 = {h2:.3f}")

    return "\n".join(lines)


# ============================================================================
# Tool 5: Diagnose low heritability
# ============================================================================

def diagnose_low_heritability(filename: str, trait: str = "") -> str:
    """Diagnose why a specific trait has low heritability.

    Args:
        filename: CSV filename from list_available_experiments
        trait: Name of the trait to diagnose (required)
    """
    if not trait.strip():
        return "Please specify a trait name to diagnose."

    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return config

    genotype_col = config["genotype_col"]
    replicate_col = config["replicate_col"]
    if not genotype_col or not replicate_col:
        return f"Cannot diagnose: genotype or replicate column not detected in {filename}."

    trait = trait.strip()
    if trait not in df.columns:
        matches = [t for t in trait_cols if trait.lower() in t.lower()]
        if matches:
            return f"Trait '{trait}' not found. Did you mean: {', '.join(matches[:5])}?"
        return f"Trait '{trait}' not found in dataset."

    h2_results = stats_module.calculate_heritability_estimates(
        df, [trait],
        genotype_col=genotype_col,
        replicate_col=replicate_col,
    )

    h2_result = h2_results.get(trait, {})

    diagnosis = stats_module.diagnose_heritability_issues(
        df, trait, h2_result,
        genotype_col=genotype_col,
        replicate_col=replicate_col,
    )

    var_analysis = stats_module.analyze_trait_variance(
        df, trait,
        genotype_col=genotype_col,
        replicate_col=replicate_col,
    )

    h2_val = h2_result.get("heritability", "N/A")
    h2_str = f"{h2_val:.3f}" if isinstance(h2_val, float) else str(h2_val)

    lines = [
        f"Heritability Diagnosis: {trait}",
        f"  File: {filename} (source: {source})",
        f"  H2 = {h2_str}",
        f"  Severity: {diagnosis['severity']}\n",
    ]

    if "error" not in var_analysis:
        lines.append("Variance Analysis:")
        lines.append(f"  Samples: {var_analysis['n_observations']}")
        lines.append(f"  Genotypes: {var_analysis['n_genotypes']}")
        lines.append(f"  Reps/genotype: {var_analysis['min_reps_per_geno']}-{var_analysis['max_reps_per_geno']} (mean: {var_analysis['mean_reps_per_geno']:.1f})")
        lines.append(f"  Between-genotype variance: {var_analysis['between_genotype_variance']:.4f}")
        lines.append(f"  Within-genotype variance: {var_analysis['within_genotype_variance']:.4f}")
        lines.append(f"  % variance between genotypes: {var_analysis['pct_variance_between_geno']:.1f}%")
        lines.append(f"  CV: {var_analysis['trait_cv']:.1f}%\n")

    if diagnosis["issues"]:
        lines.append("Issues:")
        for issue in diagnosis["issues"]:
            lines.append(f"  - {issue}")

    if diagnosis["recommendations"]:
        lines.append("\nRecommendations:")
        for rec in diagnosis["recommendations"]:
            lines.append(f"  - {rec}")

    if not diagnosis["has_issues"]:
        lines.append("No issues detected — trait looks healthy.")

    return "\n".join(lines)


# ============================================================================
# Registration
# ============================================================================

def register(mcp):
    """Register all statistics tools with the MCP server."""
    mcp.tool()(get_trait_statistics)
    mcp.tool()(run_anova_by_genotype)
    mcp.tool()(calculate_heritability)
    mcp.tool()(find_high_heritability_traits)
    mcp.tool()(diagnose_low_heritability)
