"""Atomic JSON manifest read/write with rename-and-swap for safe concurrent access."""
import json
import os
import uuid
from pathlib import Path

KNOWN_SCHEMA_VERSION = 1


class ManifestSchemaError(Exception):
    """Raised when a manifest's schema version is newer than this code understands."""


def validate_schema(manifest: dict) -> None:
    """Refuse manifests whose schema version is newer than KNOWN_SCHEMA_VERSION."""
    schema_version = manifest.get("manifest_schema_version")
    if schema_version is None:
        raise ManifestSchemaError(
            "manifest.json is missing the 'manifest_schema_version' field"
        )
    if not isinstance(schema_version, int) or schema_version > KNOWN_SCHEMA_VERSION:
        raise ManifestSchemaError(
            f"manifest_schema_version {schema_version!r} is newer than supported "
            f"(this code understands up to {KNOWN_SCHEMA_VERSION})"
        )


def read_manifest(experiment_dir: Path) -> dict | None:
    """Return the parsed manifest, or None if absent."""
    manifest_path = experiment_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)
    validate_schema(manifest)
    return manifest


def write_manifest_atomic(experiment_dir: Path, manifest: dict) -> None:
    """Write manifest.json atomically: write to a unique tmp file, fsync, then rename."""
    validate_schema(manifest)
    experiment_dir.mkdir(parents=True, exist_ok=True)
    target = experiment_dir / "manifest.json"
    tmp = experiment_dir / f"manifest.json.tmp.{uuid.uuid4().hex}"
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, target)
