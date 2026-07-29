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
- [x] 7.2 Full bloommcp test suite green (759 passed after §8); ruff + black clean at
  the repo-pinned versions (ruff 0.9.9, black 26.3.1)
- [x] 7.3 `make bloommcp-smoke` / live dev-stack verification — **ran in this PR's own
  CI** (`Dev stack smoke` job) and initially **failed**: `result["outputs"]` came back
  empty even though `store.commit()` had genuinely succeeded. Root-caused and fixed —
  see §8.1. Re-verified server-side correctness directly against the live container for
  both this tool and the long-shipped `pca_analysis` (confirming the bug was a
  pre-existing client-side test-infrastructure gap, not specific to this tool); the
  fixed smoke test itself still needs a container rebuild to re-run end-to-end, which
  CI's next run on this branch will do.

## 8. Post-PR-review fixes (two independent 5-agent reviews, posted ~1s apart)

Both reviews' findings, reconciled and resolved:

- [x] 8.1 **BLOCKING — live smoke test failing in CI.** Root-caused to
  `bloommcp/tests/smoke/conftest.py`'s `_call_tool_sync` reading `result.data`
  (fastmcp's lossy client-side reconstruction of the server's JSON, which collides two
  untitled nested `object` schemas — the top-level result and the `outputs: dict[str,
  str]` field — on the same auto-generated type name `Root`) instead of
  `result.structured_content` (the server's actual, correct JSON). Confirmed
  server-side correctness was never in question by reproducing the same empty-`outputs`
  behavior against the live container for the long-shipped `pca_analysis` tool too —
  this was a latent gap in *every* `RunLinks`-based tool's live-smoke coverage, only
  surfaced because this PR was the first to assert on `outputs` via the container
  transport. Fixed at the shared `conftest.py` level (one line), benefiting every smoke
  test in the package.
- [x] 8.2 **BLOCKING — composite-key truncation for dotted filenames.** The originally
  shipped `f"{Path(e1).stem}__x__{Path(e2).stem}"` composite is silently truncated by
  `AnalysisDir`'s own re-applied `Path(...).stem` whenever either original stem
  contains a dot (reproduced by the reviewer: `"my.experiment.v2.csv"` collapses the
  composite to `"my.experiment"`, losing `experiment_2` entirely — a silent storage
  collision risk, not a crash). Fixed with `_storage_safe_stem(name) ->
  Path(name).stem.replace(".", "_")`, making the composite string itself dot-free and
  therefore immune to re-stemming for *any* filename. New tests exercise the real
  `AnalysisDir` class directly (not `FakeResultStore`, whose simplified stem helper
  cannot reproduce this real-backend bug) — see design.md D1's update.
- [x] 8.3 **IMPORTANT — self-correlation unrejected.** `experiment_1 == experiment_2`
  now raises `BloomMCPError(invalid_input)` before any I/O.
- [x] 8.4 **IMPORTANT — argument-order sensitivity undiscoverable from the schema.**
  Both `experiment_1`/`experiment_2` field descriptions now state that argument order
  determines the storage key.
- [x] 8.5 **IMPORTANT — path-traversal protection was incidental, not explicit.** The
  tool now calls the existing `_qc_shared._validate_experiment_name` guard explicitly
  on both experiment names (design.md D14). `pca_analysis`/`clustering` share this same
  pre-existing gap — fixing it there is a follow-up, out of scope here.
- [x] 8.6 **IMPORTANT — two "either side" tests only tested one side.**
  `test_missing_genotype_role_either_side_rejected` and
  `test_non_finite_value_either_side_rejected` now genuinely exercise both
  `experiment_1` and `experiment_2`.
- [x] 8.7 Verified (empirically, against the actual upstream source) that the
  "`significant.csv` schema instability for exactly-one-qualifying-row" concern does
  not hold: `identify_significant_correlations`'s else-branch adds
  `p_value_corrected`/`significant_fdr` regardless of row count once `strong_corr` has
  ≥1 row; the only genuinely columnless case is the top-level `len(strong_corr) == 0`
  early return, already normalized by `_normalized_significant`. No code change; this
  finding did not survive verification.
- [x] 8.8 Added `test_constant_genotype_mean_trait_yields_nan_correlation_not_a_crash`
  (design.md's accepted NaN-pass-through risk was previously undiscussed by any test)
  and `test_exactly_one_shared_genotype_is_degenerate`.
- [x] 8.9 Added `test_upstream_min_samples_no_op_still_present` — a regression pin
  calling the raw upstream delegate directly, so a future upstream fix (or fixture
  drift) fails this test loudly instead of the workaround silently going stale.
- [x] 8.10 Fixed `test_reproduces_golden_correlation_unfiltered` to compare actual
  `correlation`/`p_value` floats against the golden (it previously only checked
  counts).
- [x] 8.11 Extended `test_source_csv_content_addresses_both_inputs` to vary
  `experiment_1` too (previously only varied `experiment_2`).
- [x] 8.12 Parametrized `test_trait_columns_validated_independently` (was a manual
  `for` loop that would mask later cases on an early failure).
- [x] 8.13 Centralized `@`/`|`/`__x__` as module-level constants
  (`_RESERVED_ENCODING_CHARS`, `_COMPOSITE_SEPARATOR`) instead of repeated bare
  literals at the guard and the builder.
- [x] 8.14 Simplified `_normalized_significant`'s redundant `sig_df.empty and
  len(sig_df.columns) == 0` to `len(sig_df.columns) == 0` (a 0-column frame is always
  `.empty`).
- [x] 8.15 Surfaced D12's traceability limitation in `CrossExperimentCorrelationsResult`'s
  own docstring (previously only in design.md).
- [x] 8.16 Fixed design.md's Risks section citing the wrong upstream function
  (`calculate_correlations`, the public wrapper with its own zero-variance guard) for
  the NaN pass-through — the actual call path uses the private `_calculate_correlations`.
- [x] 8.17 Added a `min_samples` field-description caveat: the `ge=1` floor alone
  doesn't protect against single-replicate noise (a separate, always-enforced `< 3`
  aligned-genotypes floor does that independent of this setting).
- [x] 8.18 Noted (design.md Open Questions) that the golden fixture is not bit-for-bit
  reproducible across regenerations (~1e-16 BLAS/threading float noise) — functionally
  irrelevant given `abs=1e-6` test tolerances, but worth knowing before mistaking a
  future regeneration's diff for a real regression.

Full bloommcp suite after §8: 759 passed, 29 skipped, 0 failed. `test_cross_experiment_correlations_tool.py`: 44 tests (was 31).

## 9. Post-PR-review fixes, round 2 (re-review of commit c649f9d)

- [x] 9.1 **BLOCKING — `_storage_safe_stem`'s dot-to-underscore sanitization reopened
  the collision it closed.** `"my.experiment.csv"` and `"my_experiment.csv"` both
  sanitize to the identical stem `"my_experiment"` and would silently collide on the
  same storage key. Replaced with `_reject_unsafe_composite_stem`, rejecting a dotted
  (or, at the time, separator-embedding) stem outright instead of sanitizing it — see
  §10.1 for why the separator-embedding half of this fix was itself superseded.
- [x] 9.2 **IMPORTANT — `based_on_version`'s builder still hardcoded bare `@`/`|`.**
  Centralized into `_VERSION_SEPARATOR`/`_PAIR_SEPARATOR`, reusing the same constants
  the reserved-character guard already used.
- [x] 9.3 **IMPORTANT — path-traversal guard's message didn't name the field.**
  `_validate_experiment_name` gained an optional `label` parameter (default preserves
  every existing single-experiment caller); this tool passes `"experiment_1"`/
  `"experiment_2"` explicitly.
- [x] 9.4 **IMPORTANT — golden fixture's `min_samples_3_upstream_no_op` block recorded
  but never read by any test.** Added
  `test_upstream_min_samples_no_op_still_present_on_real_fixture_pair` — see §10.4 for
  a follow-up gap found in this same test.
- [x] 9.5 **IMPORTANT — "benefits every `RunLinks`-based tool" claim untested beyond
  this one tool.** Extended `test_pca_analysis_smoke` to assert on `outputs`.
- [x] 9.6 Strengthened the NaN pass-through test to assert `pd.isna()` directly on the
  persisted `correlations.csv` value, not just infer it from `n_significant`/
  `n_highly_significant` counts.
- [x] 9.7 Parametrized `test_missing_genotype_role_either_side_rejected` and
  `test_non_finite_value_either_side_rejected` (manual for-loops, inconsistent with
  this file's other genuinely-symmetric tests).
- [x] 9.8 Softened an overclaiming comment in `conftest.py` about the exact fastmcp
  schema-routing mechanism behind the `outputs`-empty bug (kept the confirmed symptom,
  dropped the unverified "name collision" causal claim) — itself found imprecise by
  §10's review; see §10's Testing-infrastructure-fix note in design.md.

## 10. Post-PR-review fixes, round 3 (re-review of commit a5ec16d)

- [x] 10.1 **BLOCKING — rejecting a stem containing the separator substring does not
  prevent the separator from appearing in the JOINED string.** `_COMPOSITE_SEPARATOR`
  (`"__x__"`) is self-overlapping; `stem_1="A"`/`stem_2="x__B"` and
  `stem_1="A__x"`/`stem_2="B"` both pass §9.1's guard (neither stem contains the full
  substring) yet both join to the identical `"A__x__x__B"` — confirmed by direct
  construction and a 500k-sample randomized stress test (thousands of collisions found
  in seconds). No stem-content guard can close this — the ambiguity is a property of
  the join, not either stem. **Fixed structurally**: `_composite_experiment_key`
  prefixes `stem_1` with its own length before joining
  (`f"{len(stem_1)}_{stem_1}{_COMPOSITE_SEPARATOR}{stem_2}"`), which is provably
  injective regardless of stem content (standard length-prefixed/"netstring"
  encoding). `_reject_unsafe_composite_stem`'s separator-substring branch is removed
  (renamed `_reject_dotted_stem`, dot-only now) since it is no longer needed — a stem
  containing `"__x__"` is permitted. Verified by
  `test_boundary_straddling_stems_no_longer_collide` (the exact adversarial pair) and
  `test_composite_key_round_trips_for_any_stem_pair` (a property test proving a real
  left-inverse exists for arbitrary stem content, not just spot-checked pairs — the
  testing gap that let three rounds of this bug ship).
- [x] 10.2 **BLOCKING — `spec.md` described the superseded (§9.1) sanitizing behavior
  as current**, still citing `_storage_safe_stem` and asserting the composite key
  "contains a recognizable, sanitized form of both original stems" — the opposite of
  the shipped reject-outright behavior even before this round's further fix. Rewritten
  to describe the length-prefixed encoding and its injectivity guarantee; added a
  scenario for the boundary-straddling case.
- [x] 10.3 **IMPORTANT — design.md's D1 opening bullet still cited `_storage_safe_stem`**,
  contradicted by the "Final fix" narrative later in the same section. Corrected,
  along with two other stale "second review pass" labels (D8, D14 sections) that
  should have read "third" — a minor version of the same internal-consistency gap.
- [x] 10.4 **IMPORTANT — `test_upstream_min_samples_no_op_still_present_on_real_fixture_pair`
  (added §9.4) omitted the golden block's own `p_value` field**, even though it
  asserts `n_genotypes`/`correlation` from the same block. Added.

`test_cross_experiment_correlations_tool.py`: 50 tests, all passing (§10.1 removes the
4 now-invalid `embeds-separator` parametrized cases from §9.1's
`test_unsafe_composite_stem_rejected` — a stem containing the separator substring is no
longer rejected — and adds 2 new tests in their place:
`test_boundary_straddling_stems_no_longer_collide` and
`test_composite_key_round_trips_for_any_stem_pair`). ruff/black clean at the
repo-pinned versions.
