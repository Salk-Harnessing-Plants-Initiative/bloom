## 1. Registry gap fix (tests + implementation land in ONE atomic commit)

**Tasks 1.1–1.5 land in one atomic commit.** 1.1–1.4 are RED until 1.5 lands — none of
`"pca"`/`"umap"`/`"qc_inspect"` exist in `TOOL_CLASSES`/`CANONICAL_TOOL_CLASSES` today, so
each new test fails against current code before the implementation change, exactly like
`fix-bloommcp-error-redaction-followups`' own section-1 precedent for this same file.

- [x] 1.1 In `bloommcp/tests/tools/test_list_existing_analyses_staleness.py`, add
      `test_pca_umap_qc_inspect_registered_in_discovery_and_canonical_registries`, mirroring
      `test_remove_outliers_tool.py::test_outliers_class_registered_in_discovery_and_canonical_registries`:
      assert `"pca"`, `"umap"`, and `"qc_inspect"` are each members of both
      `list_existing_analyses.TOOL_CLASSES` and `manifest.CANONICAL_TOOL_CLASSES`. MUST fail
      against current code.
- [x] 1.2 In `bloommcp/tests/tools/test_pca_analysis_tool.py`, add
      `test_discoverable_via_list_existing_analyses`, mirroring
      `test_cross_experiment_correlations_tool.py::test_discoverable_via_list_existing_analyses`
      (clear `list_existing_analyses_mod._RESPONSE_CACHE` before and after via try/finally,
      call `_run(...)` to commit a real `pca_analysis` run through the `injected_ports`
      fixture's `FakeResultStore`, call `list_existing_analyses(_EXPERIMENT)`, assert
      `"pca" in response["analyses"]`). MUST fail against current code (the loop never calls
      `store.list_runs(experiment, "pca")`, so the key is always absent regardless of what
      was committed).
- [x] 1.3 Same pattern in `bloommcp/tests/tools/test_umap_analysis_tool.py`:
      `test_discoverable_via_list_existing_analyses`, asserting
      `"umap" in response["analyses"]` after a real `umap_analysis` run. MUST fail against
      current code.
- [x] 1.4 Same pattern in `bloommcp/tests/tools/test_qc_inspect_tool.py`:
      `test_discoverable_via_list_existing_analyses`, asserting
      `"qc_inspect" in response["analyses"]` after a real `qc_inspect` run. MUST fail
      against current code.
- [x] 1.5 Implementation. In
      `bloommcp/src/bloom_mcp/sections/core/list_existing_analyses.py`, add `"pca"`,
      `"umap"`, `"qc_inspect"` to `TOOL_CLASSES`, and extend that tuple's comment with one
      sentence noting these 3 are plain re-typed literals (not imported single-sourced
      constants like `QC_TOOL_CLASS`/`OUTLIERS_TOOL_CLASS`), since each producer's own
      `_TOOL_CLASS` constant is private/unexported. In `bloommcp/src/bloom_mcp/manifest/__init__.py`,
      add the same 3 entries to `CANONICAL_TOOL_CLASSES`, and extend that tuple's comment
      with one sentence stating the superset-of-`list_existing_analyses.TOOL_CLASSES`
      invariant explicitly (today only implied, never stated in-file). Confirm tasks
      1.1–1.4's new tests now pass, and confirm
      `test_trim_is_stale_and_an_unrelated_tool_class_error_both_survive_together` (which
      asserts `len(response["errors"]) == len(list_existing_analyses_mod.TOOL_CLASSES)`)
      still passes unmodified — its count-based assertion automatically extends to cover
      the 3 new entries' error-aggregation path (a `list_runs` failure for any of them is
      still reported in `errors`, not dropped) with no test change needed.

## 2. Spec validation

- [x] 2.1 Run `openspec validate fix-bloommcp-list-existing-analyses-tool-classes --strict`
      and resolve any issues.

## 3. Full verification

- [x] 3.1 From `bloommcp/`, run the same invocation CI uses
      (`.github/workflows/pr-checks.yml`):
      `uv run --frozen --extra test pytest tests/ -m "not integration and not live_smoke" -v --tb=short`.
      Confirm no regressions.
- [x] 3.2 Run `pre-commit run --files <touched files>` (bloommcp has no dedicated CI lint job
      — ruff/black/gitleaks enforcement is via the root `.pre-commit-config.yaml` hooks
      only).
