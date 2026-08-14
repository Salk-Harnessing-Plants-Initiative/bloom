## 1. Tests (write red tests first — TDD)

### `_plots.py` unit tests (`tests/tools/test_plots_helpers.py`)

- [x] 1.1 `test_apply_font_style_noop_when_both_none` — call `apply_font_style` with a bare
  `object()` sentinel (not a `Mock()` — the point is that no attribute at all is accessed, and
  a `Mock` would silently tolerate an accessed-but-unused attribute) and both kwargs `None`;
  assert no exception (proves it never touches `fig.axes`, preserving compatibility with the
  existing string-returning `generate_figures` test doubles)
- [x] 1.2 `test_apply_font_style_sets_font_family_on_title_labels_ticks_and_legend` — build a
  real `Figure`/`Axes` with a title, x/y labels, tick labels, and a legend created with an
  explicit title (`ax.legend(title="Genotype")`, matching `create_pca_biplot`'s real call
  shape); call `apply_font_style(fig, font_family="serif")`; assert every text element's font
  family is `["serif"]`, **including the legend's own title** (`legend.get_title()`) and not
  just its entries (`legend.get_texts()`) — regression guard for the gap where
  `create_pca_biplot`'s `ax.legend(title=color_by, ...)` legend title would otherwise silently
  keep its old font while every other text element changed
- [x] 1.3 `test_apply_font_style_sets_font_size` — same fixture; call
  `apply_font_style(fig, font_size=22)`; assert every text element's font size is `22`,
  including the legend title
- [x] 1.4 `test_apply_font_style_family_only_leaves_size_unchanged` — call with only
  `font_family` set; assert font size on a sampled text element is unchanged from its
  matplotlib default
- [x] 1.4b `test_apply_font_style_size_only_leaves_family_unchanged` — symmetric case: call
  with only `font_size` set; assert font family on a sampled text element is unchanged
- [x] 1.5 `test_apply_font_style_skips_axes_without_a_legend` — an `Axes` with no legend;
  assert no exception (covers `ax.get_legend() is None`)
- [x] 1.5b `test_apply_font_style_applies_to_every_axes_on_multi_axes_figure` — build a
  `Figure` with two `Axes` (e.g. `plt.subplots(1, 2)`, or a heatmap-plus-colorbar fixture via
  `sns.heatmap(..., cbar_kws={"label": "x"})` — verified to produce `len(fig.axes) == 2`),
  each with its own title/label; call `apply_font_style(fig, font_family="serif")`; assert
  **both** axes' text reflects the override — regression guard for
  `create_feature_contribution_heatmap` (called by `pca_analysis` with `plot_type="loadings"`),
  whose seaborn colorbar occupies a second `Axes` on the same figure — the reason
  `apply_font_style` iterates `fig.axes` rather than `fig.gca()`
- [x] 1.6 `test_generate_figures_forwards_font_kwargs_to_each_figure` — call
  `generate_figures({"a": lambda: <real Figure w/ title>, "b": lambda: <second real Figure w/
  title>}, figures, font_family="serif", font_size=18)`; assert **both** `figures["a"]` and
  `figures["b"]` have the override applied (a single-figure dict wouldn't prove the override
  is applied per-iteration, not just once)
- [x] 1.7 Regression check (not a new test — verify as part of running the existing suite in
  5.2): the existing string-returning-callable tests in this file
  (`test_generate_figures_populates_caller_dict` et al.) must keep passing unmodified, since
  `font_family`/`font_size` default to `None` and `apply_font_style` never touches a
  non-`Figure` return value in that case

### Tool-level tests (`tests/tools/test_pca_analysis_tool.py`, `test_umap_analysis_tool.py`)

- [x] 1.8 `test_plot_font_family_and_size_forwarded_and_applied` (one per tool) — monkeypatch
  `_pca_plot_calls`/`_umap_plot_calls` (same pattern as the existing figure-cleanup tests) to
  return a single callable producing a real `Figure` with a title/label/legend, captured via a
  closure; call with `include_plots=True, plot_font_family="serif", plot_font_size=22`; assert
  the captured figure's text elements reflect the override. (Family-only and size-only
  coverage at the tool level is intentionally left to the lower-level `_plots.py` unit tests
  above — 1.2-1.4b already exercise those independently; this tool-level test only needs to
  prove the two `PCAAnalysisParams`/`UMAPAnalysisParams` fields actually reach
  `generate_figures`.)
- [x] 1.9 `test_plot_font_size_non_positive_is_invalid_input` (one per tool) — call
  `pca_analysis({"experiment": ..., "plot_font_size": 0})` /
  `umap_analysis({"experiment": ..., "plot_font_size": -1})`; assert `BloomMCPError`
  `invalid_input`, no run committed (same pattern as `test_n_neighbors_non_positive_is_invalid_input`)
- [x] 1.10 `test_plot_font_fields_ignored_when_include_plots_false` (one per tool) — call with
  `include_plots=False, plot_font_family="serif", plot_font_size=22`; assert no error and no
  PNG outputs (mirrors `test_include_plots_false_with_plots_param_is_silently_ignored`)
- [x] 1.11 Regression check (not a new test — verify as part of running the existing suite in
  5.2): the existing plot-PNG-round-trip tests (`test_all_four_plots_png_round_trip`,
  `test_umap_single_trait_plot_png_round_trip`, etc.), which call `include_plots=True` with no
  font fields, must keep passing unmodified — confirming the default (no font override) path
  is untouched by this change

## 2. Design

- [x] 2.1 Write `design.md` (covers: rcParams-vs-post-processing decision, application point
  inside `generate_figures`, no-op-when-both-None compatibility with existing test doubles,
  text-element coverage including legend text *and title*, no font-family validation, `gt=0`
  font-size validation, ignore-when-`include_plots=False` policy)

## 3. Shared `_plots.py` helper

- [x] 3.1 Add `apply_font_style(fig, *, font_family: str | None = None, font_size: float |
  None = None) -> None` to `bloom_mcp/tools/_plots.py`: no-op when both are `None`; otherwise
  collects `fig.texts` (figure-level text, including a `fig.suptitle`) plus, per `Axes` in
  `fig.axes`: title, x-label, y-label, tick labels, `ax.texts` (standalone annotation text —
  covers `create_pca_biplot`'s arrow labels, `create_pca_scree_plot`'s bar annotations, and
  `create_feature_contribution_heatmap`'s seaborn `annot=True` cell values), and — when a
  legend is present — every legend entry (`legend.get_texts()`) **and the legend's own title**
  (`legend.get_title()`, always a `Text` instance regardless of whether `title=` was passed to
  `ax.legend(...)`, so no extra None-check is needed once `legend is not None`)
- [x] 3.2 Extend `generate_figures`'s signature with keyword-only `font_family`/`font_size`
  (both default `None`); record each figure into `figures` **first**, then call
  `apply_font_style` on it — recording before styling (not after) so a hypothetical future
  exception from `apply_font_style` can't leak an unrecorded figure past `close_figures`'s
  `finally`
- [x] 3.3 Update `generate_figures`'s docstring to mention the font-style post-processing step
  (currently reads "Call each zero-arg plotter callable, recording each result into `figures`."
  — under-describes the function once it also applies the override before recording)

## 4. Modify `pca_analysis.py` and `umap_analysis.py`

- [x] 4.1 Add `plot_font_family: str | None = Field(default=None, ...)` and
  `plot_font_size: float | None = Field(default=None, gt=0, ...)` to `PCAAnalysisParams`
- [x] 4.2 Add the same two fields to `UMAPAnalysisParams`
- [x] 4.3 Forward `font_family=params.plot_font_family, font_size=params.plot_font_size` into
  each tool's existing `generate_figures(...)` call site
- [x] 4.4 Update `pca_analysis.py`'s module docstring "Optional plots (#426)" paragraph to
  mention `plot_font_family`/`plot_font_size` are forwarded through `generate_figures` —
  matching this codebase's convention of describing `include_plots`/`plots` in both the Field
  description and the module docstring
- [x] 4.5 Same for `umap_analysis.py`'s "Optional plots (#425, reusing #426's shared helper)"
  module-docstring paragraph

## 5. Validate

- [x] 5.1 Run `openspec validate add-bloommcp-plot-font-style --strict` — no issues
- [x] 5.2 Run the test suite exactly as CI does:
  `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and not live_smoke" -v --tb=short`
  (matches `.github/workflows/pr-checks.yml`'s unit-test job; a bare `pytest tests/` can pass
  locally while missing a lockfile-sync or marker issue CI would catch)
- [x] 5.3 Run lint/format (`ruff check`, `ruff format --check`, `black --check`) per project
  convention

## 6. PR review round 2 — coverage gaps found by the 5-subagent PR review (#665)

The review confirmed the shared-helper mechanism itself was sound but found real
`fig.axes`-only coverage gaps against real, in-scope catalog plots — not hypothetical
future ones — plus a latent figure-leak-on-raise ordering issue and a documentation gap.
Every blocking/important finding was verified independently against the installed
matplotlib/seaborn and the upstream `sleap_roots_analyze` source before being fixed.

- [x] 6.1 `apply_font_style` missed `fig.suptitle` (figure-level `Text`, not on any `Axes` —
  `create_umap_colored_by_top_traits` sets one) and standalone `ax.text(...)` annotations
  (`create_pca_biplot`'s arrow labels, `create_pca_scree_plot`'s bar annotations, and
  seaborn's `annot=True` heatmap cells, verified to live in `ax.texts`). Fixed by collecting
  `fig.texts` once plus `ax.texts` per `Axes`, alongside the existing title/label/tick/legend
  coverage.
- [x] 6.2 `generate_figures` recorded a figure into `figures` only *after* styling it — fixed
  to record first, style second, so a hypothetical future `apply_font_style` exception can't
  leak an unrecorded figure past `close_figures`'s `finally` (latent risk; nothing in the
  current code path actually raises today).
- [x] 6.3 Added regression tests: `test_apply_font_style_covers_figure_level_suptitle`,
  `test_apply_font_style_covers_standalone_annotation_text`,
  `test_apply_font_style_covers_seaborn_annot_true_heatmap_cells`,
  `test_generate_figures_records_figure_before_styling` (all in
  `test_plots_helpers.py`); `test_plots_subset_with_font_override_never_generates_non_requested_plots`
  and `test_plot_font_size_just_above_zero_is_accepted` (one per tool, in
  `test_pca_analysis_tool.py`/`test_umap_analysis_tool.py`)
- [x] 6.4 Added the silent-font-fallback note to `plot_font_family`'s `Field(description=...)`
  in both `PCAAnalysisParams`/`UMAPAnalysisParams` (previously only in `design.md`, not
  visible to a caller — human or LLM agent — at the point they'd need it)
- [x] 6.5 Added a `design.md` Risks note that `plot_font_size`'s `gt=0` constraint admits
  `float('inf')` (produces a degenerate figure, not a crash) — parity with the already-accepted
  no-font-family-validation risk
- [x] 6.6 Manual visual verification: ran `pca_analysis`/`umap_analysis` with
  `include_plots=True` and a font override, opened all four PCA catalog plots and both UMAP
  catalog plots, and visually confirmed every text element — including the UMAP top-traits
  plot's `fig.suptitle`, the PCA biplot's arrow labels, the scree plot's bar/threshold
  annotations, and the heatmap's `annot=True` cell numbers — reflects the override
- [x] 6.7 Updated `design.md` and both delta `spec.md` files to describe the corrected
  coverage (figure-level text, standalone annotations) and the record-before-style ordering
- [x] 6.8 Re-ran the full CI-matching suite and lint/format after all fixes — see commit
  history for exact results
