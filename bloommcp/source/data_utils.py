"""Utility functions for data processing and file management."""

import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union, Any
from datetime import datetime
import json
import shutil
import re


def create_run_directory(base_dir: Path) -> Path:
    """Create timestamped run directory for outputs.

    Args:
        base_dir: Base directory for runs

    Returns:
        Path to created run directory
    """
    base_dir = Path(base_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = base_dir / f"run_{timestamp}"

    run_dir.mkdir(parents=True, exist_ok=True)

    return run_dir


def convert_to_json_serializable(obj):
    """Convert numpy types, Path objects, and other types to JSON serializable types.

    Handles:
    - dicts, lists, tuples: recursively converts contents
    - Path objects: converted to POSIX string
    - numpy types: converted to Python native types
    - Non-serializable objects (e.g., sklearn PCA): converted to type name string
    """
    if isinstance(obj, dict):
        return {k: convert_to_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_json_serializable(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(convert_to_json_serializable(item) for item in obj)
    elif isinstance(obj, Path):
        return obj.as_posix()
    elif isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    elif isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif hasattr(obj, "tolist"):
        return obj.tolist()
    elif isinstance(obj, (str, int, float, type(None))):
        return obj
    else:
        # For non-serializable objects (e.g., sklearn models), store type name
        return f"<{type(obj).__name__}>"


def _detect_depth_suffix(col_name: str) -> Optional[float]:
    """Extract numeric depth value from column name with _Ncm suffix.

    Args:
        col_name: Column name to check (e.g., "RootDW_15cm", "RootCount_7.5cm")

    Returns:
        Depth value as float if pattern matches, None otherwise

    Examples:
        >>> _detect_depth_suffix("RootDW_15cm")
        15.0
        >>> _detect_depth_suffix("RootCount_7.5cm")
        7.5
        >>> _detect_depth_suffix("Median.Number.of.Roots")
        None
    """
    import re

    # Match pattern: underscore followed by number (int or float) then "cm" at end
    match = re.search(r"_(\d+\.?\d*)cm$", col_name)
    return float(match.group(1)) if match else None


def _format_depth_range(depth: float, mapping: Optional[Dict[float, str]]) -> str:
    """Format depth value as range string using mapping or fallback to original.

    Args:
        depth: Numeric depth value (e.g., 15.0, 45.0)
        mapping: Optional dict mapping depths to ranges (e.g., {15.0: "0-30"})

    Returns:
        Formatted depth string with "cm" suffix

    Examples:
        >>> _format_depth_range(15.0, {15.0: "0-30", 45.0: "30-60"})
        '0-30cm'
        >>> _format_depth_range(15.0, None)
        '15cm'
        >>> _format_depth_range(7.5, {})
        '7.5cm'
    """
    if mapping and depth in mapping:
        # Map to range notation
        return f"{mapping[depth]}cm"
    else:
        # Fallback to original depth (format as int if whole number)
        if depth == int(depth):
            return f"{int(depth)}cm"
        else:
            return f"{depth}cm"


def sanitize_trait_names(
    df: pd.DataFrame,
    trait_cols: List[str],
    abbreviate: bool = True,
    return_mapping: bool = False,
    sanitize_metadata: bool = True,
    genotype_col: Optional[str] = None,
    replicate_col: Optional[str] = None,
    barcode_col: Optional[str] = None,
    custom_replacements: Optional[Dict[str, str]] = None,
    depth_range_mapping: Optional[Dict[float, str]] = None,
) -> Union[pd.DataFrame, Tuple[pd.DataFrame, Dict[str, str]]]:
    """Sanitize trait column names and optionally metadata columns for better visualization.

    Converts trait names like "Median.Number.of.Roots" to more readable formats
    like "Med Num Roots" (with abbreviations) or "Median Number Roots" (without).

    Also sanitizes metadata column names for consistency if column names are provided.
    For example, if genotype_col="geno", it will be renamed to "Genotype".

    Transformations applied to trait columns:
    - Apply custom user-defined word replacements (if provided)
    - Replace dots and hyphens with spaces
    - Remove common filler words ("of", "the")
    - Convert units to parenthetical format with proper symbols
    - Optionally abbreviate common words
    - Apply depth range mapping for biomass/root count columns
    - Title case the result

    Args:
        df: DataFrame containing trait data
        trait_cols: List of trait column names to sanitize
        abbreviate: If True, abbreviate common words (Default: True)
        return_mapping: If True, return (df, mapping_dict) tuple (Default: False)
        sanitize_metadata: If True, also sanitize metadata columns (Default: True)
        genotype_col: Current genotype column name (e.g., "geno") to rename to "Genotype"
        replicate_col: Current replicate column name (e.g., "rep") to rename to "Replicate"
        barcode_col: Current barcode column name to ensure title case as "Barcode"
        custom_replacements: Optional dict mapping old terms to new terms for
            domain-specific terminology (e.g., {"crown": "seminal"} to replace
            "crown" with "seminal" in trait names). Matching is case-insensitive.
        depth_range_mapping: Optional dict mapping depth midpoints to depth ranges
            (e.g., {15.0: "0-30", 45.0: "30-60"} maps RootDW_15cm to "0-30cm" range).
            Used for biomass and root counting columns to show actual measurement ranges.

    Returns:
        If return_mapping=False: DataFrame with sanitized column names
        If return_mapping=True: Tuple of (DataFrame, mapping dict)

    Examples:
        >>> df, mapping = sanitize_trait_names(
        ...     df, trait_cols, abbreviate=True, return_mapping=True
        ... )
        >>> mapping["Median.Number.of.Roots"]
        "Med Num Roots"
        >>> mapping["Total.Root.Length.mm"]
        "Total Root Length (mm)"

        >>> # With custom replacements
        >>> df = sanitize_trait_names(
        ...     df, trait_cols,
        ...     custom_replacements={"crown": "seminal", "primary": "main"}
        ... )
        >>> # "crown_length_mm" -> "Seminal Length (mm)"
        >>> # "primary.root.angle" -> "Main Root Angle"

        >>> # With depth range mapping for biomass
        >>> depth_map = {15.0: "0-30", 45.0: "30-60"}
        >>> df = sanitize_trait_names(
        ...     df, trait_cols,
        ...     depth_range_mapping=depth_map
        ... )
        >>> # "RootDW_15cm" -> "Root Biomass DW (g) 0-30cm"
        >>> # "RootDW_45cm" -> "Root Biomass DW (g) 30-60cm"
    """
    df_copy = df.copy()
    name_mapping = {}

    # Sanitize metadata columns first (if enabled and columns are provided)
    if sanitize_metadata:
        # Build metadata mappings from provided column names
        metadata_mappings = {}

        if genotype_col and genotype_col in df_copy.columns:
            if genotype_col != "Genotype":
                metadata_mappings[genotype_col] = "Genotype"

        if replicate_col and replicate_col in df_copy.columns:
            if replicate_col != "Replicate":
                metadata_mappings[replicate_col] = "Replicate"

        if barcode_col and barcode_col in df_copy.columns:
            if barcode_col != "Barcode":
                metadata_mappings[barcode_col] = "Barcode"

        # Apply metadata column renaming
        for old_col, new_col in metadata_mappings.items():
            df_copy.rename(columns={old_col: new_col}, inplace=True)
            name_mapping[old_col] = new_col

    # Define abbreviation mappings
    abbreviations = (
        {
            "Number": "Num",
            "Average": "Avg",
            "Maximum": "Max",
            "Minimum": "Min",
            "Median": "Med",
            "Frequency": "Freq",
            "Orientation": "Orient",
        }
        if abbreviate
        else {}
    )

    # Words to remove for brevity
    filler_words = {"of", "the"}

    for old_name in trait_cols:
        if old_name not in df_copy.columns:
            continue

        new_name = old_name

        # Handle unit suffixes first (before splitting)
        # Order matters: check longer patterns first to avoid partial matches
        unit_replacements = {
            ".mm3": " (mm³)",
            ".mm2": " (mm²)",
            ".mm": " (mm)",
            ".deg": " (°)",
            "_mm3": " (mm³)",
            "_mm2": " (mm²)",
            "_mm": " (mm)",
            "_deg": " (°)",
            "_mg": " (mg)",
            "_g": " (g)",
        }
        for old_unit, new_unit in unit_replacements.items():
            if new_name.endswith(old_unit):
                new_name = new_name[: -len(old_unit)] + new_unit
                break

        # Apply depth range mapping for biomass/root count columns (if provided)
        # This must happen AFTER unit conversion but BEFORE splitting
        # For depth ranges, we need special handling to preserve the range notation
        depth_range_applied = False
        depth = _detect_depth_suffix(new_name)
        if depth is not None:
            # This column has a depth suffix (_Ncm)
            if depth_range_mapping and depth in depth_range_mapping:
                # Depth is in mapping - this is a range (biomass)
                range_str = depth_range_mapping[depth]  # e.g., "0-30"
                # Extract prefix (e.g., "RootDW")
                prefix = re.sub(r"_\d+\.?\d*cm$", "", new_name)
                # Build final name with proper formatting
                # Format: "Root Biomass DW (g) 0-30cm"
                if "DW" in prefix or "dw" in prefix.lower():
                    new_name = f"Root Biomass DW (g) {range_str}cm"
                elif "Count" in prefix:
                    new_name = f"Root Count {range_str}cm"
                depth_range_applied = True
            else:
                # Depth suffix detected but not in mapping (or no mapping provided)
                # Handle as single-depth measurement (root counting)
                prefix = re.sub(r"_\d+\.?\d*cm$", "", new_name)
                depth_str = _format_depth_range(
                    depth, None
                )  # Format as "5cm" or "7.5cm"
                # Build final name for single-depth measurements
                # Format: "Root Count 5cm"
                if "Count" in prefix:
                    new_name = f"Root Count {depth_str}"
                    depth_range_applied = True
                elif "DW" in prefix or "dw" in prefix.lower():
                    # Single-depth biomass (fallback to standard processing)
                    new_name = re.sub(r"_\d+\.?\d*cm$", f"_{depth_str}", new_name)

        # Split by dots, hyphens, and underscores (only if depth range not already applied)
        if depth_range_applied:
            # Already formatted, skip splitting
            parts = [new_name]
        else:
            parts = (
                new_name.replace(".", " ").replace("-", " ").replace("_", " ").split()
            )

        # Apply custom replacements (case-insensitive)
        if custom_replacements:
            # Create lowercase mapping for case-insensitive matching
            lower_replacements = {k.lower(): v for k, v in custom_replacements.items()}
            parts = [lower_replacements.get(part.lower(), part) for part in parts]

        # Remove filler words and apply abbreviations
        processed_parts = []
        for part in parts:
            # Skip filler words (case-insensitive)
            if part.lower() in filler_words:
                continue

            # Skip empty parts
            if not part.strip():
                continue

            # Apply abbreviations if enabled (case-insensitive matching)
            if abbreviate:
                # Check if this part (title-cased) matches any abbreviation key
                part_titlecase = part.title()
                if part_titlecase in abbreviations:
                    processed_parts.append(abbreviations[part_titlecase])
                else:
                    processed_parts.append(part)
            else:
                processed_parts.append(part)

        # Join and apply title case for consistent capitalization
        # Skip if depth range was already applied (already properly formatted)
        if not depth_range_applied:
            new_name = " ".join(processed_parts)
            new_name = new_name.strip().title()

            # Fix units to be lowercase (e.g., "(G)" -> "(g)", "(Mm)" -> "(mm)")
            # Keep scientific symbols correct
            new_name = re.sub(r"\(Mm³\)", "(mm³)", new_name)
            new_name = re.sub(r"\(Mm²\)", "(mm²)", new_name)
            new_name = re.sub(r"\(Mm\)", "(mm)", new_name)
            new_name = re.sub(r"\(G\)", "(g)", new_name)
            new_name = re.sub(r"\(Mg\)", "(mg)", new_name)
            # Fix metric units after numbers (title() converts "15cm" to "15Cm")
            # Note: Only fix unambiguous units (cm, mm, kg). We intentionally skip
            # standalone "M" because it could mean millions (e.g., "15M" = 15 million)
            # rather than meters. Real meter values typically use "m" with prefixes.
            # Regex: \d+(?:\.\d+)? matches whole numbers (15) or decimals (15.5),
            # but not trailing dots (15.) which would be malformed.
            new_name = re.sub(r"(\d+(?:\.\d+)?)Cm\b", r"\1cm", new_name)
            new_name = re.sub(r"(\d+(?:\.\d+)?)Mm\b", r"\1mm", new_name)
            new_name = re.sub(r"(\d+(?:\.\d+)?)Kg\b", r"\1kg", new_name)
            # Keep degree symbol as-is
        else:
            # Depth range already applied, name is already properly formatted
            # Just use it as-is (processed_parts has single element with full name)
            new_name = processed_parts[0] if processed_parts else new_name

        # Store mapping
        name_mapping[old_name] = new_name

        # Rename column in dataframe
        if old_name != new_name:
            df_copy.rename(columns={old_name: new_name}, inplace=True)

    if return_mapping:
        return df_copy, name_mapping
    else:
        return df_copy


def link_rhizovision_images_to_samples(
    df: pd.DataFrame,
    image_dir: Path | str,
    image_types: Optional[List[str]] = None,
    barcode_col: str = "Barcode",
) -> Dict[str, Dict[str, Optional[Path]]]:
    """Link Rhizovision images to their corresponding sample barcodes.

    This function is specific to Rhizovision image naming conventions,
    expecting filenames in the format: {barcode}_{suffix}

    Args:
        df: Trait dataframe with barcode/ID column
        image_dir: Directory containing Rhizovision processed images
        image_types: List of Rhizovision image suffixes to look for (default: ['features.png', 'seg.png'])
        barcode_col: Name of the barcode/plant ID column (default: "Barcode")

    Returns:
        Dictionary mapping barcode to Rhizovision image paths
    """
    if image_types is None:
        image_types = ["features.png", "seg.png"]

    image_dir = Path(image_dir)
    image_links = {}

    # Check if barcode column exists
    if barcode_col not in df.columns:
        raise ValueError(
            f"Barcode column '{barcode_col}' not found in dataframe. Available columns: {df.columns.tolist()[:10]}..."
        )

    for barcode in df[barcode_col]:
        image_links[barcode] = {}

        for img_type in image_types:
            # Images follow pattern: {barcode}_c1_p1_{type}
            img_filename = f"{barcode}_c1_p1_{img_type}"
            img_path = image_dir / img_filename

            if img_path.exists():
                image_links[barcode][img_type] = img_path
            else:
                image_links[barcode][img_type] = None

    return image_links


def link_cylinder_images_from_scan_path(
    df: pd.DataFrame,
    base_dir: Path | str,
    image_types: Optional[List[str]] = None,
    barcode_col: str = "Barcode",
    scan_path_col: str = "scan_path",
) -> Dict[str, Dict[str, Optional[Path]]]:
    """Link cylinder scanner images using the scan_path column in trait data.

    This function is specific to cylinder scanner image organization where images
    are stored in directories following the pattern: Wave/Day/Barcode/ with numbered
    JPG files (1.jpg, 2.jpg, ... 72.jpg) representing different rotation angles.

    Args:
        df: Trait dataframe with barcode and scan_path columns.
        base_dir: Base directory containing the images_downloader_output folder,
            or the images_downloader_output folder itself.
        image_types: List of image filenames to look for (default: ["1.jpg", "36.jpg"]).
            These typically represent specific rotation angles of the cylinder.
        barcode_col: Name of the barcode/plant ID column (default: "Barcode").
        scan_path_col: Name of the column containing the scan path (default: "scan_path").

    Returns:
        Dictionary mapping barcode to image paths, compatible with
        create_genotype_image_grid() and other visualization functions.

    Example:
        >>> from sleap_roots_analyze.data_utils import link_cylinder_images_from_scan_path
        >>> image_links = link_cylinder_images_from_scan_path(
        ...     df=df_traits,
        ...     base_dir="Z:/users/eberrigan/.../images_downloader_output",
        ...     image_types=["1.jpg", "36.jpg"],  # Front and back views
        ... )
        >>> # Use with create_genotype_image_grid
        >>> fig = create_genotype_image_grid(df, image_links, "GH_7293", image_type="1.jpg")
    """
    if image_types is None:
        image_types = ["1.jpg", "36.jpg"]

    base_dir = Path(base_dir)
    image_links = {}

    # Validate required columns
    if barcode_col not in df.columns:
        raise ValueError(
            f"Barcode column '{barcode_col}' not found in dataframe. "
            f"Available columns: {df.columns.tolist()[:10]}..."
        )
    if scan_path_col not in df.columns:
        raise ValueError(
            f"Scan path column '{scan_path_col}' not found in dataframe. "
            f"Available columns: {df.columns.tolist()[:10]}..."
        )

    for _, row in df.iterrows():
        barcode = row[barcode_col]
        scan_path = row.get(scan_path_col)

        image_links[barcode] = {}

        if scan_path is None or pd.isna(scan_path):
            # No scan path available
            for img_type in image_types:
                image_links[barcode][img_type] = None
            continue

        # Handle relative paths that start with "./"
        scan_path_str = str(scan_path)
        if scan_path_str.startswith("./images_downloader_output"):
            # Extract the relative part after images_downloader_output
            relative_part = scan_path_str.replace("./images_downloader_output/", "")
            img_dir = base_dir / relative_part
        elif scan_path_str.startswith("./"):
            # Other relative path
            img_dir = base_dir / scan_path_str[2:]
        else:
            # Assume it's already a full or relative path
            img_dir = base_dir / scan_path_str

        for img_type in image_types:
            img_path = img_dir / img_type

            if img_path.exists():
                image_links[barcode][img_type] = img_path
            else:
                image_links[barcode][img_type] = None

    return image_links


def setup_analysis_directories(
    base_dir: Union[str, Path], subdirs: Optional[List[str]] = None
) -> Dict[str, Path]:
    """Create organized directory structure for analysis outputs.

    Args:
        base_dir: Base run directory
        subdirs: List of subdirectory names to create

    Returns:
        Dictionary mapping directory names to Path objects
    """
    if subdirs is None:
        subdirs = [
            "figures",
            "publication_figures",
            "interactive_plots",
            "analysis_outputs",
        ]

    base_dir = Path(base_dir)
    directories = {"base": base_dir}

    for subdir in subdirs:
        dir_path = base_dir / subdir
        dir_path.mkdir(parents=True, exist_ok=True)
        directories[subdir] = dir_path

    return directories


def save_notebook_snapshot(
    notebook_path: Union[str, Path],
    output_dir: Union[str, Path],
    prefix: str = "executed_notebook",
) -> Optional[Path]:
    """Save a snapshot of the current notebook with timestamp.

    Args:
        notebook_path: Path to the notebook file
        output_dir: Directory to save the snapshot
        prefix: Prefix for the saved file

    Returns:
        Path to saved notebook or None if failed
    """
    try:
        from nbformat import read, write

        notebook_path = Path(notebook_path)
        output_dir = Path(output_dir)

        # Read the notebook
        with open(notebook_path, "r", encoding="utf-8") as f:
            nb = read(f, as_version=4)

        # Create timestamped filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"{prefix}_{timestamp}.ipynb"

        # Write the snapshot
        with open(output_path, "w", encoding="utf-8") as f:
            write(nb, f)

        return output_path

    except Exception as e:
        print(f"⚠️ Could not save notebook snapshot: {e}")
        return None


def log_analysis_summary(
    summary_dict: Dict[str, Any],
    output_dir: Union[str, Path],
    filename: str = "analysis_summary.json",
) -> None:
    """Save and display analysis summary.

    Args:
        summary_dict: Dictionary containing analysis summary
        output_dir: Directory to save the summary
        filename: Name of the output file
    """
    output_dir = Path(output_dir)
    output_path = output_dir / filename

    # Convert to JSON-serializable format
    serializable_summary = convert_to_json_serializable(summary_dict)

    # Save to file
    with open(output_path, "w") as f:
        json.dump(serializable_summary, f, indent=2)

    print(f"💾 Analysis summary saved to: {output_path}")


def create_analysis_summary(
    df: pd.DataFrame,
    trait_cols: List[str],
    pca_results: Optional[Dict] = None,
    heritability_results: Optional[pd.DataFrame] = None,
    output_counts: Optional[Dict[str, int]] = None,
    genotype_col: str = "geno",
    replicate_col: str = "rep",
) -> Dict[str, Any]:
    """Create a comprehensive analysis summary.

    Args:
        df: Main dataframe
        trait_cols: List of trait column names
        pca_results: PCA analysis results
        heritability_results: Heritability results DataFrame
        output_counts: Counts of generated outputs
        genotype_col: Name of genotype column
        replicate_col: Name of replicate column

    Returns:
        Dictionary containing analysis summary
    """
    summary = {
        "data_overview": {
            "n_samples": len(df),
            "n_traits": len(trait_cols),
            "n_genotypes": (
                df[genotype_col].nunique() if genotype_col in df.columns else 0
            ),
            "n_replicates_per_genotype": (
                df.groupby(genotype_col)[replicate_col].nunique().mean()
                if genotype_col in df.columns and replicate_col in df.columns
                else 0
            ),
        },
        "timestamp": datetime.now().isoformat(),
    }

    if pca_results is not None:
        summary["pca_analysis"] = {
            "n_components_selected": pca_results.get("n_components_selected", 0),
            "total_variance_explained": float(
                pca_results.get("total_variance_explained", 0)
            ),
        }

    if heritability_results is not None:
        summary["heritability"] = {
            "mean_heritability": float(heritability_results["heritability"].mean()),
            "min_heritability": float(heritability_results["heritability"].min()),
            "max_heritability": float(heritability_results["heritability"].max()),
            "n_traits_analyzed": len(heritability_results),
        }

    if output_counts is not None:
        summary["outputs_generated"] = output_counts

    return summary
