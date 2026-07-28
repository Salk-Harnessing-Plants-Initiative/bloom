## 1. Design

- [x] 1.1 Write `design.md` (covers: composite `experiment`/`based_on_version`/
  `source_csv` encoding for a two-experiment run, `calculate_genotype_means` correction
  vs. the issue's bare-`.groupby()` sketch, deferral of `calculate_correlation_confidence_intervals`,
  no-seed determinism, required genotype role on both sides, degenerate-vs-empty result
  handling, `convert_to_json_serializable` reuse, the one-tool-vs-family scope decision,
  the confirmed `min_samples` upstream no-op + pre-filter workaround, reuse of the
  reserved `correlation` tool_class, the `_qc_shared.py` message patch, finite-value
  defense-in-depth, genotype-means traceability artifacts, and the FDR multiplicity-family
  documentation obligation)
- [x] 1.2 Incorporate the 5-lens adversarial review (spec quality, code/architecture,
  GitHub issue alignment, TDD/testing, scientific rigor) — every BLOCKING/IMPORTANT
  finding resolved in this revision (see design.md's revised Decisions and this file)
- [ ] 1.3 Get reviewer sign-off on D1 (the composite-string persistence encoding, now
  pinned to an exact format in spec.md) before implementation starts — same bar
  `devendor-bloommcp-analysis` D3 required for its own convention bend. **Not blocking
  for this PR** — implementation proceeded on the pinned design; sign-off happens at
  code review.
- [x] 1.4 File an upstream bug report against `talmolab/sleap-roots-analyze` for the
  confirmed `min_samples` no-op (design.md D8) — filed as
  [talmolab/sleap-roots-analyze#205](https://github.com/talmolab/sleap-roots-analyze/issues/205)

## 2. Tests (written red-first, all green against the implementation in §4)

All in `bloommcp/tests/tools/test_cross_experiment_correlations_tool.py` (31 tests, all
passing):

- [x] 2.1 `test_delegates_to_upstream_correlation_chain`
- [x] 2.2 `test_min_samples_prefilter_actually_excludes_under_replicated_genotypes` —
  runs against the **real** turface_19/cylinder fixture pair (not synthetic data),
  reproducing the confirmed upstream no-op and proving the pre-filter workaround
  against the recorded golden (`turface_cylinder_cross_experiment_correlation_golden.json`)
- [x] 2.3 `test_load_and_align_experiments_never_called`
- [x] 2.4 `test_requires_cleaned_version_both_experiments` (both directions)
- [x] 2.5 `test_two_cleaned_experiments_consumed_reports_sources`
- [x] 2.6 `test_missing_genotype_role_either_side_rejected`
- [x] 2.7 `test_non_finite_value_either_side_rejected`
- [x] 2.8 `test_zero_correlations_is_degenerate_input`
- [x] 2.9 `test_zero_significant_is_not_an_error`
- [x] 2.10 `test_default_trait_selection_uses_full_certified_set_both_sides`
- [x] 2.11 `test_trait_columns_validated_independently` (all four failure modes,
  both experiments) + `test_non_numeric_trait_column_names_experiment` — required
  task 3.0's `_qc_shared.py` patch to pass (empty/duplicate/non-numeric branches now
  interpolate `experiment`)
- [x] 2.12 `test_seed_recorded_as_none`
- [x] 2.13 `test_repeated_runs_identical`
- [x] 2.14 `test_run_persisted_with_composite_experiment_and_based_on_version`
- [x] 2.15 `test_reserved_encoding_character_in_filename_rejected` (both fields,
  parametrized)
- [x] 2.16 `test_source_csv_content_addresses_both_inputs`
- [x] 2.17 `test_genotype_means_artifacts_persisted`
- [x] 2.18 `test_summary_json_serializable_no_numpy_leaks`
- [x] 2.19 `test_result_never_inlines_full_correlation_matrix`
- [x] 2.20 `test_discoverable_via_list_existing_analyses`
- [x] 2.21 `test_schema_round_trip`
- [x] 2.22 `test_out_of_range_parameters_rejected` (5 cases, parametrized)
- [x] 2.23 `test_no_error_leaks_backend_internals`
- [x] 2.24 `test_reproduces_golden_correlation_unfiltered` — the mandatory numeric
  oracle, against the real turface_19/cylinder fixture pair and the recorded golden
  (built via `scripts/gen_cross_experiment_correlation_golden.py`, no hand-transcribed
  numbers)
- [x] Plus `test_cross_experiment_correlations_in_tools_list` (registration/discovery)

Full bloommcp suite: 746 passed, 29 skipped (live-stack smoke, no dev stack marker set
in this run), 0 failed.

## 3. Shared helper patch

- [x] 3.0 Patched `bloommcp/src/bloom_mcp/tools/_qc_shared.py::_validate_trait_subset` —
  the empty-list, duplicate, and non-numeric error messages now interpolate
  `{experiment!r}` the same way the outside-certified-set message already did
  (design.md D10) — message text only, no signature or exception-type change; no
  existing test asserted the old exact message text (confirmed via search), so this is
  non-breaking for `pca_analysis`/`clustering`/`descriptive_stats`.

## 4. Tool implementation

- [x] 4.1 Created `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/cross_experiment_correlations.py`
  with `CrossExperimentCorrelationsParams`/`CrossExperimentCorrelationsResult`; the
  `trait_columns_1`/`trait_columns_2` field descriptions state the FDR
  multiplicity-family caveat (design.md D13)
- [x] 4.2 Loads both experiments via `_ports.reader().load_experiment(name,
  require_clean=True)`; `CleanedVersionRequiredError` mapped per-experiment
- [x] 4.3 Rejects a `None` `genotype_col` on either frame before any computation (D5)
- [x] 4.4 Rejects `experiment_1`/`experiment_2` containing `@`/`|` up front (D1's guard)
- [x] 4.5 Resolves `trait_columns_1`/`trait_columns_2` independently via
  `_validate_trait_subset(..., require_certified=True)`
- [x] 4.6 `np.isfinite(...)` check on each experiment's selection before aggregation (D11)
- [x] 4.7 Genotype-means via `calculate_genotype_means` (D2), pre-filtered to
  `n_samples >= params.min_samples` (D8) before delegating
- [x] 4.8 `assumption_violated` on an empty correlation result (D6); significance via
  `identify_significant_correlations`, empty result normalized to a fixed header
- [x] 4.9 `summarize_correlation_results` + `convert_to_json_serializable` (D7)
- [x] 4.10 Composite `experiment=`/`based_on_version=` strings + combined-snapshot
  `source_csv` (D1)
- [x] 4.11 Persists `correlations.csv`, `significant.csv`, `genotype_means_1.csv`,
  `genotype_means_2.csv` (D12), `summary.json` under tool class `correlation` (D9)

## 5. Registration

- [x] 5.1 Registered in `bloommcp/src/bloom_mcp/sections/sleap_roots/__init__.py`
- [x] 5.2 Added `sleap_roots_cross_experiment_correlations` to `test_expected_tool_surface`
  (`bloommcp/tests/test_devendor_invariants.py`) by name
- [x] 5.3 Confirmed (test 2.20) that no change to `manifest.CANONICAL_TOOL_CLASSES` /
  `list_existing_analyses.TOOL_CLASSES` was needed — both already reserve `"correlation"`

## 6. Fixtures / oracle

- [x] 6.1 `bloommcp/scripts/gen_cross_experiment_correlation_golden.py` generates
  `bloommcp/tests/fixtures/turface_cylinder_cross_experiment_correlation_golden.json`
  from the real turface_19/cylinder fixture pair (19 fully-overlapping genotypes) —
  records the unfiltered correlation, the confirmed upstream no-op reproduction at
  `min_samples=3`, and the bloommcp-pre-filtered correct value (excludes cylinder's
  `GH_7371`, `n_samples=2`)

## 7. Validation

- [x] 7.1 `openspec validate add-bloommcp-cross-experiment-correlations --strict` passes
- [x] 7.2 Full bloommcp test suite green (746 passed); ruff + black clean at the
  repo-pinned versions (ruff 0.9.9, black 26.3.1)
- [ ] 7.3 `make bloommcp-smoke` / live dev-stack verification — **not run in this
  session**. A container-transport smoke test was written
  (`bloommcp/tests/smoke/test_cross_experiment_correlations_smoke.py`, mirroring
  `test_clustering_smoke.py`) and collects/skips cleanly with no live stack marker, but
  actually exercising it requires the running `bloommcp` container to be rebuilt with
  this branch's code (a live dev stack was found running during this session, but
  rebuilding/restarting a shared container mid-session was judged out of scope for this
  PR). It will run automatically in CI's `dev-stack-smoke` job. Extending the
  comprehensive `tests/smoke/live_persistence_smoke.py` script with a
  cross-experiment-correlations leg (host-side, no container rebuild needed) remains a
  good follow-up if a reviewer wants pre-merge live proof beyond the unit-level golden
  fixture reproduction in 2.2/2.24.
