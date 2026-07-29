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

So this change advances #305 AC5 **partially** (it does not fully satisfy it): it delegates
the two single-module-gated analysis paths to `sleap_roots_analyze`, deletes the vendored
copies, prunes the two now-unimported deps, and confirms **every remaining declared dep is
imported by shipped code**. That is the **sufficient** half of AC5 ("no missing dep") — met
and CI-gated. The **necessary** half ("no extra unnecessary package", i.e. minimizing
`scikit-learn` / `scipy` / `matplotlib` / `seaborn`) is **deferred** to the shipped-viz
refactor; **#315 stays open** to track it. This is a clean partial slice, not a close-out.

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
  the golden within stated tolerance — gated *before* deletion. The UMAP gate asserts a
  **structural** invariant (a Procrustes/kNN-overlap check against a recorded embedding),
  not merely shape + within-process self-equality, so a delegation with wrong
  `n_neighbors`/`min_dist`/`init` cannot pass silently. The heritability golden is labeled
  as a **characterization snapshot of `0.1.0a2`** (drift gate), not an independently
  validated scientific value, until reconciled to a real reference.
- **Tool-wrapper drift guard:** add FastMCP-Client (or direct-call) assertions on the two
  affected MCP tools (`viz_tools`, `dimred`/`stats`) that pin the delegated return's
  **keys/units** — in particular that `var_genetic` / `var_residual` are present — so a
  future library key-rename surfaces as a test failure instead of a silently zero-filled
  variance decomposition shipped to a scientist.
- **Regression guard:** add a shipped-code import guard asserting no shipped
  `bloom_mcp/*` module imports `statsmodels` or `umap` (so the prune can't silently
  regress), and rely on the existing clean-env wheel-import CI gate
  (`add-bloommcp-wheel-import-ci`, `python-audit` job) to catch any missing runtime dep.
- **Drift-coverage scope:** the durable oracle runs on the balanced `turface_19` path; the
  zero-variance / mixed-model-failure / small-N (`len < 4`) / NaN branches of the deleted
  `trait_statistics.py` are **not** exercised by the fixture. Either parametrize the oracle
  over an edge-case fixture or explicitly document that drift protection covers the balanced
  path + the version pin only.
- **Reconcile #305 AC5 (partial):** record that every remaining declared dependency is
  imported by shipped code — AC5's *sufficient* half. Do **not** tick AC5 as fully met and
  do **not** close #315: the *necessary* half (sklearn/scipy/matplotlib/seaborn
  minimization) is the deferred viz refactor that #315 still tracks. (`Refs #315` does not
  auto-close, and a staging merge would not fire a close either — so no mechanical
  over-close risk; the fix is purely to not *claim* AC5 fully met.)
- **Out of scope (documented follow-ups):** delegating `pca.py` / `clustering.py` /
  `outlier_detection.py` / `cross_experiment_correlations.py` (frees no additional dep —
  scikit-learn/scipy are held by shipped viz), and refactoring shipped visualization to
  drop its direct scikit-learn/scipy usage (the only way to prune those two; matplotlib/
  seaborn stay regardless).

## Impact

- Affected specs: **bloommcp-packaging** (extends the Tier 0 capability; *partially*
  reconciles its deferred AC5 — the sufficient half — and satisfies the Tier 0 "Additive
  Dependency Set" conditional clause for the two pruned deps). This change assumes
  `add-bloommcp-package-baseline` archives first.
- Affected code:
  - `bloommcp/src/bloom_mcp/umap_embedding.py` (deleted), `trait_statistics.py` (deleted)
  - `bloommcp/src/bloom_mcp/tools/workflows/dimred.py`,
    `tools/workflows/stats.py`, `tools/viz_tools.py` (delegate to `sleap_roots_analyze`)
  - `bloommcp/pyproject.toml` (prune 2 deps), `bloommcp/uv.lock`, root `uv.lock`
  - `bloommcp/tests/` — extend the oracle; add the import guard
  - `bloommcp/pyproject.toml` `[tool.ruff.lint.per-file-ignores]` for the two deleted
    modules (remove the now-dead ignores)
- Related: #313 (Tier 0), #305 (AC5 — partially advanced, not closed), #315 (full
  vendored-stack delegation — **stays open** for the deferred viz refactor), #306 /
  `add-bloommcp-contract-layer` (oracle = `perform_*`), `add-bloommcp-wheel-import-ci`
  (the clean-env gate).
