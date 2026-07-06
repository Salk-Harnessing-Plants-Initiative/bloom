## Context

Phase 2 Tiers 1–3 shipped the reusable seams (contract + ports) and the QC **producer**
(`qc_clean`). This tier is the first **consumer** that binds them into an analysis tool. The
constraints are fixed by the existing code:

- `@as_mcp_tool(input_model=, output_model=, errors=)` validates Pydantic I/O, maps exceptions to
  `BloomMCPError`, and stamps one `Provenance`. It reads a requested seed from the input model's
  `seed` field and injects `random_state` / `provenance` **only** into parameters the tool
  function declares. A tool that declares **no** `random_state` records `seed = None`; a tool that
  declares it gets the resolved seed injected and recorded. A `BloomMCPError` raised _inside_ the
  tool is passed through verbatim (`wrap.py:127-128`); a **declared** exception (the `errors=`
  tuple) is mapped to a **fixed** `code="tool_error"` with a generic remedy — it does **not**
  carry a custom code or remedy. So to surface a specific code/remedy the tool must raise the
  `BloomMCPError` itself. `contract/wrap.py:80-130`, `contract/errors.py:90-95`
- `ExperimentReader.load_experiment(name, *, version="latest", require_clean=False)` returns an
  `ExperimentFrame` exposing `df`, `trait_cols`, `metadata_cols`, the detected role columns, and a
  `source` label (`"raw"`, `"legacy_cleaned"`, or `"v<N>_cleaned"`). `require_clean=True` raises
  `CleanedVersionRequiredError` when no cleaned version exists. For a cleaned source, `trait_cols`
  is the set `qc_clean` certified no-NaN. `data_access/ports.py:36-86`, `fake_reader.py`
- `ResultStore.create_run(*, experiment, tool_class, provenance, user_label, source_csv) ->
RunHandle` then `commit(run, outputs) -> StoredRun`; `RunHandle` exposes `staging_dir` /
  `version_id` / `manifest_path`, and `StoredRun` exposes `run_ref`, `version_dir`,
  `manifest_path`, `outputs`, `output_keys`, `output_sha256`, `seed`, `code_versions`. `params`
  and `based_on_version` flow via the stamped `Provenance`. `result_store/ports.py:38-121`
- Tools reach the ports through `bloom_mcp.tools._ports` (`reader()`, `store()`, `configure()`);
  the composition root injects Supabase adapters at boot and fakes in tests. `FakeReader` exposes
  `add_cleaned_version(name, version_id, df, *, make_latest=True)` — no `trait_cols` argument; the
  cleaned frame's `trait_cols` are auto-derived by `detect_columns(df)`. `FakeReader` and
  `FakeResultStore` are **disjoint** in-memory stores (a commit to the store is not visible to the
  reader), so a cleaned version consumed by `require_clean=True` must be seeded **into the reader**
  directly. `data_access/fake_reader.py:51`, `result_store/fake_store.py`
- The delegate: `perform_pca_analysis(data, standardize=True, explained_variance_threshold=0.95,
n_components=None, random_state=42, include_feature_metrics=True, ddof_feature_var=None) ->
Dict`. It `dropna()`s internally and **raises `ValueError`** on degenerate input (not 2D, empty,
  no samples after NaN-drop, no non-zero-variance numeric column, < 2 samples), and **clamps**
  `n_components` to the feature count (verified: it does not raise on `n_components > n_features`).
  On the pinned **sklearn 1.8.0**, `PCA` is constructed with `svd_solver="auto"`, which selects the
  deterministic `covariance_eigh` path for this tool's data regime (n_samples ≫ few features); the
  randomized/arpack path is not taken, and `covariance_eigh` ignores `random_state`. So the fit is
  deterministic and `random_state` is inert here. The typed bridge is
  `PCAResult.from_pca_dict(result_dict) -> PCAResult`, whose fields are `n_components`,
  `feature_names`, `explained_variance_ratio` (per-PC), `cumulative_variance_ratio`, `eigenvalues`,
  `loadings`, `scores`, `standardized`, `random_state`, `explained_variance_threshold`,
  `feature_contributions`; it offers `to_dict`, `to_json`, `cumulative_variance`. Released in
  `0.1.0a3`, already pinned. (`sleap_roots_analyze.pca`)
- The golden: on the post-QC `turface_19_final_data.csv` restricted to the 8 recorded `trait_cols`,
  the delegate yields `n_components_selected == 3`, per-PC `explained_variance_ratio ==
[0.8612933510667774, 0.05820169635401897, 0.040414549139183936]` and
  `cumulative_variance_ratio[2] == 0.9599095965599803`. The **cumulative** value + `n=3` are the
  independent #120 / PR #146 oracle (recorded in upstream `viz_pca_metadata.json`); the **per-PC
  split is not in the upstream metadata** — it is only reproducible by running the delegate, so it
  is a characterization snapshot, not an independent oracle (see Decisions).

## Goals / Non-Goals

- **Goals:** one contract-wrapped `pca_analysis` tool, registered + discoverable, delegating all
  PCA to `perform_pca_analysis` (typed via `PCAResult.from_pca_dict`), consuming a **cleaned**
  input through `require_clean=True` and selecting only certified-clean traits, **deterministic
  with `seed = None`**, reproducing the #120 cumulative-variance oracle through the MCP boundary,
  persisting a versioned `pca` run whose manifest records the cleaned-source lineage, with the 5
  contract patterns under test.
- **Non-Goals:** any PCA math in the MCP; re-cleaning or running PCA on raw data; a stochastic seed
  (PCA here is deterministic); removing `bloom_mcp.pca` or `run_dimensionality_reduction_workflow`
  (deferred to after Stage 1); heritability / UMAP / clustering (separate tiers); inline
  score/loadings matrices; a `v1/` tool namespace; the DB-direct reader or per-user-identity writer
  (deferred adapters).

## Decisions

- **Decision: delegate everything to `perform_pca_analysis`; the MCP owns no PCA.** The tool reads,
  calls the one upstream entry point, wraps the dict into the upstream `PCAResult` via
  `PCAResult.from_pca_dict` (analyze's own adapter — so the dict→typed mapping is tested upstream,
  not re-implemented here), persists, and returns links. It does **not** call the vendored
  `bloom_mcp.pca` and does **not** standardize / decompose / select components itself. A
  delegation-pinning test (spy the delegate is called once; assert `bloom_mcp.pca` is never called)
  guards this.
- **Decision: require a cleaned input (`require_clean=True`) AND restrict selection to
  `frame.trait_cols`.** `pca_analysis` is the _consumer_. Requiring a cleaned version is necessary
  but **not sufficient** — the reader's cleaned frame guarantees no-NaN only in its _surviving_
  trait columns, and the frame still carries other numeric columns that may hold NaNs. If the tool
  validated `trait_columns` by mere existence + numeric dtype (as `qc_clean` does over a raw frame),
  a caller could select a NaN-bearing numeric non-surviving column and `perform_pca_analysis` would
  silently `dropna()` those rows — the exact uncontrolled loss `require_clean` is meant to prevent.
  So the tool requires each requested column to be in `frame.trait_cols` (the certified set) and
  numeric, raising `invalid_input` otherwise, and asserts the selected subset is NaN-free before
  fitting (defense-in-depth against a mis-reporting reader → `assumption_violated`). This makes
  "PCA runs only on a certified no-NaN table" literally true, not assumed.
- **Decision: deterministic; declare no `random_state`; record `seed = None`.** Verified: on the
  pinned sklearn the delegate fits via the deterministic `covariance_eigh` solver and the golden is
  bit-identical across seeds 42 / 7 / 999999. Recording a resolved seed for a computation that does
  not consume one would be reproducibility theater — and the codebase's own convention (`qc_clean`,
  deterministic) is to record `seed = None` and declare no `random_state`. `pca_analysis` follows
  it. The genuinely stochastic tools (KMeans / GMM / UMAP) arrive in the clustering tier (#309),
  where `random_state` is consequential; that is where the contract's seed-resolution path is
  exercised for real.
- **Decision: catch-and-remap errors in-tool for specific codes/remedies.** The contract's
  `errors=` declared-exception path yields a generic `tool_error`, so the tool raises its own
  `BloomMCPError`: `CleanedVersionRequiredError` → `code="tool_error"`-is-not-enough, so catch it
  and raise `BloomMCPError(code=..., remedy="run qc_clean first ...")`; the delegate's `ValueError`
  (degenerate fit) → `BloomMCPError(code="assumption_violated", remedy=pick-a-broader-subset)`;
  unknown/out-of-set/non-numeric `trait_columns` → `BloomMCPError(code="invalid_input", ...)` naming
  the columns. The `errors=` tuple is therefore **not** relied on to produce these codes.
- **Decision: forward `standardize` / `explained_variance_threshold` / `n_components` with
  validated ranges.** `explained_variance_threshold` is `[0,1]`; `n_components` is `>= 1` and, when
  given, overrides threshold-based selection (delegate precedence). The delegate **clamps**
  `n_components` to the feature count rather than raising, and the tool surfaces the clamped value in
  `n_components` — a scenario pins this. `standardize` defaults `True` (the golden was computed with
  standardization on).
- **Decision: return the variance summary inline, persist the matrices as links.** `n_components`,
  per-PC `explained_variance_ratio`, `cumulative_variance_ratio`, `eigenvalues`, `feature_names`,
  and the certified `n_samples` / `n_features` go inline; `loadings.csv`, `scores.csv`, and the
  serialized `PCAResult` (`pca_result.json`) go to `ResultStore` and come back as `resource_link`s.
  The N×k score matrix is **never** inlined. (Resolves the earlier open question on the artifact
  set: three artifacts — `loadings.csv`, `scores.csv`, `pca_result.json`.) `scores.csv` prepends the
  frame's `metadata_cols` (Barcode/Genotype/Replicate, mirroring upstream's
  `run_pca_and_export_artifacts` stitch) so a score row is traceable to its plant by a shared key,
  not by fragile positional alignment — sound because the finite-guard makes the delegate's `dropna()`
  a no-op, keeping `pca.scores` row-aligned with `frame.df`. `loadings.csv` keeps its
  feature-name index.
- **Decision: persist under tool class `pca` and record the cleaned-source lineage.** A new analysis
  class, distinct from the legacy `dimred` workflow and from `qc`. The stamped `Provenance` sets
  `based_on_version = frame.source` (the `v<N>_cleaned` label) so the manifest answers "which
  `qc_clean` run produced the input this PCA consumed"; `input_sha256` / `source_csv` capture input
  _content_, `based_on_version` captures the _version lineage_. Because the consumed cleaned frame
  lives in the backend (not at a known local path), the tool snapshots `frame.df` to a temp CSV and
  passes it as `source_csv`, so `input_sha256` content-addresses the exact bytes fed to the fit — the
  same parity `qc_clean` gives its raw input, closing the "bytes changed under an unchanged
  `v<N>_cleaned` label" gap. Versioning is single-writer.

## Risks / Trade-offs

- **Composition needs `qc_clean` (#356) merged** → only the _live-smoke_ leg exercises
  `require_clean` against a real `qc_clean` run. The unit oracle and the `require_clean` property
  test seed a cleaned version directly through `FakeReader.add_cleaned_version` (serving the post-QC
  `turface_19_final_data.csv`), so they assert the consumer contract without a live Tier 3.
  Implementation rebases onto `staging` once #356 lands; the smoke leg is gated on it. Because the
  fakes' reader and store are disjoint, the cleaned version must be seeded into the **reader**, not
  produced by a store commit.
- **The golden fixture is NaN-free in every column** → the oracle test alone cannot expose a silent
  `dropna()`. So the NaN-safety is pinned by a _separate_ test that seeds a cleaned version whose
  selected subset is clean but a sibling numeric column carries NaN, and asserts (a) selecting the
  NaN column is rejected (`invalid_input`) and (b) `result.n_samples` equals the certified row count
  — making silent sample loss a test failure.
- **The per-PC golden split is a characterization snapshot, not an independent oracle** → the
  independent, externally recorded values are the cumulative `0.9599…` + `n=3` (from upstream
  `viz_pca_metadata.json`). The added per-PC `pca_explained_variance_ratio` is honestly labeled as a
  drift gate re-derived from `perform_pca_analysis==0.1.0a3` (its own `_pca_evr_source` field),
  matching how the fixture already frames heritability/UMAP. It guards drift; it does not
  independently corroborate the split.
- **`from_pca_dict` coupling to the result-dict shape** → `from_pca_dict` is upstream's own,
  version-pinned adapter; the tool relies on it rather than mapping keys itself, so a breaking
  upstream change surfaces as an upstream test failure, not silent corruption here.
- **Rank-deficient selection** → a rank-deficient-but-not-constant selection (e.g. two perfectly
  collinear traits, or `n_components > rank`) yields a near-zero-variance trailing PC without a
  warning (constant/all-zero/<2-sample inputs correctly raise `ValueError → assumption_violated`).
  Documented as a known limitation for now; a low-variance-component warning is a possible follow-up.
  Note that duplicate **column names** (`["Holes", "Holes"]`) are a distinct hazard — the delegate's
  `standardize_data` re-selects each, inflating the feature set while `n_features` under-reports — so
  the tool rejects duplicates up front (`invalid_input`) rather than fitting collinear copies.
- **`seed = None` is regime-scoped, not universal** → the determinism claim rests on sklearn's
  `svd_solver="auto"` choosing the deterministic `covariance_eigh` path, which holds only while
  `n_features <= 1000` **and** `n_samples >= 10 * n_features`. That is comfortably true for this
  tool's tabular-phenotyping regime (a handful to a few dozen traits over hundreds–thousands of
  samples), and every shipped test exercises it. A pathologically **wide** selection (e.g. ~150
  traits × ~1300 samples fails the `10×` guard) could route `auto` to the randomized solver, which
  consumes the delegate's hard-coded `random_state=42`. The fit stays *reproducible* (fixed internal
  seed), but `seed = None` would then under-describe the computation. This is a documented boundary,
  not a silent assumption; if wide selections become in-scope, the tool should either assert the
  `covariance_eigh` precondition (reject/branch otherwise) or record the effective seed.
- **`n_features` mismatch from a constant certified trait** → the delegate silently drops
  zero-variance columns before fitting, which would leave `n_features` (requested count) disagreeing
  with the shorter `loadings`/`feature_names` it actually fit. The tool detects the drop after the
  fit and raises `assumption_violated` naming the constant column(s) rather than persisting an
  internally inconsistent artifact, so the reported `n_features` always equals the fitted feature
  count.
- **Cleaned frame has 12 detected traits, golden uses 8** → `detect_columns(turface_19_final_data)`
  finds 12 numeric traits (15 columns − Barcode/Genotype/Replicate); the golden oracle is over a
  specific 8 (`golden["trait_cols"]`). All 8 are within the 12, so the certified-set restriction
  keeps them; the oracle test passes `trait_columns=golden["trait_cols"]`.

## Migration Plan

Additive only — a new tool + one registration line (+ one docstring entry), reusing an existing
fixture (with an added, clearly-labeled per-PC drift key). No schema or data migration; old
manifests are unaffected; no dependency pin moves. Rollback = unregister the tool.

## Open Questions

- Whether to inline a compact `feature_contributions` summary (top-loading traits per PC) alongside
  the variance ratios, or link only the full `pca_result.json` — settle during RED against the
  small-model agent surface (the small surface favors a terse summary; upstream `viz_pca_metadata`
  records a `top_features` list that could seed this).
- Whether to extract the `trait_columns` subset validator now (shared with `qc_clean`) or after both
  tools are on `staging` — deferred to a follow-up refactor, since `qc_clean_tool.py` is not on
  `staging` until #356 merges.
