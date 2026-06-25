## Context

The deployed bloom-mcp prototype reads experiment inputs from the **local `BLOOM_TRAITS_DIR`** (`experiment_utils.load_experiment_data` raw branch; the Supabase-backed `read_input_csv` exists but has zero callers) and writes versioned outputs to Supabase Storage via `AnalysisWriter`/`AnalysisDir` + `manifest.json`. Tiers 0/1 are merged on `staging`: the installable `src/bloom_mcp` package, the `@as_mcp_tool` contract, the canonical `Provenance`, and manifest schema v3. Two gaps remain: (1) tools couple directly to persistence — `_helpers.py` (`AnalysisWriter`), `experiment_utils.py` (inline reads), `storage_tools.py`/`correlation_tools.py` (`AnalysisDir` / local `TRAITS_DIR`); (2) `Provenance.to_version_entry` is wired into nothing — `AnalysisWriter.commit` hand-rolls a `VersionEntry`, so `seed`/`agent`/`environment`/per-artifact hashes are never persisted despite the v3 schema. Two Phase 2 futures (DB-direct reads; orchestrator-owned/per-user writes) must replace the backend without touching tools, and every test currently needs live Supabase or the local FS.

> The Tier 2 persistence design doc and roadmap the issue cites (`bloommcp/docs/...`) are **not committed** in the repo. This design is grounded in the code; the missing docs are flagged for the approval gate.

## Goals / Non-Goals

- **Goals:** two **backend-agnostic** ports (`ExperimentReader`, `ResultStore`) whose deferred adapters drop in without tool changes; Supabase adapters wrapping the deployed storage; in-memory fakes for a no-live-DB slice; close the provenance gap at commit; repoint all read/write consumers behind a composition root.
- **Non-Goals:** retiring `BLOOM_TRAITS_DIR` (follow-up PR — it is the sole raw-input source in dev *and* prod); DB-direct reads; per-user identity / real RLS; manifest compare-and-swap; new analysis tools; agent-surface tool-list changes.

## Decisions

- **Decision: Backend-agnostic ports, not a thin rename of `AnalysisWriter`.** The issue's acceptance criterion is that the DB-direct reader and per-user writer drop in untouched. So Supabase concepts stay in the adapter: `run_ref` is opaque; `tool_class`/`v<N>`/`latest`/object-keys live in `SupabaseResultStore`; `ExperimentReader` returns an `ExperimentFrame` whose roles are **declared by the adapter**, not re-inferred via pandas dtypes (a tidy/long DB source can't reproduce dtype detection).
- **Decision: `RunHandle` is an opaque dataclass holding the live `AnalysisWriter` + staging dir + version id.** `AnalysisWriter` is single-commit with a `__del__` that cleans orphaned staging; a serializable Pydantic handle would split that invariant across two objects. Workflows write into `RunHandle.staging_dir` and read `RunHandle.version_id` before commit (dimred/clustering name plots by version id). `StoredRun` (returned by `commit`) is the serializable model: `run_ref`, `manifest_path`, `outputs`, links.
- **Decision: Provenance is built at the commit boundary by the adapter.** `SupabaseResultStore.commit` constructs the `VersionEntry` via `Provenance.to_version_entry(version_id=...)` rather than reusing `AnalysisWriter.commit`'s hand-rolled entry, and computes per-artifact `output_sha256` over the **exact bytes uploaded** (single read → hash → upload the same buffer), recording logical Supabase keys — never an S3/MinIO ETag. `input_sha256` stays on `ExperimentBlock`. This is honestly *new logic layered on the writer's upload loop*, not pure delegation; workflows also newly construct a `Provenance` (the contract decorator isn't on these workflows yet).
- **Decision: Composition root in `server.py`.** Adapters are constructed once at boot and threaded through `*.register(mcp, reader=..., store=...)`; registration signatures change so no tool module imports `supabase`/`AnalysisWriter`. (Note: `get_postgrest_client()` still builds a client per call *inside* the adapter — injection is at the port layer; tests fake the `supabase_client` boundary.)
- **Decision: keep the local raw read (deprecated).** `SupabaseReader` retains the `BLOOM_TRAITS_DIR` raw branch behind the port with a deprecation log, so this PR is green and revertible; removal is a separate migration PR.
- **Alternatives considered:** (a) thin-wrap ports mirroring `AnalysisWriter` — rejected, the deferred adapters wouldn't fit (inverts the goal); (b) retire `BLOOM_TRAITS_DIR` now — rejected, prod-affecting blast radius (compose mounts, `validate_env`, `test_package_baseline`, 7+ integration tests); (c) reuse `AnalysisWriter.commit`'s entry — rejected, it's provenance-lossy.

## Risks / Trade-offs

- **Provenance-lossy regression if the adapter delegates entry-building to `AnalysisWriter.commit`.** → Adapter builds the entry via `to_version_entry`; test asserts `seed`/`agent`/`environment` round-trip (1.2, 3.3a).
- **`correlation_tools` still reads local `TRAITS_DIR`.** → Repointed in 4.2 and covered by the import guard, else the "read via port" requirement is falsifiable.
- **Single-writer / no-CAS clobber inherited from `AnalysisWriter`.** → Documented as a known limitation; safe under the one-container topology; CAS deferred.
- **Backend-agnostic port shape costs more design now.** → Paid down by the on-paper deferred-adapter stress test (5.3) before merge.
- **Fakes drifting from adapters.** → Explicit fake↔adapter parity tests (2.4, 3.4).

## Migration Plan

1. Fixtures + fakes + ports + adapters behind the interface (no consumer change) — suite green.
2. Repoint `_helpers.py`/workflows, `storage_tools`, `qc_tools`, `correlation_tools`, and add the `server.py` composition root.
3. Rollback = revert the repoint commit; adapters wrap unchanged storage code and the local raw read is retained, so reverting restores prior behaviour.
4. Follow-up PR (out of scope): migrate prod inputs to `bloommcp_input/`, drop the local raw branch, update compose/`validate_env`/`test_package_baseline`, port integration tests to `FakeReader`.

## Open Questions

- Resolved: the only local-dir `list_experiments` callers are `correlation_tools.list_experiments` and `qc_tools.list_available_experiments` — both repointed here.
- Where does the 30s response cache in `storage_tools.py` live after repointing — in the tool (consumer) or the adapter? Lean: keep it in the consumer so fakes/DB-adapter needn't reimplement TTL. Confirm in 4.2.
- Does `Supabase`-side `list_experiments` need a real `bloommcp_input/` enumeration now, or is the local-dir listing (retained, deprecated) sufficient until the follow-up? Lean: sufficient for this PR.
