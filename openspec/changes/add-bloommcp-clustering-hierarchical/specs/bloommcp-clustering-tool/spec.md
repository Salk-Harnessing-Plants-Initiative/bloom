## MODIFIED Requirements

### Requirement: Clustering Delegates All Computation to the Tested Upstream Entry Points

The `clustering` tool SHALL delegate the clustering computation to
`sleap_roots_analyze.perform_kmeans_clustering`, `sleap_roots_analyze.perform_gmm_clustering`, or the
hierarchical entry point introduced in `0.1.0a5` according to the requested `method`, and SHALL wrap
the delegate's result into the matching upstream typed result. It SHALL contain no clustering math of
its own — no standardization, distance computation, EM/Lloyd iteration, dendrogram construction, tree
cutting, or internal-validation metric computation — and SHALL NOT call the vendored
`bloom_mcp.clustering`. The `method` field SHALL accept `"kmeans"`, `"gmm"`, and `"hierarchical"` as
valid values. Caller-supplied controls incompatible with the chosen method SHALL be rejected with
`invalid_input` rather than silently ignored.

#### Scenario: k-means is delegated to the correct entry point and wrapper

- **WHEN** `clustering` runs with `method = "kmeans"` on a cleaned experiment frame
- **THEN** `perform_kmeans_clustering` is invoked exactly once and its result is wrapped via
  `KMeansResult.from_kmeans_dict`
- **AND** `perform_gmm_clustering`, the hierarchical entry point, and the vendored `bloom_mcp.clustering`
  are never called

#### Scenario: GMM is delegated to the correct entry point and wrapper

- **WHEN** `clustering` runs with `method = "gmm"` on a cleaned experiment frame
- **THEN** `perform_gmm_clustering` is invoked exactly once and its result is wrapped via
  `GMMResult.from_gmm_dict`
- **AND** `perform_kmeans_clustering`, the hierarchical entry point, and the vendored
  `bloom_mcp.clustering` are never called

#### Scenario: Hierarchical is delegated to the upstream 0.1.0a5 entry point

- **WHEN** `clustering` runs with `method = "hierarchical"` on a cleaned experiment frame
- **THEN** the upstream `0.1.0a5` hierarchical entry point is invoked exactly once and its result is
  wrapped via the matching `ClusterResult` constructor
- **AND** `perform_kmeans_clustering`, `perform_gmm_clustering`, and the vendored `bloom_mcp.clustering`
  are never called, and the tool performs no dendrogram construction, tree cutting, or quality-metric
  computation itself

#### Scenario: Cluster-count controls are forwarded and auto-selection is honored

- **WHEN** a caller sets an explicit `n_clusters` (k-means) or `n_components` (GMM)
- **THEN** the tool forwards it to the delegate and the result's `n_clusters` reflects it
- **AND** when the caller omits it, the delegate auto-selects the cluster count and the tool surfaces
  the selected count in its summary rather than hiding it

#### Scenario: GMM auto-selection may collapse to a single component, surfaced honestly

- **WHEN** `clustering` runs with `method = "gmm"` and `n_components` omitted, and BIC selects a single
  component on the data
- **THEN** the tool reports `n_clusters == 1` in its summary and does not raise, rather than hiding the
  collapse or presenting a spurious multi-cluster result

#### Scenario: GMM auto-selected BIC/AIC describe the selected model, not the last candidate

- **WHEN** `clustering` runs with `method = "gmm"` and `n_components` omitted
- **THEN** the reported `bic` and `aic` are those of the **selected** model, not the last candidate the
  delegate happened to test

#### Scenario: A cluster-count control set for the wrong method is rejected

- **WHEN** a request supplies a gmm-only control (`n_components`, `max_components`, or `covariance_type`)
  with `method = "kmeans"` or `method = "hierarchical"`, or a kmeans-only control (`n_clusters` or
  `max_clusters`) with `method = "gmm"`, or a hierarchical-only control (`linkage_method`,
  `distance_metric`, or `optimization_method`) with `method = "kmeans"` or `method = "gmm"`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the mismatched control,
  rather than silently ignoring it
- **NOTE** `n_clusters` and `max_clusters` are valid for both `"kmeans"` and `"hierarchical"` and are
  never rejected for those methods

The `clustering` tool SHALL load its experiment frame through the injected `ExperimentReader` port
with `require_clean=True` and SHALL restrict the clustering to columns within the resolved frame's
certified-clean trait set (`frame.trait_cols`). It SHALL NOT cluster a raw input and SHALL NOT perform
its own cleaning.

#### Scenario: A cleaned experiment is consumed

- **WHEN** `clustering` is invoked on an experiment that has a committed cleaned version
- **THEN** the reader resolves the cleaned version and the tool clusters it

#### Scenario: An experiment with no cleaned version is rejected with a remedy

- **WHEN** `clustering` is invoked on an experiment that has only a raw input and no committed cleaned
  version
- **THEN** the tool returns a `BloomMCPError` whose remedy is to run `qc_clean` first, and no
  clustering run is produced

#### Scenario: A trait column outside the certified-clean set is rejected, not silently dropped

- **WHEN** `trait_columns` names a numeric column that is present in the frame but not in the
  certified-clean trait set, including one that still carries NaN values
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the column, and does
  not fit clustering on it

#### Scenario: Omitting the trait selection uses the full certified-clean set

- **WHEN** `trait_columns` is omitted on a cleaned experiment
- **THEN** the tool clusters over the full certified-clean set (`feature_names` equals `frame.trait_cols`)
  and `n_samples` equals the certified cleaned row count

### Requirement: Clustering Is Deterministic Under a Fixed Seed and Records the Resolved Seed

The `clustering` tool SHALL handle seed resolution per method: for `"kmeans"` and `"gmm"` (stochastic)
the contract resolves the caller's `seed` into `random_state`, forwards it to the delegate, and records
the resolved seed in `Provenance`. For `"hierarchical"` (deterministic) no `random_state` is forwarded
and `Provenance` records `seed = None`, mirroring `qc_clean` / `pca_analysis`. Two runs with the same
inputs and (where applicable) seed SHALL produce **identical** cluster labels.

#### Scenario: The resolved seed reaches the delegate for stochastic methods, not just the manifest

- **WHEN** `clustering` runs with `method = "kmeans"` or `method = "gmm"` and a requested `seed`
- **THEN** the chosen delegate is invoked with `random_state` equal to that resolved seed
- **AND** the stamped `Provenance` records that resolved seed

#### Scenario: Hierarchical records seed = None in provenance

- **WHEN** `clustering` runs with `method = "hierarchical"`
- **THEN** the stamped `Provenance` records `seed = None` and no `random_state` is forwarded to the
  upstream entry point

#### Scenario: Same seed produces identical labels for stochastic methods

- **WHEN** `clustering` is invoked twice on the same cleaned experiment with the same stochastic
  `method` and `seed`
- **THEN** the two results' `cluster_labels` are element-wise identical

#### Scenario: Hierarchical is deterministic without a seed

- **WHEN** `clustering` is invoked twice on the same cleaned experiment with `method = "hierarchical"`
- **THEN** the two results' `cluster_labels` are element-wise identical regardless of any seed input

### Requirement: Clustering Reproduces the turface_19 Characterization Snapshot Through the Tool

The `clustering` tool SHALL, when invoked through the MCP boundary on the cleaned turface_19 fixture
at the pinned per-method parameters, reproduce the recorded cluster-metric characterization snapshot
within tolerance for all three methods (`"kmeans"`, `"gmm"`, `"hierarchical"`). Each entry in the
snapshot fixture SHALL be labeled as a drift gate re-derived from the pinned `sleap-roots-analyze`
version — **not** an independently recorded oracle.

#### Scenario: k-means cluster metrics match the recorded snapshot

- **WHEN** `clustering` runs with `method = "kmeans"`, the 8 recorded golden trait columns,
  `n_clusters = 3`, `standardize = true`, and `seed = 42`
- **THEN** the result matches the recorded k-means snapshot values within `abs = 1e-6`

#### Scenario: GMM cluster metrics match the recorded snapshot

- **WHEN** `clustering` runs with `method = "gmm"`, the same 8 golden trait columns,
  `n_components = 3`, `covariance_type = "full"`, `standardize = true`, and `seed = 42`
- **THEN** the result matches the recorded GMM snapshot values within `abs = 1e-6`

#### Scenario: Hierarchical cluster metrics match the recorded snapshot

- **WHEN** `clustering` runs with `method = "hierarchical"`, the same 8 golden trait columns, and
  `standardize = true`
- **THEN** the result matches the recorded hierarchical snapshot values within `abs = 1e-6`, and
  `seed = None` is recorded in provenance

### Requirement: Clustering Is Exercised End-to-End by the Live Persistence Smoke

The `clustering` tool SHALL be validated against a running dev stack through the **real**
`SupabaseReader` and `SupabaseResultStore` adapters by the `make bloommcp-smoke` driver for all three
methods, consuming a cleaned version committed by a prior `qc_clean` run.

#### Scenario: hierarchical consumes a committed cleaned run through the real ports

- **WHEN** the live persistence smoke, after a `qc_clean` run has committed a cleaned version, calls
  `clustering(experiment=…, method="hierarchical", trait_columns=…)` through the real
  `SupabaseReader` / `SupabaseResultStore`
- **THEN** the reader resolves the cleaned version (`require_clean=True` succeeds)
- **AND** the committed clustering run's manifest records `based_on_version` equal to the consumed
  cleaned version, `seed = None`, and each recorded `output_sha256` equals the SHA-256 of the bytes
  actually stored

#### Scenario: clustering leg covers all three methods in smoke

- **WHEN** the smoke driver runs the clustering legs
- **THEN** kmeans, gmm, and hierarchical each produce a committed run with a valid manifest, proving
  the polymorphic dispatch and persisted lineage end-to-end for all supported methods
