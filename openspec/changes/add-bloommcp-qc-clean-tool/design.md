## Context

Phase 2 Tiers 1–2 shipped the reusable seams; this tier is the first *consumer* that binds
them into a real tool — and the **QC foundation** every downstream analysis tool depends on.
The constraints are fixed by the existing code:

- `@as_mcp_tool(input_model=, output_model=, errors=)` validates Pydantic I/O, maps
  exceptions to `BloomMCPError`, resolves the seed, and stamps one `Provenance`. It injects
  `random_state` / `provenance` **only** into parameters the tool function declares (explicit
  kwarg-injection, not name inference). A tool that declares no `random_state` records
  `seed=None`; a seed explicitly passed to such a tool raises `internal_error` (a
  reproducibility-lie guard). `contract/wrap.py:80-130`
- `ExperimentReader.load_experiment(name, *, version="latest", require_clean=False)` returns
  an `ExperimentFrame` exposing `df`, `trait_cols`, the detected `genotype_col` /
  `replicate_col` / `sample_id_col` role columns, and a `source` label (`"raw"`,
  `"legacy_cleaned"`, or `"v<N>_cleaned"`). `data_access/ports.py:36-90`
- The `SupabaseReader` resolves a cleaned version from **versioned-cleaned outputs in
  Storage** (the `qc` tool-class runs that wrote `_cleaned.csv`), then the legacy
  un-versioned cleaned CSV, then the raw input. `data_access/supabase_reader.py:4-6`
- `ResultStore.create_run(*, experiment, tool_class, provenance, user_label, source_csv)
  -> RunHandle` then `commit(run, outputs) -> StoredRun`; `StoredRun` exposes `run_ref`,
  `version_dir`, `manifest_path`, `outputs`, `output_keys`, `output_sha256`, `seed`,
  `code_versions`. The port arg is `tool_class=` (not `tool=`) and carries **no** `params`
  arg — `params` flow via `Provenance.stamp(params=…)`, which the decorator stamps and the
  tool hands to `create_run(provenance=…)`. (NB: the `bloommcp-result-store` *spec* text
  still reads `create_run(experiment, tool, params, …)` — a stale spec vs. the shipped code;
  this tool follows the **code** signature. Flagged for a separate spec-sync fix, not this
  change's job.) `result_store/ports.py:99-135`, `tools/_ports.py:70-90`
- Tools reach the ports through `bloom_mcp.tools._ports` (`reader()`, `store()`); the
  composition root injects Supabase adapters at boot and fakes in tests. `tools/_ports.py`
- The existing `run_qc_workflow` already shows the persistence shape (writes `_cleaned.csv` +
  `cleanup_log.json` under tool class `qc`) — but it delegates to the **vendored**
  `bloom_mcp.data_cleanup.apply_data_cleanup_filters`, not to analyze. `tools/workflows/qc.py`
- The delegate: `clean_traits_for_analysis(df, trait_cols=None, *, barcode_col="Barcode",
  genotype_col="geno", replicate_col="rep", **cleanup_kwargs) -> (cleaned_df, kept_cols,
  log)` — released in analyze `0.1.0a3` (analyze#164); `inspect_nan_samples(df, trait_cols,
  …)` returns the NaN-location frame.

## Goals / Non-Goals

- **Goals:** one contract-wrapped `qc_clean` tool, registered + discoverable, delegating all
  QC to `clean_traits_for_analysis`, producing a no-NaN cleaned run through the MCP boundary
  with **less sample loss than naive `dropna()`**, persisting a versioned cleaned run that
  composes into `pca_analysis`, with the 5 contract patterns under test.
- **Non-Goals:** any QC logic in the MCP; running the full `QCPipeline`; re-stitching
  `load → cleanup → validate` in the MCP; removing `bloom_mcp.data_cleanup` or
  `run_qc_workflow` (deferred to after Stage 1); seed threading (QC is deterministic); a
  `v1/` tool namespace; the DB-direct reader or per-user-identity writer (deferred adapters).

## Decisions

- **Decision: delegate everything to `clean_traits_for_analysis`; the MCP owns no QC.** This
  is the whole point of the tier — prove thin delegation. The tool reads, calls the one
  upstream entry point (cleanup + validate, tested in analyze), persists, and returns links.
  It explicitly does **not** call the vendored `bloom_mcp.data_cleanup`, does **not** run
  `QCPipeline`, and does **not** re-stitch the load/cleanup/validate orchestration.
  - *Alternative considered:* keep using the vendored `apply_data_cleanup_filters` like
    `run_qc_workflow` does. Rejected: it duplicates logic the MCP should delegate, and the
    roadmap retires the vendored copy after Stage 1.
- **Decision: read raw, do not set `require_clean`.** `qc_clean` is the *producer* of cleaned
  data; requiring a cleaned input would be circular. It loads the raw frame (default
  `version`/`require_clean`) and surfaces an unresolvable experiment as a `BloomMCPError`.
- **Decision: forward the `ExperimentFrame`'s detected role columns into the delegate.** The
  turface fixture uses `Genotype`/`Replicate`, but `clean_traits_for_analysis` defaults to
  `geno`/`rep`/`Barcode`. The reader already detects the roles; passing `frame.genotype_col`
  / `frame.replicate_col` / `frame.sample_id_col` keeps role detection out of the tool and
  makes the delegate operate on the right columns.
- **Decision: the tool declares `provenance` but not `random_state`; seed is `None`.** QC is
  deterministic (threshold filters). Declaring `provenance` lets the decorator inject the
  single stamped record, which the tool hands to `store().create_run(provenance=...)` so the
  manifest records the *same* provenance the contract stamped (no double-stamp via
  `_ports.start_run`, which re-stamps its own). The four cleanup thresholds + trait selection
  are the determinism-governing params captured in provenance.
- **Decision: persist under tool class `qc` with `_cleaned.csv` so the run composes.** The
  reader resolves cleaned versions from `qc` runs that wrote `_cleaned.csv`. Writing the same
  class + filename makes `qc_clean`'s output the cleaned version that `pca_analysis`
  (`require_clean=True`, #308) consumes — the composition pattern the tier is meant to prove
  — without `qc_clean` depending on PCA.
- **Decision: return a summary + links, not the table.** `n_samples_in/out`,
  `n_traits_in/out`, retention, and the NaN-location summary go inline; the cleaned CSV and
  the cleanup log go to `ResultStore` and come back as `resource_link`s — per design
  (small structured results inline + `resource_link`s, never inline blobs).
- **Decision: a cleanup that would drop every trait is a structured error, not an empty
  run.** If the delegate returns zero kept trait columns, the tool raises a `BloomMCPError`
  with a relax-the-thresholds remedy rather than persisting a degenerate, unusable cleaned
  run that a downstream PCA would then fail on opaquely. The property invariant
  (`0 < n_traits_out`) is enforced by this guard.

## Risks / Trade-offs

- **No raw fixture today** → only the post-QC `turface_19_final_data.csv` is vendored; the
  oracle needs a raw, NaN-bearing table. Pre-work task 1.2 sources the raw input from the
  same #120/#146 origin and records the golden cleaned shape, so the oracle is an explicit
  asserted value, not re-derived from the code under test.
- **Hard dep on `0.1.0a3`** → unlike PCA, the delegate did not exist in `0.1.0a2`; the pin
  bump (bloom #327) is a prerequisite, captured as task 1.1. Without it the tool cannot
  import `clean_traits_for_analysis`.
- **Fakes diverge from Supabase adapters** → drive the per-tool tests through the same
  `_ports` seam the server uses (`configure(reader=FakeReader(...),
  store=FakeResultStore(...))`) for the persisted-run + provenance assertions.
- **The composition (commit → `require_clean` resolves) is NOT wireable through the fakes** →
  `FakeReader._cleaned` (populated only by `add_cleaned_version`) and `FakeResultStore._runs`
  are disjoint in-memory stores with no bridge, so a `qc_clean` commit to the fake store does
  not become a cleaned version the fake reader resolves. The honest composition test drives
  the **Supabase adapters** (`SupabaseReader` + `SupabaseResultStore`) over the shared
  `_InMemoryObjectStore` test double in `tests/conftest.py:41` — which exercises the real
  `qc`-class / `_cleaned.csv` resolution rule end-to-end. The fakes path asserts the per-port
  contracts; the adapters-over-object-store path asserts the cross-port handoff.
- **Role-column mismatch** → if detected roles are `None` (adapter could not detect), fall
  back to the delegate defaults rather than passing `None`; covered by the property test.

## Migration Plan

Additive only — a new tool + one registration line + a dependency-pin bump. No schema or data
migration; old manifests (provenance v2/v3) are unaffected. Rollback = unregister the tool
(the pin bump is independently safe).

## Open Questions

- Whether to inline a compact NaN-location summary (counts per trait/sample) or only link the
  full `inspect_nan_samples` output — settle during RED tests against the small-model agent
  surface (the issue lists this as optional).
- Exact shape of the recorded golden cleaned snapshot (store `n_samples_out`/`n_traits_out`
  only, or also the kept-column list) — settle when the raw fixture is sourced.
</content>
