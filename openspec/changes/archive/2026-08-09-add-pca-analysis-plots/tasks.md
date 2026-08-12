## 1. Tests (write red tests first — TDD)

- [x] 1.1 `test_default_no_plots_oracle_unchanged` — call `_run()` (no `include_plots`);
  assert `result.outputs` contains exactly `{loadings.csv, scores.csv, pca_result.json}`
  (no new `plot_links` field — plots merge into the existing `outputs` dict, see design.md);
  assert numeric fields match turface_19 golden within `_VAR_TOL`
- [x] 1.2 `test_unknown_plot_key_invalid_input_no_run_committed` — call with
  `include_plots=True, plots=["not_a_real_plot"]`; assert `BloomMCPError(invalid_input)`;
  assert `store.list_runs(experiment, "pca") == []`
- [x] 1.3 `test_duplicate_plot_key_invalid_input_no_run_committed` — call with
  `plots=["create_pca_scree_plot", "create_pca_scree_plot"]`; assert
  `BloomMCPError(invalid_input)` naming the duplicate; assert no run committed
- [x] 1.4 `test_empty_plots_list_invalid_input` — call with `include_plots=True, plots=[]`;
  assert `BloomMCPError(invalid_input)` (use `plots=None` for all, or omit `include_plots`)
- [x] 1.5 `test_include_plots_false_with_plots_param_ignored` — call with
  `include_plots=False, plots=["create_pca_scree_plot"]`; assert no error; assert `outputs`
  contains only the three data artifacts (no PNG keys)
- [x] 1.6 `test_all_four_plots_png_round_trip` — call with `include_plots=True, plots=None`;
  assert `outputs` contains four extra `*.png` keys; fetch each from the store and assert
  `content.startswith(b"\x89PNG")` (real PNG, not empty bytes)
- [x] 1.7 `test_plots_subset_generates_only_requested` — call with
  `include_plots=True, plots=["create_pca_scree_plot", "create_pca_biplot"]`; assert
  `outputs` contains exactly those two `.png` keys plus the three data keys
- [x] 1.8 `test_figure_cleanup_get_fignums_empty` — after any `include_plots=True` call
  (success or error path), assert `matplotlib.pyplot.get_fignums() == []`
- [x] 1.9 `test_matplotlib_not_imported_on_default_path` — `monkeypatch.setitem(sys.modules,
  'matplotlib', None)` before calling `_run()` (no `include_plots`); assert no `ImportError`
- [x] 1.10 Unit tests for `_plots.py` helpers in `tests/tools/test_plots_helpers.py`
  (no live stack required):
  - `validate_plot_keys(["not_real"], valid)` → `BloomMCPError(invalid_input)` naming the key
  - `validate_plot_keys(["k", "k"], valid)` → `BloomMCPError(invalid_input)` naming duplicate
  - `validate_plot_keys([], valid)` → `BloomMCPError(invalid_input)` (empty list)
  - `close_figures({})` → no error on empty dict

## 2. Design

- [x] 2.1 Write `design.md` (covers: raw dict retention, `_plots.py` zero-arg callable
  protocol, heatmap `plot_type='loadings'`, graceful degradation, figure/tempdir nesting,
  `plots=[]` rejection, `include_plots=False+plots` ignore policy, `outputs` merge, lazy
  `Agg` import)

## 3. Shared `_plots.py` helper

- [x] 3.1 Create `bloommcp/src/bloom_mcp/tools/_plots.py` with:
  - `validate_plot_keys(requested: list[str] | None, valid_keys: set[str]) -> None` —
    raises `BloomMCPError(invalid_input)` on unknown, duplicate, or empty list
  - `generate_figures(resolved_calls: dict[str, Callable[[], Figure]], figures: dict[str,
    Figure]) -> None` — calls each zero-arg callable, recording each result into the
    caller-supplied `figures` dict one key at a time; on exception, propagates without
    swallowing, leaving every already-successful figure in `figures` for `close_figures`
  - `close_figures(figures: dict[str, Figure]) -> None` — best-effort; never raises

## 4. Modify `pca_analysis_tool.py`

- [x] 4.1 Add `include_plots: bool = False` and `plots: Optional[list[str]] = None` to
  `PCAAnalysisParams`
- [x] 4.2 Retain `result_dict` in local scope after `PCAResult.from_pca_dict` (plotters
  take the raw dict, not a `PCAResult` instance)
- [x] 4.3 Define `_pca_plot_calls(result_dict, pca, frame, threshold)` returning a dict of
  four zero-arg lambdas with lazy plotter imports (`color_by=frame.genotype_col` on biplot,
  via `_biplot_df(frame)` which casts a copy of the genotype column to `pd.Categorical` — the
  upstream plotter's categorical-coloring check doesn't recognize pandas's `StringDtype`, so
  an uncast column crashes; see design.md § "color_by decision")
- [x] 4.4 Restructure persistence: wrap with `try/finally` (outer) nesting the existing
  `with tempfile.TemporaryDirectory` (inner). In `try`: validate plot keys, generate figures,
  then enter the `with` block for `create_run` + savefig + `commit`. In `finally`:
  `close_figures(figures)`.
- [x] 4.5 Merge PNG outputs into the existing `outputs` dict passed to `store.commit()` —
  no new result field

## 5. Validate

- [x] 5.1 Run `openspec validate add-pca-analysis-plots --strict` — no issues
- [x] 5.2 Run full test suite: `cd bloommcp && uv run pytest tests/ -x` — 47/47 passed
