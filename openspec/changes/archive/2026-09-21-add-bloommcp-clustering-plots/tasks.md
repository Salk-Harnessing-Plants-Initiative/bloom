## 1. Tests (write red tests first — TDD)

All in `bloommcp/tests/tools/test_clustering_tool.py`, mirroring
`test_pca_analysis_tool.py`'s plot section (search `include_plots`, ~L540-785):

- [x] 1.1 `test_default_no_plots_outputs_unchanged` — parametrized over all three methods;
  call `_run()` (no `include_plots`); assert `result.outputs == {labels.csv,
  cluster_result.json}` (no PNG keys); assert cluster summary fields unchanged
- [x] 1.2 `test_unknown_plot_key_invalid_input_no_run_committed` — `include_plots=True,
  plots=["not_a_real_plot"]`; assert `BloomMCPError(invalid_input)` naming it; assert
  `store.list_runs(experiment, "clustering") == []`
- [x] 1.3 `test_duplicate_plot_key_invalid_input_no_run_committed` — `plots=[
  "create_cluster_scatter_pca", "create_cluster_scatter_pca"]`; assert `invalid_input` naming
  the duplicate; assert no run committed
- [x] 1.4 `test_empty_plots_list_is_invalid_input` — `include_plots=True, plots=[]`; assert
  `invalid_input`; assert no run committed
- [x] 1.5 `test_include_plots_false_with_plots_param_is_silently_ignored` —
  `include_plots=False, plots=["create_cluster_scatter_pca"]`; assert no error; assert
  `outputs` has no PNG keys
- [x] 1.6 `test_both_plots_png_round_trip` — parametrized over all three methods;
  `include_plots=True, plots=None`; assert `outputs` gains
  `{create_cluster_scatter_pca.png, create_cluster_size_barplot.png}`; fetch staged bytes via
  the `store.commit` monkeypatch pattern (mirrors `_capture_staged_bytes` in
  `test_pca_analysis_tool.py`) and assert each starts with `b"\x89PNG"`. For the
  `method="hierarchical"` case specifically, add a canary assertion —
  `assert "data_processed" not in result_dict` (captured via a `perform_kmeans_clustering`/
  `hierarchical_cluster_labels`-adjacent monkeypatch or by inspecting the persisted
  `cluster_result.json`) — so the test documents *why* the internal-PCA design is needed,
  not just that it happens to pass
- [x] 1.7 `test_plots_subset_generates_only_requested` — `plots=[
  "create_cluster_size_barplot"]`; assert `outputs` contains exactly that one PNG key plus
  the two data keys
- [x] 1.8 `test_plotters_invoked_once_with_correct_args` — monkeypatch
  `create_cluster_scatter_pca`/`create_cluster_size_barplot` at the `sleap_roots_analyze`
  module level (mirrors `test_plot_alpha_forwarded_to_create_pca_biplot`'s spy pattern);
  assert each called exactly once; assert `create_cluster_size_barplot` receives
  `np.asarray(result_dict["cluster_labels"])` and `int(result.n_clusters)`
- [x] 1.9 `test_internal_pca_receives_same_trait_selection` — spy on
  `sleap_roots_analyze.perform_pca_analysis`; call with an explicit `trait_columns` subset;
  assert the spy's positional/keyword frame argument's columns equal that subset
- [x] 1.10 `test_degenerate_internal_pca_is_assumption_violated_no_run_no_leaked_figures` —
  force the internal `perform_pca_analysis` call to raise (monkeypatch it to raise
  `ValueError`); assert `BloomMCPError(assumption_violated)`; assert
  `store.list_runs(experiment, "clustering") == []`; assert
  `matplotlib.pyplot.get_fignums() == []`. Run at least once with `method="hierarchical"`
  specifically (the branch with no `data_processed` fallback available)
- [x] 1.11 `test_figure_cleanup_get_fignums_empty_on_success` — after a successful
  `include_plots=True` call (any method), assert `plt.get_fignums() == []`
- [x] 1.12 `test_figure_cleanup_get_fignums_empty_on_invalid_key` — after an invalid-key
  error, assert `plt.get_fignums() == []`
- [x] 1.13 `test_figure_cleanup_on_partial_plotter_failure_no_run_committed` — patch
  `_clustering_plot_calls` so the second requested plotter raises mid-generation (mirrors
  `test_figure_cleanup_get_fignums_empty_on_partial_plotter_failure` in
  `test_pca_analysis_tool.py`); assert `plt.get_fignums() == []` **and**
  `store.list_runs(experiment, "clustering") == []` (the PCA precedent only checks fignums
  here — add the run-count assertion since it isn't covered elsewhere either)
- [x] 1.14 `test_default_path_never_executes_an_import_matplotlib_statement` — name matches
  `umap_analysis`'s corrected framing, not the disproven PCA-precedent name. Docstring notes
  this does NOT prove matplotlib is absent from `sys.modules` (it's already resident via this
  module's own top-level `sleap_roots_analyze` import — see design.md); it proves only that
  the `include_plots=False` path never itself executes a fresh `import matplotlib` statement.
  `monkeypatch.setitem(sys.modules, "matplotlib", None)`; call `_run()`; assert no
  `ImportError`
- [x] 1.15 `test_plot_outputs_included_in_schema_round_trip` — PNG keys survive
  `model_dump_json`/`model_validate`
- [x] 1.16 `test_create_cluster_size_barplot_on_gmm_single_component_collapse` — force GMM's
  `n_components` auto-select to land on 1 (mirrors the existing
  `test_gmm_autoselect_may_collapse_to_one_component` fixture setup); `include_plots=True,
  plots=["create_cluster_size_barplot"]`; assert success and a valid PNG, not a raise

No changes needed to `test_plots_helpers.py` — `_plots.py` is reused verbatim, already covered.

## 2. Implementation

`bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/clustering.py`:

- [x] 2.1 Add `include_plots: bool = False` and `plots: list[str] | None = None` to
  `ClusteringParams`, with a `Field(description=...)` that discloses matplotlib's existing
  transitive residency (mirrors `umap_analysis`'s `include_plots` Field description, not
  `pca_analysis`'s — see design.md's corrected "Lazy matplotlib import" section)
- [x] 2.2 Add `_CLUSTERING_CATALOG_KEYS` frozenset: `create_cluster_scatter_pca`,
  `create_cluster_size_barplot`
- [x] 2.3 Add `_clustering_plot_calls(result_dict, result, frame, trait_cols)` returning
  zero-arg callables for both keys, lazily importing plotters (see design.md's code sample)
- [x] 2.4 `create_cluster_scatter_pca`'s callable computes `pca_result` via an internal,
  non-persisted `perform_pca_analysis(frame.df[trait_cols])` call, translating
  `(ValueError, KeyError, RuntimeError, TypeError)` to `assumption_violated` (mirrors
  `umap_analysis`'s `_top_traits` precedent)
- [x] 2.5 `create_cluster_size_barplot`'s callable uses
  `np.asarray(result_dict["cluster_labels"])` / `int(result.n_clusters)` directly
- [x] 2.6 Retain `result_dict` in scope after wrapping into the typed result for all three
  method branches (kmeans/gmm already do; verify hierarchical too)
- [x] 2.7 Wrap the existing `tempfile.TemporaryDirectory` persistence block in `try/finally`:
  figure validation + generation before the block (so an invalid key never reaches
  `create_run`), `close_figures` in `finally`
- [x] 2.8 Merge generated PNGs into the existing `outputs` dict passed to `store.commit`
- [x] 2.9 Lazy `import matplotlib; matplotlib.use("Agg")` inside `if params.include_plots:`
  only
- [x] 2.10 Update the module's top-of-file docstring with an "Optional plots (#601)" section
  mirroring `pca_analysis`/`umap_analysis`'s existing docstring sections — including the
  corrected matplotlib-residency framing (no fresh Tier-0 claim; see design.md)

## 3. Validate

- [x] 3.1 `openspec validate add-bloommcp-clustering-plots --strict` — run after every edit
  to this change directory, not just once at the end
- [x] 3.2 `cd bloommcp && uv run pytest tests/tools/test_clustering_tool.py -x` — all new and
  existing tests pass
- [x] 3.3 `cd bloommcp && uv run pytest tests/ -x` — full suite, no regressions
- [x] 3.4 `cd bloommcp && uv run black --check src/ tests/` and `uv run ruff check src/
  tests/`

## 4. PR review follow-up (5-lens subagent review of #668)

- [x] 4.1 **Blocking**: forward `standardize=params.standardize` into the internal
  `perform_pca_analysis` call (`_clustering_plot_calls` gained a keyword-only
  `standardize` param) — previously the plotted PCA projection always standardized
  regardless of what the caller requested for the actual clustering fit, so the plot's
  geometry could disagree with the real fit. Regression test:
  `test_internal_pca_receives_the_requested_standardize_flag` (parametrized True/False)
- [x] 4.2 Narrow `_scatter_pca`'s exception tuple from the copy-pasted
  `(ValueError, KeyError, RuntimeError, TypeError)` (borrowed from `umap_analysis`'s
  differently-justified `_top_traits`) to `(ValueError, np.linalg.LinAlgError)` — derived
  from what `perform_pca_analysis` actually raises (mirrors `pca_analysis`'s own narrower
  catch of the same function), plus the one genuinely reachable non-`ValueError` failure
  mode. Regression test: `test_internal_pca_linalg_error_is_assumption_violated`
- [x] 4.3 Add a module-level `logger` (previously absent) and log before translating the
  internal PCA failure to `assumption_violated`, mirroring `umap_analysis`'s identical
  path — makes a genuine upstream failure distinguishable from routine degenerate input
  server-side
- [x] 4.4 Fix spec.md's "Plotter failure surfaces as tool_error" scenario — traced
  `BloomMCPError.from_exception` against clustering's declared `errors=` tuple: an
  undeclared plotter exception actually maps to `internal_error`, not `tool_error`.
  Renamed the scenario and pinned the real code with
  `test_figure_cleanup_on_partial_plotter_failure_no_run_committed`'s new `.code`
  assertion
- [x] 4.5 Add `test_commit_failure_after_pngs_staged_surfaces_as_tool_error` — proves the
  already-staged PNGs are real bytes at the moment `store.commit` fails, and that no run
  is committed
- [x] 4.6 Add `test_single_trait_scatter_pca_degrades_gracefully_through_the_tool` —
  proves design.md's graceful-degradation claim (a <2-component PCA projection renders a
  placeholder figure, not a raise) through this tool's own wrapping, not just against the
  upstream library directly
- [x] 4.7 Add `test_plots_ignored_list_with_include_plots_false_no_error` — explicit
  one-line coverage for `plots=[]` with `include_plots=False`
- [x] 4.8 Deterministic plot-generation order: `keys_to_generate` now uses
  `sorted(_CLUSTERING_CATALOG_KEYS)` instead of `list(frozenset(...))` (whose iteration
  order depends on Python's randomized hash seed) when `plots=None`
- [x] 4.9 Reverted an unrelated black-reformat hunk to a pre-existing assert in
  `test_clustering_tool.py` that had leaked into the original diff — confirmed (against
  `origin/staging` directly) it was pre-existing lint debt unrelated to this change, not
  introduced by it, so left as-is rather than fixed here
- Not fixed, deliberately out of scope (see PR discussion): `fig.savefig()`/`store.commit`
  mid-loop tempdir orphaning is an architectural pattern inherited identically from
  `pca_analysis`/`umap_analysis` — fixing it only here would create asymmetry with the
  siblings; a proper fix is a separate cross-cutting change touching all three tools
