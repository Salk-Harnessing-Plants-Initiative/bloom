## Why

bloom-mcp tools reach the deployed Supabase persistence directly, with no seam: workflows construct `AnalysisWriter` via `tools/workflows/_helpers.py`; `experiment_utils.load_experiment_data` reads raw inputs from the local `BLOOM_TRAITS_DIR` and versioned-cleaned outputs from Supabase Storage inline; `storage_tools.py` walks `AnalysisDir`; and `correlation_tools.py` reads experiment CSVs straight from `TRAITS_DIR`. Because nothing depends on an interface, the two deferred Phase 2 futures — DB-direct trait reads and orchestrator-owned/per-user-identity writes — cannot drop in without editing tool code, and every test must go through live Supabase or the local filesystem. Separately, the canonical `Provenance` (`contract/provenance.py`) is wired into nothing: `to_version_entry` has zero production callers, and `AnalysisWriter.commit` hand-rolls its own `VersionEntry`, so `seed`/`agent`/`environment`/`output_sha256`/`output_keys` are never persisted — runs carry a v3 schema number with v2 content.

Tier 2 (issue #307) introduces two **backend-agnostic** ports so tools depend on interfaces, not Supabase, and routes the deployed read/write code behind them. The `ResultStore` adapter also closes the provenance gap by building the manifest entry from `Provenance.to_version_entry` at commit. In-memory fakes make the slice testable with no live database. Builds on merged Tier 0 (installable `src/bloom_mcp`) and Tier 1 (`Provenance`, manifest schema v3).

> **Note — referenced design docs are not in the repo.** The issue cites `bloommcp/docs/2026-06-15-bloom-mcp-phase2-persistence-design.md` and a roadmap; neither is committed (the `bloommcp/docs/` directory does not exist). This proposal is grounded in the code as it actually is. The missing docs are flagged for the `/review-openspec` → approval gate.

## What Changes

- **New capability `bloommcp-experiment-read`** — a backend-agnostic `ExperimentReader` port:
  - `load_experiment(name, version="latest", require_clean=False) -> ExperimentFrame` where `ExperimentFrame` carries the frame plus **adapter-declared** column roles (trait/metadata columns) and a source label — roles are declared by the adapter, never re-inferred by callers — and `list_experiments() -> list[ExperimentSummary]`.
  - `version` supports `"latest"`, `"raw"`, and explicit `"v<N>"` with the deployed not-found semantics (explicit `v<N>` miss is a hard error; `"latest"` miss falls through the resolution order).
  - `SupabaseReader` adapter — wraps the current `experiment_utils` read path: raw inputs from the local `BLOOM_TRAITS_DIR` (**retained but deprecated** — see Non-Goals) and versioned-cleaned outputs from Supabase Storage, preserving the three-tier resolution order (versioned-manifest `latest` → legacy un-versioned `qc_<stem>/<stem>_cleaned.csv` → raw input).
  - `FakeReader` — in-memory adapter for tests.
  - **Route read consumers through the port:** `storage_tools.py`, `qc_tools.py`, and `correlation_tools.py` (which today reads `TRAITS_DIR` directly and registers its own `list_experiments`) obtain data via the injected `ExperimentReader`.
- **New capability `bloommcp-result-store`** — a backend-agnostic `ResultStore` port (`create_run(experiment, tool, params, provenance, user_label) -> RunHandle`; `commit(run, outputs) -> StoredRun`; `list_runs(experiment, tool)`; `get_run(experiment, tool, run_ref="latest")`):
  - `RunHandle` exposes the allocated version id and the staging directory (the actual write surface workflows write into) and the manifest path consumers return in responses; `run_ref` is an **opaque** adapter-defined handle — `tool_class`/`v<N>`/`latest`/object-key live in the adapter, not the port.
  - `SupabaseResultStore` adapter — wraps `AnalysisWriter`/`AnalysisDir` for versioning/staging/upload, and **builds the v3 `VersionEntry` via `Provenance.to_version_entry`** (persisting `seed`/`agent`/`environment`/`code_versions`), computing each artifact's `output_sha256` over the exact uploaded bytes (not an S3/MinIO ETag) and recording its logical `output_keys`. Tolerates pre-existing v2 manifests on read.
  - `FakeResultStore` — in-memory adapter for tests.
  - **Repoint workflows:** `tools/workflows/_helpers.py` builds runs via the injected `ResultStore`; each workflow constructs and passes a `Provenance` (new wiring — this is not a pure wrap).
- **Composition root in `server.py`** — construct the concrete adapters once at boot and thread them into `*.register(mcp, reader=..., store=...)`; tool registration signatures change so no tool module imports `supabase`, `AnalysisWriter`, or `AnalysisDir` directly.

## Impact

- **Affected specs:** new capabilities `bloommcp-experiment-read`, `bloommcp-result-store`.
- **Affected code (new):** `bloommcp/src/bloom_mcp/data_access/{__init__,ports,supabase_reader,fake_reader}.py`; `bloommcp/src/bloom_mcp/result_store/{__init__,ports,supabase_store,fake_store}.py`.
- **Affected code (modified):** `tools/workflows/_helpers.py` and all five workflows (build via `ResultStore`, construct `Provenance`, read `version_id`/staging from `RunHandle`); `tools/storage_tools.py`, `tools/qc_tools.py`, `tools/correlation_tools.py` (read via `ExperimentReader`); `experiment_utils.py` (read path moves into `SupabaseReader`; local raw read retained, deprecated); `server.py` (composition root + changed `register` signatures).
- **Reused unchanged:** `storage/` primitives (`writer.py`, `analysis_dir.py`, `schema.py`, `manifest.py`, `versioning.py`, `code_versions.py`), `supabase_client.py`, `contract/provenance.py` (`to_version_entry`).
- **New tests + fixtures:** `bloommcp/tests/data_access/`, `bloommcp/tests/result_store/`; a raw-input turface_19 fixture and a v3 `qc_turface_19` manifest fixture; a shared `fake_supabase_storage` conftest fixture so adapter tests monkeypatch the `supabase_client` boundary and never hit the network.
- **Non-Goals (explicitly deferred):**
  - **Retiring `BLOOM_TRAITS_DIR`** — it is the sole raw-input source in **dev and prod** (both compose files bind-mount `SLEAP_OUT_CSV`). Removal requires a prod input migration into `bloommcp_input/`, porting 7+ integration tests to `FakeReader`, and `validate_env`/`test_package_baseline` changes. Deferred to a dedicated follow-up PR; this change keeps the local read behind `SupabaseReader` with a deprecation log.
  - DB-direct trait reads (future `ExperimentReader` adapter); orchestrator-owned / per-user-identity writes + real RLS (future `ResultStore` adapter); manifest compare-and-swap. All deferred *behind* the ports; `ResultStore` inherits `AnalysisWriter`'s single-writer / no-CAS clobber risk (documented).
- **Branch/PR:** off `origin/staging`, PR targets `staging`; links issue #307.
