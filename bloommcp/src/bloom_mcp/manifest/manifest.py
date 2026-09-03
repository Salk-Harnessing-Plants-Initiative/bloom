"""Storage-backed JSON manifest read/write.

The manifest.json for each (experiment, tool_class) pair lives at
`<prefix>/manifest.json` in the bloommcp-data bucket. Reads return None
when no manifest exists (a fresh experiment is a normal state, not an
error). Writes overwrite via upsert — safe under the single-writer
deployment topology bloommcp runs in.
"""

import logging
from typing import Optional

from bloom_mcp.storage_backend import active_backend_name
from bloom_mcp.supabase_client import list_prefix, read_json, write_json

from .schema import CURRENT_SCHEMA_VERSION, Manifest

logger = logging.getLogger(__name__)

KNOWN_SCHEMA_VERSION = CURRENT_SCHEMA_VERSION

_MANIFEST_BASENAME = "manifest.json"


class ManifestSchemaError(Exception):
    """Raised when a manifest's schema version is newer than this code understands."""


class ManifestBackendMismatchError(Exception):
    """Raised when a manifest's `storage_backend` sentinel names a backend other
    than the one now serving the read — a *foreign catalog* (#573): a bucket
    copied to a local root, a restored backup, a shared/overlapping root, or a
    tampered sentinel. Never raised for a manifest with no usable sentinel
    (absent/empty — written before schema v5), and never for the disjoint
    A→B→A flip, where each catalog's own sentinel always matches the backend
    serving it (the #395/#572 locally-undetectable non-goal)."""


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
    """Return the manifest at `<prefix>/manifest.json`, or None if absent.

    Every manifest read in the process funnels through here
    (`AnalysisDir.read_manifest`/`get_version`/`list_versions`), so the #573
    foreign-catalog check below covers `get_run`, `list_runs`, `create_run`,
    `commit`'s reads, and the reader's cleaned-tier resolution structurally.
    """
    if _MANIFEST_BASENAME not in list_prefix(prefix):
        return None
    raw = read_json(_manifest_key(prefix))
    validate_schema(raw)
    manifest = Manifest.model_validate(raw)
    _check_backend_sentinel(prefix, manifest)
    return manifest


def _check_backend_sentinel(prefix: str, manifest: Manifest) -> None:
    """Fail closed when the manifest was written by a different backend (#573).

    Runs only after schema validation (so `ManifestSchemaError` keeps
    precedence) and compares against `active_backend_name()` — the same
    function `write_manifest` stamps from, so stamp and check cannot disagree.
    An absent/empty sentinel (pre-v5 manifest) passes: failing it would brick
    every catalog written before #572; the window closes when the catalog's
    next commit re-stamps it. The message carries only the logical storage
    prefix — never an absolute host path.
    """
    recorded = (manifest.storage_backend or "").strip()
    if not recorded:
        return
    active = active_backend_name()
    if recorded == active:
        return
    raise ManifestBackendMismatchError(
        f"manifest at {prefix.rstrip('/')} was written by storage backend "
        f"{recorded!r} but the active backend is {active!r} — refusing to "
        f"serve a catalog another backend wrote. Do not mix storage backends "
        f"for one experiment; for a deliberate offline copy set "
        f"BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1 (reads only; a containerized "
        f"deployment must pass the variable through compose)."
    )


def write_manifest(prefix: str, manifest: Manifest) -> None:
    """Save the manifest under `prefix`. Overwrites if it already exists.

    Stamps `storage_backend` with the active backend's name (#395) — derived
    from what `active_backend()` actually resolved to, not an independent env
    re-read, so it can't disagree with the backend that performs the write —
    on a copy, so the caller's `manifest` instance is never mutated as a side
    effect of writing it.
    """
    stamped = manifest.model_copy(update={"storage_backend": active_backend_name()})
    payload = stamped.model_dump(mode="json")
    validate_schema(payload)
    write_json(_manifest_key(prefix), payload)
