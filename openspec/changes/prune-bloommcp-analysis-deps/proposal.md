## Why

Tier 0 (`add-bloommcp-package-baseline`, #313) was deliberately additive: it added
`sleap-roots-analyze` + `sleap-roots-contracts[pandas]` while **retaining** the full
vendored analysis stack, because the not-yet-delegated `bloom_mcp/*` modules still import
those libraries directly. That left **#305 AC5** ("dependencies are necessary and
sufficient without any extra unnecessary packages") only partially met — the package
ships packages that exist specifically to be removed once delegation lands (#315, a
pre-PyPI-publish gate).

Investigation shows only **two** declared deps are *cleanly* prunable via analysis
delegation, because shipped MCP visualization tools (`viz_tools`, `correlation_tools`,
`tools/workflows/{clustering,dimred}`) import the rest **directly** for rendering:

| Dep | Gated only by deletable analysis modules? | Action |
| --- | --- | --- |
| `statsmodels` | Yes — only `trait_statistics.py` | **prune** |
| `umap-learn` | Yes — only `umap_embedding.py` | **prune** |
| `scikit-learn` | No — also `tools/workflows/clustering.py` (PCA-for-plot) + viz | retain |
| `scipy` | No — also viz tools (`dendrogram`, `chi2`) | retain |
| `matplotlib` / `seaborn` | No — shipped viz tools import directly | retain |

So AC5 is reached by delegating the two single-module-gated analysis paths to
`sleap_roots_analyze`, deleting the vendored copies, pruning the two now-unimported deps,
and confirming **every remaining declared dep is imported by shipped code**.

(Note: `sleap-roots-analyze` itself hard-depends on scikit-learn/scipy/statsmodels/
umap-learn/matplotlib/seaborn, so pruning bloommcp's *direct* deps does **not** shrink the
install — they remain transitive. This change is about AC5's "necessary-and-sufficient
**declared** deps", not install size.)

## What Changes

- **Delegate the UMAP-embedding path** to `sleap_roots_analyze.perform_umap_analysis`
  (signature is identical to the vendored copy) and **delete**
  `src/bloom_mcp/umap_embedding.py`. Update its sole call site
  (`tools/workflows/dimred.py`).
- **Delegate the trait-statistics / heritability path** to
  `sleap_roots_analyze` (`calculate_trait_statistics`, `perform_anova_by_genotype`,
  `calculate_heritability_estimates`, …) and **delete**
  `src/bloom_mcp/trait_statistics.py`. Update its call sites
  (`tools/workflows/stats.py`, `tools/viz_tools.py`).
- **Prune** `statsmodels` and `umap-learn` from `bloommcp/pyproject.toml`; re-lock
  `bloommcp/uv.lock` + the root lock.
- **Behavior preservation:** extend the existing cross-tier oracle (turface_19 fixture +
  golden values from Tier 0) to assert the delegated heritability + UMAP outputs reproduce
  the golden within stated tolerance — gated *before* deletion.
- **Regression guard:** add a shipped-code import guard asserting no shipped
  `bloom_mcp/*` module imports `statsmodels` or `umap` (so the prune can't silently
  regress), and rely on the existing clean-env wheel-import CI gate
  (`add-bloommcp-wheel-import-ci`, `python-audit` job) to catch any missing runtime dep.
- **Reconcile #305 AC5:** every remaining declared dependency is imported by shipped
  code; check the AC5 box.
- **Out of scope (documented follow-ups):** delegating `pca.py` / `clustering.py` /
  `outlier_detection.py` / `cross_experiment_correlations.py` (frees no additional dep —
  scikit-learn/scipy are held by shipped viz), and refactoring shipped visualization to
  drop its direct scikit-learn/scipy usage (the only way to prune those two; matplotlib/
  seaborn stay regardless).

## Impact

- Affected specs: **bloommcp-packaging** (extends the Tier 0 capability; reconciles its
  deferred AC5 and the "Additive Dependency Set" stance). This change assumes
  `add-bloommcp-package-baseline` archives first.
- Affected code:
  - `bloommcp/src/bloom_mcp/umap_embedding.py` (deleted), `trait_statistics.py` (deleted)
  - `bloommcp/src/bloom_mcp/tools/workflows/dimred.py`,
    `tools/workflows/stats.py`, `tools/viz_tools.py` (delegate to `sleap_roots_analyze`)
  - `bloommcp/pyproject.toml` (prune 2 deps), `bloommcp/uv.lock`, root `uv.lock`
  - `bloommcp/tests/` — extend the oracle; add the import guard
  - `bloommcp/pyproject.toml` `[tool.ruff.lint.per-file-ignores]` for the two deleted
    modules (remove the now-dead ignores)
- Related: #313 (Tier 0), #305 (AC5), #306 / `add-bloommcp-contract-layer` (oracle =
  `perform_*`), `add-bloommcp-wheel-import-ci` (the clean-env gate).
