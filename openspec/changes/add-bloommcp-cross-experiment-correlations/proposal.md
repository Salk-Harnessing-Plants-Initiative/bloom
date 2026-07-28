## Why

`devendor-bloommcp-analysis` (PR #438) deleted the 8 legacy correlation tools
(`tools/correlation_tools.py` + vendored `bloom_mcp/cross_experiment_correlations.py`)
rather than rewiring them onto `sleap_roots_analyze.cross_experiment_analysis`, because
the upstream module shares function names but has a genuinely different contract
(columns, `min_samples` semantics, no significance flags, different sort order) —
rewiring would have silently changed the reported numbers (design.md D2 of that
change). Issue #489 is the fresh design that deletion deferred: bloommcp has had zero
cross-experiment correlation capability since, and the capability is real — comparing
trait correlations between root-phenotyping modalities (e.g. cylinder vs. turface) is
a genotype-comparison workflow the old tools served.

## What Changes

- Add one new granular consumer tool, `cross_experiment_correlations`, in
  `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/`, registered alongside
  `pca_analysis`/`clustering`/`umap_analysis` (namespaced
  `sleap_roots_cross_experiment_correlations`).
- It is bloommcp's **first two-experiment-input** granular tool — every existing
  consumer (`qc_clean`, `pca_analysis`, `remove_outliers`, `clustering`,
  `umap_analysis`) takes one `experiment` filename. This proposal resolves how a
  two-experiment consumer fits the existing single-experiment `ResultStore`/
  `Provenance` shape **without changing those shared ports** (see `design.md`).
- Both experiments are consumed the same way every existing tool consumes cleaned
  data — via `ExperimentReader.load_experiment(require_clean=True)` — so the read
  path never touches raw CSVs directly and never bypasses the Supabase-backed port
  (the exact contract mismatch #489 identifies in upstream's own
  `load_and_align_experiments`, which is **not** used here).
- Delegates all correlation math to upstream, tested `sleap_roots_analyze` entry
  points: `calculate_genotype_means`, `calculate_cross_experiment_correlations`,
  `identify_significant_correlations`, `summarize_correlation_results`. The MCP
  contains no correlation math of its own.
- **Out of scope, explicitly deferred** (named here so they aren't lost, each a
  candidate follow-up issue):
  - `calculate_per_trait_correlations` — single-trait-pair, individual-sample-level
    correlation (a different granularity from the genotype-mean-level tool proposed
    here).
  - `calculate_cross_experiment_correlations_extended` — multi-statistic
    (mean/median/std) correlation combinations.
  - `calculate_correlation_confidence_intervals` — a real upstream inconsistency
    found during this design (see `design.md`); deferred rather than worked around
    speculatively.
  - All plotting (`create_cross_experiment_heatmap`, `create_top_correlations_plot`,
    `create_scatter_plot_grid`, `create_joint_plot`, `create_genotype_boxplots`,
    `create_correlation_summary_plot`) — mirrors `pca_analysis`'s `include_plots`
    flag landing in a later, separate PR (#426) rather than with the first cut.
  - Power analysis (`minimum_detectable_correlation`/`achieved_power` — was the old
    `check_correlation_power` tool).
  - Redundant-trait identification (`cluster_correlated_traits`/
    `select_cluster_representatives` — was old `find_redundant_traits`/
    `compare_trait_across_experiments`).
  - `sleap_roots_analyze.pc_correlations` and the entire `cross_platform_prediction`
    module — already excluded by issue #489 itself as separate, larger upstream
    work.

## Impact

- Affected specs: `bloommcp-cross-experiment-correlations-tool` (new capability).
- Affected code:
  - New: `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/cross_experiment_correlations.py`
  - Modified: `bloommcp/src/bloom_mcp/sections/sleap_roots/__init__.py` (register the
    new tool)
  - Tests: a new `bloommcp/tests/tools/test_cross_experiment_correlations_tool.py`
    following the existing `test_clustering_tool.py`/PCA test conventions; a
    positive-registration assertion added wherever the tool-surface drift guard
    (`test_devendor_invariants.py`) enumerates live tool names.
- No changes to `ExperimentReader`, `ResultStore`, `Provenance`, or the manifest
  schema — the two-experiment shape is encoded inside existing single-string fields
  (see `design.md` Decision D1), not by extending shared contract/port types.
- No dependency changes: `sleap-roots-analyze>=0.1.0a5` (already a dependency) is
  the only import; no new third-party package (FDR correction's `statsmodels`
  dependency is upstream's, inside `sleap_roots_analyze`, not a new bloommcp
  dependency).
