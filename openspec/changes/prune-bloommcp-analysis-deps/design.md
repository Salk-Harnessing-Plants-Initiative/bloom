## Context

`add-bloommcp-package-baseline` (Tier 0) moved bloommcp to an installable package via
`git mv`, keeping the vendored analysis stack and explicitly deferring delegation +
dependency pruning to "a later tier with its own oracle gate". Its **Additive Dependency
Set** requirement states *"No dependency that is still imported by shipped code SHALL be
removed"*, and its **Supabase-Free Test Stack with Cross-Tier Oracle** requirement
commits the turface_19 fixture + independently-recorded golden values, asserting that
**both** external `sleap_roots_analyze` and shipped `bloom_mcp` reproduce them.

This change is the AC5-focused slice of that deferred work: it delegates the two analysis
paths whose deletion actually prunes a declared dependency, and uses the existing oracle
as the behavior-preservation gate.

## Goals / Non-Goals

- **Goals**
  - Satisfy #305 AC5: every declared `bloommcp` runtime dep is imported by shipped code.
  - Prune `statsmodels` and `umap-learn` (the only single-analysis-module-gated deps).
  - Preserve MCP tool behavior (external signatures + output schema) and numerical output
    (within the oracle tolerance).
- **Non-Goals**
  - Delegating `pca` / `clustering` / `outlier_detection` / `cross_experiment_correlations`
    — frees no dep (scikit-learn/scipy held by shipped viz); a separate dedup tier.
  - Refactoring shipped visualization to drop direct scikit-learn/scipy — the only way to
    prune those two; out of scope and behavior-risky.
  - Shrinking the install (impossible — `sleap-roots-analyze` pulls the same libs
    transitively).

## Decisions

- **Decision: delegate exactly `umap_embedding.py` and `trait_statistics.py`.** These are
  the only modules whose removal makes a declared dep unimported by shipped code
  (`umap-learn` and `statsmodels` respectively). `umap_embedding.perform_umap_analysis`
  has a byte-compatible signature with `sleap_roots_analyze.perform_umap_analysis`, so the
  UMAP path is a drop-in. The trait-statistics call sites
  (`stats.py` → `calculate_trait_statistics`, `viz_tools.py` →
  `calculate_heritability_estimates(..., genotype_col=, replicate_col=)`) match the
  `sleap_roots_analyze.statistics` signatures; the delegation task verifies return-key
  compatibility and adapts the thin tool wrappers if the vendored copy drifted.

- **Decision: oracle-gate the delegation before deleting.** Extend the Tier 0 oracle to
  assert the delegated heritability + UMAP outputs reproduce the committed golden within
  the stated tolerance, with the assertion in place *before* the vendored modules are
  removed (so a drift between the vendored copy and `sleap_roots_analyze` is caught, not
  silently shipped). This honors the baseline's "its own oracle gate" condition.

- **Decision: ADDED requirements, not MODIFIED.** The baseline `bloommcp-packaging` spec
  is still in-flight (un-archived), so a MODIFIED/RENAMED delta against its requirements
  would dangle until it archives. This change instead ADDs two requirements that
  explicitly reconcile #305 AC5 and supersede the baseline's additive/deferred stance for
  the two pruned deps. Ordering: `add-bloommcp-package-baseline` archives first; a future
  archive of this change folds the new requirements into `specs/bloommcp-packaging/`.

- **Decision: a shipped-code import guard.** A unit test (mirroring the repo's existing
  `test_ci_workflow_uv_conventions.py` regression-guard pattern) asserts that no shipped
  `bloom_mcp/*` module imports `statsmodels` or `umap`. This makes the prune
  self-defending; the existing clean-env wheel-import gate is the backstop for *missing*
  runtime deps.

## Risks / Trade-offs

- **Vendored copy drifted from `sleap_roots_analyze` → delegation changes numbers.**
  Mitigation: the oracle gate runs before deletion; any divergence beyond tolerance fails
  the PR and is reconciled (adapt the wrapper, or record a reviewer-approved golden
  update) rather than silently shipped.
- **A retained dep turns out to be viz-only and removable later.** Accepted: this change
  is scoped to the two unambiguous prunes; scikit-learn/scipy removal is a separate viz
  refactor tracked as a follow-up.
- **Tool output schema accidentally changes** (e.g. `sleap_roots_analyze` returns extra
  dict keys). Mitigation: the tool wrappers project only the keys they already expose;
  FastMCP Client tests assert the tool output schema is unchanged.

## Migration Plan

Additive-then-subtractive, CI-green at each step: (1) extend the oracle to the delegated
paths and confirm it passes against the *current* vendored code; (2) repoint the call
sites to `sleap_roots_analyze` and re-run the oracle; (3) delete the vendored modules +
their ruff per-file-ignores; (4) prune the two deps and re-lock; (5) add the import guard;
(6) clean-env wheel import + full suite. Rollback is reverting the prune commit (deps
return) and restoring the two modules.

## Open Questions

- Does `viz_tools.calculate_heritability_estimates` rely on any return key that
  `sleap_roots_analyze` names differently? Resolved in task 1 by diffing the two modules'
  return contracts on the turface_19 fixture before repointing.
