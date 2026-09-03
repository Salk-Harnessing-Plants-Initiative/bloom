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
  and non-empty and names a backend other than `active_backend_name()`, raise a new
  `ManifestBackendMismatchError` naming both backends instead of returning the
  manifest. The comparison runs only after the existing schema validation
  (`ManifestSchemaError` keeps precedence); a manifest with no sentinel (written
  before schema v5) passes unchanged.
- Map that error into the `ResultStore` error taxonomy as
  `CatalogBackendMismatchError`, a subclass of `ManifestReadError` — mirroring the
  existing `ManifestSchemaError` → `ManifestIncompatibleError` pattern — so every
  existing `except ManifestReadError` handler and every consumer tool's existing
  `errors=(…, ManifestReadError)` declaration still catches it, while a caller can
  `isinstance()`-distinguish it. On the commit path it surfaces as itself with
  do-not-retry semantics (mirroring the `KeyScopeGuardError` branch), never as a
  "transient — retry" `CommitFailedError`.
- Propagate the mismatch through the reader layer as a **typed** error:
  `_resolve_one_class` lets `ManifestBackendMismatchError` propagate (today it
  stringifies unknown failures, and both reader adapters then *discard* the string
  — `LocalReader` demotes any resolution failure to `CleanedVersionRequiredError`,
  `SupabaseReader` to `ExperimentNotFoundError`), and both `LocalReader` and
  `SupabaseReader` surface it as `ForeignCatalogError`, a new
  `ExperimentReadError` subclass in `data_access/ports.py`. Never a soft miss
  (no fall-through to a lower-priority tool class, the legacy cleaned CSV, or
  raw), never the "run `qc_clean` first" demotion (which would invite committing
  fresh runs on top of the foreign catalog), never "not found". Because tools
  already declare `errors=(ExperimentReadError, CommitFailedError,
  ManifestReadError)`, no per-tool code changes are needed for the message to
  surface structurally.
- Add an opt-out for the deliberate case (inspecting an offline copy of a bucket):
  `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1` downgrades the **read** failure to a
  warning-level log per guarded read. The hatch sanctions reads only — the write
  path (`create_run`/`commit`) rejects a foreign catalog unconditionally, so the
  hatch can never sanction re-stamping/taking over a foreign catalog. Accepted
  values: unset/empty (≡ default, guard active), `0`, `1`; anything else fails
  boot validation (like `BLOOM_STORAGE_BACKEND`); at guard time only the exact
  value `1` enables the hatch; the variable is read lazily and never memoized. So
  the hatch is actually reachable in the standard dev flow, the variable is passed
  through `docker-compose.dev.yml` via the existing `${VAR:-}` pattern and
  documented in `.env.dev.example` per the `development-environment` conventions
  (empty ≡ unset makes the `${VAR:-}` empty-string delivery safe). Staging/prod
  compose files deliberately do NOT pass it through — an operator there edits
  compose + redeploys, or reverts; the docs and error remedy say so honestly.
- Correct a small spec/code drift while touching this requirement: the shipped
  `bloommcp-storage-backend` spec says the sentinel is stamped from
  `selected_backend_name()`, but the code (deliberately, per PR #572's review)
  stamps from `active_backend_name()`. The MODIFIED delta records the deployed
  behavior.
- Pin (by characterization test) that `list_existing_analyses`' existing
  per-tool-class error isolation contains the new error: one foreign tool-class
  catalog contributes a per-class error entry naming both backends while the
  experiment's other classes still list.
- Document the guard, the env var, and — honestly — what the guard **cannot**
  catch: two physically disjoint catalogs each remain self-consistent, so an
  A → B → A backend flip still resolves A's own stale "latest" with no mismatch.
  Closing that would require contacting the inactive backend, which #395/#572
  already establish as infeasible from local information; it stays a documented
  non-goal.

**Behavior change, not BREAKING:** under supported usage (one backend per
experiment, the documented contract since #395) no read changes. Reads that newly
fail are exactly the ones serving a catalog another backend wrote — the
silently-wrong state #573 exists to make loud. The env var restores read access
explicitly, per read, with a warning trail.

Out of scope: surfacing `storage_backend` in tool-facing provenance output — that
is companion issue #574. Cross-backend detection of disjoint split histories —
infeasible locally, unchanged non-goal (#395/#572). Prod/staging compose
passthrough for the escape hatch (deliberate — see design.md).

Coordination notes: the deltas reference the deployed `bloom_mcp/manifest/`
package layout (the rename from `bloom_mcp/storage/` is already in the code;
the `rename-bloommcp-storage-manifest` change that specs it is still unarchived).
`update-bloommcp-result-store-durability` (PR #464) already landed the commit
lock/two-phase re-check this change's commit-path guard sits inside — no
requirement overlap, but the same `supabase_store.commit` region.

## Impact

- Affected specs:
  - `bloommcp-storage-backend` — ADDED `Foreign-Catalog Manifest Read Guard`;
    MODIFIED `Backend Parity and Provenance Integrity` (cross-reference the guard;
    fix the `selected_backend_name()` → `active_backend_name()` drift)
  - `bloommcp-result-store` — ADDED `Foreign-Catalog Mismatch Surfaces as a
    Distinguishable Structured Error`; MODIFIED `FakeResultStore Adapter`
    (foreign-catalog equivalence carve-out)
  - `bloommcp-experiment-read` — ADDED `Cleaned-Version Resolution Rejects a
    Foreign Catalog` (includes the `list_existing_analyses` isolation pin);
    MODIFIED `FakeReader Adapter` (same carve-out)
  - `development-environment` — MODIFIED `Committed Local Environment Template`
    and `Externalized Local-Only Storage Backend Vars` (add
    `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST` to the enumerated opt-in vars)
  - `bloommcp-qc-clean-tool` / `bloommcp-pca-analysis-tool` are deliberately
    untouched: both tools already declare `errors=(ExperimentReadError,
    CommitFailedError, ManifestReadError)`, and the new errors subclass those —
    the consumer-visible acceptance scenario lives in the
    `bloommcp-experiment-read` delta (the port both tools are required to read
    through), with an end-to-end test through `pca_analysis`/`qc_clean`.
- Affected code (all Python under `bloommcp/` unless noted):
  - `bloommcp/src/bloom_mcp/manifest/manifest.py` — `ManifestBackendMismatchError`
    + the guard in `read_manifest`
  - `bloommcp/src/bloom_mcp/manifest/__init__.py` — export the new error
  - `bloommcp/src/bloom_mcp/storage_backend.py` — `allow_foreign_manifest()`
    accessor + `validate_storage_backend()` extension
  - `bloommcp/src/bloom_mcp/result_store/ports.py` — `CatalogBackendMismatchError`
  - `bloommcp/src/bloom_mcp/result_store/supabase_store.py` —
    `_guarded_manifest_read` mapping + hatch-independent write-path sentinel check
    in `create_run`/`commit`
  - `bloommcp/src/bloom_mcp/experiment_utils.py` — let the mismatch propagate out
    of `_resolve_one_class` (and audit the remaining
    `load_experiment_data`/`_resolve_versioned_cleaned` callers)
  - `bloommcp/src/bloom_mcp/data_access/ports.py` — `ForeignCatalogError`
  - `bloommcp/src/bloom_mcp/data_access/local_reader.py`,
    `bloommcp/src/bloom_mcp/data_access/supabase_reader.py` — surface the typed
    error instead of demoting it
  - `docker-compose.dev.yml`, `.env.dev.example` (repo root) — dev passthrough +
    template entry for the new var
  - `bloommcp/docs/storage-backends.md`, `bloommcp/CHANGELOG.md`
  - tests: `bloommcp/tests/test_storage_backend.py`,
    `bloommcp/tests/result_store/test_supabase_result_store.py`,
    `bloommcp/tests/result_store/test_store_parity.py` (exemption note),
    `bloommcp/tests/data_access/test_reader_parity.py` (exemption note), reader
    tests, `bloommcp/tests/tools/` (end-to-end consumer coverage +
    `list_existing_analyses` isolation pin), `bloommcp/tests/test_package_baseline.py`
    (env scrub list); root `tests/unit/` suite re-run (compose/env/docs pins)
- Not affected: manifest schema (no version bump — the guard only reads the
  existing v5 field), `FakeResultStore`/`FakeReader` behavior (carve-outs recorded
  in the deltas), the `bloommcp_input/` read path, PostgREST table reads,
  `.env.prod.defaults`/`.env.staging.defaults` (no new entries — the var is
  optional and not passed through prod compose), `.github/workflows/*`.
