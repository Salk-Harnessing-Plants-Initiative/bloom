"""Storage-backed JSON manifest read/write.

The manifest.json for each (experiment, tool_class) pair lives at
`<prefix>/manifest.json` in the bloommcp-data bucket. Reads return None
when no manifest exists (a fresh experiment is a normal state, not an
error). Writes overwrite via upsert — safe under the single-writer
deployment topology bloommcp runs in.
"""

import logging
from typing import Optional

from bloom_mcp.storage_backend import (
    VALID_BACKENDS,
    active_backend_name,
    allow_foreign_manifest,
)
from bloom_mcp.supabase_client import list_prefix, read_json, write_json

from .schema import CURRENT_SCHEMA_VERSION, Manifest

logger = logging.getLogger(__name__)

KNOWN_SCHEMA_VERSION = CURRENT_SCHEMA_VERSION

_MANIFEST_BASENAME = "manifest.json"

# Display placeholder for a sentinel value outside VALID_BACKENDS: the sentinel
# is unvalidated storage bytes writable by anyone holding the bloom_agent key,
# and its text flows into agent-facing error messages on paths with no length
# cap — so an unrecognized value is clamped rather than interpolated verbatim.
_UNRECOGNIZED_SENTINEL = "<unrecognized backend name>"

# The catalog identity is the only variable-length part of a foreign-catalog
# message, so it is clamped here and placed AFTER the backend names: consumer
# paths run these messages through `safe_error_text(limit=300)`, and a long
# experiment stem must never push the two backend names — the one thing this
# feature exists to surface — past the truncation point (PR #782 review).
_IDENTITY_CAP = 60


def _clamp_identity(identity: str) -> str:
    """Bound a catalog identity so the message around it stays under the cap."""
    identity = identity.rstrip("/")
    if len(identity) <= _IDENTITY_CAP:
        return identity
    return identity[: _IDENTITY_CAP - 1] + "\u2026"


def foreign_catalog_message(identity: str, recorded: str, active: str, action: str) -> str:
    """The single foreign-catalog message template (#573).

    Shared by the read guard here and the write-path re-check in
    `result_store.supabase_store` so a wording change is one edit, not two.
    Backend names lead; the clamped identity follows, keeping the whole
    message inside `safe_error_text`'s limit for any experiment name.
    Deliberately never names the escape-hatch variable: bloommcp is
    LLM-driven, and a failure response must direct investigation rather than
    advertise its own bypass.
    """
    return (
        f"foreign catalog: written by storage backend {recorded!r}, active "
        f"backend is {active!r} \u2014 refusing to {action}. Catalog: "
        f"{_clamp_identity(identity)}. Do not mix storage backends for one "
        f"experiment; see bloommcp/docs/storage-backends.md."
    )


def _is_unstamped(value: object) -> bool:
    """Whether a sentinel is absent/blank — a pre-v5 catalog the guard can't check."""
    return value is None or (isinstance(value, str) and not value.strip())


class ManifestSchemaError(Exception):
    """Raised when a manifest's schema version is newer than this code understands."""


class ManifestBackendMismatchError(Exception):
    """A manifest's `storage_backend` sentinel names a backend other than the
    one now serving the read — a *foreign catalog* (#573).

    Not raised for an absent/blank sentinel (pre-v5 pass-through), nor for the
    disjoint A→B→A flip, where each catalog's sentinel always matches its own
    server. An accident-detection control, not a tamper-proof one: whoever can
    edit a manifest controls the compared value. See the change's design.md
    for the full trigger analysis.
    """


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


def foreign_sentinel(value: object) -> Optional[str]:
    """The single #573 sentinel predicate, shared by the read guard here and
    the write-path re-check in `result_store.supabase_store`.

    Returns a display-safe recorded-backend name when `value` (the raw, and
    therefore untrusted, `storage_backend` field) is foreign, else None.
    Absent/blank is not foreign (pre-v5 pass-through). Comparison strips and
    lower-cases, mirroring `_selected_backend_name`, so a hand-edited
    ``"LOCAL"`` matches instead of bricking the catalog. A non-string, or a
    string outside `VALID_BACKENDS`, is foreign but reported through the
    clamped placeholder — keep that clamp AND the ``!r`` at interpolation
    sites; they guard different failure modes.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return _UNRECOGNIZED_SENTINEL
    recorded = value.strip().lower()
    if not recorded or recorded == active_backend_name():
        return None
    if recorded not in VALID_BACKENDS:
        return _UNRECOGNIZED_SENTINEL
    return recorded


# Sticky, process-lifetime flag: has this process served a foreign catalog
# under the escape hatch? The ResultStore write path then refuses ALL commits
# (see design.md — the hatch is an inspection mode, and foreign-derived output
# would otherwise land in a native catalog with clean provenance). Plain bool
# assignment, so the GIL makes set/read atomic without a lock. Reset only via
# storage_backend.reset_backend_for_tests().
_foreign_read_served = False


def foreign_read_served() -> bool:
    """Whether a foreign catalog has been served under the hatch in this process."""
    return _foreign_read_served


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
    # The sentinel is checked on the RAW dict, after the schema-version gate
    # but BEFORE full model validation: a version-valid foreign manifest that
    # is otherwise unparseable (e.g. one unknown key under extra="forbid" — a
    # restored backup arriving malformed) must still be identified as foreign,
    # not fall into the generic ValidationError path and out as a misleading
    # "run the QC workflow first".
    _check_backend_sentinel(prefix, raw.get("storage_backend"))
    return Manifest.model_validate(raw)


def _check_backend_sentinel(prefix: str, raw_sentinel: object) -> None:
    """Fail closed when the manifest was written by a different backend (#573).

    Runs after `validate_schema` (so `ManifestSchemaError` keeps precedence)
    and compares against `active_backend_name()` — the same function
    `write_manifest` stamps from, so stamp and check cannot disagree.
    """
    if _is_unstamped(raw_sentinel):
        # Pre-v5 catalog: nothing to compare, so the guard is inert here. Logged
        # at debug (not info/warning): on an environment that predates #572 this
        # fires on every read of every catalog, and it describes the absence of
        # a check rather than a problem — but without it the guard's blind spot
        # leaves no trace at all (PR #782 review). `audit_backend_sentinels.py`
        # reports the population-level count.
        logger.debug(
            "no storage_backend sentinel on catalog %s (pre-v5): the "
            "foreign-catalog guard cannot verify it until its next commit "
            "stamps it for the active backend",
            _clamp_identity(prefix),
        )
        return
    recorded = foreign_sentinel(raw_sentinel)
    if recorded is None:
        return
    active = active_backend_name()
    if allow_foreign_manifest():
        # Deliberate foreign inspection: warn per guarded read — never
        # once-per-process, the one-time-signal failure mode #573 exists to
        # avoid — and latch the flag that makes this process read-only.
        global _foreign_read_served
        _foreign_read_served = True
        logger.warning(
            "serving foreign catalog %s under BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST: "
            "written by storage backend %r, active backend is %r; commits are "
            "disabled for the rest of this process",
            prefix.rstrip("/"),
            recorded,
            active,
        )
        return
    raise ManifestBackendMismatchError(
        foreign_catalog_message(
            prefix, recorded, active, "serve a catalog another backend wrote"
        )
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
