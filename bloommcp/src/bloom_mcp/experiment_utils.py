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


def _fully_local_root() -> Optional[Path]:
    """The configured ``BLOOM_LOCAL_ROOT`` when fully-local mode selected it, else ``None``.

    ``BLOOM_LOCAL_ROOT`` is a single opt-in root that supplies a default for the
    input/output/plots subpaths of fully-local mode (#479), beneath the existing
    granular overrides (``BLOOM_EXPERIMENT_LOCAL_ROOT`` / ``BLOOM_STORAGE_LOCAL_ROOT``
    / ``BLOOM_PLOTS_DIR``), which always win when set.

    ``BLOOM_STORAGE_BACKEND`` is read here (via ``is_local_backend``) only when
    ``BLOOM_LOCAL_ROOT`` is itself set — unset by default, and unset in every
    deployment that hasn't opted in — so this adds no observable import-time
    behavior for dev/staging/prod. ``is_local_backend`` never raises (a plain
    string compare), so this can't turn an invalid ``BLOOM_STORAGE_BACKEND`` value
    into an import-time crash.
    """
    raw = os.getenv("BLOOM_LOCAL_ROOT")
    if not raw:
        return None
    from bloom_mcp.storage_backend import is_local_backend

    if not is_local_backend():
        return None
    return Path(raw)


def _resolve_plots_dir() -> str:
    """``BLOOM_PLOTS_DIR`` if set, else ``<BLOOM_LOCAL_ROOT>/plots`` in fully-local mode."""
    explicit = os.getenv("BLOOM_PLOTS_DIR")
    if explicit:
        return explicit
    local_root = _fully_local_root()
    if local_root is not None:
        return str(local_root / "plots")
    return ""


TRAITS_DIR = Path(os.getenv("BLOOM_TRAITS_DIR", ""))
OUTPUT_DIR = Path(os.getenv("BLOOM_OUTPUT_DIR", ""))
PLOTS_DIR = Path(_resolve_plots_dir())
PLOTS_URL = os.getenv("BLOOM_PLOTS_URL", "")


def _ensure_subfolder(path: Path, label: str) -> None:
    """Auto-create a ``BLOOM_LOCAL_ROOT``-derived subfolder, failing clearly if blocked.

    Only the top-level ``BLOOM_LOCAL_ROOT`` folder must pre-exist (validated by
    ``_validate_local_root_dir``); its subfolders auto-create here, mirroring the
    ``PLOTS_DIR.mkdir(parents=True, exist_ok=True)`` idiom ``_viz_shared.save_plot``
    already uses, just run at boot instead of at first write. ``label`` names the
    subfolder in the raised error (e.g. "input root") without leaking the
    absolute host path.
    """
    if path.exists() and not path.is_dir():
        raise RuntimeError(f"BLOOM_LOCAL_ROOT's {label} exists but is not a directory.")
    existed = path.exists()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("could not create BLOOM_LOCAL_ROOT's %s: %s", label, path)
        raise RuntimeError(f"Could not create BLOOM_LOCAL_ROOT's {label}.") from exc
    if not existed:
        logger.info("created BLOOM_LOCAL_ROOT's %s: %s", label, path)


def _validate_local_root_dir(root: Path) -> None:
    """Fail fast if BLOOM_LOCAL_ROOT itself is missing, not a dir, or not writable.

    Runs once, early (from ``validate_env``), so a bad ``BLOOM_LOCAL_ROOT``
    surfaces as one clear error before any of the three subfolder-specific
    validators (input/output/plots) run. The absolute path is logged
    server-side only, not included in the raised message — mirrors
    ``_ensure_subfolder`` and ``validate_experiment_local_root``'s no-host-path
    -leak convention for boot errors that can appear in LLM-agent-visible
    tracebacks.
    """
    if not root.exists():
        logger.error("BLOOM_LOCAL_ROOT does not exist: %s", root)
        raise RuntimeError("BLOOM_LOCAL_ROOT does not exist.")
    if not root.is_dir():
        logger.error("BLOOM_LOCAL_ROOT is not a directory: %s", root)
        raise RuntimeError("BLOOM_LOCAL_ROOT is not a directory.")
    if not os.access(root, os.W_OK):
        logger.error("BLOOM_LOCAL_ROOT is not writable: %s", root)
        raise RuntimeError("BLOOM_LOCAL_ROOT is not writable.")


def resolve_experiment_local_root() -> Path:
    """The local input root for the opt-in ``LocalReader`` (fully-local mode).

    Precedence: ``BLOOM_EXPERIMENT_LOCAL_ROOT`` when explicitly set; otherwise
    ``<BLOOM_LOCAL_ROOT>/input`` when the single ``BLOOM_LOCAL_ROOT`` variable
    supplies a default (#479); otherwise ``BLOOM_TRAITS_DIR`` — mirroring how the
    object-storage ``local`` backend resolves its root
    (``BLOOM_STORAGE_LOCAL_ROOT`` → ``<BLOOM_LOCAL_ROOT>/output`` →
    ``BLOOM_OUTPUT_DIR``). ``BLOOM_TRAITS_DIR`` is required only when neither of
    the first two tiers applies (the default Supabase path, or fully-local mode
    without ``BLOOM_LOCAL_ROOT``) — see ``validate_env``'s conditional-optional
    handling. Read via the module-level ``TRAITS_DIR`` so tests that monkeypatch
    it are honoured. Unlike the storage-backend bridge fallback,
    ``BLOOM_TRAITS_DIR`` is a **supported** default here — this change promotes
    the local input path rather than retiring it.
    """
    explicit = os.getenv("BLOOM_EXPERIMENT_LOCAL_ROOT")
    if explicit:
        return Path(explicit)
    local_root = _fully_local_root()
    if local_root is not None:
        return local_root / "input"
    return TRAITS_DIR


def validate_experiment_local_root() -> None:
    """Fail fast at boot when fully-local mode has no usable local input root.

    Called by ``server.main()`` only in fully-local mode (the default Supabase
    path keeps validating Supabase credentials instead). The absolute root path
    is logged server-side but not included in the raised RuntimeError — boot
    errors can appear in LLM-agent-visible tracebacks, and the design goal is to
    avoid leaking host paths there. When the root is the ``BLOOM_LOCAL_ROOT``
    -derived default (``BLOOM_EXPERIMENT_LOCAL_ROOT`` unset), it is auto-created
    if missing rather than required to pre-exist; an explicitly-set
    ``BLOOM_EXPERIMENT_LOCAL_ROOT`` (or the ``BLOOM_TRAITS_DIR`` fallback when
    ``BLOOM_LOCAL_ROOT`` is also unset) keeps the stricter must-exist contract.
    """
    root = resolve_experiment_local_root()
    # Only Path("") has empty .parts; Path(".").parts == (".",) and resolves to
    # CWD, which is not a safe local input root for LLM-controlled reads.
    if not root.parts or str(root) == ".":
        raise RuntimeError(
            "BLOOM_STORAGE_BACKEND=local but neither BLOOM_EXPERIMENT_LOCAL_ROOT "
            "nor BLOOM_TRAITS_DIR is set for the local input root."
        )
    explicit = os.getenv("BLOOM_EXPERIMENT_LOCAL_ROOT")
    if not explicit and _fully_local_root() is not None:
        _ensure_subfolder(root, "input root")
    elif not root.exists() or not root.is_dir():
        logger.error("local input root does not exist or is not a directory: %s", root)
        raise RuntimeError(
            "Local input root does not exist or is not a directory. "
            "Check BLOOM_EXPERIMENT_LOCAL_ROOT or BLOOM_TRAITS_DIR."
        )
    if not os.access(root, os.R_OK):
        logger.error("local input root is not readable: %s", root)
        raise RuntimeError("Local input root is not readable.")


def _validate_dirs() -> None:
    """Check that configured data directories exist and are writable.

    In fully-local mode with ``BLOOM_LOCAL_ROOT`` set, ``BLOOM_TRAITS_DIR`` /
    ``BLOOM_OUTPUT_DIR`` are tier-3 fallbacks that are never consulted (the
    ``BLOOM_LOCAL_ROOT``-derived tier always wins over them in that mode), so
    their own value — even a stale one pointing at a deleted path — is skipped
    entirely. ``BLOOM_PLOTS_DIR`` is itself the tier-1 override: if explicitly
    set it keeps the strict must-exist contract below; if unset, its module
    constant (``PLOTS_DIR``) already resolved to ``<BLOOM_LOCAL_ROOT>/plots``, so
    it is auto-created here instead of required to pre-exist.
    """
    local_root_mode = _fully_local_root() is not None
    for name, path in [
        ("BLOOM_TRAITS_DIR", TRAITS_DIR),
        ("BLOOM_OUTPUT_DIR", OUTPUT_DIR),
        ("BLOOM_PLOTS_DIR", PLOTS_DIR),
    ]:
        if local_root_mode:
            if name in ("BLOOM_TRAITS_DIR", "BLOOM_OUTPUT_DIR"):
                continue
            if name == "BLOOM_PLOTS_DIR" and not os.getenv(name):
                _ensure_subfolder(path, "plots root")
                # Post-create writability recheck — mirrors the fall-through
                # checks validate_experiment_local_root (readable) and
                # validate_storage_backend (writable) both perform after their
                # own _ensure_subfolder call; plots are a write destination
                # (_viz_shared.save_plot), so a raw PermissionError there
                # should surface at boot, not mid-analysis.
                if not os.access(path, os.W_OK):
                    raise RuntimeError("BLOOM_LOCAL_ROOT's plots root is not writable.")
                continue
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

    When ``BLOOM_STORAGE_BACKEND=local`` and ``BLOOM_LOCAL_ROOT`` is set,
    ``BLOOM_LOCAL_ROOT`` itself is validated first (one clear error if it's
    missing, not a directory, or not writable — see ``_validate_local_root_dir``),
    and ``BLOOM_TRAITS_DIR`` / ``BLOOM_OUTPUT_DIR`` / ``BLOOM_PLOTS_DIR`` drop out
    of the required-vars check below; in every other combination they remain
    exactly as required as before this change.
    """
    local_root = _fully_local_root()
    if local_root is not None:
        _validate_local_root_dir(local_root)

    optional_when_local = (
        {"BLOOM_TRAITS_DIR", "BLOOM_OUTPUT_DIR", "BLOOM_PLOTS_DIR"}
        if local_root is not None
        else set()
    )
    required = [
        k
        for k in list(_REQUIRED_DIRS) + ["BLOOM_PLOTS_URL"]
        if k not in optional_when_local
    ]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}. "
            f"Set them in .env or docker-compose."
        )
    _validate_dirs()

    # Fail fast on a misconfigured storage backend (invalid BLOOM_STORAGE_BACKEND,
    # or BLOOM_STORAGE_BACKEND=local with an unusable resolved root). Imported here
    # (not at module top) so importing this module stays side-effect-free.
    from bloom_mcp.storage_backend import validate_storage_backend

    validate_storage_backend()


# Column-role matching + trait detection now live in
# ``bloom_mcp.data_access.columns`` (``resolve_columns``); ``detect_columns`` below
# is a thin shim over it so the read adapters and ``qc_clean`` share one source of
# truth. Imported lazily inside the function to avoid an import cycle
# (``data_access`` -> readers -> ``experiment_utils``).


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

    Thin shim over :func:`bloom_mcp.data_access.columns.resolve_columns` (the single
    source of truth): role-name matching is bloommcp's; **trait detection delegates
    to** ``sleap_roots_analyze.get_trait_columns``, so numeric metadata such as
    ``Computation.Time.s`` is excluded from the trait set. Retained for the reader
    adapters and ``list_experiments`` / ``load_experiment_data`` that call it;
    returns the same dict shape as before.

    Returns:
        {
            "trait_cols": [...],
            "metadata_cols": [...],
            "genotype_col": str or None,
            "replicate_col": str or None,
            "sample_id_col": str or None,
        }
    """
    # Lazy import breaks the data_access -> readers -> experiment_utils cycle.
    from bloom_mcp.data_access.columns import resolve_columns

    resolved = resolve_columns(df)
    return {
        "trait_cols": resolved.trait_cols,
        "metadata_cols": resolved.metadata_cols,
        "genotype_col": resolved.genotype,
        "replicate_col": resolved.replicate,
        "sample_id_col": resolved.sample_id,
    }


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

    from bloom_mcp.manifest import AnalysisDir, ManifestSchemaError
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
    allow_legacy_cleaned: bool = True,
) -> tuple:
    """Load experiment CSV with auto-detected columns.

    Resolution order for version="latest" (the default):
      1. Versioned manifest entry (qc_<stem>/manifest.json -> latest -> _cleaned.csv)
      2. Legacy un-versioned cleaned CSV (qc_<stem>/<stem>_cleaned.csv) — preserves
         pre-migration behaviour; replaced by v0_legacy after the Phase B migration runs
      3. Raw CSV from BLOOM_TRAITS_DIR

    Args:
        filename: experiment identifier (e.g., "alfalfa_gwas_wave2.csv" today; a
            database-backed identifier once data-access-roadmap.md Tier 2 lands)
        traits_dir: Override for BLOOM_TRAITS_DIR
        output_dir: Override for BLOOM_OUTPUT_DIR
        require_clean: If True, fail when no cleaned CSV exists (for UMAP)
        version: "latest" (default), "raw", or an explicit "v<N>"
        allow_legacy_cleaned: If False, the un-versioned legacy cleaned CSV tier is
            skipped — it carries no manifest/hash lineage, so a certified-clean
            consumer must not be satisfied by a stale legacy file that may not
            correspond to the current input. `LocalReader` sets this False.

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
            if allow_legacy_cleaned and legacy_path.exists():
                df = pd.read_csv(legacy_path)
                config = detect_columns(df)
                return df, config["trait_cols"], config, "legacy_cleaned"
            elif not allow_legacy_cleaned and legacy_path.exists():
                logger.warning(
                    "Skipping un-versioned legacy cleaned CSV %s; "
                    "run the QC workflow to produce a versioned output.",
                    legacy_path,
                )

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
