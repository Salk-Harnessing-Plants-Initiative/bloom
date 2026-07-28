## 1. Design

- [x] 1.1 Write `design.md` (covers: composite `experiment`/`based_on_version`/
  `source_csv` encoding for a two-experiment run, `calculate_genotype_means` correction
  vs. the issue's bare-`.groupby()` sketch, deferral of `calculate_correlation_confidence_intervals`,
  no-seed determinism, required genotype role on both sides, degenerate-vs-empty result
  handling, `convert_to_json_serializable` reuse)
- [ ] 1.2 Get reviewer sign-off on D1 (the composite-string persistence encoding) before
  implementation starts — same bar `devendor-bloommcp-analysis` D3 required for its own
  convention bend

## 2. Tests (write red tests first — TDD)

- [ ] 2.1 `test_delegates_to_upstream_correlation_chain` — with two small fake cleaned
  experiments (shared genotypes, ≥3 overlapping with `min_samples`), assert
  `calculate_genotype_means`, `calculate_cross_experiment_correlations`,
  `identify_significant_correlations`, and `summarize_correlation_results` are each
  called exactly once (spy/monkeypatch), and that no bespoke `.groupby().mean()`
  reimplementation exists in the tool module
- [ ] 2.2 `test_requires_cleaned_version_both_experiments` — one experiment has no
  cleaned version → `BloomMCPError(tool_error)` naming *that* experiment, remedy points
  at `qc_clean`; repeat with the other experiment missing instead
- [ ] 2.3 `test_missing_genotype_role_either_side_rejected` — a fake reader frame with
  `genotype_col=None` on experiment_1 (then experiment_2) → `BloomMCPError(assumption_violated)`
  naming which experiment lacks a resolvable genotype column; no run persisted
- [ ] 2.4 `test_zero_correlations_is_degenerate_input` — two experiments with no shared
  genotypes (or fewer than `min_samples`) → `BloomMCPError(assumption_violated)` with a
  remedy (lower `min_samples` / check genotype overlap); no run persisted
- [ ] 2.5 `test_zero_significant_is_not_an_error` — correlations exist but none clear
  `r_threshold`/`p_threshold` → normal success, `n_significant == 0`, `significant.csv`
  persisted with a fixed header (not upstream's columnless empty frame) and zero data
  rows
- [ ] 2.6 `test_trait_columns_1_2_validated_independently` — `trait_columns_1`/
  `trait_columns_2` each validated via `_validate_trait_subset(..., require_certified=True)`
  against their own experiment's certified set; an unknown/non-numeric/duplicate/empty-list
  column on either side → `BloomMCPError(invalid_input)` naming the offending experiment +
  column(s)
- [ ] 2.7 `test_seed_recorded_as_none` — stamped `Provenance.seed == None` (no
  `random_state` anywhere in the delegation chain)
- [ ] 2.8 `test_run_persisted_with_composite_experiment_and_based_on_version` —
  `store.create_run` receives `experiment == f"{stem1}__x__{stem2}"`; the committed run's
  `Provenance.based_on_version` equals `f"{experiment_1}@{frame1.source}|{experiment_2}@{frame2.source}"`
- [ ] 2.9 `test_source_csv_content_addresses_both_inputs` — the manifest's `input_sha256`
  changes if *either* experiment's selected trait data changes (not just one side)
- [ ] 2.10 `test_summary_json_serializable_no_numpy_leaks` — `summary.json` round-trips
  through `json.loads` with no `numpy.int64`/`numpy.float64` leaking through (i.e.
  `convert_to_json_serializable` was actually applied)
- [ ] 2.11 `test_result_never_inlines_full_correlation_matrix` — the tool result model
  carries only summary counts + `RunLinks`; the full `exp1_trait × exp2_trait` matrix is
  reachable only via the persisted `correlations.csv` link
- [ ] 2.12 `test_schema_round_trip` — a valid request serializes to the input schema and
  the result validates against the output schema without loss
- [ ] 2.13 `test_no_error_leaks_backend_internals` — any mapped `BloomMCPError`'s message
  contains no raw upstream traceback text or storage/path internals (matches the
  no-leak convention already enforced for `pca_analysis`/`clustering`)

## 3. Tool implementation

- [ ] 3.1 Create `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/cross_experiment_correlations.py`
  with `CrossExperimentCorrelationsParams` (`experiment_1`, `experiment_2`,
  `trait_columns_1`, `trait_columns_2`, `min_samples=3`, `p_threshold=0.05`,
  `r_threshold=0.5`, `use_fdr=True`, `user_label`) and `CrossExperimentCorrelationsResult`
  (extends `RunLinks`)
- [ ] 3.2 Load both experiments via `_ports.reader().load_experiment(name, require_clean=True)`;
  map `CleanedVersionRequiredError` per-experiment to a named `BloomMCPError(tool_error)`
- [ ] 3.3 Reject a `None` `genotype_col` on either frame before any computation (D5)
- [ ] 3.4 Resolve `trait_columns_1`/`trait_columns_2` via `_validate_trait_subset(...,
  require_certified=True)`, independently per experiment (D6 default: full `frame.trait_cols`
  when omitted)
- [ ] 3.5 Build genotype-means via `calculate_genotype_means` (D2) for each experiment,
  then `calculate_cross_experiment_correlations(exp1_means, exp2_means, trait_cols_1,
  trait_cols_2, min_samples=params.min_samples)`
- [ ] 3.6 Raise `assumption_violated` on an empty correlation result (D6); otherwise call
  `identify_significant_correlations(corr_df, p_threshold, r_threshold, use_fdr)`,
  normalizing an empty result to a fixed-header empty frame
- [ ] 3.7 Call `summarize_correlation_results(corr_df, exp1_name=experiment_1,
  exp2_name=experiment_2)`; convert via
  `sleap_roots_analyze.data_utils.convert_to_json_serializable` (D7) before persisting
- [ ] 3.8 Build the composite `experiment=`/`based_on_version=` strings (D1) and the
  combined-snapshot `source_csv` (D1) for `store.create_run(...)`
- [ ] 3.9 Persist `correlations.csv`, `significant.csv`, `summary.json` under tool class
  `cross_experiment_correlation`; return the summary counts + `RunLinks`

## 4. Registration

- [ ] 4.1 Add `cross_experiment_correlations` to the imports and `register(...)` call in
  `bloommcp/src/bloom_mcp/sections/sleap_roots/__init__.py`
- [ ] 4.2 Add the new tool name to any tool-surface drift-guard list in
  `bloommcp/tests/test_devendor_invariants.py` (or equivalent live-registry assertion)
  so the guard stays exhaustive

## 5. Fixtures / oracle (optional, discuss with reviewer)

- [ ] 5.1 If a reviewer wants a numeric characterization oracle (this proposal does not
  fabricate one — no independently recorded cross-experiment correlation value exists
  yet, unlike PCA's turface_19 golden): construct a small two-experiment fixture pair
  with a hand-computable Pearson correlation for at least one trait pair, and record it
  as a `*_correlation_golden.json` fixture following the `turface_19_pca_golden.json`
  pattern

## 6. Validation

- [ ] 6.1 `openspec validate add-bloommcp-cross-experiment-correlations --strict` passes
- [ ] 6.2 Full bloommcp test suite green; `test_server_boots_after_devendor`-style boot
  smoke still passes with the new tool registered
- [ ] 6.3 `make bloommcp-smoke` (or equivalent live-persistence smoke) exercises
  `qc_clean` (×2) → `cross_experiment_correlations` through the real
  `SupabaseReader`/`SupabaseResultStore`, confirming the composite `experiment`/
  `based_on_version` encoding round-trips through a real manifest
