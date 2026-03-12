"""
Shared experiment discovery and column auto-detection for SLEAP tool modules.

All tool modules import from this instead of hardcoding EXPERIMENTS dicts.
"""

import os
import pandas as pd
from pathlib import Path
from typing import Optional

TRAITS_DIR = Path(os.getenv("BLOOM_TRAITS_DIR", "/app/data/SLEAP_OUT_CSV"))
OUTPUT_DIR = Path(os.getenv("BLOOM_OUTPUT_DIR", "/app/data/ANALYSIS_OUTPUT"))
PLOTS_DIR = Path(os.getenv("BLOOM_PLOTS_DIR", "/app/data/PLOTS_DIR"))
PLOTS_URL = os.getenv("BLOOM_PLOTS_URL")
if not PLOTS_URL:
    raise RuntimeError("BLOOM_PLOTS_URL environment variable is required")

# metadata columns, matched case-insensitively
KNOWN_METADATA_COLS = {
    "scan_id", "plant_qr_code", "scan_path", "scanner_id",
    "species_id", "species_name", "species_genus", "species_species",
    "uploaded_at", "wave_id", "wave_number", "wave_name",
    "accession_id", "date_scanned", "experiment_id", "experiment_name",
    "germ_day", "germ_day_color", "phenotyper_id", "plant_age_days",
    "plant_id", "plant_name", "primary", "lateral", "crown",
    "barcode", "geno", "genotype", "rep", "replicate",
}

# Patterns to auto-detect special columns (checked case-insensitively)
GENOTYPE_PATTERNS = ["geno", "genotype", "accession", "species_name"]
REPLICATE_PATTERNS = ["rep", "replicate", "wave_number"]
SAMPLE_ID_PATTERNS = ["barcode", "plant_qr_code", "scan_id", "plant_id", "plant_name"]


def list_experiments(traits_dir: Optional[Path] = None) -> list[dict]:
    """Scan BLOOM_TRAITS_DIR for CSV files and return metadata about each.

    Returns:
        List of dicts with keys: filename, stem, rows, columns, trait_columns, experiment_name
    """
    d = traits_dir or TRAITS_DIR
    if not d.exists():
        return []

    experiments = []
    for csv_path in sorted(d.glob("*.csv")):
        try:
            df = pd.read_csv(csv_path, nrows=5)
            row_count = sum(1 for _ in open(csv_path)) - 1  # fast line count
            detected = detect_columns(df)

            # Try to extract experiment name from data
            exp_name = None
            if "experiment_name" in df.columns:
                exp_name = df["experiment_name"].iloc[0]

            experiments.append({
                "filename": csv_path.name,
                "stem": csv_path.stem,
                "rows": row_count,
                "total_columns": len(df.columns),
                "trait_columns": len(detected["trait_cols"]),
                "experiment_name": exp_name or csv_path.stem,
                "genotype_col": detected["genotype_col"],
                "sample_id_col": detected["sample_id_col"],
            })
        except Exception:
            continue

    return experiments


def detect_columns(df: pd.DataFrame) -> dict:
    """Auto-detect metadata vs trait columns, and identify special columns.

    Logic:
    - Known metadata column names → metadata
    - Non-numeric columns → metadata
    - Remaining numeric columns → traits

    Returns:
        {
            "trait_cols": [...],
            "metadata_cols": [...],
            "genotype_col": str or None,
            "replicate_col": str or None,
            "sample_id_col": str or None,
        }
    """
    metadata_cols = []
    trait_cols = []

    for col in df.columns:
        col_lower = col.lower().strip()

        # Known metadata name?
        if col_lower in KNOWN_METADATA_COLS:
            metadata_cols.append(col)
            continue

        # Non-numeric dtype?
        if not pd.api.types.is_numeric_dtype(df[col]):
            metadata_cols.append(col)
            continue

        # Numeric → trait
        trait_cols.append(col)

    # Detect special columns
    genotype_col = _find_column(df.columns, GENOTYPE_PATTERNS)
    replicate_col = _find_column(df.columns, REPLICATE_PATTERNS)
    sample_id_col = _find_column(df.columns, SAMPLE_ID_PATTERNS)

    return {
        "trait_cols": trait_cols,
        "metadata_cols": metadata_cols,
        "genotype_col": genotype_col,
        "replicate_col": replicate_col,
        "sample_id_col": sample_id_col,
    }


def _find_column(columns, patterns: list[str]) -> Optional[str]:
    """Find first column matching any pattern (case-insensitive exact match)."""
    col_lower_map = {c.lower().strip(): c for c in columns}
    for pattern in patterns:
        if pattern.lower() in col_lower_map:
            return col_lower_map[pattern.lower()]
    return None


def load_experiment_data(
    filename: str,
    traits_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    require_clean: bool = False,
) -> tuple:
    """Load experiment CSV with auto-detected columns.

    Checks for cleaned CSV first, falls back to raw.

    Args:
        filename: CSV filename (e.g., "alfalfa_gwas_wave2.csv")
        traits_dir: Override for BLOOM_TRAITS_DIR
        output_dir: Override for BLOOM_OUTPUT_DIR
        require_clean: If True, fail when no cleaned CSV exists (for UMAP)

    Returns:
        (df, trait_cols, column_config, source_label)
        column_config is the dict from detect_columns()
        On error: (None, None, None, error_string)
    """
    t_dir = traits_dir or TRAITS_DIR
    o_dir = output_dir or OUTPUT_DIR
    stem = Path(filename).stem

    # 1. Check for cleaned CSV from previous QC run
    cleaned_path = o_dir / f"qc_{stem}" / f"{stem}_cleaned.csv"
    if cleaned_path.exists():
        df = pd.read_csv(cleaned_path)
        config = detect_columns(df)
        return df, config["trait_cols"], config, f"cleaned ({cleaned_path.name})"

    # 2. Hard-fail if clean required (e.g., UMAP)
    if require_clean:
        return None, None, None, (
            f"No cleaned dataset found for '{filename}'. "
            "UMAP cannot handle missing values. "
            "Run clean_experiment_data first."
        )

    # 3. Fallback to raw CSV
    raw_path = t_dir / filename
    if raw_path.exists():
        df = pd.read_csv(raw_path)
        config = detect_columns(df)
        return df, config["trait_cols"], config, "raw (run clean_experiment_data for better results)"

    # 4. Not found
    available = [f.name for f in t_dir.glob("*.csv")] if t_dir.exists() else []
    avail_str = ", ".join(available) if available else "none"
    return None, None, None, f"File '{filename}' not found in {t_dir}. Available: {avail_str}"
