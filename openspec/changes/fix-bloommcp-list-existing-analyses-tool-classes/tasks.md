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
      constants like `QC_TOOL_CLASS`/`OUTLIERS_TOOL_CLASS`), since importing each producer's
      own `_TOOL_CLASS` constant would invert the intended dependency direction between
      `sections/core` (foundational, session-bootstrap tools) and
      `sections/sleap_roots/analysis` (the granular analysis tools) — the same reasoning
      `fix-bloommcp-error-redaction-followups/design.md` Decision 1 applies to its own
      lookup. In `bloommcp/src/bloom_mcp/manifest/__init__.py`, add the same 3 entries to
      `CANONICAL_TOOL_CLASSES`, and extend that tuple's comment with one sentence stating
      the superset-of-`list_existing_analyses.TOOL_CLASSES` invariant explicitly (today
      only implied, never stated in-file). Confirm tasks 1.1–1.4's new tests now pass, and
      confirm `test_trim_is_stale_and_an_unrelated_tool_class_error_both_survive_together`
      (which asserts `len(response["errors"]) == len(list_existing_analyses_mod.TOOL_CLASSES)`)
      still passes unmodified.

## 2. Spec validation

- [x] 2.1 Run `openspec validate fix-bloommcp-list-existing-analyses-tool-classes --strict`
      and resolve any issues.

## 3. PR review follow-ups (bloom#673 review)

A 5-agent adversarial review of PR #673 found the section-1 error-path claim above
(`test_trim_is_stale_and_an_unrelated_tool_class_error_both_survive_together`
"automatically extends" coverage) was a tautology — that test raises for every tool_class,
so its count-based assertion passes regardless of whether `pca`/`umap`/`qc_inspect` are in
the tuple at all. It also found the `CANONICAL_TOOL_CLASSES` superset invariant was only
spot-tested for these 3 literals rather than generically enforced, the "private/unexported"
comment wording was technically imprecise (the real constraint is the dependency-direction
one, not name-mangling), and cross-PR tracking with #671 only covered one merge order.

- [x] 3.1 Add `test_pca_umap_qc_inspect_list_runs_failure_is_individually_reported` to
      `test_list_existing_analyses_staleness.py`: monkeypatch `store.list_runs` to raise
      _only_ for `tool_class in {"pca", "umap", "qc_inspect"}` (returning `[]` for every
      other class), and assert each of the 3 produces its own `errors` entry and
      `len(errors) == 3` — proving the aggregation loop actually visits these 3 specifically,
      not just that a vacuous raise-for-everything count happens to match tuple length.
- [x] 3.2 Add `test_tool_classes_is_a_subset_of_canonical_tool_classes` to the same file:
      `assert set(TOOL_CLASSES) <= set(CANONICAL_TOOL_CLASSES)` — enforces the superset
      invariant generically, so a future entry added to one tuple but not the other fails
      this test regardless of which literal it is.
- [x] 3.3 Reword both new tuple comments (`list_existing_analyses.py`, `manifest/__init__.py`)
      to state the actual constraint — importing a producer's `_TOOL_CLASS` would invert the
      `sections/core`/`manifest` (foundational) vs. `sections/sleap_roots/analysis`
      (granular tools) dependency direction — rather than the imprecise "private/unexported"
      framing.
- [x] 3.4 Update `proposal.md`'s Non-Goals to document cross-PR tracking with #671
      bidirectionally (both merge orders), and leave a comment on PR #671 itself so the
      `_TOOL_CLASS_TO_PUBLIC_NAME` follow-up isn't lost regardless of which PR merges second.

## 4. Full verification

- [x] 4.1 From `bloommcp/`, run the same invocation CI uses
      (`.github/workflows/pr-checks.yml`):
      `uv run --frozen --extra test pytest tests/ -m "not integration and not live_smoke" -v --tb=short`.
      Confirm no regressions.
- [x] 4.2 Run `pre-commit run --files <touched files>` (bloommcp has no dedicated CI lint job
      — ruff/black/gitleaks enforcement is via the root `.pre-commit-config.yaml` hooks
      only).
