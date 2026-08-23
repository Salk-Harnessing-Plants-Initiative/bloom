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

`PCAAnalysisParams.plot_font_size` SHALL be rejected as `invalid_input` when greater than
`100`, in addition to the existing `gt=0` lower-bound rejection, enforced as a `Field`
constraint on `PCAAnalysisParams` itself, before the tool body runs (and therefore before
`include_plots` is examined) — an out-of-range value is rejected regardless of
`include_plots`'s value, the same rule already established for `plot_alpha`.

#### Scenario: An excessive plot_font_size is rejected regardless of include_plots

- **WHEN** `pca_analysis` is called with `plot_font_size` greater than `100` (including
  `float("inf")`) — with `include_plots` set to either `True` or `False`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, before any figure is
  generated, in both cases

#### Scenario: Boundary value 100 is accepted

- **WHEN** `pca_analysis` is called with `include_plots=True`, `plots=["create_pca_biplot"]`,
  and `plot_font_size=100`
- **THEN** the tool succeeds and the figure is generated with the font size applied — the
  inclusive ceiling is a valid value, not rejected
