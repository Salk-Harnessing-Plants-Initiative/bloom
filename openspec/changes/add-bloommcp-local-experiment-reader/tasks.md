## 1. LocalReader adapter (TDD)

- [ ] 1.1 Write failing unit tests for `LocalReader` under `bloommcp/tests/data_access/test_local_reader.py`: raw-input load with declared roles; `version="latest"` resolution order (versioned-cleaned → raw — see 1.5 for the legacy tier); explicit-version miss → `ExperimentNotFoundError`; `require_clean=True` with no cleaned output → `CleanedVersionRequiredError`; unknown name → not-found with **no host path** leaked; `list_experiments()` returns summaries and `[]` when the dir is empty; asserts **no** `DeprecationWarning`.
- [ ] 1.2 Write failing tests for the **path-containment guard**: `load_experiment(name)` with `name` that resolves outside the configured root (`..` traversal, absolute path, symlink escape) raises, performs no read, and leaks no host path.
- [ ] 1.3 Write a failing **dtype-parity** test: seed one on-disk CSV with a dtype-ambiguous trait column (numeric with an `"NA"` token, a quoted number, a Euro-decimal); read it through `SupabaseReader` (raw tier, `TRAITS_DIR` monkeypatched) and `LocalReader`; assert identical `trait_cols` / `metadata_cols`. (Guards against `pd.read_csv` inferring `object` and flipping trait→metadata.)
- [ ] 1.4 Implement `bloommcp/src/bloom_mcp/data_access/local_reader.py` — `LocalReader` implementing the `ExperimentReader` port, rooted at `BLOOM_EXPERIMENT_LOCAL_ROOT` → `BLOOM_TRAITS_DIR`, reading raw CSVs with the **same `pd.read_csv` config** as the deployed raw path, resolving cleaned/versioned outputs from the local output store, declaring roles via `detect_columns`, applying the containment guard, and raising caller-safe errors. Export from `data_access/__init__.py`.
- [ ] 1.5 Write a failing test then implement: under `LocalReader`, `require_clean=True` does **not** honor the un-versioned legacy `OUTPUT_DIR/qc_<stem>/<stem>_cleaned.csv` tier as a certified clean (requires a versioned, manifest-backed cleaned output) — so a stale legacy CSV cannot silently satisfy `require_clean` (seed the cleaned/versioned output the way #389's `test_qc_workflow_local_roundtrip` does, via a real `SupabaseResultStore` commit under `BLOOM_STORAGE_BACKEND=local`).
- [ ] 1.6 Add a static grep/AST guard test asserting `local_reader.py` imports no `supabase_client` and references no `supabase` — making Supabase-independence a durable structural property.
- [ ] 1.7 Parity test with a **named oracle**: `FakeReader` is the behavioral oracle for signalling and role declaration; run the shared scenario set (load, version selection, not-found, empty list) against `FakeReader`, `SupabaseReader` (on the `fake_supabase_storage` boundary), and `LocalReader` (temp dir) — put `SupabaseReader` and `LocalReader` on comparable storage (both under `BLOOM_STORAGE_BACKEND=local`) so the cleaned-tier comparison is apples-to-apples; assert equal trait/metadata role sets and source labels.

## 2. Promote (not retire) the local input path

- [ ] 2.1 Write a failing test asserting `SupabaseReader`'s raw-read `DeprecationWarning` **message text** now names `BLOOM_STORAGE_BACKEND=local` / the `LocalReader` adapter (and that `LocalReader` emits none on the same raw read — covered in 1.1). Then re-point the message; keep the warning on the default (Supabase) path.
- [ ] 2.2 Update `data_access` docstrings noting the local input path is a supported opt-in adapter, not a slated-for-removal legacy path.

## 3. Single fully-local switch + composition-root wiring

- [ ] 3.1 Add a **public** `selected_backend_name()` / `is_local_backend()` accessor to `storage_backend.py` (composition root and boot gate must not import the private `_selected_backend_name()`). Add a `BLOOM_EXPERIMENT_LOCAL_ROOT` → `BLOOM_TRAITS_DIR` resolver + a boot-time validator for the local input root (exists, is a readable dir), mirroring `_resolve_local_root`.
- [ ] 3.2 Write failing tests: the composition root wires `LocalReader` when `is_local_backend()` and `SupabaseReader` otherwise; default wiring unchanged. Add a **coupling** test: wiring `LocalReader` while the active storage backend is `supabase` is rejected at boot (no silent reader-local / store-supabase split).
- [ ] 3.3 Implement the selection at the composition root (`server.main()` / `_ports.configure`): choose `LocalReader` vs `SupabaseReader` from `is_local_backend()`, enforcing reader/store coupling.

## 4. Backend-aware boot gate

- [ ] 4.1 Write failing tests: with `BLOOM_STORAGE_BACKEND=local`, `server.main()` boot does **not** require `SUPABASE_URL` / `BLOOM_AGENT_KEY` but **does** validate the local input root; a fully-local boot still fails fast on a missing `BLOOM_*_DIR` / `BLOOM_PLOTS_URL`; an **invalid** `BLOOM_STORAGE_BACKEND` value (e.g. `locel`) fails fast rather than being treated as "not local, require Supabase"; and the default backend still fails fast when Supabase creds are unset (mirror `test_package_baseline`'s boot patterns, spying on `mcp.run`).
- [ ] 4.2 Implement the gate in `server.main()`: compute `fully_local = is_local_backend()`; skip `validate_supabase_env()` and validate the local input root when fully-local; keep `validate_data_env()` / `validate_storage_backend()` in both modes.

## 5. Route the stragglers through the port

- [ ] 5.1 Pin current `correlation_tools` cross-experiment outputs with a characterization test, then route reads through `reader.load_experiment(name, version="raw")` — grow a **frame-accepting** entry point on `cross_experiment_correlations.load_and_align_experiments` (it currently takes paths), and resolve the hardcoded `EXPERIMENTS` dict / local `list_experiments` through the port; assert outputs unchanged and the active adapter is honored.
- [ ] 5.2 Write a characterization test for `start_run`'s current source-CSV provenance (both the concrete-path and the degrade-to-`None` branches), then route it through the reader by **snapshotting the resolved frame to a temp CSV and hashing it** (mirror `pca_analysis_tool`'s `source_snapshot`); assert a workflow run under `LocalReader` records a **non-empty** `input_sha256` equal to `sha256` of the resolved input (no silent provenance loss). Confirm `test_pca_analysis_tool::test_passes_source_csv_for_input_lineage` still passes.
- [ ] 5.3 Grep-guard test: neither `correlation_tools.py` nor `cross_experiment_correlations.py` reads `pd.read_csv(TRAITS_DIR / …)` directly; the cross-experiment reads flow through the injected `ExperimentReader`.

## 6. Purity + fully-local end-to-end tests

- [ ] 6.1 Import-purity subprocess test (copy #389's `test_import_does_not_resolve_backend` pattern, adding `BLOOM_EXPERIMENT_LOCAL_ROOT` to the stripped-env set): `import bloom_mcp.server` in a fresh interpreter reads no `BLOOM_STORAGE_BACKEND` and touches no filesystem.
- [ ] 6.2 Fully-local end-to-end test with `SUPABASE_URL` / `BLOOM_AGENT_KEY` **unset**, `BLOOM_STORAGE_BACKEND=local` (temp input + output roots), calling `reset_backend_for_tests()` to clear the memoized backend: `import bloom_mcp`, boot through `main()`'s validators, and run a full `qc_clean → pca_analysis` — asserting success and real output files on disk. Add a **hard network guard** (monkeypatch `supabase.create_client` to raise, as `fake_supabase_storage._no_network` does) so "no live Supabase" is enforced structurally, not just by absence of env.
- [ ] 6.3 Guard test: with the default backend, boot still fails fast when Supabase creds are unset.
- [ ] 6.4 Regression checkpoint — existing suites stay green after the boot-gate and straggler changes: name `test_package_baseline.py` (boot fail-fast), `test_supabase_reader.py`, `test_pca_analysis_tool.py::test_passes_source_csv_for_input_lineage`, and #389's `test_storage_backend.py`.

## 7. Config + docs

- [ ] 7.1 Document `BLOOM_EXPERIMENT_LOCAL_ROOT` alongside #389's storage vars in `docker-compose.dev.yml` (commented; default off; no `${VAR}` reference, so env-parity CI is unaffected).
- [ ] 7.2 Update `bloommcp/docs/storage-backends.md`: `BLOOM_STORAGE_BACKEND=local` is now a **fully-local (offline) dev mode** (input + output); document `BLOOM_EXPERIMENT_LOCAL_ROOT`, the backend-aware boot gate, and that this is a **dev / power-user** path, not a normal-user packaged distribution; replace the "not a fully-offline mode" caveat.

## 8. Validation + follow-up

- [ ] 8.1 `openspec validate add-bloommcp-local-experiment-reader --strict` passes.
- [ ] 8.2 `bloommcp` unit suite green with no live Supabase; lint/format clean.
- [ ] 8.3 File the reconciliation follow-up: once `bloommcp-storage-backend` (#389) archives, MODIFY its `Backend Selection via BLOOM_STORAGE_BACKEND` requirement so it no longer says `local` "governs only the five object-storage helpers" (this change makes that false — `local` now also selects the reader and the boot gate).
