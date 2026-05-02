"""One-time migration: convert pre-existing un-versioned tool-class directories to v0_legacy."""
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .manifest import write_manifest_atomic
from .schema import (
    CodeVersions,
    ExperimentBlock,
    Manifest,
    VersionEntry,
)

logger = logging.getLogger(__name__)

CANONICAL_TOOL_CLASSES = ("qc", "stats", "dimred", "clustering", "outlier", "viz", "correlation")
LEGACY_PREFIX_ALIASES = {
    "outliers": "outlier",  # plural → singular
    "pca": "dimred",        # PCA was its own dir before dimred became the umbrella
}
MIGRATED_MARKER = ".migrated"


def migrate_legacy_dirs(output_root: Path) -> int:
    """Walk output_root and convert un-versioned tool-class directories into v<N>_legacy form.

    Idempotent: dirs containing .migrated or manifest.json are skipped. If a
    single directory fails to migrate, the helper logs and continues with the
    rest — bloommcp startup never crashes on migration error.

    Returns the count of directories successfully migrated this call.
    """
    output_root = Path(output_root)
    if not output_root.exists():
        return 0

    migrated = 0
    for child in sorted(output_root.iterdir()):
        if not child.is_dir():
            continue
        if not _looks_like_tool_class_dir(child.name):
            continue
        if (child / "manifest.json").exists() or (child / MIGRATED_MARKER).exists():
            continue

        try:
            target = _resolve_canonical_dir(output_root, child)
            _migrate_one(child, target)
            migrated += 1
        except Exception as exc:
            logger.warning("Could not migrate %s: %s", child, exc)
            continue

    if migrated:
        logger.info("Migrated %d legacy analysis dirs to versioned format", migrated)
    return migrated


def _looks_like_tool_class_dir(name: str) -> bool:
    """True if `name` starts with a canonical or legacy tool-class prefix + underscore."""
    for prefix in CANONICAL_TOOL_CLASSES:
        if name.startswith(f"{prefix}_"):
            return True
    for legacy in LEGACY_PREFIX_ALIASES:
        if name.startswith(f"{legacy}_"):
            return True
    return False


def _resolve_canonical_dir(output_root: Path, legacy_dir: Path) -> Path:
    """Return the canonical-named directory for this legacy dir.

    For aliased prefixes (outliers → outlier, pca → dimred), this rewrites the
    name so all per-experiment data lives under the canonical TOOL_CLASSES
    naming. For already-canonical names, returns the directory unchanged.
    """
    name = legacy_dir.name
    for legacy_prefix, canonical_prefix in LEGACY_PREFIX_ALIASES.items():
        if name.startswith(f"{legacy_prefix}_"):
            stem = name[len(legacy_prefix) + 1:]
            return output_root / f"{canonical_prefix}_{stem}"
    return legacy_dir


def _migrate_one(legacy_dir: Path, target_dir: Path) -> None:
    """Move loose files from `legacy_dir` into `target_dir/v0_legacy/` and synthesize a manifest.

    If legacy_dir != target_dir (alias rename), the legacy dir is removed after
    its contents are relocated.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    v0 = target_dir / "v0_legacy"
    v0.mkdir(exist_ok=False)

    moved_outputs: dict[str, str] = {}
    for entry in sorted(legacy_dir.iterdir()):
        if entry.name == MIGRATED_MARKER or entry.name == "manifest.json":
            continue
        if entry.is_dir() and entry.name.startswith("v"):
            # Probably a partially-versioned dir; skip to be safe
            continue
        dest = v0 / entry.name
        shutil.move(str(entry), str(dest))
        moved_outputs[entry.name] = f"v0_legacy/{entry.name}"

    if not moved_outputs:
        # Nothing to migrate — clean up and bail
        v0.rmdir()
        return

    experiment_filename = _stem_to_experiment_filename(target_dir)
    manifest = Manifest(
        experiment=ExperimentBlock(
            filename=experiment_filename,
            source_path="",
            input_sha256="",
        ),
        versions=[
            VersionEntry(
                id="v0_legacy",
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                tool="unknown",
                params={},
                based_on_version="raw",
                code_versions=CodeVersions(bloommcp="unknown", sleap_roots_analyze="unknown"),
                outputs=moved_outputs,
                user_label=None,
            )
        ],
        latest="v0_legacy",
    )
    write_manifest_atomic(target_dir, manifest)
    (target_dir / MIGRATED_MARKER).touch()

    if legacy_dir != target_dir:
        # Alias rename: drop the now-empty plural-named legacy dir
        try:
            legacy_dir.rmdir()
        except OSError:
            # Non-empty (shouldn't happen given we moved everything) — leave alone
            pass


def _stem_to_experiment_filename(target_dir: Path) -> str:
    """Reverse `<tool_class>_<stem>` → `<stem>.csv` for the manifest's experiment block."""
    name = target_dir.name
    for prefix in CANONICAL_TOOL_CLASSES:
        if name.startswith(f"{prefix}_"):
            return f"{name[len(prefix) + 1:]}.csv"
    return f"{name}.csv"
