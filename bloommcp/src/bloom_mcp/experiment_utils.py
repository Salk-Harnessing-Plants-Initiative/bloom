"""
Shared experiment discovery and column auto-detection for SLEAP tool modules.

All tool modules import from this instead of hardcoding EXPERIMENTS dicts.
"""

import logging
import os
import warnings
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# --- Required environment variables (validated at startup, not at import) ---
#
# The package must be importable with no env set (`import bloom_mcp`, unit tests,
# tooling), mirroring the lazy bloom_mcp.supabase_client.validate_env(). The
# module-level paths below read env with empty-string fallbacks so import never
# crashes; validate_env() does the hard check (missing vars + dirs exist) and is
# called by server.main() before mcp.run(), so a misconfigured deploy still
# fails fast at boot.

_REQUIRED_DIRS = {
    "BLOOM_TRAITS_DIR": "Directory containing experiment CSV files",
    "BLOOM_OUTPUT_DIR": "Directory for analysis output",
    "BLOOM_PLOTS_DIR": "Directory for generated plots",
}

TRAITS_DIR = Path(os.getenv("BLOOM_TRAITS_DIR", ""))
OUTPUT_DIR = Path(os.getenv("BLOOM_OUTPUT_DIR", ""))
PLOTS_DIR = Path(os.getenv("BLOOM_PLOTS_DIR", ""))
PLOTS_URL = os.getenv("BLOOM_PLOTS_URL", "")


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


def validate_env() -> None:
    """Validate the BLOOM_*_DIR / BLOOM_PLOTS_URL env and the data dirs.

    Deferred from import to an explicit call (server startup) so the package
    imports with no env; mirrors :func:`bloom_mcp.supabase_client.validate_env`.
    """
    required = list(_REQUIRED_DIRS) + ["BLOOM_PLOTS_URL"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}. "
            f"Set them in .env or docker-compose."
        )
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


# Backend read failures (network / RLS / 5xx) are tagged in the error slot so
# the reader surfaces them as ExperimentReadError, not "not found" (#368 review).
READ_ERROR_PREFIX = "__read_error__: "


def _list_bucket_csv_names() -> list[str]:
    """Basenames of ``*.csv`` objects under ``bloommcp_input/``.

    Returns ``[]`` and logs a warning if the bucket can't be listed, so a
    storage outage degrades to the local mount rather than erroring every read.
    """
    try:
        from bloom_mcp.supabase_client import INPUT_PREFIX, list_prefix

        names = [n.rsplit("/", 1)[-1] for n in list_prefix(INPUT_PREFIX)]
        return [n for n in names if n.endswith(".csv")]
    except Exception as exc:
        logger.warning("could not list bloommcp_input/: %s", exc)
        return []


def _experiment_summary(df: pd.DataFrame, filename: str, rows: int) -> dict:
    """Build one list_experiments entry from a loaded frame.

    Column roles are detected on the *full* frame (not a 5-row sample) so the
    same file yields the same trait set regardless of source (#368 review).
    """
    stem = Path(filename).stem
    detected = detect_columns(df)
    raw_name = (
        df["experiment_name"].iloc[0]
        if "experiment_name" in df.columns and len(df) > 0
        else None
    )
    exp_name = (
        stem
        if raw_name is None or pd.isna(raw_name) or str(raw_name).strip() == ""
        else str(raw_name)
    )
    return {
        "filename": filename,
        "stem": stem,
        "rows": rows,
        "total_columns": len(df.columns),
        "trait_columns": len(detected["trait_cols"]),
        "experiment_name": exp_name,
        "genotype_col": detected["genotype_col"],
        "sample_id_col": detected["sample_id_col"],
    }


def list_experiments(traits_dir: Optional[Path] = None) -> list[dict]:
    """List experiment CSVs available for analysis.

    Reads the ``bloommcp_input/`` bucket first, then the local BLOOM_TRAITS_DIR
    mount (deprecated, retired in #370) for anything not already in the bucket;
    a bucket file shadows a same-named local one. Column roles and row counts
    come from the full frame in **both** sources, for consistency.

    Cost: this reads every input CSV in full (bucket download + local read) to
    build each summary — not a cheap metadata-only listing. A preview/range read
    is deferred to #370.

    Returns:
        List of dicts with keys: filename, stem, rows, total_columns,
        trait_columns, experiment_name, genotype_col, sample_id_col
    """
    experiments: list[dict] = []
    seen: set[str] = set()

    # Bucket inputs (bloommcp_input/) first; a bucket file shadows a local one.
    for name in _list_bucket_csv_names():
        try:
            from bloom_mcp.supabase_client import read_input_csv

            df = read_input_csv(name)
        except Exception as exc:
            logger.warning("skipping bucket input %r: %s", name, exc)
            continue
        experiments.append(_experiment_summary(df, name, len(df)))
        seen.add(name)

    # Local mount (deprecated) for anything not already in the bucket.
    d = traits_dir or TRAITS_DIR
    if d.exists():
        for csv_path in sorted(d.glob("*.csv")):
            if csv_path.name in seen:
                continue
            try:
                df = pd.read_csv(csv_path)
            except Exception as exc:
                logger.warning("skipping local input %r: %s", csv_path.name, exc)
                continue
            experiments.append(_experiment_summary(df, csv_path.name, len(df)))

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


# Logical output key + filename for the cleaned trait CSV. The producer
# (`qc_clean`, `run_qc_workflow`) and the `require_clean` consumer
# (`_resolve_versioned_cleaned`) MUST agree on this string, so it lives here and
# is imported on both sides rather than repeated as a literal.
CLEANED_CSV_NAME = "_cleaned.csv"


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

    rel = entry.outputs.get(CLEANED_CSV_NAME)
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
      3. Raw CSV from the bloommcp_input/ bucket, then the local BLOOM_TRAITS_DIR
         mount (deprecated, retired in #370)

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

    # Raw input: prefer the bloommcp_input/ bucket; fall back to the local
    # BLOOM_TRAITS_DIR mount (deprecated, retired in #370). Listing the bucket
    # first distinguishes a genuine miss (fall through to local) from a backend
    # failure on a file that IS there — the latter is surfaced, not silently
    # served from a possibly-stale local copy (#368 review).
    bucket_names = _list_bucket_csv_names()
    if filename in bucket_names:
        try:
            from bloom_mcp.supabase_client import read_input_csv

            df = read_input_csv(filename)
        except Exception as exc:
            logger.warning("bucket read failed for %r: %s", filename, exc)
            return (
                None,
                None,
                None,
                f"{READ_ERROR_PREFIX}could not read {filename!r} from bloommcp_input/",
            )
        config = detect_columns(df)
        return df, config["trait_cols"], config, "raw"

    raw_path = t_dir / filename
    if raw_path.exists():
        warnings.warn(
            "Reading raw experiment inputs from the local BLOOM_TRAITS_DIR is "
            "deprecated; upload inputs to Supabase Storage (bloommcp_input/).",
            DeprecationWarning,
            stacklevel=2,
        )
        df = pd.read_csv(raw_path)
        config = detect_columns(df)
        return df, config["trait_cols"], config, "raw"

    # Build the hint from names only (no per-file downloads).
    local_names = [p.name for p in t_dir.glob("*.csv")] if t_dir.exists() else []
    avail_str = ", ".join(sorted(set(bucket_names) | set(local_names))) or "none"
    return (
        None,
        None,
        None,
        f"File '{filename}' not found in bloommcp_input/ or the local mount. "
        f"Available: {avail_str}",
    )
