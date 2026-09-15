# Tasks — add-bloommcp-foreign-catalog-read-guard

TDD discipline: within each section, the failing test lands before the code
that turns it green, and each RED+GREEN pair merges as **one commit** (a
failing-test-only commit would break CI's `python-audit` job).

Foreign-manifest recipe (used throughout — a flip-and-read can never produce a
mismatch because the two stores are physically disjoint): commit or write a
manifest normally, then **hand-patch** the stored sentinel — edit the JSON file
under the local temp root, or mutate/seed the in-memory Supabase boundary's
stored bytes (`fake_supabase_storage` patches the manifest module's storage
helpers but not `active_backend_name()`, so the guard runs for real there).
Hygiene for every test in this change: use the opt-in `local_manifest_backend`
fixture or explicit setup+teardown `reset_backend_for_tests()` when the backend
is involved (there is no repo-wide autouse reset), and
`monkeypatch.delenv("BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST", raising=False)` in
every test asserting default fail-closed behavior.

## 1. Manifest-layer guard (`bloom_mcp.manifest`)

- [x] 1.1 RED: in `bloommcp/tests/test_storage_backend.py` (import the
      new error inside the test functions so collection survives the RED
      phase), failing tests for `read_manifest` over a hand-patched foreign
      manifest:
      (a) sentinel names the other backend → raises
      `ManifestBackendMismatchError` whose message names both backends and the
      catalog's storage prefix (e.g. `bloommcp_output/qc_<stem>`), with no
      absolute host path;
      (b) sentinel matches → manifest returned unchanged AND no log record
      emitted at any level (caplog);
      (c) sentinel absent/None (reuse/mirror the pre-v5 fixture shape from
      `tests/fixtures/manifest_v2.json`) or empty string → manifest returned,
      no raise;
      (d) precedence: a manifest that is both schema-incompatible and foreign
      raises `ManifestSchemaError` (guard runs only after schema validation).
- [x] 1.2 GREEN: add `ManifestBackendMismatchError` beside
      `ManifestSchemaError` in `bloom_mcp/manifest/manifest.py`, export it from
      `bloom_mcp/manifest/__init__.py`, and add the sentinel comparison to
      `read_manifest` after `Manifest.model_validate` (compare against
      `active_backend_name()`; skip when the field is None/empty).
- [x] 1.3 RED: failing tests for the escape hatch:
      (a) `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1`: **two consecutive**
      `read_manifest` calls over the same foreign catalog each return the
      manifest and each emit their own warning-level record naming both
      backends and the prefix (per-read, never once-per-process);
      (b) hatch set + matching sentinel → no warning;
      (c) `=0`, `=""`, and whitespace keep the raise (empty ≡ unset);
      (d) an invalid value at guard time (e.g. `yes`, env mutated post-boot)
      keeps the guard fail-closed — only the exact value `1` enables the hatch;
      (e) `allow_foreign_manifest()` is not memoized: flip the env var between
      two calls in one process and assert the second call sees the new value.
- [x] 1.4 GREEN: implement the lazily-read, unmemoized
      `allow_foreign_manifest()` accessor in `bloom_mcp/storage_backend.py` and
      wire it into the guard's warning branch.
- [x] 1.5 RED: failing tests for boot validation: an unrecognized value (e.g.
      `yes`) makes `validate_storage_backend()` raise naming the offending
      value and the accepted values; unset, `""`, whitespace, `0`, and `1` all
      pass.
- [x] 1.6 GREEN: extend `validate_storage_backend()` accordingly (empty ≡
      unset, mirroring `_selected_backend_name`'s treatment of
      `BLOOM_STORAGE_BACKEND`).
- [x] 1.7 RED+GREEN: subprocess test — with
      `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=yes` (an invalid value) in the
      environment and no other bloom env, `import bloom_mcp.server` exits 0
      (the only shape that distinguishes a lazy read from an
      import-time-with-default read). Add the new var to
      `test_package_baseline.py`'s `_BLOOM_ENV_VARS` scrub list.

## 2. ResultStore surfacing (`bloom_mcp.result_store`)

- [x] 2.1 RED: in `bloommcp/tests/result_store/test_supabase_result_store.py`,
      failing tests over a hand-patched foreign catalog:
      (a) parametrized over the existing `_CALL_SITES`
      (`create_run`/`list_runs`/`get_run`, mirroring the schema/generic-error
      tests at ~:535): each raises `CatalogBackendMismatchError`, an
      `isinstance` of `ManifestReadError`, message naming both backends, no
      path/URL leak;
      (b) `create_run` raises before any staging dir is handed out and before
      any object is uploaded — **also when the escape hatch is set to `1`**;
      (c) a commit-path mismatch raises `CatalogBackendMismatchError` itself
      (assert the type, and that the message does not claim a transient/
      retryable condition — mirror the `KeyScopeGuardError` do-not-retry
      branch, not the generic `CommitFailedError` wrapper), uploads no object,
      appends no version entry, and never reaches `write_manifest` (the foreign
      sentinel is not re-stamped) — **also when the escape hatch is set**.
- [x] 2.2 GREEN: add `CatalogBackendMismatchError(ManifestReadError)` to
      `result_store/ports.py`; catch `ManifestBackendMismatchError` in
      `_guarded_manifest_read` (before the generic branch, logging server-side
      like the `ManifestIncompatibleError` branch); add the hatch-independent
      sentinel check on the manifest object `create_run`/`commit` read in
      `result_store/supabase_store.py`, surfacing on the commit path with
      do-not-retry semantics.
- [x] 2.3 Record the `FakeResultStore` exemption where
      `tests/result_store/test_store_parity.py` defines the shared scenario
      set, and the `FakeReader` exemption where
      `tests/data_access/test_reader_parity.py` defines its scenarios
      (comment + pointer to the real-manifest coverage), per the deltas.

## 3. Reader / cleaned-tier resolution and consumer surface

- [x] 3.1 RED: failing tests that resolution treats the mismatch as a typed
      hard error:
      (a) `_resolve_one_class`/`_resolve_versioned_cleaned` let
      `ManifestBackendMismatchError` propagate — with a foreign
      higher-priority (`outliers`) catalog and a healthy `qc` catalog, a legacy
      cleaned CSV, and a raw input all present, resolution raises and serves
      none of them;
      (b) `LocalReader.load_experiment(require_clean=True)` over a foreign
      catalog raises `ForeignCatalogError` naming both backends — not
      `CleanedVersionRequiredError` (today's demotion at
      `local_reader.py:~93`);
      (c) `SupabaseReader.load_experiment(require_clean=True)` likewise — not
      `ExperimentNotFoundError` (today's discard-and-demote at
      `supabase_reader.py:~89`).
- [x] 3.2 GREEN: add `ForeignCatalogError(ExperimentReadError)` to
      `data_access/ports.py`; exclude `ManifestBackendMismatchError` from
      `_resolve_one_class`'s generic `except Exception` in
      `bloom_mcp/experiment_utils.py` with an explicit branch (beside the
      `ManifestSchemaError` one) that raises `ForeignCatalogError` at the
      shared helper — so both reader adapters AND every direct
      `load_experiment_data` caller (the viz tools) surface the same typed
      error with no per-file wrapping, closing the caller audit structurally.
- [x] 3.3 RED+GREEN: end-to-end tool tests (local backend on a temp root,
      commit a cleaned run, hand-patch the sentinel): `pca_analysis` returns a
      structured `BloomMCPError` naming both backends (not `internal_error`,
      not the run-`qc_clean`-first remedy) and persists no run; `qc_clean`
      against the same foreign catalog fails at `create_run` without writing.
      No tool-code changes expected: both tools already declare
      `errors=(ExperimentReadError, CommitFailedError, ManifestReadError)` —
      verify the declared tuples cover the two new subclasses and fix only if
      a tool's declaration differs.
- [x] 3.4 Characterization pin (test-first, expected green on arrival):
      `list_existing_analyses` with a foreign `outliers` catalog and a healthy
      `qc` catalog still lists the healthy class and reports the mismatch in
      the per-tool-class `errors` entries (via `safe_error_text`, naming both
      backends). Mind the module-level 30s `_RESPONSE_CACHE` (unique
      experiment name or cache clear) and that `trim_staleness`'s own manifest
      reads may contribute a second error entry.
- [x] 3.5 Escape-hatch end-to-end: with
      `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1`, the same `require_clean=True`
      read resolves the cleaned version and the per-read warning records are
      present — while a `qc_clean` commit against the foreign catalog still
      fails (reads-only hatch).

## 4. Docs, env plumbing, and housekeeping

- [x] 4.1 Update `bloommcp/docs/storage-backends.md` — rewrite, not append;
      these existing sentences become wrong or stale and must change:
      (a) "This can't be _prevented_ from purely local information … It is made
      **observable** instead" (~line 272) → serving a foreign catalog IS now
      prevented locally; cross-catalog divergence detection is not;
      (b) the sentinel bullet (~276) describing it as forensic-only
      ("inspecting either store's file directly identifies…") → now enforced
      at read time, with the hatch, pre-v5 pass-through, and reads-only
      semantics;
      (c) "Known limitation" (~287): "they only make the _moment_ of a
      potential split observable, not the mixing itself" → the guard rejects a
      catalog served by a backend that did not write it; it cannot join two
      disjoint catalogs, and A → B → A stays silent;
      (d) "A backend is not a migration" (~264): add that a hand-rolled
      migration now fails at read time instead of silently taking over.
      Also document the containerized-deploy reachability caveat (dev compose
      passes the var through; staging/prod require a compose edit + redeploy).
- [x] 4.2 Update `bloommcp/CHANGELOG.md` under `[Unreleased]`, split per Keep a
      Changelog: **Added** — the guard, the two/three new error types, the env
      var (pointer to `docs/storage-backends.md`); **Changed** — reads over a
      foreign catalog, previously served silently, now fail closed;
      single-backend usage unaffected.
- [x] 4.3 Add `${BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST:-}` passthrough to the
      `bloommcp` service in `docker-compose.dev.yml` (beside the existing
      `BLOOM_STORAGE_*` family) and the empty opt-in entry with comment to
      `.env.dev.example` (the `development-environment` conventions). Do NOT
      touch `.env.prod.defaults`/`.env.staging.defaults` or
      `docker-compose.prod.yml`. Re-run the root `tests/unit/` suite — the
      compose/env/docs pins (`test_compose_dev_env_files.py`,
      `test_env_dev_example.py`, `test_env_defaults.py`,
      `test_bloommcp_local_mode_docs.py`) are the gate for this task.
- [x] 4.4 Mark this tasks.md complete and re-validate the change.

## 5. Verification

- [x] 5.1 `openspec validate add-bloommcp-foreign-catalog-read-guard --strict`
      passes.
- [x] 5.2 CI-equivalent bloommcp suite passes: in `bloommcp/`,
      `uv run --frozen --extra test pytest tests/ -m "not integration and not
live_smoke"` (matching `python-audit`), with the pre-existing
      storage/parity/result-store suites unchanged except where tasks above
      touch them.
- [x] 5.3 Root unit suite passes: `uv run --extra test pytest tests/unit/`
      (env-defaults, compose-shape, and storage-docs pins live here and run in
      CI's `python-audit`).
- [x] 5.4 `pre-commit run --files <touched files>` clean (black, ruff,
      ruff-format on Python; prettier on the touched `.md`).
- [x] 5.5 Dev-stack live smoke: covered by CI — the PR #782 review verified
      that `pr-checks.yml`'s dev-stack-smoke job (init → dev-up →
      migrate-local → `make bloommcp-smoke`) runs against the real stack and
      is green on this head, and confirmed from the code that the smoke
      cannot fire the guard (single backend throughout; `write_manifest`
      stamps from the same `active_backend_name()` the guard reads).
- [ ] 5.6 One-time pre-merge audit (operator step, staging + prod, **gates
      merge** — CI's database is empty, so only this can verify the real
      buckets): run `scripts/audit_backend_sentinels.py` with each
      environment's storage env and record BOTH numbers in the PR body — the
      foreign/unrecognized count (must be 0: those reads fail closed on
      deploy, and prod compose has no escape-hatch passthrough) AND the
      unstamped count (pre-#572 catalogs the guard silently passes until
      their next commit re-stamps them — the guard's actual day-one blind
      spot, per the PR #782 review's amendment). Exit 0 = clean gate; exit 2 =
      resolve before merging.

## Status notes

- 5.6 is the one remaining gate: an operator step needing staging/prod bucket
  access. `scripts/audit_backend_sentinels.py` (added in the review round) is
  the runnable form; paste its summary lines into the PR body.

## 6. PR #782 review round (@eberrigan, 2026-09-15)

- [x] 6.1 (finding 8) Sentinel checked on the raw document before model
      validation; precedence pinned both ways.
- [x] 6.2 (finding 7 + suggestions) Shared `foreign_sentinel` predicate;
      unrecognized/non-string values clamped, `!r` kept; case-insensitive
      compare; whitespace-tolerant hatch value pinned.
- [x] 6.3 (finding 6) Raised messages no longer advertise the escape hatch;
      under safe_error_text's cap; docs/warning keep the hatch.
- [x] 6.4 (finding 5) Hatch is inspection-only: sticky process flag; all
      commits refused after a foreign read; staging torn down.
- [x] 6.5 (finding 4) Post-upload re-check pinned (foreignize-via-upload
      test asserting cleanup); delta wording states both windows honestly.
- [x] 6.6 (finding 3) `agent_remedy` on the two error types, honored by the
      envelope for declared errors only; new `bloommcp-tool-contract` delta.
- [x] 6.7 (finding 2a) `load_frame`/`summarize_trait` and core
      `load_experiment_data` surface the typed message (the legacy viz tools
      were patched too, then retired/converged onto the envelope by #462's
      merge — their successors, incl. `heritability_analysis`, are covered by
      the `errors=` declarations and a dedicated test);
      `trim_staleness` wraps instead of leaking the manifest-layer type;
      experiment_utils comment corrected.
- [x] 6.8 (finding 2b) "Tampered sentinel" struck/qualified in all six
      places; unstamped-catalog adoption logged at INFO and pinned.
- [x] 6.9 (finding 2c) The new var joined the hardcoded tuples in
      `tests/unit/test_compose_dev_env_files.py` and
      `tests/unit/test_init_dev.py` — deleting the compose or
      `.env.dev.example` line now fails the root suite.
- [x] 6.10 (finding 1) `scripts/audit_backend_sentinels.py` + tests; task
      5.6 reworded to gate merge and report the unstamped count.
- [x] 6.11 (suggestions) Double-traceback logging downgraded to one warning;
      audit scripts document the offline-sweep hatch requirement;
      `data_access` exports alphabetized; `load_experiment_data` docstring
      records the raise; explicit-version sibling-class fail-closed trade-off
      recorded in design.md.
