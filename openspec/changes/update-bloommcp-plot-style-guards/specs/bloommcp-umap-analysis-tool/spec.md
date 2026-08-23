## MODIFIED Requirements

### Requirement: Optional plots — persistence and figure cleanup

The system SHALL persist each requested plot as an additional `*.png` entry in the existing
`outputs` dict (no new result field) via the existing
`bloom_mcp.tools._plots.generate_figures` / `close_figures` helpers, and SHALL close every
generated figure regardless of success or failure — including a figure a plotter callable
allocates internally (e.g. via `plt.subplots()`) before raising partway through the same
call, before the callable ever returns.

#### Scenario: Requested plots are persisted as additional outputs

- **WHEN** `umap_analysis` is called with `include_plots=True` and a valid subset of `plots`
- **THEN** each requested plot is persisted as an additional `*.png` entry in `outputs`

#### Scenario: Figures are closed on success, on an invalid key, and on partial plotter failure

- **WHEN** a `umap_analysis` call with `include_plots=True` succeeds, is rejected for an
  invalid plot key, or fails partway through generating multiple plots
- **THEN** every figure already generated in that call is closed
  (`matplotlib.pyplot.get_fignums() == []` afterward) in every case

#### Scenario: A figure allocated then abandoned mid-call is still closed

- **WHEN** a requested plot's callable internally allocates a matplotlib figure (e.g. via
  `plt.subplots()`) and then raises before returning it — as happens today when an invalid
  `plot_cmap` reaches `create_umap_single_trait`'s `ax.scatter(cmap=...)` call after the
  figure was already created
- **THEN** that figure is closed too (`matplotlib.pyplot.get_fignums() == []` afterward),
  even though it was never recorded into the tool's own `figures` dict

## ADDED Requirements

### Requirement: UMAP Plot Style Fields Have Sanity Ceilings

`UMAPAnalysisParams.plot_font_size` SHALL be rejected as `invalid_input` when greater than
`100`, and `UMAPAnalysisParams.plot_point_size` SHALL be rejected as `invalid_input` when
greater than `10000` — both in addition to the existing `gt=0` lower-bound rejection, and
both enforced as `Field` constraints on `UMAPAnalysisParams` itself, before the tool body
runs (and therefore before `include_plots` is examined) — an out-of-range value is rejected
regardless of `include_plots`'s value, the same rule already established for
`plot_alpha`/`plot_point_size`'s lower bound.

#### Scenario: An excessive plot_font_size is rejected regardless of include_plots

- **WHEN** `umap_analysis` is called with `plot_font_size` greater than `100` (including
  `float("inf")`) — with `include_plots` set to either `True` or `False`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, before any figure is
  generated, in both cases

#### Scenario: An excessive plot_point_size is rejected regardless of include_plots

- **WHEN** `umap_analysis` is called with `plot_point_size` greater than `10000` — with
  `include_plots` set to either `True` or `False`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, before any figure is
  generated, in both cases

#### Scenario: Boundary values 100 and 10000 are accepted

- **WHEN** `umap_analysis` is called with `include_plots=True`,
  `plots=["create_umap_single_trait"]`, and `plot_font_size=100` (or `plot_point_size=10000`)
- **THEN** the tool succeeds and `create_umap_single_trait` is invoked accordingly — the
  inclusive ceilings are valid values, not rejected

### Requirement: UMAP plot_cmap Is Restricted to Known Sequential and Diverging Colormaps

`UMAPAnalysisParams.plot_cmap`, when set, SHALL be validated in the `umap_analysis` tool body
— before `perform_umap_analysis` is called — against a fixed allowlist of matplotlib's
documented sequential and diverging colormap names (including each name's `_r` reversed
variant). A name outside the allowlist — whether unregistered (e.g. a misspelling) or a
registered-but-excluded qualitative/cyclic colormap (e.g. `hsv`, `tab10`) — SHALL be rejected
as `invalid_input`, naming the invalid value, before any UMAP computation runs. This
validation SHALL run regardless of `include_plots`'s value, the same rule already
established for the other `plot_*` fields' out-of-range checks. `plot_cmap=None` (the
default) SHALL skip the check entirely.

#### Scenario: An unregistered colormap name is rejected before any computation runs

- **WHEN** `umap_analysis` is called with `plot_cmap="virdis"` (a misspelling of `viridis`)
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming `"virdis"`,
  and `perform_umap_analysis` is never called

#### Scenario: A registered but excluded colormap is rejected before any computation runs

- **WHEN** `umap_analysis` is called with `plot_cmap="hsv"` or `plot_cmap="tab10"` — both
  valid matplotlib colormap names, neither sequential nor diverging
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the value, and
  `perform_umap_analysis` is never called

#### Scenario: An allowed sequential or diverging colormap is accepted

- **WHEN** `umap_analysis` is called with `include_plots=True`,
  `plots=["create_umap_single_trait"]`, and `plot_cmap="viridis"` (or `plot_cmap="RdBu"`)
- **THEN** the tool succeeds and `create_umap_single_trait` is invoked with `cmap="viridis"`
  (or `cmap="RdBu"`)

#### Scenario: An invalid plot_cmap is rejected regardless of include_plots

- **WHEN** `umap_analysis` is called with an invalid `plot_cmap` value — with `include_plots`
  set to either `True` or `False`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, before any figure is
  generated, in both cases

#### Scenario: An unset plot_cmap skips the check entirely

- **WHEN** `umap_analysis` is called with `plot_cmap` unset (the default, `None`)
- **THEN** no allowlist check is performed and the call behaves exactly as it did before this
  change
