## ADDED Requirements

### Requirement: Clustering Accepts Optional Plot Requests

The `clustering` tool input SHALL accept `include_plots: bool = False` and
`plots: Optional[list[str]] = None` in `ClusteringParams`. When `include_plots` is `False`
(the default), the tool SHALL behave identically to its pre-plots behavior: no figures are
generated, no PNG artifacts are persisted, and the result is unchanged. A `plots` value
provided alongside `include_plots=False` SHALL be silently ignored (not rejected). When
`include_plots` is `True` and `plots` is `None`, the tool SHALL generate both catalog plots.
When `plots` is a non-empty list, the tool SHALL generate only the requested subset. An empty
`plots=[]` with `include_plots=True` SHALL be rejected as `invalid_input`. This behavior SHALL
be identical for all three `method` values (`"kmeans"`, `"gmm"`, `"hierarchical"`).

#### Scenario: Default call produces no plots and unchanged result

- **WHEN** `clustering` is called without `include_plots` (or with `include_plots=False`), for
  any `method`
- **THEN** the result's `outputs` contains only `{labels.csv, cluster_result.json}` (no PNG
  keys)
- **AND** the cluster summary fields are identical to pre-plots behavior

#### Scenario: include_plots=True with no plots list generates both catalog plots

- **WHEN** `clustering` is called with `include_plots=True` and `plots=None`
- **THEN** the tool generates both catalog plots: `create_cluster_scatter_pca` and
  `create_cluster_size_barplot`
- **AND** the result's `outputs` contains two additional `.png` keys, one per plot

#### Scenario: A plots subset generates only the requested figure

- **WHEN** `clustering` is called with `include_plots=True` and
  `plots=["create_cluster_size_barplot"]`
- **THEN** the result's `outputs` contains exactly that one PNG key plus the two data keys

#### Scenario: include_plots=False with plots param is silently ignored

- **WHEN** `clustering` is called with `include_plots=False` and
  `plots=["create_cluster_scatter_pca"]`
- **THEN** the tool returns successfully with no PNG outputs — the `plots` value is ignored
- **AND** no `BloomMCPError` is raised

#### Scenario: Empty plots list is rejected as invalid_input

- **WHEN** `clustering` is called with `include_plots=True` and `plots=[]`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` (use `plots=None` for
  all plots, or omit `include_plots` for none)
- **AND** no run is committed

### Requirement: Clustering Validates Plot Keys Before Committing Any Run

The `clustering` tool SHALL validate the requested `plots` list against the known two-key
catalog **before** calling `create_run`. An unknown or duplicate plot key SHALL return a
`BloomMCPError` with code `invalid_input` naming the offending key(s), and no run SHALL be
committed to the `ResultStore`. Validation is delegated to `_plots.validate_plot_keys` (reused
verbatim from `pca_analysis`/`umap_analysis`, with no changes to that module).

#### Scenario: Unknown plot key returns invalid_input with no run committed

- **WHEN** `clustering` is called with `include_plots=True` and `plots=["not_a_real_plot"]`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming `not_a_real_plot`
- **AND** no run is committed to the `ResultStore` (the run count for
  `(experiment, "clustering")` is unchanged)

#### Scenario: Duplicate plot key returns invalid_input with no run committed

- **WHEN** `clustering` is called with
  `plots=["create_cluster_scatter_pca", "create_cluster_scatter_pca"]`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the duplicate
- **AND** no run is committed

### Requirement: Clustering Persists Plot PNGs Into the Run and Returns Object-Key Links

When plots are requested, the `clustering` tool SHALL persist each generated figure as a PNG
into the **existing** clustering run (alongside `labels.csv` and `cluster_result.json`) via the
`ResultStore` port, and SHALL return them as additional entries in the existing
`outputs: dict[str, str]` result field — not as a separate `plot_links` field. Every figure
SHALL be closed in a `finally` block that wraps both figure generation and the persistence
scope, regardless of success or failure.

#### Scenario: Plot PNGs round-trip as valid PNG bytes

- **WHEN** `clustering` is called with `include_plots=True` on a valid cleaned experiment, for
  any `method`
- **THEN** each PNG key in `outputs` maps to a non-empty object key
- **AND** the bytes stored at each key begin with the PNG magic number `\x89PNG`

#### Scenario: Figures are closed after the call regardless of outcome

- **WHEN** `clustering` completes (success or error path, plots requested or not)
- **THEN** `matplotlib.pyplot.get_fignums()` returns an empty list

#### Scenario: Plot outputs appear alongside data outputs in the result

- **WHEN** `clustering` is called with `include_plots=True`
- **THEN** `result.outputs` contains both the two existing data keys (`labels.csv`,
  `cluster_result.json`) and the requested plot PNG keys (e.g.
  `create_cluster_scatter_pca.png`)

### Requirement: Clustering Plot Generation Delegates Entirely to the Upstream Plotters, Uniformly Across Methods

The `clustering` tool SHALL delegate all figure construction to the corresponding
`sleap_roots_analyze.cluster_visualization` plotter functions — `create_cluster_scatter_pca`
and `create_cluster_size_barplot` — with call sites defined in `_clustering_plot_calls()` and
documented in `design.md`. The tool SHALL contain no matplotlib drawing logic of its own. Both
catalog keys SHALL be usable identically regardless of `method` (`"kmeans"`, `"gmm"`, or
`"hierarchical"`) — `create_cluster_scatter_pca` SHALL be invoked with an explicit `pca_result`
computed via an internal, non-persisted `perform_pca_analysis` call over the same
certified-clean trait selection used for clustering, rather than depending on the raw clustering
`result_dict`'s `data_processed` key (which the `hierarchical` method's entry point does not
return). Matplotlib SHALL be imported lazily (on the plots path only, via `import matplotlib;
matplotlib.use("Agg")` inside the `include_plots` branch) — this does not guarantee matplotlib
is absent from `sys.modules` (the tool's existing top-level `sleap_roots_analyze` import
already puts it there transitively, identically to `pca_analysis`/`umap_analysis`), only that
the `include_plots=False` path never itself executes a fresh `import matplotlib` statement.

#### Scenario: Each catalog key maps to its upstream plotter with the documented args

- **WHEN** `clustering` is called with both plot keys
- **THEN** `create_cluster_scatter_pca` is invoked exactly once, with the raw clustering
  `result_dict` and a `pca_result` computed via the internal `perform_pca_analysis` call
- **AND** `create_cluster_size_barplot` is invoked exactly once, with
  `np.asarray(result_dict["cluster_labels"])` and `int(result.n_clusters)`

#### Scenario: create_cluster_scatter_pca works uniformly across all three methods

- **WHEN** `clustering` is called with `include_plots=True` and
  `plots=["create_cluster_scatter_pca"]` for `method="kmeans"`, `method="gmm"`, and
  `method="hierarchical"` in turn, on the same cleaned experiment and trait selection
- **THEN** all three calls succeed and each produces a valid `create_cluster_scatter_pca.png`
  output — including for `hierarchical`, whose raw `result_dict` carries no `data_processed`
  key

#### Scenario: The default no-plots path never executes a fresh import matplotlib statement

- **WHEN** `clustering` is called without `include_plots` (the default), with the
  `matplotlib` entry in `sys.modules` set to `None` (so any executed `import matplotlib`
  statement raises `ImportError` immediately)
- **THEN** no `ImportError` is raised — confirming the `import matplotlib` statement inside
  the `include_plots` branch is never reached (this does not assert matplotlib is absent
  from `sys.modules` — it is already resident via the tool's own top-level
  `sleap_roots_analyze` import)

#### Scenario: The internal PCA call reuses the same trait selection used for clustering

- **WHEN** `create_cluster_scatter_pca` is requested with an explicit `trait_columns` subset
- **THEN** the internal `perform_pca_analysis` call receives exactly that subset
  (`frame.df[trait_cols]`) — not the full certified-clean set and not a different selection

#### Scenario: A degenerate internal PCA projection surfaces as assumption_violated with no run committed

- **WHEN** `create_cluster_scatter_pca` is requested and the internal, non-persisted
  `perform_pca_analysis` call over the certified-clean trait selection fails (e.g. the selection
  is degenerate for PCA)
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` and a remedy, and
  no clustering run is committed
- **AND** all figures accumulated before the failure are closed in `finally`, and
  `matplotlib.pyplot.get_fignums()` returns an empty list

#### Scenario: An undeclared plotter failure surfaces as internal_error with no run committed

- **WHEN** a plotter raises an exception not wrapped into a `BloomMCPError` by
  `_clustering_plot_calls` (i.e. not one of the declared `errors=(ExperimentReadError,
  CommitFailedError, ManifestReadError)`) during figure generation, before `create_run`
- **THEN** the tool returns a `BloomMCPError` with code `internal_error` (the contract
  envelope's fallback for any undeclared exception — see `BloomMCPError.from_exception`)
  and no run is committed
- **AND** all figures accumulated before the failure are closed in `finally`
- **AND** the run count for `(experiment, "clustering")` in the `ResultStore` is unchanged

#### Scenario: create_cluster_size_barplot handles GMM's single-component auto-select collapse

- **WHEN** `clustering` runs with `method = "gmm"`, `n_components` omitted, and BIC selects a
  single component (`result.n_clusters == 1`), with `include_plots=True` and
  `plots=["create_cluster_size_barplot"]`
- **THEN** the tool succeeds and produces a valid `create_cluster_size_barplot.png` showing a
  single bar, rather than raising or silently omitting the plot
