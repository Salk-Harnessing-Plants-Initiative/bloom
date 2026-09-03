# Tasks — add-bloommcp-foreign-catalog-read-guard

TDD discipline: within each section, the failing test lands before the code
that turns it green.

## 1. Manifest-layer guard (`bloom_mcp.manifest`)

- [ ] 1.1 RED: in `bloommcp/tests/test_storage_backend.py`, write failing tests
      for `read_manifest` over a real manifest on a local-backend temp root:
      (a) sentinel names the other backend → raises
      `ManifestBackendMismatchError` whose message names both backends and the
      `<tool_class>/<stem>` catalog, with no absolute host path;
      (b) sentinel matches → manifest returned unchanged;
      (c) sentinel absent (pre-v5 manifest JSON fixture) → manifest returned,
      no raise.
- [ ] 1.2 GREEN: add `ManifestBackendMismatchError` beside
      `ManifestSchemaError` in `bloom_mcp/manifest/manifest.py`, export it from
      `bloom_mcp.manifest`, and add the sentinel comparison to `read_manifest`
      (compare against `active_backend_name()`; skip when the field is None).
- [ ] 1.3 RED: failing tests for the escape hatch:
      `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1` returns the foreign manifest and
      emits one warning-level log per guarded read naming both backends;
      `=0`/unset keeps the raise.
- [ ] 1.4 GREEN: implement the lazily-read `allow_foreign_manifest()` accessor
      in `bloom_mcp/storage_backend.py` and wire it into the guard.
- [ ] 1.5 RED: failing tests for boot validation: an unrecognized value (e.g.
      `yes`) makes `validate_storage_backend()` raise naming the offending
      value and accepted values; unset/`0`/`1` pass.
- [ ] 1.6 GREEN: extend `validate_storage_backend()` accordingly.
- [ ] 1.7 Confirm the existing import-side-effect tests still prove
      `import bloom_mcp.server` reads no env (extend the guard's env access if
      any test shows an import-time read).

## 2. ResultStore surfacing (`bloom_mcp.result_store`)

- [ ] 2.1 RED: in `bloommcp/tests/result_store/test_supabase_result_store.py`,
      failing tests that against a foreign catalog (real manifest via the
      monkeypatched storage boundary or local backend):
      (a) `get_run("latest")` raises `CatalogBackendMismatchError`, an
      `isinstance` of `ManifestReadError`, message naming both backends, no
      path/URL leak;
      (b) `create_run` raises before any object is uploaded;
      (c) a commit-path manifest read mismatch appends no version entry and
      never reaches `write_manifest` (the foreign sentinel is not re-stamped).
- [ ] 2.2 GREEN: add `CatalogBackendMismatchError(ManifestReadError)` to
      `result_store/ports.py`; catch `ManifestBackendMismatchError` in
      `_guarded_manifest_read` (before the generic branch) and in `commit`'s
      manifest-read path in `result_store/supabase_store.py`, logging
      server-side like the `ManifestIncompatibleError` branch.
- [ ] 2.3 Record the `FakeResultStore` exemption where
      `tests/result_store/test_store_parity.py` defines the shared scenario
      set (comment + a pointer to the real-manifest coverage), per the delta.

## 3. Reader / cleaned-tier resolution (`experiment_utils`, consumer surface)

- [ ] 3.1 RED: failing tests that `_resolve_one_class` (and
      `_resolve_versioned_cleaned` through it) reports the mismatch as a hard
      error naming both backends: no fall-through to a lower-priority tool
      class, the legacy cleaned CSV, or raw; and that
      `load_experiment(require_clean=True)` surfaces the mismatch, not
      `CleanedVersionRequiredError`.
- [ ] 3.2 GREEN: add the explicit `except ManifestBackendMismatchError` branch
      to `_resolve_one_class` in `bloom_mcp/experiment_utils.py` (alongside the
      `ManifestSchemaError` branch) and verify the reader's error routing keeps
      it out of the clean-required condition.
- [ ] 3.3 RED: an end-to-end tool test (local backend on a temp root, commit a
      cleaned run, flip the sentinel or the active backend): `pca_analysis`
      returns a structured `BloomMCPError` naming both backends (not
      `internal_error`, not the run-`qc_clean`-first remedy) and persists no
      run; `qc_clean` against the same foreign catalog fails at
      `create_run`/commit without writing.
- [ ] 3.4 GREEN: wire whatever error declaration the contract envelope needs
      (declare the mismatch error type in the tools' `errors=` so it maps to a
      message-passthrough `tool_error`, per `contract/errors.py`), keeping the
      message's both-backends content intact.
- [ ] 3.5 Verify `list_existing_analyses` isolates a per-experiment mismatch
      (one foreign catalog does not abort the whole listing); add a regression
      test, implementing the isolation for this error type if it is missing.
- [ ] 3.6 Escape-hatch end-to-end: with
      `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1`, the same `require_clean=True`
      read resolves the cleaned version and the warning log is present.

## 4. Docs and housekeeping

- [ ] 4.1 Update `bloommcp/docs/storage-backends.md`: the guard, the escape
      hatch and its warning trail, the pre-v5 pass-through limitation, and the
      unchanged A → B → A non-goal — in the existing "do not mix backends"
      section.
- [ ] 4.2 Update `bloommcp/CHANGELOG.md` (Keep a Changelog: Added — the guard
      and env var; note the intentional behavior change for foreign catalogs).
- [ ] 4.3 Run the env-defaults/env-parity checks; confirm the optional
      variable needs no defaults-file entries (add them if the checker
      requires it).

## 5. Verification

- [ ] 5.1 `openspec validate add-bloommcp-foreign-catalog-read-guard --strict`
      passes.
- [ ] 5.2 Full bloommcp test suite passes locally (`uv run pytest` in
      `bloommcp/`), including the pre-existing storage/parity/result-store
      suites unchanged except where tasks above touch them.
- [ ] 5.3 Lint/format clean (`black --check`, `ruff check` via the repo's
      lint tooling) on every touched file.
