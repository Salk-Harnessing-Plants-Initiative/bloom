"""load_experiment_data — discovery tool: summarize a CSV experiment.

Not a ``sleap-roots-analyze`` wrapper — reads through the injected
``ExperimentReader`` port (via ``_ports.load_frame``, the legacy 4-tuple read
adapter). Always-included in the agent's tool set.
"""

from typing import Optional

from bloom_mcp.tools import _ports

_load_data = _ports.load_frame


def load_experiment_data(
    filename: str,
    source_id: Optional[int] = None,
    run_id: Optional[str] = None,
) -> str:
    """Load a SLEAP experiment CSV and show a summary of its contents.

    Shows the number of samples, genotypes, replicates, trait columns,
    and a preview of missing data.

    Args:
        filename: experiment identifier from list_available_experiments
        source_id: pin the summary to a specific raw DB source (see
            core_list_experiment_sources). Omit to use the latest source,
            same as today. Mutually exclusive with run_id.
        run_id: pin the summary to a specific raw DB source by its pipeline
            run id (see core_list_experiment_sources). Omit to use the latest
            source, same as today. Mutually exclusive with source_id.
    """
    df, trait_cols, config, source = _load_data(
        filename, source_id=source_id, run_id=run_id
    )
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
