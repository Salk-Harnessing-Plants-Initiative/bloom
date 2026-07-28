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
  `devendor-bloommcp-analysis` D3 required for its own convention bend
- [x] 1.4 File an upstream bug report against `talmolab/sleap-roots-analyze` for the
  confirmed `min_samples` no-op (design.md D8) — filed as
  [talmolab/sleap-roots-analyze#205](https://github.com/talmolab/sleap-roots-analyze/issues/205)

## 2. Tests (write red tests first — TDD)

- [ ] 2.1 `test_delegates_to_upstream_correlation_chain` — with two small fake cleaned
  experiments (shared genotypes, ≥3 overlapping with `min_samples`), assert
  `calculate_genotype_means`, `calculate_cross_experiment_correlations`,
  `identify_significant_correlations`, and `summarize_correlation_results` are each
  called exactly once (spy/monkeypatch), and that no bespoke `.groupby().mean()`
  reimplementation exists in the tool module
- [ ] 2.2 `test_min_samples_prefilter_actually_excludes_under_replicated_genotypes` —
  construct two fake cleaned experiments where genotype G has `n_samples` below
  `min_samples` in one experiment; assert G is absent from both persisted
  `genotype_means_*.csv` post-filter rows used in the correlation call (spy on the
  DataFrames passed into `calculate_cross_experiment_correlations` and assert G's row is
  gone), directly validating design.md D8's workaround — this is the test that would
  have caught the upstream no-op had it been written against a naive forward-only
  implementation
- [ ] 2.3 `test_load_and_align_experiments_never_called` — spy/monkeypatch
  `sleap_roots_analyze.load_and_align_experiments` to raise if called; assert a normal
  run never touches it
- [ ] 2.4 `test_requires_cleaned_version_both_experiments` — one experiment has no
  cleaned version → `BloomMCPError(tool_error)` naming *that* experiment, remedy points
  at `qc_clean`; repeat with the other experiment missing instead
- [ ] 2.5 `test_two_cleaned_experiments_consumed_reports_sources` — both experiments have
  committed cleaned versions (e.g. `v3_cleaned`, `v2_cleaned`); assert the reader
  resolved each as cleaned (not `raw`) and the tool's result exposes both resolved
  source labels
- [ ] 2.6 `test_missing_genotype_role_either_side_rejected` — a fake reader frame with
  `genotype_col=None` on experiment_1 (then experiment_2) → `BloomMCPError(assumption_violated)`
  naming which experiment lacks a resolvable genotype column; no run persisted
- [ ] 2.7 `test_non_finite_value_either_side_rejected` — a non-finite value (`NaN`,
  `+inf`, or `-inf`) surviving into experiment_1's (then experiment_2's) selected trait
  columns → `BloomMCPError(assumption_violated)` naming the offending experiment; no
  genotype-mean aggregation attempted
- [ ] 2.8 `test_zero_correlations_is_degenerate_input` — two experiments with no shared
  genotypes (or fewer than `min_samples`, post-pre-filter) → `BloomMCPError(assumption_violated)`
  with a remedy (lower `min_samples` / check genotype overlap); no run persisted
- [ ] 2.9 `test_zero_significant_is_not_an_error` — correlations exist but none clear
  `r_threshold`/`p_threshold` → normal success, `n_significant == 0`, `significant.csv`
  persisted with a fixed header (not upstream's columnless empty frame) and zero data
  rows
- [ ] 2.10 `test_default_trait_selection_uses_full_certified_set_both_sides` — both
  `trait_columns_1`/`trait_columns_2` omitted → the tool correlates every certified
  trait of `experiment_1` against every certified trait of `experiment_2` (assert the
  delegate call's trait-column arguments equal each frame's full `trait_cols`)
- [ ] 2.11 `test_trait_columns_1_2_validated_independently` — for each of the four
  failure modes (outside certified set, non-numeric, empty list, duplicate) on either
  experiment → `BloomMCPError(invalid_input)` naming the offending experiment +
  column(s) — this requires task 3.0's `_qc_shared.py` patch (empty/duplicate/non-numeric
  branches did not previously interpolate `experiment`; only the outside-certified-set
  branch did)
- [ ] 2.12 `test_seed_recorded_as_none` — stamped `Provenance.seed == None` (no
  `random_state` anywhere in the delegation chain)
- [ ] 2.13 `test_repeated_runs_identical` — invoke the tool twice on the same pair of
  cleaned experiments with identical parameters; assert `n_correlations`,
  `n_significant`, and `n_highly_significant` are equal across both runs
- [ ] 2.14 `test_run_persisted_with_composite_experiment_and_based_on_version` —
  `store.create_run` receives `experiment == f"{stem1}__x__{stem2}"`; the committed run's
  `Provenance.based_on_version` equals exactly
  `f"{experiment_1}@{frame1.source}|{experiment_2}@{frame2.source}"`
- [ ] 2.15 `test_reserved_encoding_character_in_filename_rejected` — `experiment_1` (then
  `experiment_2`) contains `@` or `|` → `BloomMCPError(invalid_input)` before either
  composite string is built; no run persisted
- [ ] 2.16 `test_source_csv_content_addresses_both_inputs` — the manifest's `input_sha256`
  changes if *either* experiment's selected trait data changes (not just one side)
- [ ] 2.17 `test_genotype_means_artifacts_persisted` — the committed run's outputs
  include `genotype_means_1.csv`/`genotype_means_2.csv`, each containing every selected
  trait's per-genotype mean and the `n_samples` column, post `min_samples` pre-filter
- [ ] 2.18 `test_summary_json_serializable_no_numpy_leaks` — `summary.json` round-trips
  through `json.loads` with no `numpy.int64`/`numpy.float64` leaking through (i.e.
  `convert_to_json_serializable` was actually applied)
- [ ] 2.19 `test_result_never_inlines_full_correlation_matrix` — the tool result model
  carries only summary counts + `RunLinks`; the full `exp1_trait × exp2_trait` matrix is
  reachable only via the persisted `correlations.csv` link
- [ ] 2.20 `test_discoverable_via_list_existing_analyses` — after a committed run,
  `list_existing_analyses(composite_experiment_key)` surfaces it under `"correlation"`
  with no change to that tool's `TOOL_CLASSES`
- [ ] 2.21 `test_schema_round_trip` — a valid request serializes to the input schema and
  the result validates against the output schema without loss
- [ ] 2.22 `test_out_of_range_parameters_rejected` — `min_samples < 1`, or
  `p_threshold`/`r_threshold` outside `[0, 1]` → `BloomMCPError` input-validation code;
  no run persisted
- [ ] 2.23 `test_no_error_leaks_backend_internals` — every mapped `BloomMCPError`'s
  message (missing cleaned version, missing genotype column, non-finite input, invalid
  trait selection, reserved encoding character, degenerate correlation result) contains
  no raw upstream traceback text or storage/path internals (matches the no-leak
  convention already enforced for `pca_analysis`/`clustering`)
- [ ] 2.24 (Mandatory — promoted from an earlier "optional" framing) numeric
  characterization test against a hand-computable golden: build a small fixture pair
  (e.g. drawn from the existing `turface_19_final_data.csv`/`cylinder_final_data.csv`,
  already used together in `test_oracle.py`) with an independently hand-computed
  Pearson r for at least one trait pair; record it as
  `*_cross_experiment_correlation_golden.json` following the `turface_19_pca_golden.json`
  pattern, and assert the tool reproduces it within tolerance

## 3. Shared helper patch

- [ ] 3.0 Patch `bloommcp/src/bloom_mcp/tools/_qc_shared.py::_validate_trait_subset` so
  the empty-list, duplicate, and non-numeric error messages interpolate `{experiment!r}`
  the same way the outside-certified-set message already does (design.md D10) — message
  text only, no signature or exception-type change; benefits `pca_analysis`/`clustering`/
  `descriptive_stats` too

## 4. Tool implementation

- [ ] 4.1 Create `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/cross_experiment_correlations.py`
  with `CrossExperimentCorrelationsParams` (`experiment_1`, `experiment_2`,
  `trait_columns_1`, `trait_columns_2`, `min_samples: int = 3` (`ge=1`),
  `p_threshold: float = 0.05` (`ge=0, le=1`), `r_threshold: float = 0.5` (`ge=0, le=1`),
  `use_fdr=True`, `user_label`) and `CrossExperimentCorrelationsResult` (extends
  `RunLinks`). The `trait_columns_1`/`trait_columns_2` field descriptions SHALL state
  the FDR multiplicity-family caveat (design.md D13): re-running with a narrower subset
  changes the correction family, so `p_value_corrected` values are not comparable
  across runs with different trait selections.
- [ ] 4.2 Load both experiments via `_ports.reader().load_experiment(name, require_clean=True)`;
  map `CleanedVersionRequiredError` per-experiment to a named `BloomMCPError(tool_error)`
- [ ] 4.3 Reject a `None` `genotype_col` on either frame before any computation (D5)
- [ ] 4.4 Reject `experiment_1`/`experiment_2` containing `@` or `|` before building any
  composite string (D1's guard)
- [ ] 4.5 Resolve `trait_columns_1`/`trait_columns_2` via `_validate_trait_subset(...,
  require_certified=True)`, independently per experiment (default: full `frame.trait_cols`
  when omitted)
- [ ] 4.6 Check `np.isfinite(...)` on each experiment's selected trait columns before
  aggregation (D11); raise `assumption_violated` naming the offending experiment
- [ ] 4.7 Build genotype-means via `calculate_genotype_means` (D2) for each experiment,
  then pre-filter each to `n_samples >= params.min_samples` (D8) before calling
  `calculate_cross_experiment_correlations(exp1_means, exp2_means, trait_cols_1,
  trait_cols_2, min_samples=params.min_samples)`
- [ ] 4.8 Raise `assumption_violated` on an empty correlation result (D6); otherwise call
  `identify_significant_correlations(corr_df, p_threshold, r_threshold, use_fdr)`,
  normalizing an empty result to a fixed-header empty frame
- [ ] 4.9 Call `summarize_correlation_results(corr_df, exp1_name=experiment_1,
  exp2_name=experiment_2)`; convert via
  `sleap_roots_analyze.data_utils.convert_to_json_serializable` (D7) before persisting
- [ ] 4.10 Build the composite `experiment=`/`based_on_version=` strings (D1) and the
  combined-snapshot `source_csv` (D1) for `store.create_run(...)`
- [ ] 4.11 Persist `correlations.csv`, `significant.csv`, `genotype_means_1.csv`,
  `genotype_means_2.csv` (D12), and `summary.json` under the reused tool class
  `correlation` (D9); return the summary counts + `RunLinks`

## 5. Registration

- [ ] 5.1 Add `cross_experiment_correlations` to the imports and `register(...)` call in
  `bloommcp/src/bloom_mcp/sections/sleap_roots/__init__.py`
- [ ] 5.2 Add the new tool name to `test_expected_tool_surface`
  (`bloommcp/tests/test_devendor_invariants.py`) explicitly by name — that test's
  `live & relevant == expected` check silently passes if a name is absent from both its
  `expected` and `not_expected` lists, so this must be named, not left to "any drift-guard
  list"
- [ ] 5.3 No changes needed to `manifest.CANONICAL_TOOL_CLASSES` or
  `list_existing_analyses.TOOL_CLASSES` — both already reserve `"correlation"` (D9);
  confirm this with a test (2.20) rather than skipping verification because "no change
  was needed"

## 6. Fixtures / oracle

- [ ] 6.1 See 2.24 (now mandatory) — construct the two-experiment fixture pair and the
  hand-computed golden value

## 7. Validation

- [ ] 7.1 `openspec validate add-bloommcp-cross-experiment-correlations --strict` passes
- [ ] 7.2 Full bloommcp test suite green; `test_server_boots_after_devendor`-style boot
  smoke still passes with the new tool registered
- [ ] 7.3 `make bloommcp-smoke` (or equivalent live-persistence smoke) exercises
  `qc_clean` (×2) → `cross_experiment_correlations` through the real
  `SupabaseReader`/`SupabaseResultStore`, confirming the composite `experiment`/
  `based_on_version` encoding round-trips through a real manifest, and that the run is
  discoverable via `list_existing_analyses` under `correlation`
