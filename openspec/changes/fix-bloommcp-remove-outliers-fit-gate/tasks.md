## 1. Implementation

**Note on ordering:** the gate change and its test/golden repointing are inseparable — landing
1.2 alone (the gate) turns ~18 currently-passing tests red until 1.3-1.7 land alongside. This
entire section ships as **one commit**; there is no intermediate green state to split across.
Write the new raise-path tests (1.2) first, confirm they fail red against today's code (proving
they exercise the real gap #419 describes), then implement the gate (1.3) to turn them green.

- [x] 1.1 Triage the full existing test suite in `bloommcp/tests/tools/test_remove_outliers_tool.py`
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
      **Done:** actual count was 22 tests (confirmed by running the suite red against the
      implemented gate before fixing); `test_persistence_failure_closes_all_figures` was also
      vacuous-pass-affected (not caught by the red run — figures/commit-failure path was never
      reached — but not itself failing, since a raised `BloomMCPError` still satisfied its
      `pytest.raises`) and was repointed too.
- [x] 1.2 Write the new raise-path tests first (red against today's code):
      - mahalanobis default on turface_19 raises `assumption_violated`, embeds the historical
        `n_outliers=8`/`n_output_samples=150`/`fit_quality="very_poor"`/`outlier_barcodes`
        characterization in the message, names `isolation_forest` + `contamination=0.1` in the
        remedy, and commits no new run (assert against the fake `ResultStore`).
      - same for cylinder (`fit_quality="poor"`, `n_outliers=9`/`n_output_samples=120`).
      - `test_unknown_plot_key_is_invalid_input_with_no_run` and
        `test_unknown_plot_key_failure_closes_all_figures` — the two tests currently asserting
        `invalid_input` on a bad `plots` key, both against turface_19 mahalanobis defaults
        (cylinder has no plot-key test today) — move to `method="isolation_forest"` (Decision 6 —
        the fit gate now fires first and would otherwise mask them).
      - regression: `fit_is_trustworthy is True` (an acceptable-or-better mahalanobis fit) is
        **not** gated. No existing fixture has a trustworthy mahalanobis fit — use the codebase's
        existing monkeypatch pattern to stub `remove_outlier_samples`'s returned `report` (or
        `_fit_is_trustworthy` directly) rather than fabricating a new real fixture.
      - regression: `isolation_forest` (`fit_is_trustworthy is None`) is unaffected — persists
        exactly as before.
      **Done:** added `test_mahalanobis_default_untrustworthy_fit_is_gated_not_persisted`,
      `test_cylinder_mahalanobis_default_untrustworthy_fit_is_gated_not_persisted`, repointed the
      two plot-key tests, and added `_force_trustworthy_mahalanobis_fit` (a shared monkeypatch
      helper wrapping the REAL delegate, overriding only `fit_quality`) for the True-path
      regression (`test_goodness_of_fit_true_fit_is_not_gated_dict_shape_and_types`) — reused for
      `test_include_plots_none_persists_full_mahalanobis_figure_set` and
      `test_outlier_report_json_round_trips`, both of which need a real mahalanobis
      figure/report shape, not isolation_forest's.
- [x] 1.3 Implement the gate in `remove_outliers.py`: compute `fit_is_trustworthy` right after
      `remove_outlier_samples` returns (alongside the existing `n_input`/`n_outliers`/`n_output`
      extraction), and when it is `False`, raise `BloomMCPError(code="assumption_violated")` —
      message embedding `n_input_samples`/`n_outliers`/`n_output_samples`/`fit_quality`/sorted
      `outlier_barcodes`, remedy naming `method="isolation_forest"` + `contamination=0.1` — before
      the existing structural (NaN/row-subset) guard, before `plots=` validation/figure
      generation, and before `store.create_run`. Confirm 1.2's tests now pass.
- [x] 1.4 Work through 1.1's checklist: repoint the remaining affected tests to
      `method="isolation_forest"` (or restructure as needed) so the full file is green again.
      **Done:** all 22 originally-affected tests repointed/restructured; full test file (49
      tests) green.
- [x] 1.5 Compute new `isolation_forest` golden values (flagged count, retained count, sorted
      barcodes) for turface_19 and cylinder against the shipped delegate (same seed=42, forwarded
      `random_state`); add golden fixture(s) (decide file naming — new files vs. extending the
      existing golden JSON) and use them for the repointed "successful persisted trim"
      characterization tests. Note isolation_forest determinism can in principle drift across
      sklearn/numpy versions — not blocking, but worth a one-line comment in the golden file.
      **Done:** computed via the exact same cleaning/role/port test helpers, method=isolation_forest,
      seed=42 (contamination default 0.1) — turface_19: 16/158 flagged, 142 retained; cylinder:
      13/129 flagged, 116 retained. New files `turface_19_outlier_iforest_golden.json` /
      `cylinder_outlier_iforest_golden.json` (kept separate from the mahalanobis goldens, which
      stay as the raise-path's historical reference).
- [x] 1.6 `bloommcp/tests/smoke/live_persistence_smoke.py` (the real `make bloommcp-smoke`
      driver, wired into CI at `.github/workflows/pr-checks.yml`) and its wrapper
      `bloommcp/tests/scripts/test_live_persistence_smoke_logic.py`: this smoke path cleans at a
      different threshold than the unit golden (`max_nans_per_trait=0.1` → 187 samples, vs. the
      golden's canonical-default 0.2 → 158) — **empirically verify whether it also trips the
      gate** (run it, don't assume). If it does, repoint its `remove_outliers` calls to
      `isolation_forest` the same way as the unit tests, and update the downstream
      clustering/descriptive-stats legs that depend on the trim persisting. Also update
      `bloommcp/tests/smoke/test_remove_outliers_smoke.py` (the narrower unit-style smoke test)
      the same way. Confirm `make bloommcp-smoke` is green after.
      **Partially empirical — disclosed limitation:** this dev environment has no Docker/live
      Supabase stack (`docker` is not installed), so `make bloommcp-smoke` could not actually be
      run here. `live_persistence_smoke.py` reads from a live-seeded Postgres experiment
      (`BLOOM_SMOKE_EXPERIMENT_ID`), not the local raw CSV fixtures directly, so the exact
      chi-squared fit quality on that seeded data could not be computed locally either.
      Confirmed via source inspection that `qc_clean`'s own default `max_nans_per_trait=0.2`
      matches the canonical default (`qc_clean.py`), but this smoke path explicitly overrides it
      to `0.1` — a materially different cleaned frame than any golden characterized. Rather than
      guess, repointed both files' `remove_outliers` calls to `isolation_forest` unconditionally:
      this leg's actual purpose is persistence mechanics (versioning, provenance, require_clean
      composition), none of which are mahalanobis-specific, so isolation_forest (never gated)
      sidesteps the unknown-fit-quality question entirely rather than risking the leg (and the
      ~7 checks gated on it) going red on live data this change has no way to characterize from
      this environment. `test_live_persistence_smoke_logic.py` (pure decision-logic, no live
      stack needed) re-run green after the edit. **Follow-up for whoever has stack access:**
      confirm `make bloommcp-smoke` is still green end-to-end before merging, or as part of CI.
- [x] 1.7 Update `bloommcp/docs/local-validation.md`'s "Leg 2" runbook prose to match whatever
      1.6 lands on (still mahalanobis if it turns out not to trip the gate; isolation_forest
      otherwise).
      **Done:** updated to isolation_forest with a one-paragraph note explaining why, matching 1.6.
- [x] 1.8 Update `RemoveOutliersParams`/`remove_outliers`'s docstrings and the module docstring's
      "Goodness of fit" section to state the gate is enforced, not merely advisory.
- [x] 1.9 Add a pointer note to
      `openspec/changes/add-bloommcp-remove-outliers-tool/specs/bloommcp-remove-outliers-tool/spec.md`'s
      "Reproduces the Golden Trim Through the Tool" requirement only (its scenarios' mahalanobis-
      default success claim is what this change supersedes — the "Guarantees a
      Non-Degenerate..." requirement's guarantee is unaffected and should not get a note),
      referencing this change, mirroring the `#420` superseded-note pattern.
- [x] 1.10 Run the **full** `bloommcp` test suite (not just the changed files — per the
      cross-file `conftest.py` collision lesson from `#585`) before calling this done.
      **Done:** `uv run --extra test pytest -q -m 'not live_smoke'` → 893 passed, 29 deselected
      (the `live_smoke`-marked tests requiring a running dev stack, unavailable in this
      environment — see 1.6).

## 2. Spec

- [x] 2.1 `openspec validate fix-bloommcp-remove-outliers-fit-gate --strict` passes.
