"""Data loading utilities for wheat trait analysis."""

from __future__ import annotations

import pandas as pd
import numpy as np
import json
import logging

from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from .data_utils import convert_to_json_serializable, create_run_directory

# Set up module logger
logger = logging.getLogger(__name__)


def load_trait_data(
    csv_path: Path | str,
    barcode_col: str = "Barcode",
    genotype_col: str = "geno",
    replicate_col: Optional[str] = "rep",
) -> pd.DataFrame:
    """Load and validate trait data from CSV file.

    Args:
        csv_path: Path to trait CSV file
        barcode_col: Name of the barcode/plant ID column (default: "Barcode")
        genotype_col: Name of the genotype column (default: "geno")
        replicate_col: Name of the replicate column if present (default: "rep", None if not needed)

    Returns:
        DataFrame with trait data

    Raises:
        FileNotFoundError: If CSV file doesn't exist
        ValueError: If required columns are missing
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Trait data file not found: {csv_path}")

    df = pd.read_csv(csv_path)

    # Check for required columns
    required_cols = [barcode_col, genotype_col]
    if replicate_col:
        required_cols.append(replicate_col)

    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        # Try to find similar column names for helpful error message
        available_cols = df.columns.tolist()
        suggestions = []
        for missing in missing_cols:
            similar = [
                col
                for col in available_cols
                if missing.lower() in col.lower() or col.lower() in missing.lower()
            ]
            if similar:
                suggestions.append(f"{missing} (maybe: {', '.join(similar[:3])})")
            else:
                suggestions.append(missing)
        raise ValueError(
            f"Missing required columns: {suggestions}. Available columns: {available_cols[:10]}..."
        )

    return df


def get_trait_columns(
    df: pd.DataFrame,
    barcode_col: str = "Barcode",
    genotype_col: str = "geno",
    replicate_col: Optional[str] = "rep",
    additional_exclude: Optional[List[str]] = None,
) -> List[str]:
    """Get list of numeric trait columns excluding metadata.

    Args:
        df: Trait dataframe
        barcode_col: Name of the barcode/plant ID column to exclude (default: "Barcode")
        genotype_col: Name of the genotype column to exclude (default: "geno")
        replicate_col: Name of the replicate column to exclude if present (default: "rep")
        additional_exclude: Additional columns to exclude (e.g., date columns)

    Returns:
        List of trait column names
    """
    # Build list of columns to exclude
    exclude_cols = [barcode_col, genotype_col]
    if replicate_col:
        exclude_cols.append(replicate_col)

    # Add any additional exclusions
    if additional_exclude:
        exclude_cols.extend(additional_exclude)

    # CRITICAL: Also exclude Plot column (root core pipeline metadata)
    # Root core pipeline uses: Plot, Rep, geno, Barcode
    if "Plot" in df.columns:
        exclude_cols.append("Plot")

    # Also exclude common metadata columns that might exist with different names
    # These are case-insensitive matches
    common_metadata = [
        "date",
        "time",
        "sterilization",
        "experiment",
        "batch",
        "operator",
        "notes",
        "comments",
        "id",
        "index",
        "qc_",  # QC-related columns
        "outlier",  # Outlier flags
        "wave_name",  # Experimental metadata
        "wave_number",  # Experimental metadata
        "germ_day",  # Germination day (experimental metadata)
        "plant_age",  # Plant age (experimental metadata)
        "age_days",  # Age in days (experimental metadata)
        "day_",  # Day-related columns
        "_color",  # Color coding columns
        "dot",  # Date of treatment/transplant
        "scan_",  # Scan-related metadata
        "scanner",  # Scanner information
        "phenotyper",  # Phenotyper information
        "uploaded",  # Upload timestamps
        "accession",  # Accession IDs
        "species_",  # Species information
        "plant_name",  # Plant naming
        "plant_id",  # Plant IDs
    ]
    for col in df.columns:
        col_lower = col.lower()
        if any(meta in col_lower for meta in common_metadata):
            exclude_cols.append(col)

    # Remove duplicates and filter to columns that actually exist
    exclude_cols = list(set(col for col in exclude_cols if col in df.columns))

    # Get all columns not in exclude list
    trait_cols = [col for col in df.columns if col not in exclude_cols]

    # Only return numeric columns
    numeric_cols = []
    for col in trait_cols:
        if pd.api.types.is_numeric_dtype(df[col]):
            numeric_cols.append(col)

    return numeric_cols


def get_numeric_traits_only(
    df: pd.DataFrame,
    barcode_col: str = "Barcode",
    genotype_col: str = "geno",
    replicate_col: Optional[str] = "rep",
    additional_exclude: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Extract only numeric trait columns for analysis.

    Args:
        df: Full trait dataframe
        barcode_col: Name of the barcode/plant ID column to exclude (default: "Barcode")
        genotype_col: Name of the genotype column to exclude (default: "geno")
        replicate_col: Name of the replicate column to exclude if present (default: "rep")
        additional_exclude: Additional columns to exclude (e.g., date columns)

    Returns:
        DataFrame with only numeric trait columns
    """
    trait_cols = get_trait_columns(
        df,
        barcode_col=barcode_col,
        genotype_col=genotype_col,
        replicate_col=replicate_col,
        additional_exclude=additional_exclude,
    )
    return df[trait_cols].copy()


def save_cleaned_data(
    df: pd.DataFrame,
    outliers: Dict,
    run_dir: Path | str,
    log_info: Optional[Dict] = None,
) -> Tuple[Path, Path]:
    """Save cleaned data and analysis logs.

    Args:
        df: Cleaned trait dataframe
        outliers: Dictionary with outlier detection results
        run_dir: Run directory path
        log_info: Additional information to log

    Returns:
        Tuple of (cleaned_data_path, log_path)
    """
    run_dir = Path(run_dir)

    # Save cleaned data
    cleaned_path = run_dir / "cleaned_traits.csv"
    df.to_csv(cleaned_path, index=False)

    # Create log dictionary
    log_data = {
        "timestamp": datetime.now().isoformat(),
        "original_samples": len(df),
        "outlier_detection": convert_to_json_serializable(outliers),
        "cleaned_data_path": cleaned_path.as_posix(),
    }

    if log_info:
        log_data.update(convert_to_json_serializable(log_info))

    # Save log
    log_path = run_dir / "cleanup_log.json"
    with open(log_path, "w") as f:
        json.dump(log_data, f, indent=2)

    return cleaned_path, log_path


def remove_nan_samples(
    df: pd.DataFrame,
    trait_cols: List[str],
    max_nan_fraction: float = 0.2,
    barcode_col: str = "Barcode",
    genotype_col: str = "geno",
    replicate_col: Optional[str] = "rep",
    save_removed_path: Optional[Path | str] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict]:
    """Remove samples with NaN values in trait columns and optionally save removed rows.

    This is the centralized function for NaN removal that should be called
    before any outlier detection or other analysis. It handles both removal
    and saving of removed samples in a single operation.

    Args:
        df: Original dataframe
        trait_cols: List of trait columns to check for NaN
        max_nan_fraction: Maximum fraction of NaN allowed per sample (0-1)
        barcode_col: Name of the barcode/plant ID column (default: "Barcode")
        genotype_col: Name of the genotype column (default: "geno")
        replicate_col: Name of the replicate column if present (default: "rep")
        save_removed_path: Optional path to save removed samples CSV. If provided,
                          removed samples will be saved with NaN information.

    Returns:
        Tuple of:
        - DataFrame with NaN samples removed
        - DataFrame of removed samples with NaN info
        - Dictionary with removal statistics (includes 'saved_path' if saved)
    """
    removal_stats = {
        "original_samples": len(df),
        "samples_with_any_nan": 0,
        "samples_removed": 0,
        "removal_details": [],
    }

    # Identify samples with NaN in trait columns
    samples_to_remove = []
    removed_details = []

    for idx in df.index:
        nan_traits = [col for col in trait_cols if pd.isna(df.loc[idx, col])]
        nan_count = len(nan_traits)
        nan_fraction = nan_count / len(trait_cols) if trait_cols else 0

        if nan_count > 0:
            removal_stats["samples_with_any_nan"] += 1

            # Remove if exceeds threshold
            if nan_fraction > max_nan_fraction:
                samples_to_remove.append(idx)
                removed_details.append(
                    {
                        "sample_index": int(idx),
                        "barcode": (
                            df.loc[idx, barcode_col]
                            if barcode_col in df.columns
                            else ""
                        ),
                        "genotype": (
                            df.loc[idx, genotype_col]
                            if genotype_col in df.columns
                            else ""
                        ),
                        "rep": (
                            df.loc[idx, replicate_col]
                            if replicate_col and replicate_col in df.columns
                            else ""
                        ),
                        "nan_count": nan_count,
                        "nan_fraction": float(nan_fraction),
                        "nan_traits": "; ".join(nan_traits),
                        "removal_reason": f"Contains {nan_count} NaN values",
                    }
                )

    # Create cleaned dataframe
    df_cleaned = df.drop(index=samples_to_remove).copy()

    # Create removed samples dataframe
    if removed_details:
        df_removed = pd.DataFrame(removed_details)
        # Add the actual data for the removed samples
        for col in df.columns:
            if col not in df_removed.columns:
                df_removed[col] = df.loc[samples_to_remove, col].values
    else:
        # Create empty dataframe with expected columns
        base_columns = [
            "sample_index",
            "barcode",
            "genotype",
            "nan_count",
            "nan_fraction",
            "nan_traits",
            "removal_reason",
        ]
        if replicate_col:
            # Insert rep column after genotype
            base_columns.insert(3, "rep")
        df_removed = pd.DataFrame(columns=base_columns)

    # Update stats
    removal_stats["samples_removed"] = len(samples_to_remove)
    removal_stats["samples_retained"] = len(df_cleaned)
    removal_stats["removal_details"] = removed_details

    # Save removed samples if path provided
    if save_removed_path and not df_removed.empty:
        save_path = Path(save_removed_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        df_removed.to_csv(save_path, index=False)
        removal_stats["saved_path"] = str(save_path)
    elif save_removed_path:
        # Create empty file if no rows were removed
        save_path = Path(save_removed_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(columns=df_removed.columns).to_csv(save_path, index=False)
        removal_stats["saved_path"] = str(save_path)

    return df_cleaned, df_removed, removal_stats


def get_numeric_traits_only(
    df: pd.DataFrame,
    barcode_col: str = "Barcode",
    genotype_col: str = "geno",
    replicate_col: Optional[str] = "rep",
    additional_exclude: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Extract only numeric trait columns for analysis.

    Args:
        df: Full trait dataframe
        barcode_col: Name of the barcode/plant ID column to exclude (default: "Barcode")
        genotype_col: Name of the genotype column to exclude (default: "geno")
        replicate_col: Name of the replicate column to exclude if present (default: "rep")
        additional_exclude: Additional columns to exclude (e.g., date columns)

    Returns:
        DataFrame with only numeric trait columns
    """
    trait_cols = get_trait_columns(
        df,
        barcode_col=barcode_col,
        genotype_col=genotype_col,
        replicate_col=replicate_col,
        additional_exclude=additional_exclude,
    )
    return df[trait_cols].copy()


def remove_low_heritability_traits(
    df: pd.DataFrame,
    heritability_results: Dict[str, Dict],
    heritability_threshold: float = 0.3,
    barcode_col: str = "Barcode",
    genotype_col: str = "geno",
    replicate_col: Optional[str] = "rep",
    additional_exclude: Optional[List[str]] = None,
) -> Tuple[pd.DataFrame, List[str], Dict]:
    """Remove traits with heritability below threshold.

    Args:
        df: Trait dataframe
        heritability_results: Dictionary with heritability results for each trait
        heritability_threshold: Minimum H² value to retain trait (default: 0.3)
        barcode_col: Name of the barcode/plant ID column (default: "Barcode")
        genotype_col: Name of the genotype column (default: "geno")
        replicate_col: Name of the replicate column if present (default: "rep")
        additional_exclude: Additional columns to exclude from trait columns

    Returns:
        Tuple of:
            - DataFrame with low heritability traits removed
            - List of removed trait names
            - Dictionary with removal details
    """
    # Get current trait columns - MUST pass column parameters!
    trait_cols = get_trait_columns(
        df,
        barcode_col=barcode_col,
        genotype_col=genotype_col,
        replicate_col=replicate_col,
        additional_exclude=additional_exclude,
    )
    metadata_cols = [col for col in df.columns if col not in trait_cols]

    # Identify traits to remove
    traits_to_remove = []
    removal_details = []

    for trait in trait_cols:
        if trait in heritability_results:
            result = heritability_results[trait]
            if isinstance(result, dict) and "heritability" in result:
                h2 = result["heritability"]
                if h2 < heritability_threshold:
                    traits_to_remove.append(trait)
                    removal_details.append(
                        {
                            "trait": trait,
                            "heritability": h2,
                            "reason": f"H² = {h2:.3f} < threshold ({heritability_threshold})",
                            "genetic_variance": result.get(
                                "var_genetic", result.get("genetic_variance", np.nan)
                            ),
                            "environmental_variance": result.get(
                                "var_residual",
                                result.get("environmental_variance", np.nan),
                            ),
                        }
                    )
            else:
                # Invalid heritability result - consider removing
                traits_to_remove.append(trait)
                removal_details.append(
                    {
                        "trait": trait,
                        "heritability": np.nan,
                        "reason": "Invalid heritability estimate",
                        "genetic_variance": (
                            result.get(
                                "var_genetic", result.get("genetic_variance", np.nan)
                            )
                            if isinstance(result, dict)
                            else np.nan
                        ),
                        "environmental_variance": (
                            result.get(
                                "var_residual",
                                result.get("environmental_variance", np.nan),
                            )
                            if isinstance(result, dict)
                            else np.nan
                        ),
                    }
                )
        else:
            # No heritability result - consider removing
            traits_to_remove.append(trait)
            removal_details.append(
                {
                    "trait": trait,
                    "heritability": np.nan,
                    "reason": "No heritability estimate available",
                    "genetic_variance": np.nan,
                    "environmental_variance": np.nan,
                }
            )

    # Keep only high heritability traits
    traits_to_keep = [t for t in trait_cols if t not in traits_to_remove]
    df_cleaned = df[metadata_cols + traits_to_keep].copy()

    # Create summary
    summary = {
        "threshold": heritability_threshold,
        "original_traits": len(trait_cols),
        "removed_traits": len(traits_to_remove),
        "retained_traits": len(traits_to_keep),
        "removal_details": removal_details,
    }

    return df_cleaned, traits_to_remove, summary


def remove_zero_inflated_traits(
    df: pd.DataFrame,
    trait_cols: List[str],
    max_zero_fraction: float = 0.5,
) -> Tuple[pd.DataFrame, List[str], Dict]:
    """Remove traits with excessive zero values.

    Args:
        df: DataFrame with trait data
        trait_cols: List of trait column names to check
        max_zero_fraction: Maximum allowed fraction of zeros (0-1)

    Returns:
        Tuple of (filtered_dataframe, remaining_trait_cols, removal_details)
    """
    removed_traits = []
    removal_details = {}
    df_filtered = df.copy()

    for trait in trait_cols:
        if trait in df.columns:
            zero_fraction = (df[trait] == 0).sum() / len(df)
            if zero_fraction > max_zero_fraction:
                removed_traits.append(trait)
                removal_details[trait] = {
                    "reason": "too_many_zeros",
                    "zero_fraction": float(zero_fraction),
                    "threshold": max_zero_fraction,
                }

    if removed_traits:
        df_filtered = df_filtered.drop(columns=removed_traits)

    remaining_traits = [t for t in trait_cols if t not in removed_traits]

    return df_filtered, remaining_traits, removal_details


def remove_traits_with_many_nans(
    df: pd.DataFrame,
    trait_cols: List[str],
    max_nan_fraction: float = 0.3,
) -> Tuple[pd.DataFrame, List[str], Dict]:
    """Remove traits with excessive NaN values.

    Args:
        df: DataFrame with trait data
        trait_cols: List of trait column names to check
        max_nan_fraction: Maximum allowed fraction of NaNs (0-1)

    Returns:
        Tuple of (filtered_dataframe, remaining_trait_cols, removal_details)
    """
    removed_traits = []
    removal_details = {}
    df_filtered = df.copy()

    for trait in trait_cols:
        if trait in df.columns:
            nan_fraction = df[trait].isna().sum() / len(df)
            if nan_fraction > max_nan_fraction:
                removed_traits.append(trait)
                removal_details[trait] = {
                    "reason": "too_many_nans",
                    "nan_fraction": float(nan_fraction),
                    "threshold": max_nan_fraction,
                }

    if removed_traits:
        df_filtered = df_filtered.drop(columns=removed_traits)

    remaining_traits = [t for t in trait_cols if t not in removed_traits]

    return df_filtered, remaining_traits, removal_details


def remove_low_sample_traits(
    df: pd.DataFrame,
    trait_cols: List[str],
    min_samples: int = 10,
) -> Tuple[pd.DataFrame, List[str], Dict]:
    """Remove traits with insufficient valid samples.

    Args:
        df: DataFrame with trait data
        trait_cols: List of trait column names to check
        min_samples: Minimum number of valid (non-NaN) samples required

    Returns:
        Tuple of (filtered_dataframe, remaining_trait_cols, removal_details)
    """
    removed_traits = []
    removal_details = {}
    df_filtered = df.copy()

    for trait in trait_cols:
        if trait in df.columns:
            valid_samples = df[trait].notna().sum()
            if valid_samples < min_samples:
                removed_traits.append(trait)
                removal_details[trait] = {
                    "reason": "insufficient_samples",
                    "valid_samples": int(valid_samples),
                    "required_samples": min_samples,
                }

    if removed_traits:
        df_filtered = df_filtered.drop(columns=removed_traits)

    remaining_traits = [t for t in trait_cols if t not in removed_traits]

    return df_filtered, remaining_traits, removal_details


def apply_data_cleanup_filters(
    df: pd.DataFrame,
    trait_cols: List[str],
    max_zeros_per_trait: float = 0.5,
    max_nans_per_trait: float = 0.3,
    max_nans_per_sample: float = 0.2,
    min_samples_per_trait: int = 10,
) -> Tuple[pd.DataFrame, Dict]:
    """Apply configurable data cleanup filters to minimize sample loss.

    This function orchestrates multiple cleanup steps using modular functions:
    1. Remove zero-inflated traits
    2. Remove traits with many NaNs
    3. Remove samples with many NaNs
    4. Remove traits with insufficient samples

    Args:
        df: Original dataframe
        trait_cols: List of trait column names
        max_zeros_per_trait: Maximum fraction of zeros allowed per trait (0-1)
        max_nans_per_trait: Maximum fraction of NaNs allowed per trait (0-1)
        max_nans_per_sample: Maximum fraction of NaNs allowed per sample (0-1)
        min_samples_per_trait: Minimum number of valid samples required per trait

    Returns:
        Tuple of (cleaned_dataframe, cleanup_log)
    """
    cleanup_log = {
        "original_samples": len(df),
        "original_traits": len(trait_cols),
        "removed_traits": [],
        "removed_samples": [],
        "cleanup_steps": [],
        "removed_samples_detail": [],  # Detailed info about removed samples
    }

    df_clean = df.copy()
    valid_traits = trait_cols.copy()

    # Step 1: Remove traits with too many zeros
    df_clean, valid_traits, zero_removal_details = remove_zero_inflated_traits(
        df_clean, valid_traits, max_zero_fraction=max_zeros_per_trait
    )

    # Log removed traits
    for trait, details in zero_removal_details.items():
        cleanup_log["removed_traits"].append({"trait": trait, **details})

    cleanup_log["cleanup_steps"].append(
        {
            "step": "remove_high_zero_traits",
            "traits_removed": len(zero_removal_details),
            "remaining_traits": len(valid_traits),
        }
    )

    # Step 2: Remove traits with too many NaNs
    df_clean, valid_traits, nan_removal_details = remove_traits_with_many_nans(
        df_clean, valid_traits, max_nan_fraction=max_nans_per_trait
    )

    # Log removed traits
    for trait, details in nan_removal_details.items():
        cleanup_log["removed_traits"].append({"trait": trait, **details})

    cleanup_log["cleanup_steps"].append(
        {
            "step": "remove_high_nan_traits",
            "traits_removed": len(nan_removal_details),
            "remaining_traits": len(valid_traits),
        }
    )

    # Step 3: Remove samples with too many NaNs (reuse existing function)
    if valid_traits:
        # Use existing remove_nan_samples function
        df_clean, df_removed, removal_stats = remove_nan_samples(
            df_clean,
            valid_traits,
            max_nan_fraction=max_nans_per_sample,
            save_removed_path=None,  # Don't save to file here
        )

        # Update cleanup log with sample removal details
        cleanup_log["removed_samples_detail"] = removal_stats.get(
            "removed_samples_detail", []
        )
        cleanup_log["removed_samples"] = cleanup_log["removed_samples_detail"]

        cleanup_log["cleanup_steps"].append(
            {
                "step": "remove_high_nan_samples",
                "samples_removed": removal_stats["samples_removed"],
                "remaining_samples": removal_stats["samples_retained"],
            }
        )

    # Step 4: Remove traits with insufficient samples after cleanup
    df_clean, valid_traits, low_sample_removal_details = remove_low_sample_traits(
        df_clean, valid_traits, min_samples=min_samples_per_trait
    )

    # Log removed traits
    for trait, details in low_sample_removal_details.items():
        cleanup_log["removed_traits"].append({"trait": trait, **details})

    cleanup_log["cleanup_steps"].append(
        {
            "step": "remove_low_sample_traits",
            "traits_removed": len(low_sample_removal_details),
            "remaining_traits": len(valid_traits),
        }
    )

    # Final summary
    cleanup_log["final_samples"] = len(df_clean)
    cleanup_log["final_traits"] = len(valid_traits)
    cleanup_log["samples_retained_fraction"] = (
        len(df_clean) / cleanup_log["original_samples"]
        if cleanup_log["original_samples"] > 0
        else 0
    )
    cleanup_log["traits_retained_fraction"] = (
        len(valid_traits) / cleanup_log["original_traits"]
        if cleanup_log["original_traits"] > 0
        else 0
    )

    return df_clean, cleanup_log


def inspect_nan_samples(
    df: pd.DataFrame,
    trait_cols: List[str],
    barcode_col: str = "Barcode",
    genotype_col: str = "geno",
    replicate_col: str = "rep",
    save_path: Optional[str] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Inspect samples with NaN values and provide detailed information.

    This function identifies samples containing NaN values in trait columns
    and creates a detailed report including which traits have missing values
    and the fraction of missing data per sample. When verbose=True, it logs
    helpful summary statistics about the NaN distribution.

    Args:
        df: DataFrame containing trait data
        trait_cols: List of trait column names to inspect
        barcode_col: Name of the barcode/sample ID column (default: "Barcode")
        genotype_col: Name of the genotype column (default: "geno")
        replicate_col: Name of the replicate column (default: "rep")
        save_path: Optional path to save the inspection results as CSV
        verbose: Whether to log inspection summary (default: True)

    Returns:
        DataFrame with NaN inspection details including:
        - sample_index: Original DataFrame index
        - barcode: Sample barcode/ID
        - genotype: Genotype name
        - rep: Replicate number
        - nan_count: Number of traits with NaN
        - nan_fraction: Fraction of traits with NaN
        - nan_traits: Semicolon-separated list of traits with NaN
        - data_status: Status indicator ("original_data_with_nan")

    Examples:
        >>> df = pd.DataFrame({
        ...     'Barcode': ['BC1', 'BC2', 'BC3'],
        ...     'geno': ['G1', 'G2', 'G3'],
        ...     'rep': [1, 2, 3],
        ...     'trait1': [1.0, np.nan, 3.0],
        ...     'trait2': [4.0, 5.0, np.nan]
        ... })
        >>> trait_cols = ['trait1', 'trait2']
        >>> inspection_df = inspect_nan_samples(df, trait_cols)
        >>> len(inspection_df)  # 2 samples have NaN
        2
    """
    # Identify samples with NaN values in trait columns
    samples_with_nan = df[trait_cols].isna().any(axis=1)
    n_samples_with_nan = samples_with_nan.sum()

    if n_samples_with_nan > 0:
        # Get samples with NaN
        nan_samples = df[samples_with_nan].copy()
        nan_info_detailed = []

        # Create detailed information for each sample with NaN
        for idx in nan_samples.index:
            # Find which traits have NaN for this sample
            nan_traits_list = [col for col in trait_cols if pd.isna(df.loc[idx, col])]
            nan_count = len(nan_traits_list)
            nan_fraction = nan_count / len(trait_cols) if len(trait_cols) > 0 else 0

            # Build info dictionary
            info = {
                "sample_index": int(idx),
                "nan_count": nan_count,
                "nan_fraction": nan_fraction,
                "nan_traits": "; ".join(nan_traits_list),
                "data_status": "original_data_with_nan",
            }

            # Add sample metadata if columns exist
            if barcode_col in df.columns:
                info["barcode"] = nan_samples.loc[idx, barcode_col]
            if genotype_col in df.columns:
                info["genotype"] = nan_samples.loc[idx, genotype_col]
            if replicate_col in df.columns:
                info["rep"] = nan_samples.loc[idx, replicate_col]

            nan_info_detailed.append(info)

        # Create DataFrame from collected information
        inspection_df = pd.DataFrame(nan_info_detailed)

        # Reorder columns for better readability
        col_order = []
        if "sample_index" in inspection_df.columns:
            col_order.append("sample_index")
        if "barcode" in inspection_df.columns:
            col_order.append("barcode")
        if "genotype" in inspection_df.columns:
            col_order.append("genotype")
        if "rep" in inspection_df.columns:
            col_order.append("rep")
        col_order.extend(["nan_count", "nan_fraction", "nan_traits", "data_status"])

        inspection_df = inspection_df[col_order]

        # Log helpful information if verbose
        if verbose:
            logger.info(
                f"Initial NaN inspection - found {len(inspection_df)} samples with NaN values"
            )
            logger.info(
                "These samples will be handled based on your data cleaning strategy"
            )

            # Show sample distribution of NaN counts
            nan_count_distribution = (
                inspection_df["nan_count"].value_counts().sort_index()
            )
            if len(nan_count_distribution) > 0:
                logger.info("NaN distribution:")
                for count, n_samples in nan_count_distribution.items():
                    logger.info(f"  - {n_samples} sample(s) with {count} NaN trait(s)")

            # Log summary statistics
            logger.info(
                f"Summary: {len(inspection_df)} samples with NaN values out of {len(df)} total samples ({len(inspection_df) / len(df) * 100:.1f}%)"
            )

        # Save to CSV if path provided
        if save_path is not None:
            inspection_df.to_csv(save_path, index=False)
            if verbose:
                logger.info(f"Saved NaN inspection to: {save_path}")

        return inspection_df
    else:
        # No samples with NaN
        if verbose:
            logger.info("No NaN values found in the data")

        # Return empty DataFrame with expected structure
        return pd.DataFrame(
            columns=[
                "sample_index",
                "barcode",
                "genotype",
                "rep",
                "nan_count",
                "nan_fraction",
                "nan_traits",
                "data_status",
            ]
        )
