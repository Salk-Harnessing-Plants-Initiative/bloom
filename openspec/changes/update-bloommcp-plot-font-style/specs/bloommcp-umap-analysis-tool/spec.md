## ADDED Requirements

### Requirement: UMAP Analysis Accepts the Same Font-Style Override, Reusing the Shared Helper Unmodified

The `umap_analysis` tool input SHALL accept `plot_font_family: Optional[str] = None` and
`plot_font_size: Optional[float] = None` in `UMAPAnalysisParams`, with identical semantics and
validation to `pca_analysis` (`plot_font_size` rejected as `invalid_input` when not strictly
positive; both fields silently ignored when `include_plots=False`), forwarding both values
into the same `bloom_mcp.tools._plots.generate_figures` call already used for its
`include_plots`/`plots` params — no UMAP-specific font-styling code.

#### Scenario: Default call keeps default matplotlib styling

- **WHEN** `umap_analysis` is called with `include_plots=True` and neither
  `plot_font_family` nor `plot_font_size` set
- **THEN** every generated figure's text elements keep the styling the upstream plotter drew
  them with, unchanged from behavior before this change

#### Scenario: A font family and/or size override is applied to every generated figure

- **WHEN** `umap_analysis` is called with `include_plots=True` and `plot_font_family` and/or
  `plot_font_size` set
- **THEN** every generated figure's title, axis labels, tick labels, and legend text (when
  present) reflect the requested override(s)

#### Scenario: A non-positive font size is rejected as invalid_input

- **WHEN** `umap_analysis` is called with `plot_font_size` less than or equal to `0`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, and no run is
  committed

#### Scenario: Font-style fields are ignored when include_plots is False

- **WHEN** `umap_analysis` is called with `include_plots=False` and either
  `plot_font_family` or `plot_font_size` set
- **THEN** the tool returns successfully with no `BloomMCPError`, and no figures are
  generated — the font-style fields have no effect
