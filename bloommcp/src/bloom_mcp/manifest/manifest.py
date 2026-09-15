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


class ManifestSchemaError(Exception):
    """Raised when a manifest's schema version is newer than this code understands."""


class ManifestBackendMismatchError(Exception):
    """Raised when a manifest's `storage_backend` sentinel names a backend other
    than the one now serving the read — a *foreign catalog* (#573): a bucket
    copied to a local root, a restored backup, or a shared/overlapping root.
    Never raised for a manifest with no usable sentinel (absent/empty — written
    before schema v5), and never for the disjoint A→B→A flip, where each
    catalog's own sentinel always matches the backend serving it (the
    #395/#572 locally-undetectable non-goal). This is an accident-detection
    control, not a tamper-proof one: whoever can edit the manifest controls
    the compared value, and deleting/blanking the sentinel takes the pre-v5
    pass-through."""


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
    the write-path re-check in `result_store.supabase_store` so the two can
    never drift apart.

    `value` is the raw `storage_backend` field — possibly unvalidated storage
    bytes. Returns a display-safe recorded-backend name when the manifest is
    foreign (written by a backend other than the active one), else None.
    Rules:

    - absent (`None`) or empty/whitespace string → not foreign (pre-v5
      pass-through — failing it would brick every catalog written before
      #572; the window closes when the catalog's next commit re-stamps it);
    - comparison is stripped + lower-cased, mirroring
      `_selected_backend_name`'s treatment of `BLOOM_STORAGE_BACKEND`, so a
      hand-edited ``"LOCAL"`` matches rather than bricking the catalog;
    - a present value that is not a string, or a string outside
      `VALID_BACKENDS`, is foreign with the clamped display placeholder —
      never interpolated verbatim into caller-facing text (interpolation
      sites additionally keep ``%r``/``!r`` so control characters could not
      render even if this clamp were bypassed — keep both).
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


# Sticky, process-lifetime flag: has this process served at least one foreign
# catalog under the escape hatch? Consulted by the ResultStore write path to
# refuse ALL commits afterwards — the hatch is an inspection mode, and a
# commit derived from foreign-read data would land in a native catalog with
# clean provenance (and, for remove_outliers, a based_on_version that exists
# only in the foreign catalog) — an affirmatively false lineage. Reset only
# via storage_backend.reset_backend_for_tests().
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
    and compares — via the shared `foreign_sentinel` predicate — against
    `active_backend_name()`, the same function `write_manifest` stamps from,
    so stamp and check cannot disagree. The message carries only the logical
    storage prefix and clamped backend names — never an absolute host path,
    and never the escape-hatch variable: bloommcp is LLM-driven, and the
    failure response must direct investigation, not advertise its own bypass
    (the hatch is documented in docs/storage-backends.md and named in the
    server-side warning below, the right audiences for it).
    """
    recorded = foreign_sentinel(raw_sentinel)
    if recorded is None:
        return
    active = active_backend_name()
    if allow_foreign_manifest():
        # Deliberate foreign inspection (BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1):
        # warn per guarded read — never once-per-process, the one-time-signal
        # failure mode of #572's fresh-catalog log that #573 exists to avoid —
        # and remember it: the write path refuses all commits in a process
        # that has served foreign data (see foreign_read_served).
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
        f"manifest at {prefix.rstrip('/')} was written by storage backend "
        f"{recorded!r} but the active backend is {active!r} — refusing to "
        f"serve a catalog another backend wrote. Do not mix storage backends "
        f"for one experiment; see bloommcp/docs/storage-backends.md "
        f"('Do not mix backends') before proceeding."
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
