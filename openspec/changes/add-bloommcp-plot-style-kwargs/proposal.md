## Why

`umap_analysis` and `pca_analysis` (#425/#308) call their catalog plotters with hardcoded
scatter styling — `_umap_plot_calls`/`_pca_plot_calls` never expose the upstream
`sleap_roots_analyze` plotters' own `cmap`/`point_size`/`alpha` kwargs. Scientists preparing
figures for papers, posters, or presentations need control over these visual properties
(color scheme, point density readability, overplotting transparency) the same way #661 gave
them control over font family/size.

## What Changes

Verified against the installed `sleap_roots_analyze==0.1.0a5` (`visualization.py`) which
catalog plotters actually accept `cmap`/`point_size`/`alpha` — support is **not** uniform, so
(unlike #661's generic font post-processing) this is wired per-plotter, not per-figure:

| Plotter | `cmap` | `point_size` | `alpha` |
| --- | --- | --- | --- |
| `create_umap_single_trait` | yes | yes | yes |
| `create_umap_colored_by_top_traits` | no | no | no |
| `create_pca_biplot` | no | no | yes |
| `create_pca_scree_plot` | no | no | no |
| `create_feature_contribution_plot` | no | no | no |
| `create_feature_contribution_heatmap` | no | no | no |

- **MODIFY** `UMAPAnalysisParams`: add `plot_cmap: str | None = None`,
  `plot_point_size: float | None = None` (`gt=0`), `plot_alpha: float | None = None`
  (`ge=0.0, le=1.0`). Forwarded into `_umap_plot_calls`'s `create_umap_single_trait` call only
  — the sole UMAP catalog plotter whose signature accepts any of the three. Each is passed
  only when set (`None` preserves the plotter's own default); has no effect on
  `create_umap_colored_by_top_traits`, documented in the field description.
- **MODIFY** `PCAAnalysisParams`: add `plot_alpha: float | None = None` (`ge=0.0, le=1.0`)
  only. Forwarded into `_pca_plot_calls`'s `create_pca_biplot` call only — the sole PCA
  catalog plotter whose signature accepts `alpha`. **No** `plot_cmap`/`plot_point_size` fields
  are added to `PCAAnalysisParams`: none of the four PCA catalog plotters accept either kwarg
  today, so adding them would be dead params with no plotter to reach — out of scope until an
  upstream `sleap_roots_analyze` release adds that support.
- **Out of scope**: the legacy string-returning plot tools (`plot_trait_histograms`,
  `plot_trait_boxplots`, `plot_correlation_matrix` — held for #466, per #661's precedent);
  `clustering` (no plots yet, #601); adding `cmap`/`point_size` support to PCA upstream
  (would require a `sleap-roots-analyze` change, not a `bloommcp` one); `figsize` — every one
  of the six catalog plotters accepts it uniformly (unlike `cmap`/`point_size`/`alpha`), but
  issue #662 scopes this change to colormap/point-size/transparency only. Adding `figsize` is
  a reasonable, low-effort follow-up if wanted, tracked as a separate issue rather than
  silently bundled into this one.

## Impact

- Affected specs: `bloommcp-umap-analysis-tool`, `bloommcp-pca-analysis-tool`
- Affected code:
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/umap_analysis.py` — add the three
    fields to `UMAPAnalysisParams`, forward into `_umap_plot_calls`
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/pca_analysis.py` — add
    `plot_alpha` to `PCAAnalysisParams`, forward into `_pca_plot_calls`
  - `bloommcp/tests/tools/test_umap_analysis_tool.py`,
    `test_pca_analysis_tool.py` — new tests confirming per-plotter kwarg forwarding and the
    no-op case for plot keys that don't accept these kwargs; both files' existing
    `test_figure_cleanup_get_fignums_empty_on_partial_plotter_failure` tests replace
    `_umap_plot_calls`/`_pca_plot_calls` with a fixed-arity `_patched` wrapper and must be
    updated to accept/forward the new keyword-only style args (see design.md)
- No breaking change: all new fields default to `None`; omitting them reproduces today's
  hardcoded-default figures exactly.
- No new dependency: forwarding existing kwargs already accepted by the vendored
  `sleap_roots_analyze` plotters.
- **Known merge-conflict risk, not a functional dependency**: this branch is cut from
  `origin/main` (not from #661's branch) and only touches `UMAPAnalysisParams`/
  `PCAAnalysisParams`/`_umap_plot_calls`/`_pca_plot_calls`, so it can be implemented, tested,
  and merged fully independently of #661 (open PR #665, not yet merged). Both changes insert
  new fields at the same location in the same two Pydantic models, so whichever of #661/#662
  merges second will hit an ordinary, easily-resolved textual conflict there (two sibling
  field blocks, not overlapping logic) — flagged here so it isn't a surprise at merge time,
  not because either change should block on the other.
- This proposal's `ADDED Requirements` build on top of two still-open (not yet archived)
  sibling changes — `add-bloommcp-umap-analysis-tool` (the UMAP tool's own spec) and
  `add-pca-analysis-plots` (`pca_analysis`'s `include_plots`/`plots` support) — both of which
  are already fully implemented in the code this proposal modifies. No action needed here;
  noted so a reviewer isn't surprised that `bloommcp-umap-analysis-tool`'s baseline spec
  doesn't yet exist under `openspec/specs/`.
