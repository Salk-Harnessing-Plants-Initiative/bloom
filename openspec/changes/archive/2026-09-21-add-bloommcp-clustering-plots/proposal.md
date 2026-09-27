## Why

`clustering` (#309, #422) persists `labels.csv` + `cluster_result.json` but has no plot output,
unlike its sibling granular tools `pca_analysis` (#426/`add-pca-analysis-plots`) and
`umap_analysis` (#425/`add-bloommcp-umap-analysis-tool`), which both added an opt-in
`include_plots=True` param generating catalog figures persisted alongside their CSV/JSON
outputs. A scientist running `clustering` has no visual way to inspect cluster
separation/quality without exporting `labels.csv` and plotting it themselves outside bloommcp.
Closes #601.

## What Changes

- **MODIFY** `clustering` input model: add `include_plots: bool = False` and
  `plots: Optional[list[str]] = None` to `ClusteringParams`, matching the exact convention
  `pca_analysis`/`umap_analysis` established. When `include_plots` is `False` (default),
  behavior is **identical** to today — no figures generated, no run changes. A `plots` value
  with `include_plots=False` is silently ignored. An empty `plots=[]` with `include_plots=True`
  is rejected as `invalid_input`.
- **ADD** two-key plot catalog, uniform across all three `method` values (`kmeans`, `gmm`,
  `hierarchical`):
  - `create_cluster_scatter_pca` — 2D scatter of cluster assignments over a PCA projection of
    the certified-clean trait selection (the issue's suggested candidate). Computed via an
    internal, non-persisted `perform_pca_analysis` call so the key works identically for all
    three methods, including `hierarchical` (see design.md's Decision for why that method's
    raw `result_dict` alone isn't enough).
  - `create_cluster_size_barplot` — bar chart of per-cluster sample counts, built directly from
    `result.cluster_labels` / `result.n_clusters` (no PCA, no extra computation); trivially
    uniform across all three methods.
  - No style kwargs (`plot_alpha`/`plot_cmap`/`plot_point_size`, #662) are added: neither
    plotter's upstream signature accepts them (see design.md's per-plotter support table).
- **ADD** validation-before-commit guard (reusing `_plots.py` verbatim, no changes to that
  module): unknown, duplicate, or empty `plots` values return `invalid_input` before
  `create_run` is called, so no run is committed on bad input. Figures are generated before
  `create_run` and wrapped in `try/finally` (mirrors `pca_analysis`'s tempdir/figure nesting).
- **MODIFY** `clustering` persistence: PNGs are persisted into the existing clustering run and
  returned as additional entries in the existing `outputs: dict[str, str]` result field
  (symmetric with `pca_analysis`/`umap_analysis`; no new result field). Every figure is closed
  in `finally` regardless of success or failure.
- **Out of scope** (matches the issue): no new plotting logic beyond what
  `sleap_roots_analyze.cluster_visualization` already ships (`create_cluster_scatter_pca`,
  `create_cluster_size_barplot`, `create_silhouette_plot`, `create_bic_aic_comparison_plot`,
  `create_dendrogram`, `create_distance_distribution_plot`). The latter three are excluded —
  see design.md's Decisions for why each does not fit uniformly across all three methods
  without additional plumbing this change does not introduce.

## Impact

- Affected specs: `bloommcp-clustering-tool`
- Affected code:
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/clustering.py` — add
    `include_plots`/`plots` params, add `_CLUSTERING_CATALOG_KEYS`, add
    `_clustering_plot_calls`, restructure persistence into `try/finally`
  - `bloommcp/tests/tools/test_clustering_tool.py` — new plot test cases (mirrors
    `test_pca_analysis_tool.py`'s plot section)
  - No changes to `bloommcp/src/bloom_mcp/tools/_plots.py` (reused verbatim)
- No dependency change: `sleap-roots-analyze>=0.1.0a5` already pinned; both catalog plotters
  (`sleap_roots_analyze.cluster_visualization`) are importable today.
- No breaking change: `include_plots` defaults to `False`; all existing callers are unaffected.
