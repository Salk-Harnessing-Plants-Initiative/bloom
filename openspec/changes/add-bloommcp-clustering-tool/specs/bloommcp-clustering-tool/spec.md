## ADDED Requirements

### Requirement: Clustering Tool Registration and Discovery

The system SHALL expose a `clustering` MCP tool registered on the FastMCP server so it is
discoverable via the MCP `tools/list` operation. The tool name SHALL be stable (`clustering`, never
versioned in the name) and its registration SHALL NOT remove or rename the existing
`run_clustering_workflow` tool or the vendored `bloom_mcp.clustering` module.

#### Scenario: Tool appears in tools/list

- **WHEN** a FastMCP `Client` connects to the server and calls `tools/list`
- **THEN** a tool named `clustering` is present with a description and an input schema derived from
  its Pydantic input model

#### Scenario: Existing clustering workflow is preserved

- **WHEN** the server registers `clustering`
- **THEN** `run_clustering_workflow` remains registered and `bloom_mcp.clustering` remains importable,
  so server boot is unaffected

### Requirement: Clustering Delegates All Computation to the Tested Upstream Entry Points

The `clustering` tool SHALL delegate the clustering computation to
`sleap_roots_analyze.perform_kmeans_clustering` or `sleap_roots_analyze.perform_gmm_clustering`
according to the requested `method`, and SHALL wrap the delegate's result into the matching upstream
typed result via `KMeansResult.from_kmeans_dict` or `GMMResult.from_gmm_dict`. It SHALL contain no
clustering math of its own — no standardization, distance computation, EM/Lloyd iteration, or
internal-validation metric computation — and SHALL NOT call the vendored `bloom_mcp.clustering`.

#### Scenario: k-means is delegated to the correct entry point and wrapper

- **WHEN** `clustering` runs with `method = "kmeans"` on a cleaned experiment frame
- **THEN** `perform_kmeans_clustering` is invoked exactly once and its result is wrapped via
  `KMeansResult.from_kmeans_dict`
- **AND** `perform_gmm_clustering`, `GMMResult.from_gmm_dict`, and the vendored `bloom_mcp.clustering`
  are never called, and the tool performs no standardization or metric computation itself

#### Scenario: GMM is delegated to the correct entry point and wrapper

- **WHEN** `clustering` runs with `method = "gmm"` on a cleaned experiment frame
- **THEN** `perform_gmm_clustering` is invoked exactly once and its result is wrapped via
  `GMMResult.from_gmm_dict`
- **AND** `perform_kmeans_clustering`, `KMeansResult.from_kmeans_dict`, and the vendored
  `bloom_mcp.clustering` are never called

#### Scenario: Cluster-count controls are forwarded and auto-selection is honored

- **WHEN** a caller sets an explicit `n_clusters` (k-means) or `n_components` (GMM)
- **THEN** the tool forwards it to the delegate and the result's `n_clusters` reflects it
- **AND** when the caller omits it, the delegate auto-selects the cluster count (k-means up to
  `max_clusters` by silhouette; GMM up to `max_components` by BIC) and the tool surfaces the selected
  count in its summary rather than hiding it

#### Scenario: GMM auto-selection may collapse to a single component, surfaced honestly

- **WHEN** `clustering` runs with `method = "gmm"` and `n_components` omitted, and BIC selects a single
  component on the data
- **THEN** the tool reports `n_clusters == 1` (with the corresponding degenerate `silhouette_score`) in
  its summary and does not raise, rather than hiding the collapse or presenting a spurious multi-cluster
  result

### Requirement: Clustering Requires a Cleaned Input and Selects Only Certified-Clean Traits

The `clustering` tool SHALL load its experiment frame through the injected `ExperimentReader` port
with `require_clean=True`, as a **consumer** of cleaned data, and SHALL restrict the clustering to
columns within the resolved frame's certified-clean trait set (`frame.trait_cols`). It SHALL NOT
cluster a raw input and SHALL NOT perform its own cleaning. When no committed cleaned version exists
for the experiment, the tool SHALL surface a structured `BloomMCPError` whose remedy directs the
caller to run `qc_clean` first. A requested trait column outside the certified-clean set (or a
non-finite value that survives into the selected subset) SHALL be rejected rather than silently
row-dropped by the delegate.

#### Scenario: A cleaned experiment is consumed

- **WHEN** `clustering` is invoked on an experiment that has a committed cleaned version (a `qc_clean`
  run)
- **THEN** the reader resolves the cleaned version (source `v<N>_cleaned`, not `raw`), and the tool
  clusters it

#### Scenario: An experiment with no cleaned version is rejected with a remedy

- **WHEN** `clustering` is invoked on an experiment that has only a raw input and no committed cleaned
  version
- **THEN** the tool returns a `BloomMCPError` whose remedy is to run `qc_clean` first, and no
  clustering run is produced

#### Scenario: A trait column outside the certified-clean set is rejected, not silently dropped

- **WHEN** `trait_columns` names a numeric column that is present in the frame but not in the
  certified-clean trait set (`frame.trait_cols`), including one that still carries NaN values
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the column, and does
  not fit clustering on it — so the delegate never silently `dropna()`s the affected samples
- **AND** on a valid selection the result's `n_samples` equals the certified cleaned row count (no
  samples are silently lost)

#### Scenario: Omitting the trait selection uses the full certified-clean set

- **WHEN** `trait_columns` is omitted on a cleaned experiment whose certified-clean trait set is larger
  than the recorded golden subset (e.g. the cleaned turface_19 frame exposes more certified traits than
  the 8 golden columns)
- **THEN** the tool clusters over the **full** certified-clean set (`feature_names` equals
  `frame.trait_cols`, not a narrower subset) and `n_samples` still equals the certified cleaned row
  count — so a regression that silently narrowed the default selection is caught

### Requirement: Clustering Is Deterministic Under a Fixed Seed and Records the Resolved Seed

The `clustering` tool SHALL be stochastic in its dependency on `random_state`: it SHALL declare a
`random_state` parameter, the contract SHALL resolve the caller's requested `seed` into it and forward
it to the delegate, and the stamped `Provenance` SHALL record the **resolved seed** that produced the
labels (not `None`). The resolved seed SHALL actually reach the delegate's `random_state` (recording a
seed the fit never consumed would be a reproducibility lie). Two runs with the same seed and inputs
SHALL produce **identical** cluster labels.

#### Scenario: The resolved seed reaches the delegate, not just the manifest

- **WHEN** `clustering` runs with a requested `seed`
- **THEN** the chosen delegate (`perform_kmeans_clustering` / `perform_gmm_clustering`) is invoked with
  `random_state` equal to that resolved seed — so a tool that recorded the seed in provenance but passed
  a different (or hard-coded) `random_state` to the fit is rejected by test

#### Scenario: The resolved seed is recorded in provenance

- **WHEN** `clustering` completes with a requested `seed`
- **THEN** the stamped `Provenance` records that resolved seed (e.g. `seed = 42`), together with the
  tool name, the `method`, and the cluster params, and the committed `StoredRun` records the same seed

#### Scenario: Same seed produces identical labels

- **WHEN** `clustering` is invoked twice on the same cleaned experiment with the same `method`,
  parameters, and `seed`
- **THEN** the two results' `cluster_labels` are element-wise identical and their `cluster_sizes` are
  equal

### Requirement: Clustering Reproduces the turface_19 Characterization Snapshot Through the Tool

The `clustering` tool SHALL, when invoked through the MCP boundary on the cleaned turface_19 fixture
restricted to the recorded golden trait columns at the pinned per-method parameters and seed,
reproduce the recorded cluster-metric characterization snapshot within tolerance. This snapshot is a
drift gate re-derived from `perform_*==0.1.0a4` — **not** an independently recorded oracle — and SHALL
be labeled as such in the fixture.

#### Scenario: k-means cluster metrics match the recorded snapshot

- **WHEN** `clustering` runs with `method = "kmeans"`, `trait_columns` set to the 8 recorded golden
  trait columns, `n_clusters = 3`, `standardize = true`, and `seed = 42`
- **THEN** the result reports `n_clusters == 3`, `cluster_sizes == [40, 85, 28]`, and the silhouette,
  Davies–Bouldin, and Calinski–Harabasz scores equal the recorded snapshot values within `abs = 1e-6`

#### Scenario: GMM cluster metrics match the recorded snapshot

- **WHEN** `clustering` runs with `method = "gmm"`, the same 8 golden trait columns,
  `n_components = 3`, `covariance_type = "full"`, `standardize = true`, and `seed = 42`
- **THEN** the result reports `n_clusters == 3`, `converged == true`, `cluster_sizes == [32, 38, 83]`,
  and the three internal-validation scores equal the recorded snapshot values within `abs = 1e-6`

### Requirement: Clustering Honors the Contract Envelope

The `clustering` tool SHALL be wrapped by `@as_mcp_tool` so that inputs and outputs are validated
against declared Pydantic models, every declared/undeclared failure is mapped to a structured
`BloomMCPError` (never a raw traceback or leaked backend internals), and a single `Provenance` is
stamped per call.

#### Scenario: Input/output schema round-trip

- **WHEN** a valid request is serialized to the tool's input schema and the result is validated against
  the output schema, for both `method = "kmeans"` and `method = "gmm"`
- **THEN** both validate without loss, the output's common cluster fields are always present, and the
  method-specific scalars are present for their method and absent otherwise

#### Scenario: Method-specific scalars are mutually exclusive by method

- **WHEN** a `method = "kmeans"` result and a `method = "gmm"` result are compared
- **THEN** the k-means result carries `inertia` set and `bic` / `aic` / `converged` / `covariance_type`
  unset, and the GMM result carries `bic` / `aic` / `converged` set and `inertia` unset — the tool never
  populates both families or neither (the polymorphic-shape guarantee the tier exists to prove)

#### Scenario: Out-of-range parameters are rejected

- **WHEN** a request sets `n_clusters < 2` (k-means) or `n_components < 1` (GMM)
- **THEN** the tool returns a `BloomMCPError` with an input/validation code, and no run is persisted

#### Scenario: A cluster-count control set for the wrong method is rejected

- **WHEN** a request supplies `n_components` (or `covariance_type`) with `method = "kmeans"`, or
  `n_clusters` with `method = "gmm"`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the mismatched control,
  rather than silently ignoring it

#### Scenario: A caller-supplied trait column that is unknown, non-numeric, empty, or duplicated is rejected

- **WHEN** `trait_columns` names a column absent from the experiment, a non-numeric
  (metadata/identifier) column, an explicitly empty list `[]`, or the same column more than once
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` whose message names the
  offending column(s), rather than an opaque internal error, a full-frame fit the caller did not
  request, or a fit over duplicated collinear copies

#### Scenario: A degenerate fit surfaces as a self-correctable error

- **WHEN** the chosen delegate raises `ValueError` (e.g. a `trait_columns` subset leaving fewer than
  two samples, a single constant column, or a requested cluster count exceeding the sample count)
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` and a remedy (not
  `internal_error`), the message does not leak a traceback or backend path, and no run is persisted

#### Scenario: A non-finite value in a certified trait is rejected

- **WHEN** a selected certified-clean trait carries a non-finite value (`NaN`, `+inf`, or `-inf`) that
  would survive the delegate's internal `dropna()`
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` and no run is persisted,
  rather than fitting on the non-finite value

### Requirement: Clustering Persists a Versioned Run With Lineage and Returns Links

The `clustering` tool SHALL persist its outputs as a versioned run via the `ResultStore` port under
tool class `clustering`, carrying the contract-stamped `Provenance` into the manifest, recording the
cleaned-source version it consumed as `based_on_version` and content-addressing the consumed frame via
`source_csv` (so the manifest's `input_sha256` pins the exact input bytes, not just the mutable
`v<N>_cleaned` label), writing the per-sample cluster labels **with sample identity** and the
serialized typed result, and SHALL return the small cluster summary inline together with **links** to
the persisted artifacts — never the full label vector inline.

#### Scenario: Run is committed with provenance and cleaned-source lineage

- **WHEN** `clustering` completes successfully
- **THEN** a `StoredRun` is recorded for `(experiment, "clustering")` with a `run_ref`, a manifest
  path, the same `Provenance` (including the resolved `seed`) the contract stamped, and
  `based_on_version` equal to the consumed cleaned source version (e.g. `v3_cleaned`)
- **AND** the committed outputs include the per-sample cluster labels CSV and the serialized typed
  result (`cluster_result.json`)
- **AND** the tool passes the consumed cleaned frame as `source_csv`, so the manifest's `input_sha256`
  content-addresses the exact input rather than resting on the mutable version label

#### Scenario: Persisted labels carry sample identity for traceability

- **WHEN** the tool writes the cluster-labels CSV
- **THEN** each label row is prefixed with the frame's `metadata_cols` (e.g. Barcode/Genotype/
  Replicate), so a cluster assignment maps back to its plant by a shared key rather than by fragile
  positional alignment against the cleaned version

#### Scenario: Result returns a summary and links, not the label vector

- **WHEN** the tool returns its result
- **THEN** `n_clusters`, `cluster_sizes`, the three internal-validation scores, `feature_names`, the
  certified `n_samples` / `n_features`, and the method-specific scalars (`inertia` for k-means;
  `bic` / `aic` / `converged` / `covariance_type` for GMM) are inline
- **AND** the N-length per-sample label vector is referenced via links (object keys + manifest path) to
  the persisted run rather than embedded inline

#### Scenario: A second run increments the version without clobbering the first

- **WHEN** `clustering` is invoked a second time on the same experiment after a first run committed `v1`
- **THEN** the store commits `v2` and advances `latest` to `v2`, leaving `v1` retrievable — versioning
  is single-writer and non-destructive

### Requirement: Clustering Is Exercised End-to-End by the Live Persistence Smoke

The `clustering` tool SHALL be validated against a running dev stack through the **real**
`SupabaseReader` and `SupabaseResultStore` adapters (not the in-memory fakes) by the
`make bloommcp-smoke` driver, consuming a cleaned version committed by a prior `qc_clean` run —
proving the `qc_clean` → `clustering` composition resolves and persists end-to-end. The smoke driver's
pure decision logic SHALL remain factored into importable helpers that are unit-testable with no live
stack.

#### Scenario: clustering consumes a committed cleaned run through the real ports

- **WHEN** the live persistence smoke, after a `qc_clean` run has committed a cleaned version, calls
  `clustering(experiment=…, method="kmeans", trait_columns=…, seed=42)` through the real
  `SupabaseReader` / `SupabaseResultStore`
- **THEN** the reader resolves the cleaned version (`require_clean=True` succeeds, source
  `v<N>_cleaned`, not `raw`)
- **AND** the committed clustering run's manifest reports `manifest_schema_version == 3`, records
  `based_on_version` equal to the consumed cleaned version and the resolved `seed`, and each recorded
  `output_sha256` equals the SHA-256 of the bytes actually stored for its artifact
