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
  - *Partially* advance #305 AC5: meet its **sufficient** half — every declared `bloommcp`
    runtime dep is imported by shipped code — via the two single-module-gated prunes. The
    **necessary** half (minimizing the viz-held deps) is explicitly a Non-Goal here.
  - Prune `statsmodels` and `umap-learn` (the only single-analysis-module-gated deps).
  - Preserve MCP tool behavior (external signatures + output schema) and numerical output
    (within the oracle tolerance), with drift gates strong enough to catch a future
    `sleap_roots_analyze` bump that changes parameters, return keys, or coordinates.
- **Non-Goals**
  - Fully satisfying #305 AC5 — its *necessary* half (minimizing sklearn/scipy/matplotlib/
    seaborn) requires the viz refactor below and remains tracked by **#315 (kept open)**.
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
  *partially* reconcile #305 AC5. Note there is **no conflict to "supersede"**: the Tier 0
  "Additive Dependency Set" clause is conditional — "no dep **still imported by shipped
  code** SHALL be removed" — and pruning `statsmodels`/`umap-learn` *after* delegation
  satisfies that condition rather than overriding it. Ordering:
  `add-bloommcp-package-baseline` archives first; a future archive of this change folds the
  new requirements into `specs/bloommcp-packaging/`.

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
- **Tool output schema accidentally changes** (e.g. `sleap_roots_analyze` renames/drops a
  dict key). This is not hypothetical: `viz_tools` reads `.get("var_genetic", 0)` /
  `.get("var_residual", 0)`, so a renamed variance key would make the tool **silently plot
  0 variance** — a wrong variance-decomposition shipped to a scientist with no failure.
  Mitigation: a wrapper-level test (FastMCP Client or direct call) asserts the two affected
  tools' output **keys/units** — specifically that `var_genetic`/`var_residual` are present
  and non-defaulted on the fixture — so a key-rename fails CI instead of zero-filling. The
  oracle testing the library directly is **not** sufficient for this; the wrappers need
  their own schema assertion.

- **UMAP drift gate too weak to catch a delegation regression.** A gate asserting only
  `shape == (n, 2)` + within-process self-equality is trivially true regardless of
  correctness — a delegation with wrong `n_neighbors`/`min_dist`/`init` that produced a
  structurally different but same-shape, deterministic embedding would pass (and
  `dimred.py` reads those params back with `.get(default)`, so a silent default-swap would
  not even surface in the manifest). Mitigation: assert a **structural** invariant against
  a recorded embedding — Procrustes-on-aligned-coords (as upstream #162 does) or a
  kNN-overlap/trustworthiness check — rather than dropping all coordinate assertions to
  dodge cross-OS instability.

- **Heritability golden is a characterization snapshot, not an independent reference.**
  `heritability_mean = 0.7650052743157678` pins what `0.1.0a2` currently emits; it gates
  future drift (good) but does **not** validate that the value is scientifically correct,
  and its `_source` must not point at an unrelated PCA-metadata artifact. Mitigation:
  either reconcile it to a genuine reference (the R/lme4 figure the docstring claims to
  match) or label it explicitly as a characterization snapshot in the test/_source so no
  one mistakes it for an independently recorded golden.

- **Edge-case branches uncovered by the fixture.** The deleted `trait_statistics.py` had
  distinct branches (`no_variance`, `mixed_model_failed`, `anova_based` fallback, `len < 4`
  guard, `remove_low_h2`) that the balanced `turface_19` fixture never exercises, so a
  future library bump that changed any of them would ship silently. Mitigation: parametrize
  the oracle over a zero-variance / small-N / NaN fixture, or explicitly document that drift
  protection is limited to the balanced path + the version pin.

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
