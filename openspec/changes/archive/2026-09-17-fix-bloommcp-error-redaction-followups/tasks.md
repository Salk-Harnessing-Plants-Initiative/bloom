## 1. Redaction + public tool naming in `list_existing_analyses.py` (items 1 & 3)

**Tasks 1.1–1.4 land in one atomic commit.** Unlike section 2 (below), 1.1 and 1.2 are not
coverage-closing — they assert behavior that does not exist yet, so both are RED until 1.4
lands, exactly like 1.3.

- [x] 1.1 In `bloommcp/tests/tools/test_list_existing_analyses_staleness.py`, add
      `test_tool_class_error_entry_is_redacted`: monkeypatch `store.list_runs` to
      unconditionally raise an exception carrying a planted secret-shaped fragment (e.g.
      `"apikey=sk-secret123"`), mirroring the existing `_boom(_experiment, _tool_class)`
      pattern at line ~146; assert every resulting `errors` entry omits the fragment. This
      test MUST fail against current code (line 111 has no `safe_error_text` call) before any
      implementation change.
- [x] 1.2 In the same file, add `test_tool_class_error_entry_uses_public_tool_name`:
      monkeypatch `store.list_runs` to raise **unconditionally** (for every `tool_class`, not
      just one — a single-`tool_class` raise can't exercise both assertions below in one
      test), then assert (a) the entry for `tool_class="stats"` starts with
      `"descriptive_stats: "`, not `"stats: "`, and (b) the entry for the unmapped legacy
      `tool_class="dimred"` still starts with `"dimred: "` (fallback-to-self, not raised or
      dropped). MUST fail against current code before the implementation change.
- [x] 1.3 Update the existing `test_trim_is_stale_and_an_unrelated_tool_class_error_both_survive_together`
      assertion from `e.startswith("qc: ")` to `e.startswith("qc_clean: ")` — this test
      currently hard-codes the pre-fix raw-`tool_class` naming and will fail once 1.4 lands
      unless updated in the same commit.
- [x] 1.4 In `bloommcp/src/bloom_mcp/sections/core/list_existing_analyses.py`: add a
      `_TOOL_CLASS_TO_PUBLIC_NAME` lookup dict (keys: `QC_TOOL_CLASS`, `"stats"`,
      `"clustering"`, `OUTLIERS_TOOL_CLASS`, `"correlation"` → their public tool names), and
      change the loop's `errors.append(f"{tool_class}: {exc}")` to
      `errors.append(f"{_TOOL_CLASS_TO_PUBLIC_NAME.get(tool_class, tool_class)}: {safe_error_text(exc)}")`.
      Confirm tasks 1.1–1.3's tests now pass and no other test in the file regresses.

## 2. Leak-test coverage on the 5 remaining write-and-link tools (item 2)

Each of 2.1–2.5 is its own independent commit (a `test(...)`-only change per tool) and is
expected to pass against current code with **no** production change. **Contingency:** if any
of these unexpectedly fails, do not widen or alter that tool's `except`-clause classification
to make it pass — that's explicitly out of scope (see `proposal.md`'s Non-Goals) and would be
a behavior change disguised as a test. Instead, leave that one tool's task unchecked, note the
actual observed behavior against design.md's except-clause table, and raise it for a scoping
decision (a follow-up issue, or an explicit scope amendment to this proposal) before touching
that tool's implementation.

- [x] 2.1 `test_clustering_tool.py`: add `test_undeclared_delegate_raise_is_scrubbed`,
      monkeypatching `perform_kmeans_clustering` to raise `KeyError("secret path
  /var/secrets/key and host db.internal")` (outside `clustering.py`'s
      `(ValueError, RuntimeError)` except clause, so it falls through undeclared). Assert
      `exc.value.code == "internal_error"` and the planted secret text is absent from
      `f"{exc.value.message} {exc.value.remedy}"`. Expected to pass with no production-code
      change (coverage-closing).
- [x] 2.2 `test_pca_analysis_tool.py`: same pattern, monkeypatching `perform_pca_analysis` to
      raise `RuntimeError(...)` (outside `pca_analysis.py`'s `ValueError`-only except
      clause).
- [x] 2.3 `test_umap_analysis_tool.py`: same pattern, monkeypatching the umap delegate to
      raise a plain `Exception(...)` (outside `umap_analysis.py`'s
      `(ValueError, KeyError, RuntimeError, TypeError)` except clause).
- [x] 2.4 `test_cross_experiment_correlations_tool.py`: tighten the existing
      `test_no_error_leaks_backend_internals` to also assert
      `exc.value.code == "internal_error"` (it already raises a generic `RuntimeError` via
      `calculate_genotype_means`, which already falls through undeclared today — no new test
      needed, just the added assertion).
- [x] 2.5 `test_descriptive_stats_tool.py`: add a brand-new
      `test_undeclared_delegate_raise_is_scrubbed`, monkeypatching
      `calculate_trait_statistics` to raise a secret-bearing generic exception (this tool has
      no `except` clause around its delegate call and no leak test today — follow the same
      shape as `qc_inspect`/`qc_clean`/`remove_outliers`'s equivalent test from #660). Assert
      `code == "internal_error"` and secret absence.

## 3. Spec validation

- [x] 3.1 Run `openspec validate fix-bloommcp-error-redaction-followups --strict` and resolve
      any issues.

## 4. Full verification

- [x] 4.1 From `bloommcp/`, run the same invocation CI uses (`.github/workflows/pr-checks.yml`):
      `uv run --frozen --extra test pytest tests/ -m "not integration and not live_smoke" -v --tb=short`.
      Confirm no regressions beyond the one intentionally-updated assertion in task 1.3.
- [x] 4.2 Run `pre-commit run --files <touched files>` (bloommcp has no dedicated CI lint job —
      ruff/black/gitleaks enforcement is via the root `.pre-commit-config.yaml` hooks only).
