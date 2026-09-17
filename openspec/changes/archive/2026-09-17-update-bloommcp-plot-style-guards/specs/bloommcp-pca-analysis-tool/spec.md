## MODIFIED Requirements

### Requirement: PCA Analysis Persists Plot PNGs Into the Run and Returns Object-Key Links

When plots are requested, the `pca_analysis` tool SHALL persist each generated figure as a PNG
into the **existing** PCA run (alongside loadings, scores, and `pca_result.json`) via the
`ResultStore` port, and SHALL return them as additional entries in the existing
`outputs: dict[str, str]` result field — not as a separate `plot_links` field. Every figure
SHALL be closed in a `finally` block that wraps both figure generation and the persistence
scope, regardless of success or failure — including a figure a plotter callable allocates
internally (e.g. via `plt.subplots()`) before raising partway through the same call, before
the callable ever returns.

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

#### Scenario: A figure allocated then abandoned mid-call is still closed

- **WHEN** a requested plot's callable internally allocates a matplotlib figure (e.g. via
  `plt.subplots()`) and then raises before returning it
- **THEN** that figure is closed too (`matplotlib.pyplot.get_fignums() == []` afterward),
  even though it was never recorded into the tool's own `figures` dict

## ADDED Requirements

### Requirement: PCA Plot Font Size Has a Sanity Ceiling

`PCAAnalysisParams.plot_font_size` SHALL be rejected as `invalid_input` when not in
`(0, 100]`, checked in the `pca_analysis` tool body (via the shared
`bloom_mcp.tools._plots.check_plot_style_ceiling` helper, the same one UMAP uses), before
`reader.load_experiment` is called, rather than as a Pydantic `Field(gt=0, le=100)`
constraint. A `Field` constraint's violation is mapped by the contract layer's
`BloomMCPError.from_input_validation` into a message naming only the field and error type —
never the submitted value or the ceiling. The check runs regardless of `include_plots`'s
value and before any I/O, the same rule already established for `plot_alpha`. The field's
declared JSON schema SHALL still expose the ceiling as `maximum`/`exclusiveMinimum`
metadata (via Pydantic's `json_schema_extra`, not `Field(le=...)`) so a schema-reading
caller can discover the bound without needing to trigger a rejection first.

#### Scenario: An excessive plot_font_size is rejected regardless of include_plots

- **WHEN** `pca_analysis` is called with `plot_font_size` greater than `100` (including
  `float("inf")` or `float("nan")`) — with `include_plots` set to either `True` or `False`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, naming the
  submitted value and the ceiling, before any figure is generated, in both cases

#### Scenario: Boundary value 100 is accepted

- **WHEN** `pca_analysis` is called with `include_plots=True`, `plots=["create_pca_biplot"]`,
  and `plot_font_size=100`
- **THEN** the tool succeeds and the figure is generated with the font size applied — the
  inclusive ceiling is a valid value, not rejected

#### Scenario: The declared ceiling is discoverable in the tool's JSON schema

- **WHEN** `pca_analysis`'s input schema is inspected (e.g. via MCP tool discovery)
- **THEN** `plot_font_size`'s schema entry declares `maximum: 100`, even though it is not
  enforced via a Pydantic `Field` constraint
