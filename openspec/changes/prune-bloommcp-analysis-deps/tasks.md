## 1. Verify equivalence + extend the oracle (gate before deleting)

- [x] 1.1 Diffed vendored `umap_embedding.py` / `trait_statistics.py` against
      `sleap_roots_analyze` on turface_19: **byte-identical** (UMAP `array_equal`,
      `calculate_trait_statistics` 0 numeric diffs, heritability 0 diffs > 1e-9). The
      vendored copies are exact duplicates, so delegation is behavior-preserving.
- [x] 1.2 Extended the cross-tier oracle: recorded heritability golden
      (`heritability_mean=0.7650052743157678` — matches the #120 value, method
      `mixed_model`, 8 traits ≥ 0.5) and added `test_external_library_heritability_...`
      and `test_external_library_umap_is_deterministic` (UMAP coords are not cross-OS
      bit-stable, so determinism + shape is the robust claim).
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

- [x] 7.1 AC5 reconciled in-code: import guard proves every declared dep is shipped-code-
      imported. (#305 AC5 / #315 GitHub checkboxes to be ticked on the PR — outward-facing.)
- [x] 7.2 Updated `openspec/project.md` external-packages list. (`.claude/commands/pre-merge.md`
      smoke line still passes since the libs remain transitive; flagged as a manual tweak
      — agent command files are write-guarded. bloommcp has no CHANGELOG.)

## 8. Validate

- [x] 8.1 `openspec validate prune-bloommcp-analysis-deps --strict` passes.
