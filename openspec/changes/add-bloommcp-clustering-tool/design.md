## Context

Phase 2 Tiers 1–4 shipped the reusable seams (contract + ports), the QC producer (`qc_clean`), the
outlier trimmer (`remove_outliers`), and the first analysis consumer (`pca_analysis`). This tier adds
the **second** consumer and the **first stochastic fit** — and the first tool whose upstream result
type is **polymorphic**. The constraints are fixed by the existing code, verified against the
currently pinned+locked `sleap-roots-analyze==0.1.0a4`:

- `@as_mcp_tool(input_model=, output_model=, errors=)` validates Pydantic I/O, maps exceptions to
  `BloomMCPError`, and stamps one `Provenance`. **Seed path (the new part for this tier):** it reads
  `data.seed` from the input model; if the tool **function declares a `random_state` parameter**, it
  calls `resolve_seed(requested)`, injects the resolved int as `random_state=`, and stamps
  `Provenance(seed=<resolved>)`. A tool that declares **no** `random_state` records `seed = None`
  (that was `pca_analysis`). It does **not** call `np.random.seed()` — it relies on the delegate's
  per-estimator `random_state`. A `BloomMCPError` raised inside the tool passes through verbatim; a
  **declared** `errors=` exception is mapped to a fixed `code="tool_error"` with a generic remedy — so
  to surface a specific code/remedy the tool must raise the `BloomMCPError` itself.
  `contract/wrap.py:80-130`, `contract/provenance.py`
- `ExperimentReader.load_experiment(name, *, version="latest", require_clean=False)` returns an
  `ExperimentFrame` exposing `df`, `trait_cols`, `metadata_cols`, detected role columns, and a
  `source` label (`"raw"`, `"legacy_cleaned"`, `"v<N>_cleaned"`). `require_clean=True` raises
  `CleanedVersionRequiredError` when no cleaned version exists. For a cleaned source, `trait_cols` is
  the set `qc_clean` certified no-NaN. `FakeReader.add_cleaned_version(name, version_id, df, *,
  make_latest=True)` seeds one directly (no `trait_cols=` kwarg; auto-derived by `detect_columns`);
  `FakeReader` and `FakeResultStore` are **disjoint** in-memory stores.
- `ResultStore.create_run(*, experiment, tool_class, provenance, user_label, source_csv) ->
  RunHandle` then `commit(run, outputs) -> StoredRun`. `RunHandle` exposes `staging_dir`;
  `StoredRun` exposes `run_ref`, `version_dir`, `manifest_path`, `outputs`, `output_keys`,
  `output_sha256`, `seed`, `code_versions`. `params` and `based_on_version` flow via the stamped
  `Provenance`.
- Tools reach the ports through `bloom_mcp.tools._ports` (`reader()`, `store()`, `configure()`); the
  composition root injects Supabase adapters at boot and fakes in tests.
- **The delegates (verified `0.1.0a4`):**
  - `perform_kmeans_clustering(data, n_clusters=3, max_clusters=10, standardize=True,
    random_state=42) -> Dict` (keys include `cluster_labels`, `cluster_sizes`, `silhouette_score`,
    `davies_bouldin_score`, `calinski_harabasz_score`, `cluster_centers`, `inertia`, `feature_names`,
    `data_indices`, `data_processed`). `n_clusters=None` auto-selects up to `max_clusters` by
    silhouette. Typed via `KMeansResult.from_kmeans_dict(d, random_state=…)`.
  - `perform_gmm_clustering(data, n_components=None, max_components=5, covariance_type="full",
    standardize=True, random_state=42) -> Dict` (keys include the shared scores + `bic`, `aic`,
    `weights`, `covariances`, `converged`, `n_iter`, `covariance_type`, `n_components`). `n_components=None`
    auto-selects up to `max_components` by BIC. Typed via `GMMResult.from_gmm_dict(d, random_state=…)`.
  - `KMeansResult` / `GMMResult` share a `ClusterResult` base (`algorithm`, `n_clusters`,
    `cluster_labels`, `cluster_sizes`, `silhouette_score`, `davies_bouldin_score`,
    `calinski_harabasz_score`, `feature_names`, `random_state`); each adds its own fields. All offer
    `to_dict` / `to_json`.
- **Verified characterization (8 golden traits, 153 samples, `standardize=True`):** kmeans
  (`n_clusters=3, seed=42`) → 3 clusters, silhouette `0.417`, sizes `[40, 85, 28]`, deterministic;
  gmm (`n_components=3, covariance_type="full", seed=42`) → 3 clusters, silhouette `0.316`, sizes
  `[32, 38, 83]`, `converged=True`, deterministic. Both bit-identical on a repeat with the same seed.

## Goals / Non-Goals

- **Goals:** one contract-wrapped `clustering` tool, registered + discoverable, **dispatching on
  `method`** to `perform_kmeans_clustering` / `perform_gmm_clustering` (typed via
  `from_kmeans_dict` / `from_gmm_dict`), consuming a **cleaned** input through `require_clean=True`
  and selecting only certified-clean traits, **stochastic with the resolved `seed` recorded in
  provenance**, **deterministic under a fixed seed** (same seed → identical labels — the oracle),
  persisting a versioned `clustering` run whose manifest records the cleaned-source lineage, with the
  5 contract patterns + the polymorphic-dispatch contract under test.
- **Non-Goals:** any clustering math in the MCP; re-cleaning or clustering raw data; **hierarchical**
  (deferred, upstream-gated); **UMAP** (dimensionality reduction — a separate scoping decision, maps
  to the legacy `dimred_workflow`); removing `bloom_mcp.clustering` or `run_clustering_workflow`
  (deferred to after Stage 1); inline label vectors; a `v1/` tool namespace; the DB-direct reader or
  per-user-identity writer (deferred adapters).

## Decisions

- **Decision: one polymorphic tool dispatching on `method`, not two tools.** A single `clustering`
  tool with `method: Literal["kmeans", "gmm"]` proves the contract surface generalizes across result
  shapes — the tier's whole point — and keeps the agent surface small (one tool, one schema). The
  dispatch is a table `{method: (perform_fn, from_dict_fn, count_param_name)}`; adding
  `"hierarchical"` later is one row + one `Literal` member. The output model is the **union** of the
  common `ClusterResult` fields (always present) plus optional method-specific scalars (`inertia` for
  kmeans; `bic` / `aic` / `converged` / `covariance_type` for gmm), so a caller/agent sees a stable
  core with method extras.
- **Decision: delegate everything; the MCP owns no clustering math.** The tool reads, dispatches to
  the one upstream `perform_*`, wraps the dict into the upstream typed result via its own
  `from_*_dict` adapter (so the dict→typed mapping is tested upstream, not re-implemented here),
  persists, and returns links. It does **not** call the vendored `bloom_mcp.clustering` and does not
  standardize / compute distances / iterate EM / compute metrics itself. A delegation-pinning test
  (spy the chosen `perform_*` is called once; assert `bloom_mcp.clustering` is never called) guards
  this.
- **Decision: stochastic — declare `random_state`, record the resolved seed.** k-means and GMM both
  consume `random_state` and their labels depend on it, so the tool declares a `random_state: int`
  function parameter and a `seed: int = Field(default=42, ge=0)` input field (mirroring
  `remove_outliers`). The contract resolves `seed` into `random_state`, forwards it to the delegate,
  and records `Provenance(seed=<resolved>)`. This is the first tier where the recorded seed is the one
  that produced the artifact — the seed-resolution path `pca_analysis` explicitly deferred to #309.
- **Decision: the oracle is determinism; the metrics are a characterization snapshot.** No independent
  clustering oracle exists for turface_19 (the upstream `viz_pca_metadata.json` records PCA/UMAP/
  heritability, not clustering). So the north-star test asserts **same seed → identical
  `cluster_labels`** (a genuine invariant) for each method, and a *separate* test compares the cluster
  metrics through the tool against a recorded snapshot honestly labeled a drift gate re-derived from
  `perform_*==0.1.0a4` (a `_source` field on the fixture), not an independent oracle — matching how
  the PCA golden frames its per-PC split / heritability / UMAP keys. Do **not** claim the snapshot is
  an oracle; do **not** loosen the determinism assertion.
- **Decision: require a cleaned input AND restrict selection to `frame.trait_cols`.** Same rationale
  as `pca_analysis`: `require_clean=True` is necessary but not sufficient, because the cleaned frame
  still carries non-surviving numeric columns that may hold NaN, and the delegates `dropna()`
  internally. The tool requires each requested column to be in the certified set and numeric
  (`invalid_input` otherwise), and asserts the selected subset is finite (`np.isfinite(...).all()`)
  before fitting → `assumption_violated` otherwise. This reuses `_qc_shared._validate_trait_subset` via
  a **parameterized opt-in**: the shared helper today validates only "in-frame + numeric" and is
  consumed by `qc_inspect` with those looser semantics, so the certified-set / empty-list /
  duplicate-name rejections `pca_analysis` added privately are promoted into `_qc_shared` **behind a
  new `require_certified: bool = False` flag** (default preserves `qc_inspect`'s current behavior
  exactly; `pca_analysis` and `clustering` pass `require_certified=True` + `frame.trait_cols`). One
  validator, two strictness levels, **no fourth copy and no behavior change to `qc_inspect`** — a
  regression test pins that `qc_inspect`'s validation is byte-identical after the promotion. This is the
  #308 §6 / #309 carry-over, done in-PR now that the strictness split is explicit rather than deferred.

- **Decision: set `based_on_version` via `model_copy`, not in-place mutation.** The tool records the
  cleaned-source lineage on a **copy** of the stamped provenance
  (`provenance.model_copy(update={"based_on_version": frame.source})`), matching `pca_analysis` and
  **not** `remove_outliers`' in-place `provenance.based_on_version = …` (whose own docstring warns that
  mutating the injected `Provenance` "should not proliferate"). A test asserts the contract-held
  provenance object is not mutated by the tool.
- **Decision: per-method cluster-count controls, validated and cross-guarded.** kmeans `n_clusters`
  (`ge=2`; omit → delegate auto-selects up to `max_clusters` by silhouette); gmm `n_components`
  (`ge=1`; omit → delegate auto-selects up to `max_components` by BIC) + gmm `covariance_type`
  (`Literal["full","tied","diag","spherical"]`, default `"full"`). A control set for the wrong method
  (e.g. `n_components` with `method="kmeans"`, or `covariance_type` with `method="gmm"` left default
  is fine but `covariance_type` with `method="kmeans"` is rejected) → `invalid_input`, mirroring
  `remove_outliers`' per-method threshold guard. `standardize` defaults `True` (the snapshots were
  computed with standardization on).
- **Decision: catch-and-remap errors in-tool for specific codes/remedies.** The `errors=` path yields
  a generic `tool_error`, so the tool raises its own `BloomMCPError`: `CleanedVersionRequiredError` →
  `remedy="run qc_clean first …"`; the delegate's `ValueError` (degenerate fit — too few samples, a
  single constant column, `n_clusters > n_samples`) → `code="assumption_violated"` with a remedy;
  unknown/out-of-set/non-numeric/duplicate/empty `trait_columns` or a wrong-method control →
  `code="invalid_input"` naming the offenders.
- **Decision: return the cluster summary inline, persist the labels as links.** `n_clusters`,
  `cluster_sizes`, `silhouette_score`, `davies_bouldin_score`, `calinski_harabasz_score`,
  `feature_names`, `n_samples`, `n_features`, and the method-specific scalars (`inertia` |
  `bic`/`aic`/`converged`/`covariance_type`) go inline; the **N-length label vector** and any
  centers/covariances go to `ResultStore` and come back as `resource_link`s. `labels.csv` prepends the
  frame's `metadata_cols` (Barcode/Genotype/Replicate) so a label row is traceable to its plant by a
  shared key, not fragile positional alignment — sound because the finite-guard makes the delegate's
  internal `dropna()` a no-op, keeping `cluster_labels` row-aligned with `frame.df`. Artifacts:
  `labels.csv`, `cluster_result.json` (`result.to_json()`).
- **Decision: persist under tool class `clustering` and record the cleaned-source lineage.** A new
  analysis class, distinct from the legacy `clustering` workflow's persistence and from `qc` / `pca`.
  The stamped `Provenance` sets `based_on_version = frame.source`; the consumed frame is snapshotted to
  a temp CSV passed as `source_csv`, so `input_sha256` content-addresses the exact bytes fed to the
  fit (parity with `pca_analysis` / `qc_clean`). Versioning is single-writer.

## Risks / Trade-offs

- **No independent oracle → determinism carries the correctness weight.** Mitigated by making
  determinism a first-class, per-method invariant test (same seed → identical labels), and by
  labeling the metric snapshot honestly as a drift gate (not an oracle) with a `_source` provenance
  field. A drift in `perform_*` surfaces as a snapshot-diff to triage, not a silent pass.
- **GMM auto-selection can collapse to 1 component on this data.** With `n_components=None`,
  `max_components=5`, BIC selects **1** component on turface_19 (silhouette `0.0`). That is a real
  (degenerate) result, not an error — but a trivial "golden". So the snapshot pins `n_components=3`
  explicitly (silhouette `0.316`, `converged=True`), and a scenario documents that omitting
  `n_components` may yield a single cluster (surfaced honestly in the summary, not hidden).
- **Composition needs a real `qc_clean` run only for the live-smoke leg.** The unit determinism
  oracle and `require_clean` property test seed a cleaned version directly through
  `FakeReader.add_cleaned_version` (serving the post-QC `turface_19_final_data.csv`), so they assert
  the consumer contract without a live Tier 3. The store and reader fakes are disjoint, so the cleaned
  version is seeded into the **reader**.
- **Seed determinism is estimator-scoped.** k-means (Lloyd, `n_init` fixed) and GMM (EM) are
  deterministic for a fixed `random_state` on a fixed input on the pinned sklearn; the tool records the
  resolved seed so the artifact is reproducible. Unlike PCA there is no "auto solver could ignore the
  seed" boundary — the seed is genuinely consumed, which is exactly why this tier exercises the path.
- **`from_*_dict` coupling to the result-dict shape.** Each `from_*_dict` is upstream's own,
  version-pinned adapter; the tool relies on it rather than mapping keys itself, so a breaking upstream
  change surfaces as an upstream test failure, not silent corruption here. The delegation-pinning test
  asserts the **right** wrapper is used per method (a kmeans dict must not be wrapped by
  `from_gmm_dict`, which is the exact mismatch the stale #309 note tripped over).
- **Hierarchical deferral is a real upstream gap, not a scoping dodge.** `perform_hierarchical_clustering`
  returns only a linkage matrix; there is no labeled entry point and no `from_hierarchical_dict`.
  Shipping it now would mean composing the cut-tree + metric computation **in the MCP** — exactly the
  "no analysis math in the MCP" line this whole phase draws. So it waits for the upstream
  labeled-clustering entry point (+ `Optional` `random_state`) in `0.1.0a5`; the dispatch table and
  the deterministic `seed = None` branch are built now so it drops in without a contract change.

## Migration Plan

Additive only — a new tool + one registration line (+ one docstring entry) + a new, clearly-labeled
characterization fixture, reusing the existing post-QC CSV and the 8 golden trait columns. No schema
or data migration; old manifests are unaffected; no dependency pin moves. Rollback = unregister the
tool. Hierarchical is a separate future change gated on an upstream release.

## Open Questions

- Whether to inline a compact `cluster_profile` summary (per-cluster trait means / dominant features)
  alongside the sizes+scores, or link only the full `cluster_result.json` — settle during RED against
  the small-model agent surface (the terse surface favors sizes + the three scores; a profile can be a
  follow-up).
- ~~Whether the shared-validator consolidation lands in this PR or as a follow-up~~ **RESOLVED (review):**
  promote the strict checks into `_qc_shared` **in this PR** behind a `require_certified` opt-in flag so
  `qc_inspect` is unchanged (see Decisions); a regression test pins `qc_inspect`'s behavior.
- The exact recorded snapshot literals for gmm's method-specific scalars (`bic` / `aic`) — captured in
  the RED step from the pinned `0.1.0a4` delegate by a **committed generator** (not hand-authored), so
  the whole `turface_19_clustering_golden.json` is regenerable and no metric literal is transcribed by
  hand.
