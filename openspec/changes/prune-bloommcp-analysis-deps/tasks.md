## 1. Verify equivalence + extend the oracle (gate before deleting)

- [x] 1.1 Diffed vendored `umap_embedding.py` / `trait_statistics.py` against
      `sleap_roots_analyze` on turface_19: **byte-identical** (UMAP `array_equal`,
      `calculate_trait_statistics` 0 numeric diffs, heritability 0 diffs > 1e-9). The
      vendored copies are exact duplicates, so delegation is behavior-preserving.
- [x] 1.2 Extended the cross-tier oracle: recorded heritability golden
      (`heritability_mean=0.7650052743157678`, method `mixed_model`, 8 traits ≥ 0.5) and
      added `test_external_library_heritability_...` and
      `test_external_library_umap_is_deterministic`.
- [x] 1.2a **(I1 — strengthen UMAP gate)** Replaced shape + self-equality with a
      **structural** trustworthiness invariant (neighbor-preservation of the embedding
      w.r.t. the standardized input) plus a parameter-echo assertion. Recorded
      `umap_trustworthiness` ≈0.9524 + `umap_trustworthiness_floor` = 0.88 in the golden;
      `test_external_library_umap_is_deterministic_and_structural` asserts the floor +
      snapshot, and a companion `test_umap_trustworthiness_floor_rejects_wrong_parameters`
      proves a wrong `n_neighbors=2` collapses trustworthiness to ≈0.74 (< floor), so the
      gate is not a tautology. Cross-OS-robust (scalar, not raw coords).
- [x] 1.2b **(I2 — golden provenance)** Relabeled the heritability golden as an explicit
      `0.1.0a2` characterization snapshot: split `_source` into `_pca_source` (independent),
      `_heritability_source` (snapshot, with the #315 reconcile-to-R/lme4 TODO), and
      `_umap_source`; the heritability provenance no longer points at the PCA-metadata file.
      Test docstring + fixtures README updated to say it gates drift, not correctness.
- [x] 1.2c **(I4 — edge-case coverage)** Added
      `test_delegated_heritability_degrades_on_edge_cases` over a zero-variance + small-N
      frame: asserts the `no_variance` branch (not a crash), `heritability == 0.0`, and that
      the wrapper-consumed keys still exist. Balanced-path-only drift caveat also documented
      in the README.
- [x] 1.3 Oracle green before any deletion (6 passed).

## 2. Delegate UMAP

- [x] 2.1 Repointed `tools/workflows/dimred.py` to
      `from sleap_roots_analyze.umap import UMAP_AVAILABLE, perform_umap_analysis`
      (drop-in; signature identical). Updated the module docstring.
- [x] 2.2 Deleted `src/bloom_mcp/umap_embedding.py` (`git rm`).
- [x] 2.3 Import chain + suite green; UMAP tool output unchanged.

## 3. Delegate trait-statistics / heritability

- [x] 3.1 Repointed `tools/workflows/stats.py` and `tools/viz_tools.py` (×2) to
      `from sleap_roots_analyze import statistics as ...` (submodule import; the published
      0.1.0a2 does not re-export at top level). Runtime calls are only
      `calculate_trait_statistics` + `calculate_heritability_estimates`; signatures match,
      no wrapper changes needed.
- [x] 3.2 Deleted `src/bloom_mcp/trait_statistics.py` (`git rm`). No ruff per-file-ignores
      referenced it.
- [x] 3.3 Suite green; tool output schemas unchanged.
- [x] 3.4 **(I3 — wrapper drift guard)** Two-part fix: (a) hardened
      `viz_tools.plot_variance_decomposition` to **fail loudly** (return an explicit
      contract-changed error) instead of `r.get("var_genetic", 0)` zero-filling when a
      variance key is missing; (b) added `test_delegated_heritability_returns_wrapper_
      consumed_keys` pinning the delegation boundary — every plottable per-trait result
      carries `heritability`/`var_genetic`/`var_residual`, non-defaulted — so a library
      key-rename fails CI here rather than shipping a wrong (zero) decomposition.

## 4. Prune dependencies + re-lock

- [x] 4.1 Removed `statsmodels` and `umap-learn` from `bloommcp/pyproject.toml`; updated
      the dependency comment to record why scipy/scikit-learn/matplotlib/seaborn stay.
- [x] 4.2 Re-locked `bloommcp/uv.lock` + root; `scripts/check-uv-locks.py` and
      `uv lock --check` (both) report in sync. (statsmodels/umap-learn remain in the lock
      transitively via sleap-roots-analyze — expected.)

## 5. Guard the prune

- [x] 5.1 Added `test_pruned_analysis_deps_not_imported` (AST walk of `src/bloom_mcp/**`;
      fails if any shipped module imports `statsmodels` or `umap`).
- [x] 5.2 Added `test_retained_heavy_deps_are_each_imported` (necessary-and-sufficient,
      other direction: scikit-learn/scipy/matplotlib/seaborn each imported by shipped code).

## 6. Necessary-and-sufficient verification

- [x] 6.1 Clean-env wheel import gate run locally: `uv build --wheel` +
      `uv run --no-project --with <wheel> python -c "import bloom_mcp, bloom_mcp.tools,
      bloom_mcp.storage, bloom_mcp.server"` resolves from site-packages, no missing dep.
- [x] 6.2 Supabase-free suite green: **44 passed**; ruff clean on changed files.

## 7. Reconcile AC5 + docs

- [x] 7.1 AC5 reconciled in-code (**partial**): import guard proves every declared dep is
      shipped-code-imported — AC5's *sufficient* half. **(B1)** Do **not** tick #305 AC5 as
      fully met and **keep #315 open**: the *necessary* half (sklearn/scipy/matplotlib/
      seaborn minimization) is the deferred viz refactor #315 tracks. PR body should say
      "Refs #315" and "partially addresses #305 AC5", not "Closes".
- [x] 7.2 Updated `openspec/project.md` external-packages list. (`.claude/commands/pre-merge.md`
      smoke line still passes since the libs remain transitive; flagged as a manual tweak
      — agent command files are write-guarded. bloommcp has no CHANGELOG.)

## 8. Validate

- [x] 8.1 `openspec validate prune-bloommcp-analysis-deps --strict` passes.
- [ ] 8.2 **(I5 — rebase + green CI)** Rebase the branch on `staging` and push so CI
      actually runs (currently "no checks reported"). Confirm the
      `pydantic-settings>=2.14.2` security pin present on `staging` is **not** lost across
      the rebase (this PR does not drop it; the absence is fork-point divergence).
- [ ] 8.3 **(I5 — promote the wheel gate)** Confirm the existing
      `add-bloommcp-wheel-import-ci` (`python-audit`) job covers the post-prune clean-env
      wheel import, so task 6.1 is enforced by CI rather than a manual checkbox.
- [ ] 8.4 **(suggestion)** Flag the write-guarded `.claude/commands/pre-merge.md` smoke
      line (still names `statsmodels`/`umap`) for a manual tweak — the libs remain
      transitive so it still passes, but the wording is stale post-prune.
