"""Storage-backed JSON manifest read/write.

The manifest.json for each (experiment, tool_class) pair lives at
`<prefix>/manifest.json` in the bloommcp-data bucket. Reads return None
when no manifest exists (a fresh experiment is a normal state, not an
error). Writes overwrite via upsert — safe under the single-writer
deployment topology bloommcp runs in.
"""
from typing import Optional

from source.supabase_client import list_prefix, read_json, write_json

from .schema import CURRENT_SCHEMA_VERSION, Manifest

KNOWN_SCHEMA_VERSION = CURRENT_SCHEMA_VERSION

_MANIFEST_BASENAME = "manifest.json"


class ManifestSchemaError(Exception):
    """Raised when a manifest's schema version is newer than this code understands."""


def validate_schema(manifest: dict) -> None:
    """Reject manifests whose schema version is newer than KNOWN_SCHEMA_VERSION."""
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


def _manifest_key(prefix: str) -> str:
    """Compose the storage key for the manifest under `prefix`."""
    return f"{prefix.rstrip('/')}/{_MANIFEST_BASENAME}"


def read_manifest(prefix: str) -> Optional[Manifest]:
    """Return the manifest at `<prefix>/manifest.json`, or None if absent."""
    if _MANIFEST_BASENAME not in list_prefix(prefix):
        return None
    raw = read_json(_manifest_key(prefix))
    validate_schema(raw)
    return Manifest.model_validate(raw)


def write_manifest(prefix: str, manifest: Manifest) -> None:
    """Save the manifest under `prefix`. Overwrites if it already exists."""
    payload = manifest.model_dump(mode="json")
    validate_schema(payload)
    write_json(_manifest_key(prefix), payload)
