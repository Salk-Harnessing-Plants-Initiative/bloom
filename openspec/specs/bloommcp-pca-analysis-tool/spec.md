# bloommcp-pca-analysis-tool Specification

## Purpose
The `pca_analysis` MCP tool exposes principal component analysis over certified-clean experiment
data as a first-class bloom-mcp capability. It delegates all PCA math to the tested upstream entry
point `sleap_roots_analyze.perform_pca_analysis`, requires a committed cleaned version produced by
`qc_clean` as its input, and restricts the fit to the frame's certified-clean trait set. Results
are persisted as a versioned run (loadings CSV, scores CSV with sample identity, serialized
`PCAResult`) via the `ResultStore` port, with provenance stamped by the `@as_mcp_tool` contract
envelope. The tool is deterministic (no random state), records `seed = None`, and returns variance
summaries and artifact links inline rather than embedding large matrices.
## Requirements
### Requirement: PCA Analysis Tool Registration and Discovery

The system SHALL expose a `pca_analysis` MCP tool registered on the FastMCP server so it is
discoverable via the MCP `tools/list` operation. The tool name SHALL be stable (`pca_analysis`,
never versioned in the name) and its registration SHALL NOT remove or rename the existing
`run_dimensionality_reduction_workflow` tool or the vendored `bloom_mcp.pca` module.

#### Scenario: Tool appears in tools/list

- **WHEN** a FastMCP `Client` connects to the server and calls `tools/list`
- **THEN** a tool named `pca_analysis` is present with a description and an input schema derived
  from its Pydantic input model

#### Scenario: Existing dimensionality-reduction workflow is preserved

- **WHEN** the server registers `pca_analysis`
- **THEN** `run_dimensionality_reduction_workflow` remains registered and `bloom_mcp.pca` remains
  importable, so server boot is unaffected

### Requirement: PCA Analysis Delegates All Computation to the Tested Upstream Entry Point

The `pca_analysis` tool SHALL delegate the PCA computation to
`sleap_roots_analyze.perform_pca_analysis` and SHALL wrap its result into the upstream typed
`PCAResult` via `PCAResult.from_pca_dict`. It SHALL contain no PCA math of its own — no
standardization, eigendecomposition, component selection, or loadings/scores computation — and
SHALL NOT call the vendored `bloom_mcp.pca`.

#### Scenario: PCA is delegated, not re-implemented

- **WHEN** `pca_analysis` runs on a cleaned experiment frame
- **THEN** `sleap_roots_analyze.perform_pca_analysis` is invoked exactly once and the component
  count, explained-variance ratios, loadings, and scores are taken from its result via
  `PCAResult.from_pca_dict`
- **AND** the vendored `bloom_mcp.pca` is never called and the tool performs no standardization or
  decomposition itself

#### Scenario: Tunable parameters are forwarded to the delegate

- **WHEN** a caller sets `standardize`, `explained_variance_threshold`, or `n_components`
- **THEN** the tool forwards them to `perform_pca_analysis`, and an explicit `n_components`
  overrides threshold-based component selection

#### Scenario: A component count above the feature count is clamped, not rejected

- **WHEN** `n_components` exceeds the number of selected trait features
- **THEN** the delegate clamps the component count to the feature count without raising, and the
  result's reported `n_components` reflects the clamped value

### Requirement: PCA Analysis Requires a Cleaned Input and Selects Only Certified-Clean Traits

The `pca_analysis` tool SHALL load its experiment frame through the injected `ExperimentReader`
port with `require_clean=True`, as the **consumer** of cleaned data, and SHALL restrict the PCA to
columns within the resolved frame's certified-clean trait set (`frame.trait_cols`). It SHALL NOT
run PCA on a raw input and SHALL NOT perform its own cleaning. When no committed cleaned version
exists for the experiment, the tool SHALL surface a structured `BloomMCPError` whose remedy directs
the caller to run `qc_clean` first. A requested trait column outside the certified-clean set (or a
NaN that survives into the selected subset) SHALL be rejected rather than silently row-dropped by
the delegate.

#### Scenario: A cleaned experiment is consumed

- **WHEN** `pca_analysis` is invoked on an experiment that has a committed cleaned version (a
  `qc_clean` run)
- **THEN** the reader resolves the cleaned version (source `v<N>_cleaned`, not `raw`), and the tool
  runs PCA on it

#### Scenario: An experiment with no cleaned version is rejected with a remedy

- **WHEN** `pca_analysis` is invoked on an experiment that has only a raw input and no committed
  cleaned version
- **THEN** the tool returns a `BloomMCPError` whose remedy is to run `qc_clean` first, and no PCA
  run is produced

#### Scenario: A trait column outside the certified-clean set is rejected, not silently dropped

- **WHEN** `trait_columns` names a numeric column that is present in the frame but not in the
  certified-clean trait set (`frame.trait_cols`), including one that still carries NaN values
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the column, and does
  not fit PCA on it — so the delegate never silently `dropna()`s the affected samples
- **AND** on a valid selection the result's `n_samples` equals the certified cleaned row count (no
  samples are silently lost)

### Requirement: PCA Analysis Reproduces the #120 turface_19 Golden Through the Tool

The `pca_analysis` tool SHALL, when invoked through the MCP boundary on the cleaned #120 turface_19
fixture restricted to the recorded golden trait columns, reproduce the independently recorded PCA
oracle within tolerance: three selected components and the recorded **cumulative** explained
variance. It SHALL additionally reproduce the recorded per-PC explained-variance split (a
characterization drift gate, not an independent oracle) within tolerance.

#### Scenario: Golden component count and cumulative explained variance match (independent oracle)

- **WHEN** `pca_analysis` runs on the cleaned turface_19 experiment with `trait_columns` set to the
  8 recorded `turface_19_pca_golden.json` trait columns, `standardize = true`, and
  `explained_variance_threshold = 0.95`
- **THEN** the result reports `n_components == 3`
- **AND** the `cumulative_variance_ratio` at the third component equals `0.9599095965599803` within
  `abs = 1e-6` — the independently recorded #120 / PR #146 value

#### Scenario: Per-PC explained-variance split matches the recorded characterization snapshot

- **WHEN** the same call completes
- **THEN** the per-PC `explained_variance_ratio` equals the recorded
  `pca_explained_variance_ratio` `[0.8612933510667774, 0.05820169635401897, 0.040414549139183936]`
  within `abs = 1e-6` — a drift gate re-derived from `perform_pca_analysis==0.1.0a3`, whose sum
  equals the independent cumulative oracle above

### Requirement: PCA Analysis Is Deterministic and Records No Seed

The `pca_analysis` tool SHALL be deterministic: it SHALL declare no `random_state` parameter, and
the stamped `Provenance` SHALL record `seed = None` (matching the codebase convention for
non-stochastic tools such as `qc_clean`). Two runs with identical inputs SHALL produce identical
results.

#### Scenario: Seed is recorded as None

- **WHEN** `pca_analysis` completes
- **THEN** the stamped `Provenance` records `seed = None`, together with the tool name and the PCA
  params (standardize, threshold, `n_components`, trait selection)

#### Scenario: Repeated runs are identical

- **WHEN** `pca_analysis` is invoked twice on the same cleaned experiment with the same parameters
- **THEN** the two results' `explained_variance_ratio` and `cumulative_variance_ratio` are equal
  within `abs = 1e-6`

### Requirement: PCA Analysis Honors the Contract Envelope

The `pca_analysis` tool SHALL be wrapped by `@as_mcp_tool` so that inputs and outputs are validated
against declared Pydantic models, every declared/undeclared failure is mapped to a structured
`BloomMCPError` (never a raw traceback or leaked backend internals), and a single `Provenance` is
stamped per call.

#### Scenario: Input/output schema round-trip

- **WHEN** a valid request is serialized to the tool's input schema and the result is validated
  against the output schema
- **THEN** both validate without loss

#### Scenario: Out-of-range parameters are rejected

- **WHEN** a request sets `explained_variance_threshold` outside `[0,1]` or `n_components < 1`
- **THEN** the tool returns a `BloomMCPError` with an input/validation code, and no run is persisted

#### Scenario: A caller-supplied trait column that is unknown or non-numeric is rejected

- **WHEN** `trait_columns` names a column absent from the experiment, or a non-numeric
  (metadata/identifier) column
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` whose message names the
  offending column(s), rather than an opaque internal error or a silently mis-selected fit

#### Scenario: An explicitly empty trait selection is rejected, not treated as "all traits"

- **WHEN** `trait_columns` is supplied as an empty list `[]`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, rather than falling through
  to a full-frame PCA over every certified trait the caller did not select

#### Scenario: A trait selection with duplicate column names is rejected

- **WHEN** `trait_columns` names the same column more than once (e.g. `["Holes", "Holes"]`)
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the duplicate(s),
  rather than fitting collinear copies the delegate would re-select — which would inflate the fitted
  feature set while `n_features` under-reported it

#### Scenario: A degenerate fit surfaces as a self-correctable error

- **WHEN** the delegate `perform_pca_analysis` raises `ValueError` (e.g. a `trait_columns` subset
  leaving fewer than two samples or no non-zero-variance column)
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` and a remedy (not
  `internal_error`), and no run is persisted

#### Scenario: A constant certified trait is surfaced rather than silently dropped

- **WHEN** the selection includes a certified-clean trait that is constant (zero variance), so the
  delegate would silently drop it and fit fewer features than requested
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` naming the dropped
  column(s), and no run is persisted — rather than persisting an artifact whose reported
  `n_features` disagrees with the shorter `loadings`/`feature_names` actually fit

#### Scenario: A non-finite value in a certified trait is rejected

- **WHEN** a selected certified-clean trait carries a non-finite value (`NaN`, `+inf`, or `-inf`)
  that would survive the delegate's `dropna()`
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` and no run is
  persisted, rather than poisoning standardization/eigendecomposition with the non-finite value

### Requirement: PCA Analysis Persists a Versioned Run With Lineage and Returns Links

The `pca_analysis` tool SHALL persist its outputs as a versioned run via the `ResultStore` port
under tool class `pca`, carrying the contract-stamped `Provenance` into the manifest, recording the
cleaned-source version it consumed as `based_on_version` and content-addressing the consumed frame
via `source_csv` — captured using the shared `snapshot_frame` context manager from
`bloom_mcp.tools._consumer_utils`, which writes `frame.df` to a temporary CSV with `index=False`
and yields the path so the manifest's `input_sha256` pins the exact input bytes, not just the
mutable `v<N>_cleaned` label — writing the component loadings and the component scores **with
sample identity** (built via `_build_output_frame` from `bloom_mcp.tools._consumer_utils`, which
prepends `frame.metadata_cols` onto a pre-built scores `pd.DataFrame`, resets the index on both
sides before concatenation so positional alignment is preserved regardless of the frame's original
index) and the serialized `PCAResult`, and SHALL return the small variance summary inline together
with **links** to the persisted artifacts — never the loadings or score matrices inline. The
`PCAAnalysisResult` return model SHALL inherit `RunLinks` (from `bloom_mcp.contract`) for the
four run-link fields (`run_ref`, `version_dir`, `manifest_path`, `outputs`).

#### Scenario: Run is committed with provenance and cleaned-source lineage

- **WHEN** `pca_analysis` completes successfully
- **THEN** a `StoredRun` is recorded for `(experiment, "pca")` with a `run_ref`, a manifest path,
  the same `Provenance` (including `seed = None`) the contract stamped, and `based_on_version` equal
  to the consumed cleaned source version (e.g. `v3_cleaned`)
- **AND** the committed outputs include the loadings CSV, the component scores CSV, and the
  serialized `PCAResult` (`pca_result.json`)
- **AND** the tool passes the consumed cleaned frame as `source_csv` (via `snapshot_frame`), so the
  manifest's `input_sha256` content-addresses the exact input rather than resting on the mutable
  version label; the snapshot CSV has no index column (`index=False`)

#### Scenario: Persisted scores carry sample identity for traceability

- **WHEN** the tool writes the component-scores CSV
- **THEN** each score row is prefixed with the frame's `metadata_cols` (e.g. Barcode/Genotype/
  Replicate) via `_build_output_frame`, which resets the index on both the identity columns and
  the scores DataFrame before concatenation so positional alignment is guaranteed regardless of
  the frame's original index — a PC-score row maps back to its plant by a shared key rather than
  by fragile positional alignment against the cleaned version

#### Scenario: Result returns a summary and links, not the matrices

- **WHEN** the tool returns its result
- **THEN** `n_components`, the per-PC `explained_variance_ratio`, the `cumulative_variance_ratio`,
  the `eigenvalues`, `feature_names`, and the certified `n_samples` / `n_features` are inline
- **AND** the loadings and component-score matrices are referenced via `RunLinks`-inherited fields
  (`run_ref`, `version_dir`, `manifest_path`, `outputs`) to the persisted run rather than embedded
  inline

### Requirement: PCA Analysis Is Exercised End-to-End by the Live Persistence Smoke

The `pca_analysis` tool SHALL be validated against a running dev stack through the **real**
`SupabaseReader` and `SupabaseResultStore` adapters (not the in-memory fakes) by the
`make bloommcp-smoke` driver, consuming a cleaned version committed by a prior `qc_clean` run —
proving the `qc_clean` → `pca_analysis` composition resolves and persists end-to-end. The smoke
driver's pure decision logic SHALL remain factored into importable helpers that are unit-testable
with no live stack.

#### Scenario: pca_analysis consumes a committed cleaned run through the real ports

- **WHEN** the live persistence smoke, after a `qc_clean` run has committed a cleaned version, calls
  `pca_analysis(experiment=…, trait_columns=…)` through the real `SupabaseReader` /
  `SupabaseResultStore`
- **THEN** the reader resolves the cleaned version (`require_clean=True` succeeds, source
  `v<N>_cleaned`, not `raw`)
- **AND** the committed PCA run's manifest reports `manifest_schema_version == 3`, records
  `based_on_version` equal to the consumed cleaned version, and each recorded `output_sha256` equals
  the SHA-256 of the bytes actually stored for its artifact

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

