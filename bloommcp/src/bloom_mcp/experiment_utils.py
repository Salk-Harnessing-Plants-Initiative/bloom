"""
Shared experiment discovery and column auto-detection for SLEAP tool modules.

All tool modules import from this instead of hardcoding EXPERIMENTS dicts.
"""

import logging
import os
import pandas as pd
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# --- Required environment variables (no silent defaults) ---

_REQUIRED_DIRS = {
    "BLOOM_TRAITS_DIR": "Directory containing experiment CSV files",
    "BLOOM_OUTPUT_DIR": "Directory for analysis output",
    "BLOOM_PLOTS_DIR": "Directory for generated plots",
}

_missing = [k for k in _REQUIRED_DIRS if not os.getenv(k)]
if _missing:
    raise RuntimeError(
        f"Missing required environment variables: {', '.join(_missing)}. "
        f"Set them in .env or docker-compose."
    )

TRAITS_DIR = Path(os.environ["BLOOM_TRAITS_DIR"])
OUTPUT_DIR = Path(os.environ["BLOOM_OUTPUT_DIR"])
PLOTS_DIR = Path(os.environ["BLOOM_PLOTS_DIR"])

PLOTS_URL = os.getenv("BLOOM_PLOTS_URL")
if not PLOTS_URL:
    raise RuntimeError("BLOOM_PLOTS_URL environment variable is required")

# --- Startup filesystem validation ---


def _validate_dirs() -> None:
    """Check that configured data directories exist and are writable."""
    for name, path in [
        ("BLOOM_TRAITS_DIR", TRAITS_DIR),
        ("BLOOM_OUTPUT_DIR", OUTPUT_DIR),
        ("BLOOM_PLOTS_DIR", PLOTS_DIR),
    ]:
        if not path.exists():
            raise RuntimeError(
                f"{name}={path} does not exist. Create it or fix the path."
            )
        if not path.is_dir():
            raise RuntimeError(f"{name}={path} is not a directory.")
        if not os.access(path, os.R_OK):
            logger.warning(f"{name}={path} is not readable")
        if not os.access(path, os.W_OK):
            logger.warning(f"{name}={path} is not writable — analysis output will fail")


_validate_dirs()

# metadata columns, matched case-insensitively
KNOWN_METADATA_COLS = {
    "scan_id",
    "plant_qr_code",
    "scan_path",
    "scanner_id",
    "species_id",
    "species_name",
    "species_genus",
    "species_species",
    "uploaded_at",
    "wave_id",
    "wave_number",
    "wave_name",
    "accession_id",
    "date_scanned",
    "experiment_id",
    "experiment_name",
    "germ_day",
    "germ_day_color",
    "phenotyper_id",
    "plant_age_days",
    "plant_id",
    "plant_name",
    "primary",
    "lateral",
    "crown",
    "barcode",
    "geno",
    "genotype",
    "rep",
    "replicate",
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
            with open(csv_path) as f:
                row_count = sum(1 for _ in f) - 1  # fast line count
            detected = detect_columns(df)

            # Try to extract experiment name from data
            exp_name = None
            if "experiment_name" in df.columns:
                exp_name = df["experiment_name"].iloc[0]

            experiments.append(
                {
                    "filename": csv_path.name,
                    "stem": csv_path.stem,
                    "rows": row_count,
                    "total_columns": len(df.columns),
                    "trait_columns": len(detected["trait_cols"]),
                    "experiment_name": exp_name or csv_path.stem,
                    "genotype_col": detected["genotype_col"],
                    "sample_id_col": detected["sample_id_col"],
                }
            )
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


def _resolve_versioned_cleaned(
    o_dir: Path,
    stem: str,
    version: str,
) -> tuple[Optional[Path], Optional[str], Optional[str]]:
    """Resolve a versioned cleaned CSV via the QC manifest.

    The manifest lives in the bloommcp-data bucket at
    `bloommcp_output/qc_<stem>/manifest.json`; the cleaned CSV is
    downloaded to a tmp Path so callers can `pd.read_csv(path)` unchanged.
    Caller is responsible for the tmp file's lifetime (OS tmp cleanup
    handles it on process exit).

    `o_dir` is accepted for signature compatibility with the pre-migration
    caller but is ignored — the storage prefix is fixed at
    `bloommcp_output`.

    Returns (path, source_label, error). On success, error is None and
    path points at the downloaded tmp CSV. On miss with version="latest",
    returns (None, None, None) so the caller falls back. On explicit
    version="v<N>" miss, returns (None, None, error_string).
    """
    import tempfile

    from bloom_mcp.storage import AnalysisDir, ManifestSchemaError
    from bloom_mcp.supabase_client import download_file, list_prefix

    analysis_dir = AnalysisDir("bloommcp_output", f"{stem}.csv", "qc")
    try:
        entry = analysis_dir.get_version(version)
    except ManifestSchemaError as e:
        return None, None, f"manifest schema error for '{stem}': {e}"

    if entry is None:
        if version == "latest":
            return None, None, None
        return (
            None,
            None,
            (
                f"Version {version!r} not found for experiment '{stem}'. "
                f"Use list_existing_analyses to see available versions."
            ),
        )

    rel = entry.outputs.get("_cleaned.csv")
    if not rel:
        if version == "latest":
            return None, None, None
        return None, None, (f"Version {entry.id} has no cleaned CSV output.")

    if entry.version_dir:
        version_dir = entry.version_dir
    else:
        try:
            siblings = list_prefix(analysis_dir.path)
        except Exception as e:
            return None, None, (f"Could not list {analysis_dir.path}: {e}")
        version_dir = next((n for n in siblings if n.startswith(f"{entry.id}_")), None)
        if version_dir is None:
            if version == "latest":
                return None, None, None
            return (
                None,
                None,
                (
                    f"Manifest references version {entry.id} but its directory was "
                    f"not found under {analysis_dir.path}."
                ),
            )

    key = analysis_dir.key(f"{version_dir}/{rel}")
    suffix = Path(rel).suffix or ".csv"
    tmp = Path(tempfile.NamedTemporaryFile(delete=False, suffix=suffix).name)
    try:
        download_file(key, tmp)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        if version == "latest":
            return None, None, None
        return (
            None,
            None,
            (
                f"Manifest references {rel} for version {entry.id} but the "
                f"download from storage failed: {e}"
            ),
        )
    return tmp, f"{entry.id}_cleaned", None


def load_experiment_data(
    filename: str,
    traits_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    require_clean: bool = False,
    version: str = "latest",
) -> tuple:
    """Load experiment CSV with auto-detected columns.

    Resolution order for version="latest" (the default):
      1. Versioned manifest entry (qc_<stem>/manifest.json -> latest -> _cleaned.csv)
      2. Legacy un-versioned cleaned CSV (qc_<stem>/<stem>_cleaned.csv) — preserves
         pre-migration behaviour; replaced by v0_legacy after the Phase B migration runs
      3. Raw CSV from BLOOM_TRAITS_DIR

    Args:
        filename: CSV filename (e.g., "alfalfa_gwas_wave2.csv")
        traits_dir: Override for BLOOM_TRAITS_DIR
        output_dir: Override for BLOOM_OUTPUT_DIR
        require_clean: If True, fail when no cleaned CSV exists (for UMAP)
        version: "latest" (default), "raw", or an explicit "v<N>"

    Returns:
        (df, trait_cols, column_config, source_label)
        source_label is one of "raw", "legacy_cleaned", or "v<N>_cleaned".
        On error: (None, None, None, error_string)
    """
    t_dir = traits_dir or TRAITS_DIR
    o_dir = output_dir or OUTPUT_DIR
    stem = Path(filename).stem

    if version != "raw":
        cleaned_path, source_label, error = _resolve_versioned_cleaned(
            o_dir, stem, version
        )
        if error:
            return None, None, None, error
        if cleaned_path is not None:
            df = pd.read_csv(cleaned_path)
            config = detect_columns(df)
            return df, config["trait_cols"], config, source_label

        if version == "latest":
            legacy_path = o_dir / f"qc_{stem}" / f"{stem}_cleaned.csv"
            if legacy_path.exists():
                df = pd.read_csv(legacy_path)
                config = detect_columns(df)
                return df, config["trait_cols"], config, "legacy_cleaned"

    if require_clean:
        return (
            None,
            None,
            None,
            (
                f"No cleaned dataset found for '{filename}'. "
                "UMAP cannot handle missing values. "
                "Run clean_experiment_data first."
            ),
        )

    raw_path = t_dir / filename
    if raw_path.exists():
        df = pd.read_csv(raw_path)
        config = detect_columns(df)
        return df, config["trait_cols"], config, "raw"

    available = [f.name for f in t_dir.glob("*.csv")] if t_dir.exists() else []
    avail_str = ", ".join(available) if available else "none"
    return (
        None,
        None,
        None,
        f"File '{filename}' not found in {t_dir}. Available: {avail_str}",
    )
