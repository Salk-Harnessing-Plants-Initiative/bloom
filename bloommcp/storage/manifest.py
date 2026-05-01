"""Atomic JSON manifest read/write with rename-and-swap for safe concurrent access."""
import json
import os
import uuid
from pathlib import Path
from typing import Optional

from .schema import CURRENT_SCHEMA_VERSION, Manifest

KNOWN_SCHEMA_VERSION = CURRENT_SCHEMA_VERSION


class ManifestSchemaError(Exception):
    """Raised when a manifest's schema version is newer than this code understands."""


def validate_schema(manifest: dict) -> None:
    """Refuse manifests whose schema version is newer than KNOWN_SCHEMA_VERSION.

    Runs *before* Pydantic parsing so a forward-incompatible manifest produces
    a clear "schema too new" error rather than a confusing "extra field" error
    from strict Pydantic validation.
    """
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


def read_manifest(experiment_dir: Path) -> Optional[Manifest]:
    """Return the parsed and validated manifest, or None if absent."""
    manifest_path = experiment_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    with manifest_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    validate_schema(raw)
    return Manifest.model_validate(raw)


def write_manifest_atomic(experiment_dir: Path, manifest: Manifest) -> None:
    """Write manifest.json atomically: write to a unique tmp file, fsync, then rename."""
    payload = manifest.model_dump(mode="json")
    validate_schema(payload)
    experiment_dir.mkdir(parents=True, exist_ok=True)
    target = experiment_dir / "manifest.json"
    tmp = experiment_dir / f"manifest.json.tmp.{uuid.uuid4().hex}"
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, target)
