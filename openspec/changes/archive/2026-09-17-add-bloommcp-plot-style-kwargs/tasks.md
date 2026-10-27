## 1. UMAP — tests first (red), then wiring (green)

- [x] 1.1 In `test_umap_analysis_tool.py`, add a `sleap_roots_analyze` import and write
      failing tests, each patching `sleap_roots_analyze.create_umap_single_trait` (module
      attribute, NOT `umap_analysis_tool.create_umap_single_trait` — the plotter is imported
      function-locally inside `_umap_plot_calls`, so it is never a `umap_analysis_tool`
      module attribute; see design.md) with a wrap-and-delegate spy that records `kwargs` and
      calls through to the real function, mirroring the existing `perform_umap_analysis` spy
      shape:
  - [x] 1.1.1 `plot_cmap`/`plot_point_size`/`plot_alpha` set → each is forwarded as the
        matching kwarg (`cmap`/`point_size`/`alpha`) to `create_umap_single_trait`
  - [x] 1.1.2 All three unset (`None`) → none of `cmap`/`point_size`/`alpha` appear in the
        captured kwargs at all (not present, not passed as `None`)
  - [x] 1.1.3 `plot_cmap` set with both plot keys requested → patching
        `sleap_roots_analyze.create_umap_colored_by_top_traits` too shows it receives no
        `cmap` kwarg
  - [x] 1.1.4 Valid style fields set alongside `include_plots=False` → no error, no figures
        (extends the existing `test_include_plots_false_with_plots_param_is_silently_ignored`
        style)
  - [x] 1.1.5 `plot_point_size=0` / negative, `plot_alpha=1.5` / negative → `invalid_input`,
        raised regardless of `include_plots`
  - [x] 1.1.6 Boundary success: `plot_alpha=0.0` and `plot_alpha=1.0` (inclusive bounds) do
        not raise
- [x] 1.2 Update `test_figure_cleanup_get_fignums_empty_on_partial_plotter_failure`'s
      `_patched(result_dict, frame, trait_cols)` wrapper to `_patched(result_dict, frame,
      trait_cols, **kwargs)`, forwarding `**kwargs` into `real(...)` — required once the
      production call site below passes new keyword args, or this existing test breaks with
      an unrelated `TypeError` (see design.md).
- [x] 1.3 Add `plot_cmap: str | None = None`, `plot_point_size: float | None = None`
      (`gt=0`), `plot_alpha: float | None = None` (`ge=0.0, le=1.0`) to `UMAPAnalysisParams`
      (`umap_analysis.py`), documenting in each field's description that they apply only to
      `create_umap_single_trait` and are ignored for `create_umap_colored_by_top_traits` and
      (when validly set) for `include_plots=False`.
- [x] 1.4 Add `*, plot_cmap: str | None = None, plot_point_size: float | None = None,
      plot_alpha: float | None = None` to `_umap_plot_calls`'s signature; build the
      `create_umap_single_trait` kwargs dict from only the non-`None` values (one `if`
      per field), forwarded via `**kwargs`; leave `create_umap_colored_by_top_traits`
      unchanged. Update the tool body's call site to pass
      `plot_cmap=params.plot_cmap, plot_point_size=params.plot_point_size,
      plot_alpha=params.plot_alpha`.
- [x] 1.5 Run the section-1 tests; confirm they were red before 1.3/1.4 and are green after.

## 2. PCA — tests first (red), then wiring (green)

- [x] 2.1 In `test_pca_analysis_tool.py`, add a `sleap_roots_analyze` import and write
      failing tests patching `sleap_roots_analyze.create_pca_biplot` (module attribute — same
      function-local-import caveat as UMAP) with a wrap-and-delegate spy:
  - [x] 2.1.1 `plot_alpha` set → forwarded as `alpha` to `create_pca_biplot`
  - [x] 2.1.2 `plot_alpha` unset (`None`) → no `alpha` kwarg present in the captured call
  - [x] 2.1.3 `plot_alpha` set with all four plot keys requested → the other three plotters'
        captured calls are unaffected (patch each, assert no `alpha` kwarg reaches them)
  - [x] 2.1.4 `plot_alpha` set alongside `include_plots=False` → no error, no figures
  - [x] 2.1.5 `plot_alpha=1.5` / negative → `invalid_input`, regardless of `include_plots`
  - [x] 2.1.6 Boundary success: `plot_alpha=0.0` and `plot_alpha=1.0` do not raise
- [x] 2.2 Update `test_figure_cleanup_get_fignums_empty_on_partial_plotter_failure`'s
      `_patched(result_dict, pca, frame, threshold)` wrapper to accept and forward
      `**kwargs` — same reason as UMAP's 1.2.
- [x] 2.3 Add `plot_alpha: float | None = None` (`ge=0.0, le=1.0`) to `PCAAnalysisParams`
      (`pca_analysis.py`), documenting that it applies only to `create_pca_biplot`.
- [x] 2.4 Add `*, plot_alpha: float | None = None` to `_pca_plot_calls`'s signature; forward
      it to `create_pca_biplot`'s call only when not `None`; leave the other three calls
      unchanged. Update the tool body's call site to pass `plot_alpha=params.plot_alpha`.
- [x] 2.5 Run the section-2 tests; confirm they were red before 2.3/2.4 and are green after.

## 3. Validation

- [x] 3.1 `openspec validate add-bloommcp-plot-style-kwargs --strict` passes
- [x] 3.2 Full `bloommcp` test suite passes (`uv run pytest`) — 1085 passed, 29 skipped
- [x] 3.3 Lint/format clean (`ruff check`, `ruff format --check`, `black --check`)
