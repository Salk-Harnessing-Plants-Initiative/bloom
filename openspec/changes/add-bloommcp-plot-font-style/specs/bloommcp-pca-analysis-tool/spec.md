## ADDED Requirements

### Requirement: PCA Analysis Accepts an Optional Font-Style Override for Generated Plots

The `pca_analysis` tool input SHALL accept `plot_font_family: Optional[str] = None` and
`plot_font_size: Optional[float] = None` in `PCAAnalysisParams`. When `include_plots` is
`True`, either value SHALL be applied uniformly to every generated figure's title, axis
labels, tick labels, standalone annotation text, figure-level text (e.g. a `fig.suptitle`),
and legend text and title (via the shared `bloom_mcp.tools._plots` figure-generation path) —
no per-plot or per-text-element styling. When both are `None` (the
default), every generated plot keeps its plotter's default matplotlib styling, unchanged from
pre-existing behavior. `plot_font_size` SHALL be rejected as `invalid_input` when not strictly
positive. Both fields SHALL be silently ignored (no error) when `include_plots=False`,
matching the existing ignore policy for `plots`.

#### Scenario: Default call keeps default matplotlib styling

- **WHEN** `pca_analysis` is called with `include_plots=True` and neither `plot_font_family`
  nor `plot_font_size` set
- **THEN** every generated figure's text elements keep the styling the upstream plotter drew
  them with, unchanged from behavior before this change

#### Scenario: A font family override is applied to every generated figure

- **WHEN** `pca_analysis` is called with `include_plots=True` and `plot_font_family="serif"`
- **THEN** every generated figure's title, axis labels, tick labels, standalone annotation
  text, figure-level text, and legend text and title (when present) have their font family
  set to `"serif"`

#### Scenario: A font size override is applied to every generated figure

- **WHEN** `pca_analysis` is called with `include_plots=True` and `plot_font_size=22`
- **THEN** every generated figure's title, axis labels, tick labels, standalone annotation
  text, figure-level text, and legend text and title (when present) have their font size set
  to `22`

#### Scenario: Both overrides apply together

- **WHEN** `pca_analysis` is called with `include_plots=True`, `plot_font_family="serif"`,
  and `plot_font_size=22`
- **THEN** every generated figure's text elements reflect both the family and the size

#### Scenario: A non-positive font size is rejected as invalid_input

- **WHEN** `pca_analysis` is called with `plot_font_size` less than or equal to `0`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, and no run is
  committed

#### Scenario: Font-style fields are ignored when include_plots is False

- **WHEN** `pca_analysis` is called with `include_plots=False` and either
  `plot_font_family` or `plot_font_size` set
- **THEN** the tool returns successfully with no `BloomMCPError`, and no figures are
  generated — the font-style fields have no effect

### Requirement: Font-Style Override Is Applied via a Shared, Tool-Agnostic Helper

`bloom_mcp/tools/_plots.py` SHALL expose `apply_font_style(fig, *, font_family=None,
font_size=None)`, invoked from `generate_figures` on each figure immediately after it is
recorded into the caller's `figures` dict (recording happens first, styling second — so a
hypothetical future exception from `apply_font_style` cannot leak a figure that was never
recorded) — so `pca_analysis`, `umap_analysis`, and any future consumer of `generate_figures`
share identical font-override behavior with no tool-specific styling code. The helper SHALL be
a no-op — touching no attribute of the passed-in object — when both `font_family` and
`font_size` are `None`, preserving compatibility with existing test doubles that exercise
`generate_figures`'s dispatch/error-propagation contract using non-`Figure` return values.

#### Scenario: generate_figures forwards font kwargs to every generated figure

- **WHEN** `generate_figures` is called with `font_family` and/or `font_size` set
- **THEN** `apply_font_style` is invoked on each figure produced by `resolved_calls`, after
  that figure is recorded into the caller's `figures` dict

#### Scenario: apply_font_style is a no-op when both are None

- **WHEN** `apply_font_style` is called with `font_family=None` and `font_size=None` on any
  object, including one that is not a `matplotlib.figure.Figure`
- **THEN** no exception is raised and no attribute of the object is accessed

#### Scenario: apply_font_style covers title, axis labels, tick labels, and legend text

- **WHEN** `apply_font_style` is called on a real `Figure` with a title, x/y axis labels,
  tick labels, and a legend
- **THEN** the font family and/or size is applied to all of: the title, the x-axis label,
  the y-axis label, every tick label, and every legend text entry

#### Scenario: apply_font_style covers figure-level text, including a suptitle

- **WHEN** `apply_font_style` is called on a `Figure` that carries figure-level text (e.g. a
  `fig.suptitle(...)`, the same call `create_umap_colored_by_top_traits` makes) — text that
  lives on the `Figure` itself, not on any `Axes`
- **THEN** the font family and/or size is applied to that figure-level text too, not just
  text reachable via `fig.axes`

#### Scenario: apply_font_style covers standalone annotation text distinct from title/labels/legend

- **WHEN** `apply_font_style` is called on a `Figure` whose `Axes` carries standalone
  annotation text added via `ax.text(...)` — the same mechanism `create_pca_biplot`'s
  per-arrow trait-name labels, `create_pca_scree_plot`'s per-bar annotations, and
  `create_feature_contribution_heatmap`'s seaborn `annot=True` cell values all use
- **THEN** the font family and/or size is applied to that standalone annotation text too,
  not just the title, axis labels, tick labels, and legend

#### Scenario: apply_font_style covers the legend's own title, not just its entries

- **WHEN** `apply_font_style` is called on a `Figure` whose `Axes` has a legend created with
  an explicit title (e.g. `ax.legend(title="Genotype")`, the same call shape
  `create_pca_biplot` uses)
- **THEN** the legend title's font family and/or size is overridden in addition to its
  individual entry labels — not skipped

#### Scenario: apply_font_style covers every Axes on a figure with more than one

- **WHEN** `apply_font_style` is called on a `Figure` with more than one `Axes` (e.g. a
  heatmap-plus-colorbar figure like `create_feature_contribution_heatmap` produces, where the
  colorbar occupies its own `Axes` alongside the main heatmap `Axes`)
- **THEN** the font family and/or size is applied to every `Axes` in `fig.axes`, not just the
  first

#### Scenario: apply_font_style skips axes with no legend

- **WHEN** `apply_font_style` is called on a `Figure` whose `Axes` has no legend
- **THEN** no exception is raised
