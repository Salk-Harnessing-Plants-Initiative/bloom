## ADDED Requirements

### Requirement: PCA Analysis Accepts Optional Plot Requests

The `pca_analysis` tool input SHALL accept `include_plots: bool = False` and
`plots: Optional[list[str]] = None` in `PCAAnalysisParams`. When `include_plots` is `False`
(the default), the tool SHALL behave identically to its pre-plots behavior: no figures are
generated, no PNG artifacts are persisted, and the result is unchanged. A `plots` value
provided alongside `include_plots=False` SHALL be silently ignored (not rejected). When
`include_plots` is `True` and `plots` is `None`, the tool SHALL generate all four catalog
plots. When `plots` is a non-empty list, the tool SHALL generate only the requested subset.
An empty `plots=[]` with `include_plots=True` SHALL be rejected as `invalid_input`.

#### Scenario: Default call produces no plots and unchanged numeric result

- **WHEN** `pca_analysis` is called without `include_plots` (or with `include_plots=False`)
- **THEN** the result's `outputs` contains only `{loadings.csv, scores.csv, pca_result.json}`
  (no PNG keys)
- **AND** the numeric summary (`n_components`, `explained_variance_ratio`,
  `cumulative_variance_ratio`, `eigenvalues`, `feature_names`, `n_samples`, `n_features`) is
  identical to pre-plots behavior

#### Scenario: include_plots=True with no plots list generates all four catalog plots

- **WHEN** `pca_analysis` is called with `include_plots=True` and `plots=None`
- **THEN** the tool generates all four catalog plots: `create_pca_scree_plot`,
  `create_pca_biplot`, `create_feature_contribution_plot`, `create_feature_contribution_heatmap`
- **AND** the result's `outputs` contains four additional `.png` keys, one per plot

#### Scenario: A plots subset generates only the requested figures

- **WHEN** `pca_analysis` is called with `include_plots=True` and
  `plots=["create_pca_scree_plot", "create_pca_biplot"]`
- **THEN** the result's `outputs` contains exactly those two PNG keys plus the three data keys

#### Scenario: include_plots=False with plots param is silently ignored

- **WHEN** `pca_analysis` is called with `include_plots=False` and
  `plots=["create_pca_scree_plot"]`
- **THEN** the tool returns successfully with no PNG outputs — the `plots` value is ignored
- **AND** no `BloomMCPError` is raised

#### Scenario: Empty plots list is rejected as invalid_input

- **WHEN** `pca_analysis` is called with `include_plots=True` and `plots=[]`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` (use `plots=None` for
  all plots, or omit `include_plots` for none)
- **AND** no run is committed

### Requirement: PCA Analysis Validates Plot Keys Before Committing Any Run

The `pca_analysis` tool SHALL validate the requested `plots` list against the known four-key
catalog **before** calling `create_run`. An unknown or duplicate plot key SHALL return a
`BloomMCPError` with code `invalid_input` naming the offending key(s), and no run SHALL be
committed to the `ResultStore`. Validation is delegated to `_plots.validate_plot_keys`.

#### Scenario: Unknown plot key returns invalid_input with no run committed

- **WHEN** `pca_analysis` is called with `include_plots=True` and `plots=["not_a_real_plot"]`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming `not_a_real_plot`
- **AND** no run is committed to the `ResultStore` (the run count for `(experiment, "pca")`
  is unchanged)

#### Scenario: Duplicate plot key returns invalid_input with no run committed

- **WHEN** `pca_analysis` is called with `plots=["create_pca_scree_plot", "create_pca_scree_plot"]`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the duplicate
- **AND** no run is committed

### Requirement: PCA Analysis Persists Plot PNGs Into the Run and Returns Object-Key Links

When plots are requested, the `pca_analysis` tool SHALL persist each generated figure as a PNG
into the **existing** PCA run (alongside loadings, scores, and `pca_result.json`) via the
`ResultStore` port, and SHALL return them as additional entries in the existing
`outputs: dict[str, str]` result field — not as a separate `plot_links` field. Every figure
SHALL be closed in a `finally` block that wraps both figure generation and the persistence
scope, regardless of success or failure.

#### Scenario: Plot PNGs round-trip as valid PNG bytes

- **WHEN** `pca_analysis` is called with `include_plots=True` on a valid cleaned experiment
- **THEN** each PNG key in `outputs` maps to a non-empty object key
- **AND** the bytes stored at each key begin with the PNG magic number `\x89PNG` (real PNG
  content, not empty or truncated bytes)

#### Scenario: Figures are closed after the call regardless of outcome

- **WHEN** `pca_analysis` completes (success or error path, plots requested or not)
- **THEN** `matplotlib.pyplot.get_fignums()` returns an empty list

#### Scenario: Plot outputs appear alongside data outputs in the result

- **WHEN** `pca_analysis` is called with `include_plots=True`
- **THEN** `result.outputs` contains both the three existing data keys
  (`loadings.csv`, `scores.csv`, `pca_result.json`) and the requested plot PNG keys
  (e.g. `create_pca_scree_plot.png`)

### Requirement: PCA Plot Generation Delegates Entirely to the Upstream Plotters

The `pca_analysis` tool SHALL delegate all figure construction to the corresponding
`sleap_roots_analyze` plotter functions — `create_pca_scree_plot`, `create_pca_biplot`,
`create_feature_contribution_plot`, and `create_feature_contribution_heatmap` — with call
sites defined in `_pca_plot_calls()` and documented in `design.md`. The tool SHALL contain
no matplotlib drawing logic of its own. `create_feature_contribution_heatmap` SHALL be called
with `plot_type="loadings"` to ensure it returns a single `Figure` (not a 2-tuple).
Matplotlib SHALL be imported lazily (on the plots path only) using the headless `Agg` backend,
preserving the Tier-0 import-clean guarantee on the no-plots path.

#### Scenario: Each catalog key maps to its upstream plotter

- **WHEN** `pca_analysis` is called with all four plot keys
- **THEN** each of the four `sleap_roots_analyze` plotter functions is invoked exactly once
  with the correct args (raw `result_dict`, frame, and `PCAResult` fields as applicable)

#### Scenario: Matplotlib is not imported on the default no-plots path

- **WHEN** `pca_analysis` is called without `include_plots` (the default), with `matplotlib`
  blocked in `sys.modules`
- **THEN** no `ImportError` is raised — the `import matplotlib` line is never reached

#### Scenario: Plotter failure surfaces as tool_error with no run committed

- **WHEN** a plotter raises an exception during figure generation (before `create_run`)
- **THEN** the tool returns a `BloomMCPError` (mapped by the contract envelope) and no run
  is committed
- **AND** all figures accumulated before the failure are closed in `finally`

### Requirement: PCA Plot Helpers Are Factored Into a Shared, Tool-Agnostic Module

The plot key validation, figure-generation dispatch, and figure cleanup SHALL be factored into
`bloom_mcp/tools/_plots.py` as a tool-agnostic module — following the `_qc_shared.py`
precedent — so the upcoming UMAP tool (#425) can import the same helpers without modification.
`_plots.py` SHALL accept zero-arg callables (not PCA-typed inputs), keeping the
PCA-specific dispatch (`_pca_plot_calls`) in `pca_analysis_tool.py`. The module SHALL be
importable and unit-testable with no live stack.

#### Scenario: _plots helpers are importable and unit-testable without a live stack

- **WHEN** `from bloom_mcp.tools._plots import validate_plot_keys, close_figures` is executed
- **THEN** both symbols are importable with no Supabase connection required
- **AND** `validate_plot_keys(["unknown"], {"k1", "k2"})` raises `BloomMCPError(invalid_input)`
  in isolation — no `pca_analysis` context needed
- **AND** `validate_plot_keys(["k1", "k1"], {"k1"})` raises `BloomMCPError(invalid_input)`
  naming the duplicate
- **AND** `validate_plot_keys([], {"k1"})` raises `BloomMCPError(invalid_input)`
