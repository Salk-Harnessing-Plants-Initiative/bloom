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

#### Scenario: A font family override is applied to every generated figure

- **WHEN** `umap_analysis` is called with `include_plots=True` and `plot_font_family="serif"`
- **THEN** every generated figure's title, axis labels, tick labels, standalone annotation
  text, figure-level text, and legend text and title (when present) have their font family
  set to `"serif"` — concretely, `create_umap_colored_by_top_traits`'s overall
  `fig.suptitle(...)` heading (figure-level text, not reachable via any `Axes`) is included.
  Neither UMAP catalog plot (`create_umap_single_trait`, `create_umap_colored_by_top_traits`)
  currently renders a legend (both use colorbars instead), so legend/legend-title coverage is
  exercised via the shared `_plots.py` behavior rather than a UMAP-specific legend fixture;
  see the `bloommcp-pca-analysis-tool` delta for the concrete legend/legend-title coverage
  (`create_pca_biplot` does render one)

#### Scenario: A font size override is applied to every generated figure

- **WHEN** `umap_analysis` is called with `include_plots=True` and `plot_font_size=22`
- **THEN** every generated figure's title, axis labels, tick labels, standalone annotation
  text, figure-level text (including `create_umap_colored_by_top_traits`'s `fig.suptitle`),
  and legend text and title (when present) have their font size set to `22`

#### Scenario: Both overrides apply together

- **WHEN** `umap_analysis` is called with `include_plots=True`, `plot_font_family="serif"`,
  and `plot_font_size=22`
- **THEN** every generated figure's text elements reflect both the family and the size

#### Scenario: A non-positive font size is rejected as invalid_input

- **WHEN** `umap_analysis` is called with `plot_font_size` less than or equal to `0`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, and no run is
  committed

#### Scenario: Font-style fields are ignored when include_plots is False

- **WHEN** `umap_analysis` is called with `include_plots=False` and either
  `plot_font_family` or `plot_font_size` set
- **THEN** the tool returns successfully with no `BloomMCPError`, and no figures are
  generated — the font-style fields have no effect
