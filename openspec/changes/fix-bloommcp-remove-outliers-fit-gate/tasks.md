## 1. Implementation

**Note on ordering:** the gate change and its test/golden repointing are inseparable — landing
1.2 alone (the gate) turns ~18 currently-passing tests red until 1.3-1.7 land alongside. This
entire section ships as **one commit**; there is no intermediate green state to split across.
Write the new raise-path tests (1.2) first, confirm they fail red against today's code (proving
they exercise the real gap #419 describes), then implement the gate (1.3) to turn them green.

- [ ] 1.1 Triage the full existing test suite in `bloommcp/tests/tools/test_remove_outliers_tool.py`
      for every test that invokes `method="mahalanobis"` (default) against the turface_19 or
      cylinder cleaned fixtures — expected to be ~18 tests, not just the two golden/
      characterization tests, including (at minimum): the goodness-of-fit tests themselves,
      report/round-trip tests, provenance tests, versioning/composition tests
      (`test_second_run_increments_version_and_supersedes_latest`,
      `test_qc_clean_rerun_does_not_revert_existing_trim`), `test_default_method_is_mahalanobis`
      (currently unwrapped in `pytest.raises` — would error, not just fail, once gated), and the
      figure-generation/cleanup tests (`test_include_plots_none_persists_full_mahalanobis_figure_set`
      and the two figure-cleanup tests around it, which would become vacuously true — asserting
      "no leak" without ever creating a figure — if left pointed at an untrustworthy fit).
      Produce a checklist of what each one becomes: repoint to `isolation_forest`, restructure to
      assert the new raise, or (rare) genuinely unaffected.
- [ ] 1.2 Write the new raise-path tests first (red against today's code):
      - mahalanobis default on turface_19 raises `assumption_violated`, embeds the historical
        `n_outliers=8`/`n_output_samples=150`/`fit_quality="very_poor"`/`outlier_barcodes`
        characterization in the message, names `isolation_forest` + `contamination=0.1` in the
        remedy, and commits no new run (assert against the fake `ResultStore`).
      - same for cylinder (`fit_quality="poor"`, `n_outliers=9`/`n_output_samples=120`).
      - the two plot-key-validation tests currently asserting `invalid_input` on a bad `plots`
        key against turface_19/cylinder mahalanobis defaults move to `method="isolation_forest"`
        (Decision 6 — the fit gate now fires first and would otherwise mask them).
      - regression: `fit_is_trustworthy is True` (an acceptable-or-better mahalanobis fit) is
        **not** gated. No existing fixture has a trustworthy mahalanobis fit — use the codebase's
        existing monkeypatch pattern to stub `remove_outlier_samples`'s returned `report` (or
        `_fit_is_trustworthy` directly) rather than fabricating a new real fixture.
      - regression: `isolation_forest` (`fit_is_trustworthy is None`) is unaffected — persists
        exactly as before.
- [ ] 1.3 Implement the gate in `remove_outliers.py`: compute `fit_is_trustworthy` right after
      `remove_outlier_samples` returns (alongside the existing `n_input`/`n_outliers`/`n_output`
      extraction), and when it is `False`, raise `BloomMCPError(code="assumption_violated")` —
      message embedding `n_input_samples`/`n_outliers`/`n_output_samples`/`fit_quality`/sorted
      `outlier_barcodes`, remedy naming `method="isolation_forest"` + `contamination=0.1` — before
      the existing structural (NaN/row-subset) guard, before `plots=` validation/figure
      generation, and before `store.create_run`. Confirm 1.2's tests now pass.
- [ ] 1.4 Work through 1.1's checklist: repoint the remaining affected tests to
      `method="isolation_forest"` (or restructure as needed) so the full file is green again.
- [ ] 1.5 Compute new `isolation_forest` golden values (flagged count, retained count, sorted
      barcodes) for turface_19 and cylinder against the shipped delegate (same seed=42, forwarded
      `random_state`); add golden fixture(s) (decide file naming — new files vs. extending the
      existing golden JSON) and use them for the repointed "successful persisted trim"
      characterization tests. Note isolation_forest determinism can in principle drift across
      sklearn/numpy versions — not blocking, but worth a one-line comment in the golden file.
- [ ] 1.6 `bloommcp/tests/smoke/live_persistence_smoke.py` (the real `make bloommcp-smoke`
      driver, wired into CI at `.github/workflows/pr-checks.yml`) and its wrapper
      `bloommcp/tests/scripts/test_live_persistence_smoke_logic.py`: this smoke path cleans at a
      different threshold than the unit golden (`max_nans_per_trait=0.1` → 187 samples, vs. the
      golden's canonical-default 0.2 → 158) — **empirically verify whether it also trips the
      gate** (run it, don't assume). If it does, repoint its `remove_outliers` calls to
      `isolation_forest` the same way as the unit tests, and update the downstream
      clustering/descriptive-stats legs that depend on the trim persisting. Also update
      `bloommcp/tests/smoke/test_remove_outliers_smoke.py` (the narrower unit-style smoke test)
      the same way. Confirm `make bloommcp-smoke` is green after.
- [ ] 1.7 Update `bloommcp/docs/local-validation.md`'s "Leg 2" runbook prose to match whatever
      1.6 lands on (still mahalanobis if it turns out not to trip the gate; isolation_forest
      otherwise).
- [ ] 1.8 Update `RemoveOutliersParams`/`remove_outliers`'s docstrings and the module docstring's
      "Goodness of fit" section to state the gate is enforced, not merely advisory.
- [ ] 1.9 Add a pointer note to
      `openspec/changes/add-bloommcp-remove-outliers-tool/specs/bloommcp-remove-outliers-tool/spec.md`'s
      "Reproduces the Golden Trim Through the Tool" requirement only (its scenarios' mahalanobis-
      default success claim is what this change supersedes — the "Guarantees a
      Non-Degenerate..." requirement's guarantee is unaffected and should not get a note),
      referencing this change, mirroring the `#420` superseded-note pattern.
- [ ] 1.10 Run the **full** `bloommcp` test suite (not just the changed files — per the
      cross-file `conftest.py` collision lesson from `#585`) before calling this done.

## 2. Spec

- [ ] 2.1 `openspec validate fix-bloommcp-remove-outliers-fit-gate --strict` passes.
