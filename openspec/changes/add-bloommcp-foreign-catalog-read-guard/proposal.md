# Add a read-time foreign-catalog guard on the `storage_backend` manifest sentinel

Tracking: issue #573 (follow-up filed from PR #572's review, per its archived design.md).

## Why

`ResultStore.get_run(run_ref="latest")` and the cleaned-version resolution behind
`require_clean=True` both resolve `manifest.latest` scoped entirely to whichever
object-storage backend is currently active. #572 made backend mixing *observable*
(the `storage_backend` sentinel stamped on every `manifest.json`, plus a one-time
fresh-catalog info log), but nothing *enforces* the sentinel: a catalog served by a
backend that did not write it — a bucket copied to a local root, a restored backup,
a shared/overlapping root — is served silently, on **every** read, long after the
one-time log line has scrolled away, and a consumer tool (`pca_analysis` gating on
`require_clean`, any `get_run("latest")` caller) silently accepts that catalog's
"latest" with a different `output_sha256` than the writing backend's own view
(#573).

## What Changes

- Add a fail-closed guard at the single manifest-read chokepoint
  (`bloom_mcp.manifest.read_manifest`, which `AnalysisDir.get_version` /
  `read_manifest` / `list_versions` — and therefore `get_run`, `list_runs`,
  `create_run`, `commit`, and the reader's `_resolve_versioned_cleaned` path — all
  pass through): when the resolved manifest's `storage_backend` sentinel is present
  and names a backend other than `active_backend_name()`, raise a new
  `ManifestBackendMismatchError` naming both backends instead of returning the
  manifest. A manifest with no sentinel (written before schema v5) passes
  unchanged.
- Map that error into the `ResultStore` error taxonomy as
  `CatalogBackendMismatchError`, a subclass of `ManifestReadError` — mirroring the
  existing `ManifestSchemaError` → `ManifestIncompatibleError` pattern — so every
  existing `except ManifestReadError` handler still catches it while a caller can
  `isinstance()`-distinguish it.
- Treat the mismatch as a **hard error** in the reader's cleaned-tier resolution
  (`experiment_utils._resolve_one_class`), alongside the existing explicit
  `ManifestSchemaError` handling: never a soft miss that falls through to a
  lower-priority tool class, the legacy cleaned CSV, or the raw input, and never
  demoted to the "no cleaned version — run `qc_clean` first" remedy (which would
  invite committing fresh runs on top of the foreign catalog).
- Add an opt-out for the deliberate case (inspecting an offline copy of a bucket):
  `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1` downgrades the failure to a
  warning-level log per read. The variable is validated fail-fast at startup like
  `BLOOM_STORAGE_BACKEND`, read lazily (never at import), and defaults to the
  guard being active.
- Correct a small spec/code drift while touching this requirement: the shipped
  `bloommcp-storage-backend` spec says the sentinel is stamped from
  `selected_backend_name()`, but the code (deliberately, per PR #572's review)
  stamps from `active_backend_name()`. The MODIFIED delta records the deployed
  behavior.
- Document the guard, the env var, and — honestly — what the guard **cannot**
  catch: two physically disjoint catalogs each remain self-consistent, so an
  A → B → A backend flip still resolves A's own stale "latest" with no mismatch.
  Closing that would require contacting the inactive backend, which #395/#572
  already establish as infeasible from local information; it stays a documented
  non-goal.

**Behavior change, not BREAKING:** under supported usage (one backend per
experiment, the documented contract since #395) no read changes. Reads that newly
fail are exactly the ones serving a catalog another backend wrote — the
silently-wrong state #573 exists to make loud. The env var restores the old
behavior explicitly, per read, with a warning trail.

Out of scope: surfacing `storage_backend` in tool-facing provenance output — that
is companion issue #574. Cross-backend detection of disjoint split histories —
infeasible locally, unchanged non-goal (#395/#572).

## Impact

- Affected specs:
  - `bloommcp-storage-backend` — ADDED `Foreign-Catalog Manifest Read Guard`;
    MODIFIED `Backend Parity and Provenance Integrity` (cross-reference the guard;
    fix the `selected_backend_name()` → `active_backend_name()` drift)
  - `bloommcp-result-store` — ADDED `Foreign-Catalog Mismatch Surfaces as a
    Distinguishable Structured Error`
  - `bloommcp-experiment-read` — ADDED `Cleaned-Version Resolution Rejects a
    Foreign Catalog`
  - `bloommcp-qc-clean-tool` / `bloommcp-pca-analysis-tool` are deliberately
    untouched: both already require every backend read/write failure to surface as
    a structured `BloomMCPError` envelope, and the new failure mode rides that
    existing requirement; the consumer-visible acceptance scenario lives in the
    `bloommcp-experiment-read` delta (the port both tools are required to read
    through).
- Affected code:
  - `bloommcp/src/bloom_mcp/manifest/manifest.py` — `ManifestBackendMismatchError`
    + the guard in `read_manifest`
  - `bloommcp/src/bloom_mcp/storage_backend.py` — `allow_foreign_manifest()`
    accessor + `validate_storage_backend()` extension
  - `bloommcp/src/bloom_mcp/result_store/ports.py` — `CatalogBackendMismatchError`
  - `bloommcp/src/bloom_mcp/result_store/supabase_store.py` —
    `_guarded_manifest_read` mapping
  - `bloommcp/src/bloom_mcp/experiment_utils.py` — explicit handling in
    `_resolve_one_class`
  - `bloommcp/docs/storage-backends.md`, `bloommcp/CHANGELOG.md`
  - tests: `bloommcp/tests/test_storage_backend.py`,
    `bloommcp/tests/result_store/test_supabase_result_store.py`,
    `bloommcp/tests/tools/` (end-to-end consumer coverage), plus the
    `list_existing_analyses` listing-isolation check
- Not affected: manifest schema (no version bump — the guard only reads the
  existing v5 field), `FakeResultStore`/`FakeReader` (no manifest/backend concept;
  exemption recorded in the deltas), the `bloommcp_input/` read path, PostgREST
  table reads.
