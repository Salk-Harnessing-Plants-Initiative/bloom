## 1. LocalReader adapter (TDD)

- [ ] 1.1 Write failing unit tests for `LocalReader` under `bloommcp/tests/data_access/test_local_reader.py`: raw-input load with declared roles; `version="latest"` resolution order (versioned-cleaned → legacy cleaned → raw); explicit-version miss → `ExperimentNotFoundError`; `require_clean=True` with no cleaned output → `CleanedVersionRequiredError`; unknown name → not-found with **no host path** leaked; `list_experiments()` returns summaries and `[]` when the dir is empty; asserts **no** `DeprecationWarning` and **no** `supabase_client` import/network access.
- [ ] 1.2 Implement `bloommcp/src/bloom_mcp/data_access/local_reader.py` — `LocalReader` implementing the `ExperimentReader` port, rooted at `BLOOM_EXPERIMENT_LOCAL_ROOT` → `BLOOM_TRAITS_DIR`, resolving cleaned/versioned outputs from the local output store, declaring roles via `detect_columns`, returning the same `ExperimentFrame`/`ExperimentSummary` contract, and raising caller-safe errors.
- [ ] 1.3 Export `LocalReader` from `bloommcp/src/bloom_mcp/data_access/__init__.py`.
- [ ] 1.4 Add a parity test running the shared scenario set (load, version selection, not-found, empty list) against `FakeReader`, `SupabaseReader` (monkeypatched boundary), and `LocalReader` (temp dir) — asserting equivalent observable results, source labels, and signalling.

## 2. Promote (not retire) the local input path

- [ ] 2.1 Re-point `SupabaseReader`'s raw-read deprecation message from "will be removed" to "use `BLOOM_STORAGE_BACKEND=local` (the `LocalReader` adapter) for local inputs"; keep the warning on the default (Supabase) path.
- [ ] 2.2 Update `data_access` docstrings noting the local input path is a supported opt-in adapter, not a slated-for-removal legacy path.

## 3. Single fully-local switch + composition-root wiring

- [ ] 3.1 Add a `BLOOM_EXPERIMENT_LOCAL_ROOT` → `BLOOM_TRAITS_DIR` resolver (mirroring `_resolve_local_root`) plus a boot-time validator for the local input root (exists, is a readable dir).
- [ ] 3.2 Write a failing test that `server.main()` (or the composition helper) wires `LocalReader` when `BLOOM_STORAGE_BACKEND=local` and `SupabaseReader` otherwise; default wiring unchanged.
- [ ] 3.3 Implement the selection at the composition root (`server.main()` / `_ports.configure`): choose `LocalReader` vs `SupabaseReader` from the selected backend; leave `store` wiring as-is (already local under #389 when `local`).

## 4. Backend-aware boot gate

- [ ] 4.1 Write failing tests: with `BLOOM_STORAGE_BACKEND=local`, `server.main()`'s boot validation does **not** require `SUPABASE_URL` / `BLOOM_AGENT_KEY`, but **does** validate the local input root; with the default backend, boot still requires Supabase creds (fail-fast unchanged).
- [ ] 4.2 Implement the gate in `server.main()`: compute `fully_local` from the selected backend; skip `validate_supabase_env()` and validate the local input root when fully-local; keep `validate_data_env()` / `validate_storage_backend()` in both modes.

## 5. Route the stragglers through the port

- [ ] 5.1 Pin current `correlation_tools` cross-experiment outputs with a characterization test, then route its raw-CSV reads (`list_experiments` + the pairwise reads) through the injected `ExperimentReader`; assert outputs unchanged and that the active adapter is honored.
- [ ] 5.2 Route `start_run`'s source-CSV read (`_ports.py:84`) through the reader; degrade `source_csv` to `None` when no concrete path is available (already supported by `create_run`).
- [ ] 5.3 Grep-guard test: `correlation_tools.py` no longer reads `pd.read_csv(TRAITS_DIR / …)` directly.

## 6. Fully-local end-to-end test

- [ ] 6.1 Add an end-to-end test with `SUPABASE_URL` / `BLOOM_AGENT_KEY` **unset** and `BLOOM_STORAGE_BACKEND=local` (temp input + output roots): `import bloom_mcp`, boot the server through `main()`'s validators, and run a full `qc_clean → pca_analysis` — asserting success, real output files on disk, and **no** live Supabase access. If a residual PostgREST/table call exists on the path, record it and open a follow-up (table locality is out of scope).
- [ ] 6.2 Guard test: with the default backend, boot still fails fast when Supabase creds are unset.

## 7. Config + docs

- [ ] 7.1 Document `BLOOM_EXPERIMENT_LOCAL_ROOT` alongside #389's storage vars in `docker-compose.dev.yml` (commented; default off; no `${VAR}` reference, so env-parity CI is unaffected).
- [ ] 7.2 Update `bloommcp/docs/storage-backends.md`: `BLOOM_STORAGE_BACKEND=local` is now a **fully-local (offline) dev mode** (input + output); document `BLOOM_EXPERIMENT_LOCAL_ROOT`, the backend-aware boot gate, and that this is a **dev / power-user** path, not a normal-user packaged distribution; remove/replace the "not a fully-offline mode" caveat.

## 8. Validation

- [ ] 8.1 `openspec validate add-bloommcp-local-experiment-reader --strict` passes.
- [ ] 8.2 `bloommcp` unit suite green with no live Supabase; lint/format clean.
