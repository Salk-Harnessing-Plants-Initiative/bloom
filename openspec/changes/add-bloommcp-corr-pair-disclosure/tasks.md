## 1. RED — tests that fail against today's code

All in `bloommcp/tests/tools/test_plot_correlation_matrix_tool.py`, using the existing
`injected_ports` fixture (`FakeReader` + `FakeResultStore`) and the `_run(...)` helper.
Confirm each FAILS before writing any implementation.

### 1.1 `strong_correlation_pairs` (#784)

- [ ] 1.1.1 `test_strong_pairs_report_overlap_and_ci` — build a frame with two known strong
      pairs at deliberately different overlaps (one at n=10, one at full n). Assert each
      reported entry's `traits`/`r`/`overlap_n` match an independent `df[[a, b]].dropna()`
      recomputation, and that the entries account for the pairs behind
      `strong_positive_correlations`/`strong_negative_correlations`.
- [ ] 1.1.2 `test_strong_pairs_ordered_by_ascending_overlap` — with ≥ 3 strong pairs at
      distinct overlaps, assert `[p.overlap_n for p in result.strong_correlation_pairs]` is
      non-decreasing and `[0]` is the global minimum-overlap strong pair.
- [ ] 1.1.3 `test_strong_pairs_capped_but_counts_stay_true` — synthesize more strong pairs than
      `_MAX_STRONG_PAIRS_REPORTED`; assert `len(strong_correlation_pairs) == cap`, that
      `strong_positive_correlations + strong_negative_correlations` still equals the
      independently-computed total, and that the *smallest*-overlap pair is still present (the
      cap truncates the well-supported end — `design.md` Decision 2).
- [ ] 1.1.4 `test_fisher_ci_matches_closed_form` — oracle: for a known (r, n), assert
      `ci_low`/`ci_high` equal `tanh(arctanh(r) ± 1.96 / sqrt(n - 3))` to a tight tolerance.
- [ ] 1.1.5 `test_perfectly_collinear_pair_has_finite_ci` — two exactly collinear traits over
      ≥ 10 rows (r == 1.0): assert `ci_low`/`ci_high` are finite (degenerate at `r`), not `inf`
      or `NaN`, and that the result survives a JSON round-trip
      (`json.loads(result.model_dump_json())`).
- [ ] 1.1.6 `test_strong_pairs_empty_when_nothing_is_strong` — a frame with no |r| > 0.7 pair
      yields `strong_correlation_pairs == []` and both counts 0.
- [ ] 1.1.7 `test_strong_pairs_ordering_is_deterministic` — two runs over the same frame
      produce identical `strong_correlation_pairs` (ties broken by descending `|r|` then trait
      order, per `design.md` Decision 2).

### 1.2 `locally_constant_trait_pairs` (#785)

- [ ] 1.2.1 `test_locally_constant_pair_is_named` — construct trait `a` globally non-constant
      and trait `b` globally non-constant, whose shared non-null overlap is ≥ the minimum and
      on which `b` is constant. Assert the pair is in `locally_constant_trait_pairs`, in
      neither `zero_variance_traits` nor `low_overlap_trait_pairs`, and in neither strong count.
- [ ] 1.2.2 `test_every_nan_cell_has_exactly_one_reason` — the taxonomy-totality property. For
      a deliberately degenerate frame exercising all three causes at once, recompute the
      guarded `corr` independently, collect every upper-triangle `NaN` cell, and assert each is
      explained by exactly one of the three lists (no cell unexplained, no cell double-counted).
- [ ] 1.2.3 `test_locally_constant_does_not_populate_heatmap_caveat` — only
      `locally_constant_trait_pairs` non-empty → `heatmap_caveat is None` (`design.md`
      Decision 4). Pair with a monkeypatched `Figure.text` spy to assert no footnote is drawn,
      mirroring the existing `test_no_annotation_added_when_nothing_is_flagged`.
- [ ] 1.2.4 `test_locally_constant_pairs_survive_a_manifest_json_round_trip` — mirroring the
      existing `test_low_overlap_pairs_survive_a_manifest_json_round_trip` (list-of-lists
      through JSON).

### 1.3 Threshold decoupling (#784, `design.md` Decision 5)

- [ ] 1.3.1 `test_min_corr_overlap_is_owned_not_aliased` — assert
      `plot_correlation_matrix._MIN_CORR_OVERLAP == 10`, that it still equals
      `_qc_shared._CANONICAL_MIN_SAMPLES_PER_TRAIT` today (so the decoupling is behavior-
      preserving), **and** that the module no longer imports that name — read the module source
      and assert `_CANONICAL_MIN_SAMPLES_PER_TRAIT` does not appear in its import block, so the
      alias cannot silently return.
- [ ] 1.3.2 Confirm the existing `test_low_overlap_boundary_at_min_periods` parametrization
      still passes unchanged — the boundary must not move.

### 1.4 Manifest + scale

- [ ] 1.4.1 `test_new_disclosure_fields_stamped_into_manifest_params` — both
      `strong_correlation_pairs` and `locally_constant_trait_pairs` are recoverable from the
      persisted manifest, matching the existing precedent for the other disclosure lists.
- [ ] 1.4.2 `test_wide_frame_stays_vectorized` — a wide (few-hundred-trait) frame completes
      within a generous wall-clock budget, guarding against a future non-vectorized refactor of
      the new code (`design.md` Risks). Keep the budget loose enough not to be flaky in CI.

## 2. GREEN — implementation

`bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/plot_correlation_matrix.py` only.

- [ ] 2.1 Replace `_MIN_CORR_OVERLAP = _CANONICAL_MIN_SAMPLES_PER_TRAIT` with a module-owned
      `10` plus its own rationale comment; drop `_CANONICAL_MIN_SAMPLES_PER_TRAIT` from the
      `_qc_shared` import (keep `_validate_experiment_name`).
- [ ] 2.2 Add `_MAX_STRONG_PAIRS_REPORTED = 50` and a `StrongCorrelationPair` Pydantic model
      (`traits: list[str]`, `r: float`, `overlap_n: int`, `ci_low: Optional[float]`,
      `ci_high: Optional[float]`) with field descriptions that state plainly that the CI is not
      a significance test.
- [ ] 2.3 Add `strong_correlation_pairs` and `locally_constant_trait_pairs` to
      `PlotCorrelationMatrixResult`, both `default_factory=list`, with descriptions covering:
      the ascending-`overlap_n` ordering and the cap (and that the counts are the true totals);
      and, for the new bucket, the taxonomy-totality claim plus the honest non-finite-overlap
      caveat (`design.md` Decision 1).
- [ ] 2.4 Compute the strong-pair list next to the existing `high_pos`/`high_neg` computation,
      reusing the already-built `overlap_counts` and the upper-triangle mask. Stay vectorized:
      mask → `np.where` → `np.argsort` on the flagged subset → slice to the cap → materialize
      only the capped entries.
- [ ] 2.5 Implement the Fisher-z CI as a small module-level helper with the two guards from
      `design.md` Decision 3 (`|r| >= 1` → degenerate interval; `n <= 3` → `None`).
- [ ] 2.6 Compute `locally_constant_trait_pairs` as the residual bucket: upper-triangle `NaN`
      cells of the guarded `corr`, minus the zero-variance rows/columns, minus the low-overlap
      mask. No new variance computation (`design.md` Decision 1).
- [ ] 2.7 Stamp both new fields into the persisted run's `params` alongside the existing
      uncapped lists, and pass both into the returned `PlotCorrelationMatrixResult`.
- [ ] 2.8 Leave `heatmap_caveat`'s population condition **untouched** (`design.md` Decision 4).

## 3. Docs

- [ ] 3.1 Rewrite the module docstring's "What that threshold does and does not buy you"
      paragraph: the per-pair `n` is now reported, so drop the "tracked at #784" deferral and
      describe the shipped field, its ordering, and its cap.
- [ ] 3.2 Replace the "Known, narrow taxonomy gap (not fixed, disclosed)" paragraph with the
      shipped three-bucket taxonomy, including why `heatmap_caveat` deliberately does not fire
      for the new bucket and the non-finite-overlap caveat.
- [ ] 3.3 Document `_MIN_CORR_OVERLAP`'s new independence in both the module docstring and the
      constant's own comment.
- [ ] 3.4 Update the test module's docstring to cover the new fields.

## 4. Verify

- [ ] 4.1 `uv run --extra test pytest tests/tools/test_plot_correlation_matrix_tool.py -q` —
      all green, including every pre-existing test unchanged.
- [ ] 4.2 `uv run --extra test pytest tests/tools/ tests/smoke/ -q` — no sibling regression, in
      particular `test_viz_snapshot.py` (the rendered PNG must be byte-identical: nothing in
      this change touches rendering) and `test_devendor_invariants.py`.
- [ ] 4.3 `uv run black --check src tests && uv run ruff check src tests` on `bloommcp/`.
- [ ] 4.4 `openspec validate add-bloommcp-corr-pair-disclosure --strict`.
- [ ] 4.5 Re-read the diff against `design.md` — confirm no existing field's value changed and
      `heatmap_caveat` is untouched.

## 5. Archive ordering (post-merge, not part of this PR)

- [ ] 5.1 This change's `bloommcp-viz-tools` delta MODIFIES a requirement that still lives in
      the pending `converge-bloommcp-viz-tools` change. Archive `converge-bloommcp-viz-tools`
      **first**; archiving this one against a missing `openspec/specs/bloommcp-viz-tools/spec.md`
      would drop the requirements it builds on (`design.md` Risks).
