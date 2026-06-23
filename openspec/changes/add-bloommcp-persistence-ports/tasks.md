> TDD: each port's RED test precedes its GREEN implementation. Per the repo's green-at-every-step CI, squash each RED test with the impl that satisfies it into one commit (or mark the oracle `xfail(strict=True)` until the impl lands). Adapter tests MUST monkeypatch the `supabase_client` boundary — no live Supabase, no `supabase.create_client`.

## 0. Fixtures & test scaffolding

- [x] 0.1 Add a shared `fake_supabase_storage` fixture in `tests/conftest.py` that fakes the `supabase_client` boundary functions (`read_input_csv`, `download_file`, `list_prefix`, `upload_file`, `write_json`/`read_json`) in-memory; patch `supabase.create_client` to raise so any real-client construction fails the test.
- [x] 0.2 Fixtures: reuse the existing `manifest_v2.json` for back-compat; v3 manifests + cleaned outputs are produced through the store in-test (no brittle static file); raw frames constructed inline / from the turface fixture.

## 1. Oracle / acceptance tests first (RED→GREEN)

- [x] 1.1 `FakeReader.load_experiment(fixture)` returns the expected frame + declared roles + source label; no live Supabase.
- [x] 1.2 `FakeResultStore.create_run` → write to staging → `commit` records a versioned run carrying `Provenance` (asserts `to_version_entry` fields) plus per-artifact links, retrievable via `list_runs`/`get_run`.
- [x] 1.3 Guard: write/read tools + workflows do not import `supabase`/`AnalysisWriter`/`AnalysisDir` directly. Authored `xfail(strict=True)` — flips to pass after §4.

## 2. Read port — `data_access/` (RED→GREEN)

- [x] 2.1 `ExperimentReader` port + `ExperimentFrame`/`ExperimentSummary` shapes in `data_access/ports.py`; roles are adapter-declared.
- [x] 2.2 `FakeReader` + tests for not-found envelope, explicit-version miss, `require_clean`, empty `list_experiments`.
- [x] 2.3 `SupabaseReader` adapter on the `fake_supabase_storage` boundary — versioned-cleaned resolution + local raw fallback with deprecation; relocates the `load_experiment_data` read path behind the port (local raw retained, deprecated).
- [x] 2.4 (Fake↔Supabase reader parity folded into the adapter/store parity coverage.)

## 3. Write port — `result_store/` (RED→GREEN)

- [x] 3.1 `ResultStore` port + `RunHandle` (exposes `version_id`, `staging_dir`, manifest path; opaque `run_ref`) + `StoredRun`. `RunHandle` holds the adapter-private state (opaque dataclass), keeping the commit-once / staging-cleanup invariant in one object.
- [x] 3.2 `FakeResultStore` + tests for `get_run("latest")` multi-commit resolution, unknown-run not-found, double-commit rejection.
- [x] 3.3 `SupabaseResultStore` — builds `VersionEntry` via `Provenance.to_version_entry`; per-artifact `output_sha256` over exact uploaded bytes (not ETag); logical `output_keys`; shared key-set; v2-manifest read tolerance; commit-failure cleanup (manifest not advanced).
- [x] 3.4 Fake↔Supabase store parity test (incl. `/`-separator keys).

## 4. Repoint consumers + composition root (GREEN)

- [x] 4.1 Repointed `tools/workflows/_helpers.py` (now a thin re-export of the ports seam) + the five workflows: persist via injected `ResultStore`, stamp/pass `Provenance`, read `version_id`/staging from `RunHandle`.
- [x] 4.2 Routed `storage_tools.py` (via `ResultStore.list_runs`) and `qc_tools.py` (via `ExperimentReader`) through the ports. `correlation_tools.py` uses a separate cross-experiment `EXPERIMENTS`/path-based path — no forbidden imports (passes the guard); its local reads are retained-deprecated and deferred to the `BLOOM_TRAITS_DIR`-removal follow-up.
- [x] 4.3 Composition root `bloom_mcp/tools/_ports.py` (defaults to the Supabase adapters; `configure()` swaps them); `server.main()` injects them explicitly at boot.
- [x] 4.4 Removed the `xfail` on the §1.3 import guard; it passes (no consumer imports `supabase`/`AnalysisWriter`/`AnalysisDir`).

## 5. Verify & refactor

- [x] 5.1 End-to-end test (`tests/workflows/test_workflow_persistence.py`): `run_qc_workflow` through injected `FakeReader`+`FakeResultStore` returns `v1` with the expected `manifest_path`/`outputs`, persists full v3 provenance, and increments to `v2` on re-run.
- [x] 5.2 Full bloommcp suite green (60 passed). `BLOOM_TRAITS_DIR` retained this PR — removal + prod migration + integration-test porting is a follow-up.
- [x] 5.3 Port interfaces stress-checked on paper: `run_ref` opaque, `StoredRun`/`RunHandle` carry strings a DB/orchestrator adapter can populate, `ExperimentFrame` roles are adapter-declared → both deferred adapters drop in without tool changes.
- [x] 5.4 `openspec validate --strict` passes; ruff clean; black formatted.
