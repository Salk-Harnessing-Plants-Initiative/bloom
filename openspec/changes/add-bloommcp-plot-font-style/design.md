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
   built `Figure` — figure-level text (`fig.texts`, including a `fig.suptitle`), and per-`Axes`
   title, axis labels, tick labels, standalone annotation text (`ax.texts`), and legend text
   and title — is deterministic regardless of how the plotter built it, and requires no
   coordination with `sleap_roots_analyze` internals.

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
        figures[key] = fn()
        apply_font_style(figures[key], font_family=font_family, font_size=font_size)
```

This keeps `_plots.py` the single place any tool touches the raw `Figure` object between
plotter and `savefig` — symmetric with `close_figures` being the single place figures are torn
down. Applying per-figure (inside the loop) rather than once after the loop means a
mid-generation exception still leaves every already-styled, already-successful figure in
`figures` for `close_figures` to reach in `finally` — the same partial-failure guarantee
`generate_figures` already provides for figure generation itself (see the
`add-pca-analysis-plots` precedent).

**Recording before styling, not after.** `figures[key] = fn()` runs *before*
`apply_font_style`, not after — deliberately, not incidentally. If `apply_font_style` ever
raised (it doesn't today: `set_fontfamily`/`set_fontsize` don't validate, `font_size` is
already Pydantic-validated `gt=0`, and `ax.title`/`legend.get_title()`/`fig.texts` entries are
always real `Text` instances), the figure is already in the caller's dict for
`close_figures` to reach in `finally` — so a hypothetical future styling failure can't leak an
unrecorded, unreachable figure from matplotlib's `Agg` registry. Recording first costs
nothing today and removes a latent failure mode later.

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
    texts = list(fig.texts)
    for ax in fig.axes:
        texts.append(ax.title)
        texts.append(ax.xaxis.label)
        texts.append(ax.yaxis.label)
        texts.extend(ax.get_xticklabels())
        texts.extend(ax.get_yticklabels())
        texts.extend(ax.texts)
        legend = ax.get_legend()
        if legend is not None:
            texts.extend(legend.get_texts())
            texts.append(legend.get_title())
    for text in texts:
        if font_family is not None:
            text.set_fontfamily(font_family)
        if font_size is not None:
            text.set_fontsize(font_size)
```

An earlier revision of this function walked only `fig.axes` — title, axis labels, tick
labels, and legend text/title — and missed two categories of real text that several
in-scope catalog plots actually draw. Both gaps were caught in review and verified directly
against the installed `matplotlib`/`seaborn` and the upstream `sleap_roots_analyze` source
before being fixed here:

- **Figure-level text (`fig.texts`), not reachable via `fig.axes` at all.**
  `create_umap_colored_by_top_traits` sets its overall heading via
  `fig.suptitle(title, fontsize=14)` — a `Text` matplotlib records on the *Figure* itself
  (confirmed: `fig.suptitle(...)` both stores the `Text` at `fig._suptitle` and appends it to
  the public `fig.texts` list), not on any `Axes`. A function that only iterates `fig.axes`
  can never reach it, no matter how many `Axes` it inspects. `list(fig.texts)` picks up the
  suptitle (and any other figure-level `fig.text(...)` calls) once, before the per-`Axes`
  loop.
- **Standalone annotation text (`ax.texts`), distinct from title/axis-label/tick-label/legend.**
  Several catalog plots draw text via freestanding `ax.text(...)` calls that are none of
  those four categories: `create_pca_biplot`'s per-arrow trait-name labels, and
  `create_pca_scree_plot`'s per-bar percentage/threshold-crossing annotations (both verified
  directly in `sleap_roots_analyze.visualization`'s source). The same mechanism also covers
  `create_feature_contribution_heatmap`'s seaborn `annot=True` cell values — verified
  empirically that `sns.heatmap(..., annot=True)`'s cell-value text lands in the heatmap
  `Axes`'s own `ax.texts`, indistinguishable from a hand-written annotation as far as
  `apply_font_style` is concerned. None of this text is reachable via `ax.title`,
  `ax.xaxis.label`/`ax.yaxis.label`, `ax.get_xticklabels()`/`get_yticklabels()`, or
  `ax.get_legend()` — `ax.texts` is the only place it lives.

With both included, `apply_font_style` now reaches every text element on every in-scope
catalog plot shipped today, not just the four categories the issue named as illustrative
examples (tick labels, titles, axis labels, legend). Concretely verified per plot: the PCA
biplot's arrow labels and legend/legend-title (`create_pca_biplot`'s
`ax.legend(title=color_by, ...)`), the scree plot's bar annotations, the feature-contribution
heatmap's cell values and its seaborn colorbar `Axes` (a second entry in `fig.axes` — see
below), and the UMAP top-traits plot's `fig.suptitle`.

`ax.get_legend()` returns `None` when the axes has no legend — skipped rather than erroring.
`legend.get_title()` is unconditionally safe to include once a legend exists: matplotlib
always attaches a `Text` instance for the legend title (empty string when no `title=` was
passed to `ax.legend(...)`), so no further None-check is needed. Iterating `fig.axes` (not
just `fig.gca()`) also covers every `Axes` a figure carries beyond the first — concretely,
`create_feature_contribution_heatmap` (called by `pca_analysis` with `plot_type="loadings"`,
always a single `Figure`, never the tuple-returning `"both"` mode) draws via
`sns.heatmap(..., cbar_kws={"label": ...})`, which places its colorbar on a second `Axes`
matplotlib appends to the same figure — `len(fig.axes) == 2` for this call, not 1 — so its
y-axis label needs the per-`Axes` loop to reach it too, not just the main heatmap panel.

**Manual visual verification.** Because this feature is fundamentally about how a figure
looks, and the unit tests only assert `Text` font-family/font-size properties rather than
rendered pixels, a manual pass was run alongside the automated tests: `pca_analysis` and
`umap_analysis` were called with `include_plots=True` and a font override set, and the
resulting PNGs for all four PCA catalog plots and both UMAP catalog plots were opened and
visually confirmed — including the UMAP top-traits plot's overall title, the PCA biplot's
arrow labels, the scree plot's bar/threshold annotations, and the heatmap's cell numbers —
every visible piece of text reflects the override, with no remaining mixed-font text on any
of the six plots.

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
- **`gt=0` admits `float('inf')`**: Pydantic's `gt=0` constraint accepts `inf` (`inf > 0` is
  `True`), which would produce a degenerate, unreadably huge figure rather than a crash.
  Accepted for parity with the "no font-family validation" risk above — this change validates
  only that the caller's value is a sane *shape* (positive), not that the *rendered result* is
  reasonable; an adversarial or mistaken caller can still request a nonsensical style, the same
  way an unrecognized `font_family` degrades silently rather than erroring.

## Open Questions

None — this change is additive, backward-compatible, and scoped to the two tools already on
the `_plots.py` pattern per the issue's explicit out-of-scope note on the legacy plot tools.
