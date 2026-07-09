"""MCP discovery tools — list experiments, load summary, inspect data quality.

The QC cleanup pipeline itself now lives in `tools/workflows/qc.py` as
`run_qc_workflow`. The three tools here are read-only discovery helpers
that the agent always loads (see `ALWAYS_INCLUDE_MCP_TOOLS`).
"""

from bloom_mcp import data_cleanup as cleanup
from bloom_mcp.experiment_utils import OUTPUT_DIR
from bloom_mcp.tools import _ports

# Ensure output directory exists
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Read through the injected ExperimentReader port (not Supabase/local FS).
_load_data = _ports.load_frame


# ============================================================================
# Tool 1: List available experiments
# ============================================================================


def list_available_experiments() -> str:
    """List all experiment CSV files available for analysis.

    Scans the data directory and shows each file with its row count,
    trait count, and auto-detected genotype column. Use this first to
    see what experiments are available before running analysis.
    """
    experiments = _ports.reader().list_experiments()

    if not experiments:
        return "No experiments available"

    lines = [f"Available experiments ({len(experiments)} files):\n"]

    for exp in experiments:
        lines.append(
            f"  {exp.filename}\n"
            f"    Experiment: {exp.experiment_name}\n"
            f"    Samples: {exp.rows}, Traits: {exp.trait_columns}, "
            f"Total columns: {exp.total_columns}\n"
            f"    Genotype column: {exp.genotype_col or 'not detected'}\n"
            f"    Sample ID column: {exp.sample_id_col or 'not detected'}"
        )

    lines.append(
        f"\nTo analyze an experiment, use its filename (e.g., '{experiments[0].filename}')"
    )

    return "\n".join(lines)


# ============================================================================
# Tool 2: Load experiment data and show summary
# ============================================================================


def load_experiment_data(filename: str) -> str:
    """Load a SLEAP experiment CSV and show a summary of its contents.

    Shows the number of samples, genotypes, replicates, trait columns,
    and a preview of missing data.

    Args:
        filename: CSV filename from list_available_experiments
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source  # error string

    n_samples = len(df)
    n_traits = len(trait_cols)
    genotype_col = config["genotype_col"]

    lines = [
        f"Experiment: {filename} (source: {source})",
        f"  Samples: {n_samples}",
    ]

    if genotype_col and genotype_col in df.columns:
        lines.append(
            f"  Genotypes: {df[genotype_col].nunique()} (column: {genotype_col})"
        )

    replicate_col = config["replicate_col"]
    if replicate_col and replicate_col in df.columns:
        lines.append(
            f"  Replicates: {df[replicate_col].nunique()} (column: {replicate_col})"
        )

    lines.append(f"  Trait columns: {n_traits}")

    # Missing data summary
    nan_counts = df[trait_cols].isna().sum()
    traits_with_nan = (nan_counts > 0).sum()
    total_nan = nan_counts.sum()
    total_cells = n_samples * n_traits

    if total_cells > 0:
        lines.append(
            f"  Missing values: {total_nan} / {total_cells} "
            f"({total_nan / total_cells * 100:.1f}%)"
        )
    lines.append(f"  Traits with any NaN: {traits_with_nan} / {n_traits}")

    # Show top 5 traits with most NaN
    if traits_with_nan > 0:
        top_nan = nan_counts[nan_counts > 0].sort_values(ascending=False).head(5)
        lines.append("\n  Top traits with missing data:")
        for trait_name, count in top_nan.items():
            pct = count / n_samples * 100
            lines.append(f"    {trait_name}: {count} ({pct:.1f}%)")

    return "\n".join(lines)


# ============================================================================
# Tool 3: Inspect data quality (NaN samples, zero-inflated traits)
# ============================================================================


def inspect_data_quality(filename: str) -> str:
    """Inspect data quality for a SLEAP experiment dataset.

    Identifies samples with NaN values, zero-inflated traits,
    and traits with insufficient valid samples. Use this before
    running cleanup to understand what will be filtered.

    Args:
        filename: CSV filename from list_available_experiments
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return source

    genotype_col = config["genotype_col"]
    sample_id_col = config["sample_id_col"]
    replicate_col = config["replicate_col"]

    lines = [
        f"Data Quality Report: {filename} (source: {source})",
        f"  {len(df)} samples, {len(trait_cols)} traits\n",
    ]

    # 1. NaN inspection
    if sample_id_col and genotype_col and replicate_col:
        nan_df = cleanup.inspect_nan_samples(
            df,
            trait_cols,
            barcode_col=sample_id_col,
            genotype_col=genotype_col,
            replicate_col=replicate_col,
            verbose=False,
        )
    else:
        # Fallback: manual NaN count
        nan_per_sample = df[trait_cols].isna().sum(axis=1)
        nan_df = df[nan_per_sample > 0].copy()
        nan_df["nan_count"] = nan_per_sample[nan_per_sample > 0]

    n_nan_samples = len(nan_df)
    lines.append(f"1. Samples with NaN values: {n_nan_samples} / {len(df)}")
    if n_nan_samples > 0 and "nan_count" in nan_df.columns:
        nan_dist = nan_df["nan_count"].value_counts().sort_index()
        for count, n in nan_dist.head(5).items():
            lines.append(f"     {n} sample(s) with {count} NaN trait(s)")

    # 2. Zero-inflated traits check
    zero_traits = []
    for trait in trait_cols:
        zero_frac = (df[trait] == 0).sum() / len(df)
        if zero_frac > 0.5:
            zero_traits.append((trait, zero_frac))

    lines.append(
        f"\n2. Zero-inflated traits (>50% zeros): {len(zero_traits)} / {len(trait_cols)}"
    )
    if zero_traits:
        for trait_name, frac in sorted(zero_traits, key=lambda x: -x[1])[:10]:
            lines.append(f"     {trait_name}: {frac * 100:.1f}% zeros")

    # 3. Traits with many NaN
    nan_heavy_traits = []
    for trait in trait_cols:
        nan_frac = df[trait].isna().sum() / len(df)
        if nan_frac > 0.3:
            nan_heavy_traits.append((trait, nan_frac))

    lines.append(
        f"\n3. NaN-heavy traits (>30% NaN): {len(nan_heavy_traits)} / {len(trait_cols)}"
    )
    if nan_heavy_traits:
        for trait_name, frac in sorted(nan_heavy_traits, key=lambda x: -x[1])[:10]:
            lines.append(f"     {trait_name}: {frac * 100:.1f}% NaN")

    # 4. Low-sample traits
    low_sample_traits = []
    for trait in trait_cols:
        valid = df[trait].notna().sum()
        if valid < 10:
            low_sample_traits.append((trait, valid))

    lines.append(
        f"\n4. Low-sample traits (<10 valid): {len(low_sample_traits)} / {len(trait_cols)}"
    )
    if low_sample_traits:
        for trait_name, valid_n in sorted(low_sample_traits, key=lambda x: x[1])[:10]:
            lines.append(f"     {trait_name}: {valid_n} valid samples")

    total_issues = len(zero_traits) + len(nan_heavy_traits) + len(low_sample_traits)
    lines.append(
        f"\nSummary: {total_issues} trait-level issues, {n_nan_samples} sample-level issues"
    )
    if total_issues > 0 or n_nan_samples > 0:
        lines.append("Recommendation: Run run_qc_workflow to apply cleanup filters")
    else:
        lines.append("Data looks clean — no major issues detected")

    return "\n".join(lines)


# ============================================================================
# Registration
# ============================================================================


def register(mcp):
    """Register the 3 always-on discovery tools with the MCP server."""
    mcp.tool()(list_available_experiments)
    mcp.tool()(load_experiment_data)
    mcp.tool()(inspect_data_quality)
