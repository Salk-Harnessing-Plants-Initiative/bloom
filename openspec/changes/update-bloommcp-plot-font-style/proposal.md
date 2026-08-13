## Why

`pca_analysis` and `umap_analysis`'s `include_plots` path renders figures with whatever
default matplotlib styling the upstream `sleap_roots_analyze` plotters ship with. Scientists
preparing figures for papers, posters, or presentations need a consistent, larger, or
specific-family font without exporting the PNG and re-styling it externally. Every consumer
tool that supports `include_plots` already funnels its generated `matplotlib.figure.Figure`
objects through the same `bloom_mcp/tools/_plots.py` helpers (`generate_figures`/
`close_figures`) before `fig.savefig(...)` is called — so a font override can be applied once,
generically, as a post-processing step over `fig.axes`, independent of which upstream plotter
drew the figure.

## What Changes

- **ADD** `apply_font_style(fig, *, font_family=None, font_size=None)` to
  `bloom_mcp/tools/_plots.py` — a tool-agnostic post-processing step that walks every
  `Axes` in `fig.axes` and overrides the font family and/or size of its title, x-axis label,
  y-axis label, tick labels, and legend text (when a legend is present). A no-op — touching
  no attribute of `fig` at all — when both `font_family` and `font_size` are `None`.
- **MODIFY** `generate_figures` to accept optional `font_family: str | None` and
  `font_size: float | None` keyword args and call `apply_font_style` on each figure
  immediately after it is produced, before recording it into the caller's `figures` dict.
  Existing callers that invoke `generate_figures(resolved_calls, figures)` with no font
  kwargs are unaffected (both default to `None`).
- **MODIFY** `PCAAnalysisParams` and `UMAPAnalysisParams`: add
  `plot_font_family: str | None = None` and `plot_font_size: float | None = None`
  (`gt=0`, rejected as `invalid_input` otherwise) to each model, forwarded into
  `generate_figures` alongside the existing `include_plots`/`plots` params. Both fields are
  ignored (no error) when `include_plots=False`, matching the existing `plots`-with-
  `include_plots=False` ignore policy.
- **Out of scope** (per issue #661): the legacy string-returning plot tools
  (`plot_trait_histograms`, `plot_trait_boxplots`, `plot_correlation_matrix`) — these don't
  go through `_plots.py` yet; held until #466 converges them onto the same pattern, to avoid
  duplicate work.

## Impact

- Affected specs: `bloommcp-pca-analysis-tool`, `bloommcp-umap-analysis-tool`
- Affected code:
  - `bloommcp/src/bloom_mcp/tools/_plots.py` — add `apply_font_style`, extend
    `generate_figures`'s signature
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/pca_analysis.py` — add the two
    params to `PCAAnalysisParams`, forward them into the existing `generate_figures` call
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/umap_analysis.py` — same, for
    `UMAPAnalysisParams`
  - `bloommcp/tests/tools/test_plots_helpers.py` — new unit tests for `apply_font_style` and
    `generate_figures`'s font-kwarg forwarding
  - `bloommcp/tests/tools/test_pca_analysis_tool.py`,
    `test_umap_analysis_tool.py` — new tool-level tests confirming the params are forwarded
    and applied
- No breaking change: both new fields default to `None`; behavior is identical to today when
  omitted.
- No new dependency: matplotlib's `Text.set_fontfamily`/`set_fontsize` are already available
  wherever `_plots.py` is used today.
