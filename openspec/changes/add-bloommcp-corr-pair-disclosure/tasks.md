## 1. RED — tests that fail against today's code

All in `bloommcp/tests/tools/test_plot_correlation_matrix_tool.py` unless stated, using the
existing `injected_ports` fixture (`FakeReader` + `FakeResultStore`) and the `_run(...)` helper.

- [x] 1.0 Write every test below **first**, run them against unmodified code, and record that
      each one fails for the expected reason (missing field / wrong bucket), not an import
      error. The TDD gate is this checkbox, not the prose.

### 1.1 `strong_correlation_pairs` and the uncapped summaries (#784)

- [x] 1.1.1 `test_strong_pairs_report_overlap_and_ci` — a frame with two known strong pairs at
      deliberately different overlaps (one at exactly n=10, one at full n). Assert each entry's
      `traits`/`r`/`overlap_n` match an independent `df[[a, b]].dropna()` recomputation, and
      that the entries account for the pairs behind the two counts.
- [x] 1.1.2 `test_strong_pairs_ordered_by_ascending_overlap` — with ≥ 3 strong pairs at distinct
      overlaps, `[p.overlap_n for p in result.strong_correlation_pairs]` is non-decreasing and
      `[0]` is the global minimum-overlap strong pair.
- [x] 1.1.3 `test_strong_pairs_capped_but_counts_stay_true` — synthesize more strong pairs than
      `_MAX_STRONG_PAIRS_REPORTED`; assert `len(...) == cap`, that
      `strong_positive_correlations + strong_negative_correlations` equals the independently
      computed total, and that the smallest-overlap pair is still present (the cap truncates the
      well-supported end — `design.md` Decision 2).
- [x] 1.1.4 `test_strong_pair_overlap_summaries_are_uncapped` — same over-cap frame: assert
      `strong_pair_overlap_min`/`_median`/`_max` match an independent computation over **all**
      strong pairs, not just the capped sample, and that `_min == strong_correlation_pairs[0]
      .overlap_n`.
- [x] 1.1.5 `test_fisher_ci_matches_closed_form` — oracle: for a known (r, n), `ci_low`/`ci_high`
      equal `tanh(arctanh(r) ± z / sqrt(n - 3))` for the exact two-sided 95% normal
      quantile `z`, to a tight tolerance.
- [x] 1.1.6 `test_perfectly_collinear_pair_reports_null_ci` — two exactly collinear traits over
      ≥ 10 rows (r == 1.0): `ci_low`/`ci_high` are `None`, **not** a degenerate `[1.0, 1.0]` and
      not `inf`/`NaN` (`design.md` Decision 3 — a zero-width interval is false precision).
- [x] 1.1.7 `test_ci_is_null_below_four_overlap` — exercises the `n <= 3` branch, unreachable
      while the floor is 10: `monkeypatch.setattr(plot_correlation_matrix_tool,
      "_MIN_CORR_OVERLAP", 2)` and assert a 3-row-overlap strong pair reports `None` bounds.
- [x] 1.1.8 `test_result_and_manifest_are_strict_json` — round-trip both the result
      (`json.loads(result.model_dump_json())`) and the persisted manifest bytes through
      `json.loads(..., parse_constant=_reject)` so a bare `NaN`/`Infinity` token fails the test.
      Manifests are written by `storage_backend._json_bytes` with `json.dumps`' default
      `allow_nan=True`, so this is a live hazard, not a hypothetical.
- [x] 1.1.9 `test_strong_pairs_empty_when_nothing_is_strong` — no |r| > 0.7 pair → empty list,
      both counts 0, and the three overlap summaries `None`.
- [x] 1.1.10 `test_strong_pairs_ordering_is_deterministic` — two runs over the same frame give
      identical lists, including a deliberate tie in `overlap_n` (broken by descending `|r|`
      then trait order).

### 1.2 `locally_constant_trait_pairs` (#785)

Fixture shape verified against the pinned pandas — `a = [0..14] + [NaN]*15`,
`b = [7.0]*15 + [0..14]` gives overlap 15, `std(a)=4.47`, `std(b)=3.11`, `corr(a,b)=NaN`.

- [x] 1.2.1 `test_locally_constant_pair_is_named` — that frame: the pair is in
      `locally_constant_trait_pairs`, in neither `zero_variance_traits` nor
      `low_overlap_trait_pairs`, and in neither strong count.
- [x] 1.2.2 `test_every_nan_cell_has_exactly_one_reason` — the taxonomy-totality property, on a
      frame exercising all three causes at once. Recompute the guarded `corr` independently,
      collect every upper-triangle `NaN` cell, and assert each is claimed by exactly one bucket
      (none unexplained, none double-counted).
- [x] 1.2.3 `test_low_overlap_and_zero_variance_pair_lands_in_one_bucket` — a pair that is
      **both** sub-threshold and involves a zero-variance trait. Pins the mask-not-list
      subtraction (`design.md` Decision 1): it must be claimed by `zero_variance_traits` alone
      and must NOT leak into `locally_constant_trait_pairs`.
- [x] 1.2.4 `test_locally_constant_capped_with_uncapped_count` — a "saturating" trait
      (globally non-constant, one value on every row where observed) against a wide frame
      produces more than the cap; assert the list is truncated and
      `locally_constant_pair_count` still reports the true total.
- [x] 1.2.5 `test_locally_constant_does_not_populate_heatmap_caveat` — only that bucket
      non-empty → `heatmap_caveat is None`, plus a `Figure.text` spy asserting no footnote is
      drawn, mirroring the existing `test_no_annotation_added_when_nothing_is_flagged`.
- [x] 1.2.6 `test_locally_constant_pairs_survive_a_manifest_json_round_trip` — mirroring the
      existing `test_low_overlap_pairs_survive_a_manifest_json_round_trip`.

### 1.3 `zero_variance_traits`' fourth case (`design.md` Decision 7)

- [x] 1.3.1 `test_non_finite_trait_is_reported_as_zero_variance` — a trait that varies but
      contains `+inf`: assert it lands in `zero_variance_traits` (today's behavior, previously
      undocumented) and in no other list, so the corrected taxonomy is pinned. Sits alongside
      the existing `test_single_non_null_value_trait_is_reported_as_zero_variance`.

### 1.4 Threshold decoupling (#784, `design.md` Decision 5)

- [x] 1.4.1 `test_min_corr_overlap_is_owned_not_aliased` — assert
      `plot_correlation_matrix_tool._MIN_CORR_OVERLAP == 10` and that the module namespace does
      **not** bind the QC name (`not hasattr(module, "_CANONICAL_MIN_SAMPLES_PER_TRAIT")`), so
      the alias cannot silently return. Deliberately does **not** assert the two constants are
      still equal — that equality is the coincidence being decoupled, and pinning it would turn
      the next legitimate QC retune into a test failure whose cheapest fix is to re-alias.
- [x] 1.4.2 Confirm the existing `test_low_overlap_boundary_at_min_periods` parametrization
      still passes unchanged — the boundary must not move.

### 1.5 Manifest

- [x] 1.5.1 `test_new_disclosure_fields_stamped_into_manifest_params` — both new lists, the
      three overlap summaries, and `locally_constant_pair_count` are recoverable from the
      persisted manifest, matching the precedent for the existing disclosure lists.

## 2. GREEN — implementation

`bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/plot_correlation_matrix.py` only.

- [x] 2.1 Replace `_MIN_CORR_OVERLAP = _CANONICAL_MIN_SAMPLES_PER_TRAIT` with a module-owned
      `10` plus its own rationale comment (honest that 10 is inherited by value and conservative
      for the degeneracy argument, not derived from it); drop that name from the `_qc_shared`
      import, keeping `_validate_experiment_name` and adding `_finite_or_none`.
- [x] 2.2 Add `_MAX_STRONG_PAIRS_REPORTED = 20`, `_MAX_LOCALLY_CONSTANT_PAIRS_REPORTED = 20`
      (20, not 50: `test_provenance_stamped_seed_none_and_links_returned` rejects any result
      field whose repr exceeds 5,000 chars, which 50 structured pairs clears on a 20-trait
      experiment — respect that contract rather than relax it),
      and a `StrongCorrelationPair` Pydantic model (`traits: list[str]`, `r: float`,
      `overlap_n: int`, `ci_low: Optional[float]`, `ci_high: Optional[float]`) whose field
      descriptions state that the CI is not a significance test, assumes approximate bivariate
      normality that raw trait data often violates, and carries no multiplicity correction.
- [x] 2.3 Add to `PlotCorrelationMatrixResult`: `strong_correlation_pairs`,
      `strong_pair_overlap_min`/`_median`/`_max`, `locally_constant_trait_pairs`,
      `locally_constant_pair_count`. Descriptions cover the ascending-`overlap_n` ordering, both
      caps (and that the counts/scalars are the true totals), and the new bucket's
      derived-by-remainder nature.
- [x] 2.4 Expand `zero_variance_traits`' description with the non-finite fourth case
      (`design.md` Decision 7). Description only — the guard is already correct.
- [x] 2.5 Compute the strong-pair list next to the existing `high_pos`/`high_neg`, reusing
      `overlap_counts` and the upper-triangle mask. Stay vectorized: mask → `np.where` →
      `np.lexsort((j, i, -|r|, overlap_n))` → slice to the cap → materialize only the capped
      entries. Compute the three summaries over the full flagged subset before slicing.
- [x] 2.6 Implement the Fisher-z CI as a module-level helper returning `(None, None)` for
      `|r| >= 1` or `n <= 3`, and coerce both bounds through `_qc_shared._finite_or_none`.
- [x] 2.7 Compute `locally_constant_trait_pairs` as the residual bucket: upper-triangle `NaN`
      cells of the guarded `corr`, minus the zero-variance rows/columns, minus the raw
      `overlap_counts < _MIN_CORR_OVERLAP` **mask** — not the published `low_overlap_trait_pairs`
      list (`design.md` Decision 1). Record the uncapped count before truncating.
- [x] 2.8 Stamp every new field into the persisted run's `params`, and pass them all into the
      returned `PlotCorrelationMatrixResult`.
- [x] 2.9 Leave `heatmap_caveat`'s population condition **untouched** (`design.md` Decision 4).

## 3. Docs

- [x] 3.1 Rewrite the module docstring's "What that threshold does and does not buy you"
      paragraph: per-pair `n` and a CI are now reported, so drop the "tracked at #784" deferral
      and describe the shipped fields, their ordering, their caps, and the CI's assumptions.
- [x] 3.2 Replace "Known, narrow taxonomy gap (not fixed, disclosed)" with the shipped
      three-bucket taxonomy: how the residual is derived, why the subtraction uses the mask, why
      `heatmap_caveat` deliberately does not fire for it, and the residual risk that a future
      pandas `NaN` cause would be absorbed under this name.
- [x] 3.3 Document `_MIN_CORR_OVERLAP`'s new independence in both the module docstring and the
      constant's comment, including that ownership is not derivation.
- [x] 3.4 Keep the docstring's #768 paragraph coherent with its rewritten neighbours — it now
      sits next to fields that supply the numeric surface its remedy wants (`design.md`
      Decision 6).
- [x] 3.5 Update the test module's docstring to cover the new fields.
- [x] 3.6 Add the new fields to `bloommcp/tests/smoke/test_plot_correlation_matrix_smoke.py`'s
      assertions — it is the only test exercising this surface through the real MCP server, and
      cylinder is exactly the scale the caps exist for.

## 4. Verify

- [x] 4.1 `cd bloommcp && uv run --extra test pytest tests/tools/test_plot_correlation_matrix_tool.py -q`
      — all green, including every pre-existing test unchanged.
- [x] 4.2 `cd bloommcp && uv run --extra test pytest tests/tools/ -q` — no sibling regression, in
      particular `test_viz_snapshot.py` (the rendered PNG must stay byte-identical: nothing here
      touches rendering) and `test_devendor_invariants.py`.
- [x] 4.3 `cd bloommcp && uv run black --check src tests && uv run ruff check src tests`.
- [x] 4.4 `openspec validate add-bloommcp-corr-pair-disclosure --strict`.
- [x] 4.5 Re-run `benchmarks/corr_pair_disclosure_bench.py` and confirm `design.md`'s tables
      still match (it exits non-zero if the taxonomy fuzz finds an unexplained cell).
- [x] 4.6 Re-read the diff against `design.md` — confirm no existing field's value changed and
      `heatmap_caveat` is untouched.

## 5. Archive ordering (post-merge, not part of this PR)

- [ ] 5.1 Archive `converge-bloommcp-viz-tools` **first**. This change's deltas MODIFY three
      requirements that still live in that pending change, and `openspec validate --strict`
      passes without checking that a MODIFIED target exists — so archiving in the wrong order
      would silently drop the requirements this change builds on, with no error.
- [ ] 5.2 Mirror that ordering note into `openspec/changes/converge-bloommcp-viz-tools/tasks.md`
      so whoever archives that change sees the dependency from its side too.
