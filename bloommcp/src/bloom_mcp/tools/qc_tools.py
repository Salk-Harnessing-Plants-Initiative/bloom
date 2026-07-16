"""MCP discovery tools — list experiments, load summary.

The QC cleanup pipeline itself now lives in `tools/workflows/qc.py` as
`run_qc_workflow`. The two tools here are read-only discovery helpers
that the agent always loads (see `ALWAYS_INCLUDE_MCP_TOOLS`).
`inspect_data_quality` was dropped — redundant with `qc_inspect`, which
delegates the same NaN/quality report to `sleap_roots_analyze`.
"""

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
# Registration
# ============================================================================


def register(mcp):
    """Register the 2 always-on discovery tools with the MCP server."""
    mcp.tool()(list_available_experiments)
    mcp.tool()(load_experiment_data)
