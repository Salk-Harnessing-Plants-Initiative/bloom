## Context

`_plots.py` is the tool-agnostic seam both `pca_analysis` and `umap_analysis` already share
for figure validation/dispatch/cleanup (`validate_plot_keys`, `generate_figures`,
`close_figures`). Both tools hold the raw `Figure` object returned by each catalog plotter
before calling `fig.savefig(...)`, so a font override can be applied generically there,
without either tool needing its own styling code, and without any change to the upstream
`sleap_roots_analyze` plotters.

## Goals / Non-Goals

- Goals: one shared font-style override usable by every current and future `generate_figures`
  caller; zero impact on callers that don't pass the new kwargs; no new dependency; no
  breaking change to `_plots.py`'s existing signature for positional callers.
- Non-Goals: per-plot or per-text-element styling (e.g. title font different from tick-label
  font); styling the legacy string-returning plot tools (`plot_trait_histograms`,
  `plot_trait_boxplots`, `plot_correlation_matrix` — tracked by #466); validating
  `font_family` against the set of fonts actually installed in the container.

## Decisions

### Post-processing over `fig.axes`, not global `rcParams`

matplotlib supports a global font override via `matplotlib.rcParams['font.family']` /
`rcParams['font.size']`, set once before any figure is drawn. This is rejected in favor of
per-figure post-processing for two reasons:

1. **Scope leakage.** `rcParams` is process-global. Under the same `Agg` backend a single
   `bloommcp` process serves multiple tool calls; a caller who sets a font override on one
   call must not silently change the styling of a concurrent or subsequent call from a
   different caller that didn't ask for it.
2. **Timing.** `rcParams` must be set *before* the plotter draws the figure to take effect on
   text created with default styling; the tools call `matplotlib.use("Agg")` and then
   dispatch to lazily-imported catalog plotters (`_pca_plot_calls`/`_umap_plot_calls`) whose
   internals are opaque to this change. Post-processing every `Text` object on the already-
   built `Figure` (title, axis labels, tick labels, legend) is deterministic regardless of how
   the plotter built it, and requires no coordination with `sleap_roots_analyze` internals.

### Applied inside `generate_figures`, immediately after each figure is produced

```python
def generate_figures(
    resolved_calls: dict[str, "Callable[[], Figure]"],
    figures: "dict[str, Figure]",
    *,
    font_family: str | None = None,
    font_size: float | None = None,
) -> None:
    for key, fn in resolved_calls.items():
        fig = fn()
        apply_font_style(fig, font_family=font_family, font_size=font_size)
        figures[key] = fig
```

This keeps `_plots.py` the single place any tool touches the raw `Figure` object between
plotter and `savefig` — symmetric with `close_figures` being the single place figures are torn
down. Applying per-figure (inside the loop) rather than once after the loop means a
mid-generation exception still leaves every already-styled, already-successful figure in
`figures` for `close_figures` to reach in `finally` — the same partial-failure guarantee
`generate_figures` already provides for figure generation itself (see the
`add-pca-analysis-plots` precedent).

### No-op when both are `None` — preserves the existing test-double contract

`tests/tools/test_plots_helpers.py`'s `generate_figures` unit tests call it with
zero-arg callables that return **plain strings** (`"fig_a"`, not real `Figure` objects) to
test dispatch/error-propagation in isolation, with no matplotlib import. Because
`font_family`/`font_size` default to `None`, `apply_font_style` returns immediately without
touching `fig.axes` (or any attribute of `fig`) when both are `None` — so those existing tests
continue to pass unmodified; `apply_font_style` is only ever asked to walk a real `Figure`'s
axes when a caller actually opts in.

### `apply_font_style` text-element coverage

```python
def apply_font_style(
    fig: "Figure",
    *,
    font_family: str | None = None,
    font_size: float | None = None,
) -> None:
    if font_family is None and font_size is None:
        return
    for ax in fig.axes:
        texts = [ax.title, ax.xaxis.label, ax.yaxis.label]
        texts.extend(ax.get_xticklabels())
        texts.extend(ax.get_yticklabels())
        legend = ax.get_legend()
        if legend is not None:
            texts.extend(legend.get_texts())
        for text in texts:
            if font_family is not None:
                text.set_fontfamily(font_family)
            if font_size is not None:
                text.set_fontsize(font_size)
```

Covers every text element the issue names as examples (tick labels, titles, axis labels) plus
legend text, since several catalog plots (e.g. `create_pca_biplot`,
`create_umap_colored_by_top_traits`) render a legend and leaving it at the old font/size while
everything else changes would look inconsistent. `ax.get_legend()` returns `None` when the
axes has no legend — skipped rather than erroring. Iterating `fig.axes` (not just
`fig.gca()`) covers every subplot on multi-axes figures (e.g. `create_feature_contribution_heatmap`'s
scree + heatmap panels) uniformly.

### `font_family` is not validated against installed fonts

`Text.set_fontfamily` accepts any string; matplotlib's font manager resolves it lazily at
draw/save time and falls back to a default font with a logged warning (not an exception) when
the family isn't found, instead of raising. Validating `font_family` against the container's
installed font list would require querying `matplotlib.font_manager` and would make the
check environment-dependent (a name valid in one container image could fail in another).
This change accepts any string and relies on matplotlib's existing graceful fallback —
consistent with the tools' general policy of delegating rendering behavior to matplotlib
rather than re-implementing its validation.

### `font_size` validated as `gt=0` — a Pydantic parameter bound, not a delegate guard

`plot_font_size: float | None = Field(default=None, gt=0)` follows the existing convention in
these same param models (`UMAPAnalysisParams.n_neighbors: ge=2`,
`PCAAnalysisParams.explained_variance_threshold: ge=0.0, le=1.0`) of catching an
obviously-invalid caller value at the Pydantic layer as `invalid_input`, before any figure is
generated — rather than letting `Text.set_fontsize(0)` or a negative size silently produce an
unreadable or degenerate figure that only fails, if at all, deep inside matplotlib's rendering
path.

### Both fields ignored when `include_plots=False`

Mirrors the existing `plots`-with-`include_plots=False` policy documented for both tools: a
`plot_font_family`/`plot_font_size` value set alongside `include_plots=False` is silently
ignored, not rejected — a caller with these fields set in a reusable workflow template
shouldn't need to unset them just because they temporarily disabled plots.

## Risks / Trade-offs

- **Legend detection cost**: `ax.get_legend()` is a cheap attribute lookup, not a redraw;
  negligible overhead added to `generate_figures`.
- **No family validation**: a typo'd font family degrades to matplotlib's default font
  silently rather than surfacing an error to the caller. Accepted — see the decision above.

## Open Questions

None — this change is additive, backward-compatible, and scoped to the two tools already on
the `_plots.py` pattern per the issue's explicit out-of-scope note on the legacy plot tools.
