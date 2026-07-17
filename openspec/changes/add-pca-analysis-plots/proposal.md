## Why

`pca_analysis` (#377) returns numeric summaries and artifact links but no figures. Scientists
need to *see* the PCA — scree, biplot, and feature contributions — without leaving the MCP
conversation. The upstream `sleap_roots_analyze` plotters already ship in `0.1.0a4` and are
fully tested; this change surfaces them as an optional, off-by-default addition to `pca_analysis`
with no impact on existing callers.

## What Changes

- **MODIFY** `pca_analysis` input model: add `include_plots: bool = False` and
  `plots: Optional[list[str]] = None` to `PCAAnalysisParams`. When `include_plots` is `False`
  (default), behavior is **identical** to today — no figures generated, no run changes. A
  `plots` value with `include_plots=False` is silently ignored. An empty `plots=[]` with
  `include_plots=True` is rejected as `invalid_input`.
- **ADD** four-key plot catalog: `create_pca_scree_plot`, `create_pca_biplot`,
  `create_feature_contribution_plot`, `create_feature_contribution_heatmap`. Each maps to the
  corresponding `sleap_roots_analyze` plotter; the exact per-plotter call sites are documented
  in `design.md`. `create_variance_decomposition_plot` is **excluded** — it requires a
  heritability comparison frame (`compare_trait_heritabilities` output) that is not derivable
  from PCA outputs; it is tracked for a future change.
- **ADD** validation-before-commit guard: unknown, duplicate, or empty `plots` values return
  `invalid_input` before `create_run` is called, so no run is committed on bad input. Figures
  are generated before `create_run` and wrapped in `try/finally` (see `design.md` for the
  `with tempfile.TemporaryDirectory` nesting order).
- **ADD** `tools/_plots.py` shared helper: a tool-agnostic module exposing
  `validate_plot_keys`, `generate_figures(resolved_calls)`, and `close_figures`. The
  PCA-specific dispatch (which plotter, which args) stays in `pca_analysis_tool.py`; the
  helper only receives zero-arg callables. This makes the module directly reusable by the
  UMAP tool (#425) with no signature changes.
- **MODIFY** `pca_analysis` persistence: PNGs are persisted into the existing PCA run and
  returned as additional entries in the existing `outputs: dict[str, str]` result field
  (symmetric with `remove_outliers`; no new `plot_links` field added). Every figure is closed
  in `finally` regardless of success or failure.
- **Raw dict retained**: `result_dict` from `perform_pca_analysis` must be kept in local scope
  alongside `PCAResult` so plotters can receive it directly (all four plotters accept
  `pca_results: Dict`, not a `PCAResult` instance).

## Dependency note

Returned entries in `outputs` are object keys, not signed URLs. Opening them as clickable
links depends on #388 Part 2 (`create_signed_url` on the `StorageBackend` seam). Until that
lands, the keys identify the artifact location but cannot be opened directly from the agent
conversation.

## Impact

- Affected specs: `bloommcp-pca-analysis-tool`
- Affected code:
  - `bloommcp/src/bloom_mcp/tools/pca_analysis_tool.py` — add `include_plots`/`plots` params,
    retain `result_dict`, add `_PCA_PLOT_CATALOG`, dispatch plots, restructure persistence
    into `try/finally`
  - `bloommcp/src/bloom_mcp/tools/_plots.py` — new shared helper (`validate_plot_keys`,
    `generate_figures`, `close_figures`)
  - `bloommcp/tests/tools/test_pca_analysis_tool.py` — new and updated test cases
  - `bloommcp/tests/tools/test_plots_helpers.py` — new unit tests for `_plots.py` in isolation
- No dependency change: `sleap-roots-analyze>=0.1.0a4` already pinned; all four plotters
  importable today.
- No breaking change: `include_plots` defaults to `False`; all existing callers are unaffected.
