"""
MCP Tool Wrappers for SLEAP Data QC & Cleanup.

Wraps functions from source/data_cleanup.py. Uses source/experiment_utils.py for
dynamic experiment discovery and column auto-detection — no hardcoded
experiment names. Any CSV in BLOOM_TRAITS_DIR is discoverable.
"""

import json
from pathlib import Path

from source import data_cleanup as cleanup
from source.experiment_utils import (
    list_experiments,
    load_experiment_data as _load_data,
    detect_columns,
    TRAITS_DIR,
    OUTPUT_DIR,
)

# Ensure output directory exists
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# Tool 1: List available experiments
# ============================================================================

def list_available_experiments() -> str:
    """List all experiment CSV files available for analysis.

    Scans the data directory and shows each file with its row count,
    trait count, and auto-detected genotype column. Use this first to
    see what experiments are available before running analysis.
    """
    experiments = list_experiments()

    if not experiments:
        return f"No CSV files found in {TRAITS_DIR}"

    lines = [f"Available experiments ({len(experiments)} files):\n"]

    for exp in experiments:
        lines.append(
            f"  {exp['filename']}\n"
            f"    Experiment: {exp['experiment_name']}\n"
            f"    Samples: {exp['rows']}, Traits: {exp['trait_columns']}, "
            f"Total columns: {exp['total_columns']}\n"
            f"    Genotype column: {exp['genotype_col'] or 'not detected'}\n"
            f"    Sample ID column: {exp['sample_id_col'] or 'not detected'}"
        )

    lines.append(f"\nTo analyze an experiment, use its filename (e.g., '{experiments[0]['filename']}')")

    return "\n".join(lines)


# ============================================================================
# Tool 2: Inspect experiment columns
# ============================================================================

def inspect_experiment_columns(filename: str) -> str:
    """Inspect the columns of an experiment CSV and show auto-detected classification.

    Shows which columns were detected as traits (numeric) vs metadata,
    and which special columns (genotype, replicate, sample ID) were found.

    Args:
        filename: CSV filename from list_available_experiments
    """
    import pandas as pd

    csv_path = TRAITS_DIR / filename
    if not csv_path.exists():
        experiments = list_experiments()
        avail = ", ".join(e["filename"] for e in experiments)
        return f"File '{filename}' not found. Available: {avail}"

    df = pd.read_csv(csv_path, nrows=50)
    config = detect_columns(df)

    lines = [
        f"Column Classification: {filename}",
        f"  Total columns: {len(df.columns)}",
        f"  Trait columns (numeric): {len(config['trait_cols'])}",
        f"  Metadata columns: {len(config['metadata_cols'])}",
        f"\n  Auto-detected special columns:",
        f"    Genotype: {config['genotype_col'] or 'NOT FOUND'}",
        f"    Replicate: {config['replicate_col'] or 'NOT FOUND'}",
        f"    Sample ID: {config['sample_id_col'] or 'NOT FOUND'}",
        f"\n  Metadata columns ({len(config['metadata_cols'])}):",
    ]

    for col in config["metadata_cols"]:
        lines.append(f"    - {col}")

    lines.append(f"\n  First 10 trait columns:")
    for col in config["trait_cols"][:10]:
        lines.append(f"    - {col}")

    if len(config["trait_cols"]) > 10:
        lines.append(f"    ... and {len(config['trait_cols']) - 10} more")

    return "\n".join(lines)


# ============================================================================
# Tool 3: Load experiment data and show summary
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
        return config  # error string

    n_samples = len(df)
    n_traits = len(trait_cols)
    genotype_col = config["genotype_col"]

    lines = [
        f"Experiment: {filename} (source: {source})",
        f"  Samples: {n_samples}",
    ]

    if genotype_col and genotype_col in df.columns:
        lines.append(f"  Genotypes: {df[genotype_col].nunique()} (column: {genotype_col})")

    replicate_col = config["replicate_col"]
    if replicate_col and replicate_col in df.columns:
        lines.append(f"  Replicates: {df[replicate_col].nunique()} (column: {replicate_col})")

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
        lines.append(f"\n  Top traits with missing data:")
        for trait_name, count in top_nan.items():
            pct = count / n_samples * 100
            lines.append(f"    {trait_name}: {count} ({pct:.1f}%)")

    return "\n".join(lines)


# ============================================================================
# Tool 4: Inspect data quality (NaN samples, zero-inflated traits)
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
        return config

    genotype_col = config["genotype_col"]
    sample_id_col = config["sample_id_col"]
    replicate_col = config["replicate_col"]

    lines = [f"Data Quality Report: {filename} (source: {source})", f"  {len(df)} samples, {len(trait_cols)} traits\n"]

    # 1. NaN inspection
    if sample_id_col and genotype_col and replicate_col:
        nan_df = cleanup.inspect_nan_samples(
            df, trait_cols,
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

    lines.append(f"\n2. Zero-inflated traits (>50% zeros): {len(zero_traits)} / {len(trait_cols)}")
    if zero_traits:
        for trait_name, frac in sorted(zero_traits, key=lambda x: -x[1])[:10]:
            lines.append(f"     {trait_name}: {frac * 100:.1f}% zeros")

    # 3. Traits with many NaN
    nan_heavy_traits = []
    for trait in trait_cols:
        nan_frac = df[trait].isna().sum() / len(df)
        if nan_frac > 0.3:
            nan_heavy_traits.append((trait, nan_frac))

    lines.append(f"\n3. NaN-heavy traits (>30% NaN): {len(nan_heavy_traits)} / {len(trait_cols)}")
    if nan_heavy_traits:
        for trait_name, frac in sorted(nan_heavy_traits, key=lambda x: -x[1])[:10]:
            lines.append(f"     {trait_name}: {frac * 100:.1f}% NaN")

    # 4. Low-sample traits
    low_sample_traits = []
    for trait in trait_cols:
        valid = df[trait].notna().sum()
        if valid < 10:
            low_sample_traits.append((trait, valid))

    lines.append(f"\n4. Low-sample traits (<10 valid): {len(low_sample_traits)} / {len(trait_cols)}")
    if low_sample_traits:
        for trait_name, valid_n in sorted(low_sample_traits, key=lambda x: x[1])[:10]:
            lines.append(f"     {trait_name}: {valid_n} valid samples")

    total_issues = len(zero_traits) + len(nan_heavy_traits) + len(low_sample_traits)
    lines.append(f"\nSummary: {total_issues} trait-level issues, {n_nan_samples} sample-level issues")
    if total_issues > 0 or n_nan_samples > 0:
        lines.append("Recommendation: Run clean_experiment_data to apply cleanup filters")
    else:
        lines.append("Data looks clean — no major issues detected")

    return "\n".join(lines)


# ============================================================================
# Tool 5: Clean experiment data (apply all filters, save cleaned CSV)
# ============================================================================

def clean_experiment_data(
    filename: str,
    max_zeros_per_trait: float = 0.5,
    max_nans_per_trait: float = 0.3,
    max_nans_per_sample: float = 0.2,
    min_samples_per_trait: int = 10,
) -> str:
    """Apply data cleanup filters to a SLEAP experiment and save cleaned CSV.

    Runs four cleanup steps in order:
    1. Remove traits with too many zeros (zero-inflated)
    2. Remove traits with too many NaN values
    3. Remove samples with too many NaN values
    4. Remove traits with insufficient valid samples

    Saves the cleaned CSV and a JSON log to the output directory.

    Args:
        filename: CSV filename from list_available_experiments
        max_zeros_per_trait: Max fraction of zeros per trait before removal (0-1, default 0.5)
        max_nans_per_trait: Max fraction of NaN per trait before removal (0-1, default 0.3)
        max_nans_per_sample: Max fraction of NaN per sample before removal (0-1, default 0.2)
        min_samples_per_trait: Min valid samples per trait (default 10)
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return config

    stem = Path(filename).stem
    original_samples = len(df)
    original_traits = len(trait_cols)

    # Apply cleanup filters
    df_clean, cleanup_log = cleanup.apply_data_cleanup_filters(
        df, trait_cols,
        max_zeros_per_trait=max_zeros_per_trait,
        max_nans_per_trait=max_nans_per_trait,
        max_nans_per_sample=max_nans_per_sample,
        min_samples_per_trait=min_samples_per_trait,
    )

    # Save cleaned data and log
    from source.data_utils import convert_to_json_serializable

    qc_dir = OUTPUT_DIR / f"qc_{stem}"
    qc_dir.mkdir(parents=True, exist_ok=True)

    cleaned_csv_path = qc_dir / f"{stem}_cleaned.csv"
    df_clean.to_csv(cleaned_csv_path, index=False)

    log_path = qc_dir / "cleanup_log.json"
    with open(log_path, "w") as f:
        json.dump(convert_to_json_serializable(cleanup_log), f, indent=2)

    # Format results
    final_samples = cleanup_log["final_samples"]
    final_traits = cleanup_log["final_traits"]

    lines = [
        f"Data Cleanup Results: {filename}",
        f"  Samples: {original_samples} -> {final_samples} "
        f"({final_samples / original_samples * 100:.1f}% retained)",
        f"  Traits: {original_traits} -> {final_traits} "
        f"({final_traits / original_traits * 100:.1f}% retained)",
        f"\nCleanup steps:",
    ]

    for step in cleanup_log["cleanup_steps"]:
        step_name = step["step"]
        if "traits_removed" in step:
            lines.append(f"  {step_name}: {step['traits_removed']} traits removed "
                         f"({step['remaining_traits']} remaining)")
        elif "samples_removed" in step:
            lines.append(f"  {step_name}: {step['samples_removed']} samples removed "
                         f"({step['remaining_samples']} remaining)")

    removed_traits = cleanup_log.get("removed_traits", [])
    if removed_traits:
        lines.append(f"\nRemoved traits ({len(removed_traits)}):")
        for rt in removed_traits[:15]:
            reason = rt.get("reason", "unknown")
            lines.append(f"  - {rt['trait']}: {reason}")
        if len(removed_traits) > 15:
            lines.append(f"  ... and {len(removed_traits) - 15} more")

    lines.append(f"\nOutput files:")
    lines.append(f"  Cleaned CSV: {cleaned_csv_path}")
    lines.append(f"  Cleanup log: {log_path}")

    return "\n".join(lines)


# ============================================================================
# Tool 6: List trait columns in an experiment
# ============================================================================

def list_trait_columns(filename: str) -> str:
    """List all numeric trait columns available in a SLEAP experiment dataset.

    Shows the trait column names that would be used for analysis,
    excluding metadata columns.

    Args:
        filename: CSV filename from list_available_experiments
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return config

    lines = [
        f"Trait columns for {filename} ({len(trait_cols)} traits, source: {source}):",
    ]

    for i, col in enumerate(trait_cols, 1):
        valid = df[col].notna().sum()
        lines.append(f"  {i:3d}. {col} ({valid} valid samples)")

    metadata_cols = config["metadata_cols"]
    lines.append(f"\nMetadata columns excluded ({len(metadata_cols)}):")
    for col in metadata_cols:
        lines.append(f"  - {col}")

    return "\n".join(lines)


# ============================================================================
# Registration
# ============================================================================

def register(mcp):
    """Register all QC tools with the MCP server."""
    mcp.tool()(list_available_experiments)
    mcp.tool()(inspect_experiment_columns)
    mcp.tool()(load_experiment_data)
    mcp.tool()(inspect_data_quality)
    mcp.tool()(clean_experiment_data)
    mcp.tool()(list_trait_columns)
