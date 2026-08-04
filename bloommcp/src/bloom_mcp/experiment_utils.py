"""
Shared experiment discovery and column auto-detection for SLEAP tool modules.

All tool modules import from this instead of hardcoding EXPERIMENTS dicts.
"""

import logging
import os
import pandas as pd
from pathlib import Path
from typing import NamedTuple, Optional

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


# The two cleaned-producing tool classes' literal names, single-sourced here so
# `qc_clean.py` and `remove_outliers.py` (the producers) and the registries in
# `list_existing_analyses.py` / `manifest.CANONICAL_TOOL_CLASSES` (the discovery
# surfaces) import the same string rather than each re-typing it — the drift #420
# itself is about (a typo in one of these would silently desync a producer from
# the resolution/discovery logic that looks for it).
QC_TOOL_CLASS = "qc"
OUTLIERS_TOOL_CLASS = "outliers"

# The `tool` value `remove_outliers` commits are recorded under. NOTE: this does
# **not** eliminate the drift risk it single-sources against — the actual
# persisted value is `func.__name__` of the decorated `remove_outliers` function
# (see `contract/wrap.py`), not this constant; a future rename of that function
# would silently desync this literal from what's actually written, with nothing
# to catch it except the regression test in
# `tests/test_remove_outliers_tool.py` that asserts this constant equals
# `remove_outliers.remove_outliers.__name__`. Still worth single-sourcing the
# *comparison* side (the audit script, test fixtures) against one name.
REMOVE_OUTLIERS_TOOL_NAME = "remove_outliers"

# The logical output name `remove_outliers` persists its numeric detection report
# under (`outlier_report.json`). Single-sourced here (promoted from
# `remove_outliers.py`'s private `_REPORT_NAME`, #593) so a consumer that reads a
# committed run's outputs by logical name — e.g. `audit_untrustworthy_outlier_fits.py`
# resolving `VersionEntry.output_keys[OUTLIER_REPORT_NAME]` — imports the same
# string rather than re-typing the literal.
OUTLIER_REPORT_NAME = "outlier_report.json"

# `goodness_of_fit.fit_quality` values (mahalanobis chi-squared fit) whose flagged
# set should NOT be trusted as-is — mirrors the delegate's own tiering
# (sleap_roots_analyze.outlier_detection: excellent/good/acceptable are trustworthy,
# poor/very_poor/unknown are not). Promoted from `remove_outliers.py`'s private
# `_UNTRUSTWORTHY_FIT` (#419) to here (#593) so `remove_outliers`'s live pre-commit
# gate and `audit_untrustworthy_outlier_fits.py`'s retroactive scan share one
# definition and can never silently disagree on what counts as untrustworthy.
UNTRUSTWORTHY_FIT_QUALITIES = frozenset({"poor", "very_poor", "unknown"})


def fit_is_trustworthy(goodness_of_fit: Optional[dict]) -> Optional[bool]:
    """Derive the machine-visible mahalanobis fit-trust flag from a delegate report.

    ``None`` when there is no fit report at all (e.g. an ``isolation_forest`` trim —
    no chi-squared assumption to trust); otherwise ``False`` for a
    poor/very_poor/unknown ``fit_quality`` and ``True`` for acceptable-or-better. See
    :data:`UNTRUSTWORTHY_FIT_QUALITIES`. Promoted from `remove_outliers.py`'s private
    `_fit_is_trustworthy` (#419) to here (#593) — see that constant's docstring.
    """
    if not isinstance(goodness_of_fit, dict):
        return None
    return goodness_of_fit.get("fit_quality") not in UNTRUSTWORTHY_FIT_QUALITIES


# Cleaned-producing tool classes, lowest to highest resolution priority. `outliers`
# outranks `qc`: for version="latest", a trim (once one exists) is preferred over a
# plain clean regardless of which was committed more recently — see
# `_resolve_versioned_cleaned` and
# `openspec/changes/fix-bloommcp-remove-outliers-tool-class/design.md` for why a
# recency comparison does not work (the reverting `qc_clean` re-run is, by
# construction, always the more recent commit).
_CLEANED_TOOL_CLASSES_BY_PRIORITY = (QC_TOOL_CLASS, OUTLIERS_TOOL_CLASS)


def _resolve_one_class(
    stem: str,
    version: str,
    tool_class: str,
) -> tuple[Optional[Path], Optional[str], Optional[str]]:
    """Resolve a versioned cleaned CSV from one tool class's manifest.

    Single-tool-class resolution, parameterized so `_resolve_versioned_cleaned`
    can check more than one class for `version="latest"`. `version` here is
    always `"latest"` or an explicit `"v<N>"` — never `"latest_qc"`, which is
    resolved by the caller as `_resolve_one_class(stem, "latest", "qc")`.

    Returns (path, source_label, error) exactly as `_resolve_versioned_cleaned`
    documents, with the **unqualified** `f"{entry.id}_cleaned"` label — a caller
    resolving across multiple classes qualifies it with the tool class itself.

    **Soft miss vs. hard error, precisely.** Only "no entry exists at all" for
    `version="latest"` is a soft miss (`(None, None, None)`, letting the caller
    fall through to another class or a lower tier). Once `entry is not None` —
    the manifest names a specific committed version — every subsequent failure
    to actually resolve it (no cleaned-CSV output key recorded, the version
    directory can't be found, the download from storage fails) is a **hard
    error**, even for `version="latest"`. Silently treating those as a soft
    miss would let a transient storage failure or a corrupt/partial commit on
    the higher-priority class fall through to a lower-priority class's valid
    entry — reproducing the exact silent-revert hazard this module exists to
    prevent, just triggered by infrastructure instead of a `qc_clean` re-run.
    """
    import tempfile

    from bloom_mcp.manifest import AnalysisDir, ManifestSchemaError
    from bloom_mcp.supabase_client import download_file, list_prefix

    analysis_dir = AnalysisDir("bloommcp_output", f"{stem}.csv", tool_class)
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

    # From here on `entry` names a real, committed version — any failure to
    # resolve it is a hard error, never a soft miss, regardless of `version`.
    rel = entry.outputs.get(CLEANED_CSV_NAME)
    if not rel:
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
        return (
            None,
            None,
            (
                f"Manifest references {rel} for version {entry.id} but the "
                f"download from storage failed: {e}"
            ),
        )
    return tmp, f"{entry.id}_cleaned", None


def _resolve_versioned_cleaned(
    o_dir: Path,
    stem: str,
    version: str,
) -> tuple[Optional[Path], Optional[str], Optional[str]]:
    """Resolve a versioned cleaned CSV via the QC/outliers manifests.

    The manifest lives in the bloommcp-data bucket at
    `bloommcp_output/<tool_class>_<stem>/manifest.json`; the cleaned CSV is
    downloaded to a tmp Path so callers can `pd.read_csv(path)` unchanged.
    Caller is responsible for the tmp file's lifetime (OS tmp cleanup
    handles it on process exit).

    `version` behavior:
      - `"latest"` checks every class in `_CLEANED_TOOL_CLASSES_BY_PRIORITY`,
        highest priority first (`outliers` before `qc`), and returns the first
        that resolves. `outliers` is preferred whenever it has *any* committed
        version at all — a fixed priority, not a recency comparison. Its label
        is qualified (`f"outliers_{entry.id}_cleaned"`) so it is
        distinguishable from a plain clean; a `qc`-class resolution keeps
        today's unqualified `f"{entry.id}_cleaned"` label — an experiment that
        has never been trimmed sees no observable change.
      - `"latest_qc"` resolves the `qc` class specifically, ignoring any
        `outliers` version, with the unqualified label — this is what
        `remove_outliers` reads as its trimming input, so a fresh `qc_clean`
        is always visible to it regardless of any prior trim.
      - An explicit `"v<N>"` resolves the `qc` class only, unqualified label —
        unchanged from before this function checked more than one class (no
        shipped caller currently passes an explicit cleaned-tier version, so
        resolving a pin across two independently-numbered class sequences is
        not yet a reachable question).
      - `"raw"` is handled by the caller and never reaches this function.

    A checked class with **no entry at all** is a soft miss (continues to the
    next class / tier). A checked class **with an entry that fails to resolve**
    — a schema error, a missing output key, an unlocatable version directory, a
    failed download — is always a hard error, propagated immediately and never
    treated as a soft miss that falls through to another class. See
    `_resolve_one_class` for why: a class with a real, committed-but-unresolvable
    entry silently deferring to a lower-priority class's valid entry would
    reproduce the exact silent-revert hazard this function exists to prevent.

    `o_dir` is accepted for signature compatibility with the pre-migration
    caller but is ignored — the storage prefix is fixed at
    `bloommcp_output`.

    Returns (path, source_label, error). On success, error is None and
    path points at the downloaded tmp CSV. On miss with version="latest" or
    "latest_qc", returns (None, None, None) so the caller falls back. On
    explicit version="v<N>" miss, returns (None, None, error_string).
    """
    if version == "latest_qc":
        return _resolve_one_class(stem, "latest", "qc")

    if version != "latest":
        return _resolve_one_class(stem, version, "qc")

    for tool_class in reversed(_CLEANED_TOOL_CLASSES_BY_PRIORITY):
        path, label, error = _resolve_one_class(stem, "latest", tool_class)
        if error:
            return None, None, error
        if path is not None:
            if tool_class != "qc":
                label = f"{tool_class}_{label}"
                _log_if_trim_is_stale(stem, label)
            return path, label, None
    return None, None, None


def safe_error_text(exc: Exception, limit: int = 300) -> str:
    """Bound and lightly redact an exception's text before it lands in a
    persisted report or a live tool response.

    Not a comprehensive secret scanner -- but the local storage backend
    already redacts absolute host paths from its own errors
    (`storage_backend._redacted_io_error`), and the Supabase backend's
    `storage3`/`httpx` errors have no equivalent convention today. Truncates
    to `limit` characters and strips anything that looks like an
    `apikey`/`authorization`/`bearer` header fragment, so an accidental
    credential/token substring doesn't propagate verbatim into a report file
    described as something that "might later be pasted into a ticket," or
    into a live MCP tool response.
    """
    import re

    # Handles both "key=value"/"key: value" forms and a standalone "Bearer
    # <token>" following an "Authorization:" prefix (consuming "Bearer" as
    # part of the same match, not as a second, independent keyword match that
    # would otherwise leave the actual token right after it untouched).
    text = re.sub(
        r"(?i)\b(apikey|authorization|bearer)\b[:=\s]*(?:bearer\s+)?\S+",
        r"\1=<redacted>",
        str(exc),
    )
    if len(text) > limit:
        text = text[:limit] + "...<truncated>"
    return text


class TrimStaleness(NamedTuple):
    """Result of comparing an experiment's `outliers`-class trim against the
    current `qc`-class latest. `current_qc_label` is `None` only in the
    no-`qc`-baseline-at-all corner (see `trim_staleness`)."""

    is_stale: bool
    outliers_based_on_version: str
    current_qc_label: Optional[str]


def trim_staleness(stem: str) -> Optional[TrimStaleness]:
    """Whether `stem`'s current `outliers`-class trim is stale relative to the
    current `qc`-class latest.

    Returns `None` when no `outliers`-class version exists at all — there is
    nothing to assess (distinct from "trimmed and current", which every caller
    needs to be able to tell apart). Otherwise returns a `TrimStaleness` whose
    `is_stale` is `True` when the trim's `based_on_version` no longer matches
    the `qc`-class latest label (a `qc_clean` has run since the trim was made),
    or when the `qc`-class manifest has **no** `latest` entry at all — a trim
    with no live baseline to confirm it against is treated as a more
    concerning state than "current," not silently equivalent to "nothing to
    see" (this exact corner was previously unreached/untested). Under the
    shipped `ExperimentReader` adapters this state cannot arise from a normal
    commit (`remove_outliers` cannot itself commit without first successfully
    reading a `qc`-class latest); its only realistic, non-corruption trigger is
    a backend-split manifest history (bloom#573).

    Propagates any manifest read failure to the caller — this function does
    not swallow exceptions; callers choose their own failure policy (compare
    `_log_if_trim_is_stale`, which does swallow, against
    `sections.core.list_existing_analyses`, which does not).
    """
    from bloom_mcp.manifest import AnalysisDir

    outliers_entry = AnalysisDir(
        "bloommcp_output", f"{stem}.csv", OUTLIERS_TOOL_CLASS
    ).get_version("latest")
    if outliers_entry is None:
        return None
    qc_entry = AnalysisDir("bloommcp_output", f"{stem}.csv", QC_TOOL_CLASS).get_version(
        "latest"
    )
    if qc_entry is None:
        return TrimStaleness(
            is_stale=True,
            outliers_based_on_version=outliers_entry.based_on_version,
            current_qc_label=None,
        )
    current_qc_label = f"{qc_entry.id}_cleaned"
    return TrimStaleness(
        is_stale=outliers_entry.based_on_version != current_qc_label,
        outliers_based_on_version=outliers_entry.based_on_version,
        current_qc_label=current_qc_label,
    )


def _log_if_trim_is_stale(stem: str, outliers_label: str) -> None:
    """Best-effort, non-blocking: log when `trim_staleness(stem)` reports the
    resolved `outliers` trim as stale. The trim still correctly resolves as
    "latest cleaned" (design.md Decision 4's disclosed trade-off,
    `fix-bloommcp-remove-outliers-tool-class`) until a fresh `remove_outliers`
    run supersedes it; this makes that staleness observable at read time
    rather than only discoverable by manually diffing manifests. Purely
    observational: never raises, never affects resolution — a failure here is
    swallowed so observability can't become its own availability hazard.
    """
    try:
        result = trim_staleness(stem)
        if result is None or not result.is_stale:
            return
        if result.current_qc_label is None:
            logger.info(
                "resolved trim %r for %r is based on %r, but no qc-class version "
                "could be found for this experiment at all; it remains 'latest "
                "cleaned' until a fresh remove_outliers run supersedes it.",
                outliers_label,
                stem,
                result.outliers_based_on_version,
            )
            return
        logger.info(
            "resolved trim %r for %r is based on %r, but the current qc "
            "latest is %r -- a qc_clean has run since this trim was made; "
            "it remains 'latest cleaned' until a fresh remove_outliers "
            "run supersedes it.",
            outliers_label,
            stem,
            result.outliers_based_on_version,
            result.current_qc_label,
        )
    except Exception:
        logger.debug("trim-staleness check failed (non-fatal)", exc_info=True)


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
      1. Versioned manifest entry, preferring a trim over a plain clean whenever one
         exists (outliers_<stem>/manifest.json -> latest, else
         qc_<stem>/manifest.json -> latest -> _cleaned.csv) — see
         `_resolve_versioned_cleaned`
      2. Legacy un-versioned cleaned CSV (qc_<stem>/<stem>_cleaned.csv) — preserves
         pre-migration behaviour; replaced by v0_legacy after the Phase B migration runs
      3. Raw CSV from BLOOM_TRAITS_DIR
    version="latest_qc" follows the same order but tier 1 resolves the qc-class
    manifest specifically, ignoring any trim.

    Args:
        filename: experiment identifier (e.g., "alfalfa_gwas_wave2.csv" today; a
            database-backed identifier once data-access-roadmap.md Tier 2 lands)
        traits_dir: Override for BLOOM_TRAITS_DIR
        output_dir: Override for BLOOM_OUTPUT_DIR
        require_clean: If True, fail when no cleaned CSV exists (for UMAP)
        version: "latest" (default, outliers-preferring), "latest_qc" (qc-class
            only, ignores any trim — what `remove_outliers` reads as its input),
            "raw", or an explicit "v<N>" (qc-class only)
        allow_legacy_cleaned: If False, the un-versioned legacy cleaned CSV tier is
            skipped — it carries no manifest/hash lineage, so a certified-clean
            consumer must not be satisfied by a stale legacy file that may not
            correspond to the current input. `LocalReader` sets this False.

    Returns:
        (df, trait_cols, column_config, source_label)
        source_label is one of "raw", "legacy_cleaned", "v<N>_cleaned", or
        "outliers_v<N>_cleaned".
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

        if version in ("latest", "latest_qc"):
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
