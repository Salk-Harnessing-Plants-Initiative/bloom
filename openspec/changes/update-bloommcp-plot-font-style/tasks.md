## 1. Tests (write red tests first — TDD)

### `_plots.py` unit tests (`tests/tools/test_plots_helpers.py`)

- [ ] 1.1 `test_apply_font_style_noop_when_both_none` — call `apply_font_style` with a
  non-Figure sentinel object (e.g. `object()`) and both kwargs `None`; assert no exception
  (proves it never touches `fig.axes`, preserving compatibility with the existing
  string-returning `generate_figures` test doubles)
- [ ] 1.2 `test_apply_font_style_sets_font_family_on_title_labels_ticks_and_legend` — build a
  real `Figure`/`Axes` with a title, x/y labels, tick labels, and a legend; call
  `apply_font_style(fig, font_family="serif")`; assert every text element's font family is
  `["serif"]`
- [ ] 1.3 `test_apply_font_style_sets_font_size` — same fixture; call
  `apply_font_style(fig, font_size=22)`; assert every text element's font size is `22`
- [ ] 1.4 `test_apply_font_style_family_only_leaves_size_unchanged` — call with only
  `font_family` set; assert font size on a sampled text element is unchanged from its
  matplotlib default
- [ ] 1.5 `test_apply_font_style_skips_axes_without_a_legend` — an `Axes` with no legend;
  assert no exception (covers `ax.get_legend() is None`)
- [ ] 1.6 `test_generate_figures_forwards_font_kwargs_to_each_figure` — call
  `generate_figures({"a": lambda: <real Figure with title>}, figures, font_family="serif",
  font_size=18)`; assert the figure recorded in `figures["a"]` has the override applied
- [ ] 1.7 `test_generate_figures_without_font_kwargs_is_unchanged` — confirm the existing
  string-returning-callable tests (`test_generate_figures_populates_caller_dict` et al.) still
  pass unmodified (no new assertions needed — regression guard via the existing suite)

### Tool-level tests (`tests/tools/test_pca_analysis_tool.py`, `test_umap_analysis_tool.py`)

- [ ] 1.8 `test_plot_font_family_and_size_forwarded_and_applied` (one per tool) — monkeypatch
  `_pca_plot_calls`/`_umap_plot_calls` (same pattern as the existing figure-cleanup tests) to
  return a single callable producing a real `Figure` with a title/label/legend, captured via a
  closure; call with `include_plots=True, plot_font_family="serif", plot_font_size=22`; assert
  the captured figure's text elements reflect the override
- [ ] 1.9 `test_plot_font_size_non_positive_is_invalid_input` (one per tool) — call
  `pca_analysis({"experiment": ..., "plot_font_size": 0})` /
  `umap_analysis({"experiment": ..., "plot_font_size": -1})`; assert `BloomMCPError`
  `invalid_input`, no run committed (same pattern as `test_n_neighbors_non_positive_is_invalid_input`)
- [ ] 1.10 `test_plot_font_fields_ignored_when_include_plots_false` (one per tool) — call with
  `include_plots=False, plot_font_family="serif", plot_font_size=22`; assert no error and no
  PNG outputs (mirrors `test_include_plots_false_with_plots_param_is_silently_ignored`)
- [ ] 1.11 `test_default_no_font_fields_plots_unchanged` (one per tool, or covered by 1.8's
  negative case) — `include_plots=True` with no font fields; existing plot-PNG-round-trip
  tests already cover this implicitly (no new assertion required if unmodified)

## 2. Design

- [x] 2.1 Write `design.md` (covers: rcParams-vs-post-processing decision, application point
  inside `generate_figures`, no-op-when-both-None compatibility with existing test doubles,
  text-element coverage including legend, no font-family validation, `gt=0` font-size
  validation, ignore-when-`include_plots=False` policy)

## 3. Shared `_plots.py` helper

- [ ] 3.1 Add `apply_font_style(fig, *, font_family: str | None = None, font_size: float |
  None = None) -> None` to `bloom_mcp/tools/_plots.py`: no-op when both are `None`; otherwise
  walks `fig.axes` setting font family/size on title, x-label, y-label, tick labels, and
  legend text (when present)
- [ ] 3.2 Extend `generate_figures`'s signature with keyword-only `font_family`/`font_size`
  (both default `None`); call `apply_font_style` on each figure immediately after `fn()`,
  before recording it into `figures`

## 4. Modify `pca_analysis.py` and `umap_analysis.py`

- [ ] 4.1 Add `plot_font_family: str | None = Field(default=None, ...)` and
  `plot_font_size: float | None = Field(default=None, gt=0, ...)` to `PCAAnalysisParams`
- [ ] 4.2 Add the same two fields to `UMAPAnalysisParams`
- [ ] 4.3 Forward `font_family=params.plot_font_family, font_size=params.plot_font_size` into
  each tool's existing `generate_figures(...)` call site

## 5. Validate

- [ ] 5.1 Run `openspec validate update-bloommcp-plot-font-style --strict` — no issues
- [ ] 5.2 Run full test suite: `cd bloommcp && uv run pytest tests/ -x`
- [ ] 5.3 Run lint/format (`ruff check`, `ruff format --check`, `black --check`) per project
  convention
