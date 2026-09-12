## Why

Two sibling follow-ups from the #466 PR review (round 7), both against
`plot_correlation_matrix`'s *reporting* surface — neither is a correctness bug:

- [#784](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/784) — the
  `min_periods` floor is a **degeneracy guard, not a significance threshold**, but the tool
  reports `strong_positive_correlations: 12` as a bare count with no per-pair `n`. At n=10,
  r=0.7 carries a 95% CI of roughly [0.13, 0.92] (Fisher z), so a pair can clear
  `min_periods`, land in the strong count, and still be entirely consistent with a weak
  underlying relationship. The per-pair overlap `n` is already computed internally
  (`notna.T @ notna`), but only the *sub-threshold* pairs are surfaced, via
  `low_overlap_trait_pairs`. A caller has no way to tell whether those 12 rest on n=10 or
  n=1000.
- [#785](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/785) — a third
  blank-cell cause is named by neither disclosure list. A pair can be globally non-constant
  (not in `zero_variance_traits`) **and** clear the overlap floor (not in
  `low_overlap_trait_pairs`) yet still be *locally* constant within the shared overlap — one
  trait takes the same value on exactly the rows where both are non-null. Pearson r is `NaN`
  there, the cell is blank, and the tool cannot tell the caller **why**.

**Why one change and not two.** They are the same block of code, the same result schema, and
the same disclosure taxonomy. Both derive from the `notna` mask and the guarded `corr` matrix
already computed at
`bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/plot_correlation_matrix.py:274-287`;
both add fields to `PlotCorrelationMatrixResult` and to the persisted manifest `params`; both
need the same cylinder-scale (~846 traits) cost check, which the existing overlap computation
is already deliberately vectorized for. Splitting them would mean two rounds of schema churn,
two rounds of manifest/snapshot test updates, and two passes at the same O(n²) question. They
are also *coupled*: #784 asks whether this tool should own its overlap threshold instead of
inheriting `qc_clean`/`qc_inspect`'s `_CANONICAL_MIN_SAMPLES_PER_TRAIT`, and that constant's
value is exactly the boundary between #785's new bucket and the existing
`low_overlap_trait_pairs` one. The threshold has to be settled before the taxonomy can be.

Sibling issues [#747](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/747)
(rendered heatmap not masked per-cell) and
[#748](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/748) (histogram/
boxplot sample-size disclosure) are deliberately **out of scope** — see `design.md`
Decision 6.

## What Changes

- **`strong_correlation_pairs` (new result field, #784).** The pairs behind
  `strong_positive_correlations`/`strong_negative_correlations`, each carrying its Pearson
  `r`, its pairwise overlap `overlap_n`, and a Fisher-z 95% confidence interval
  (`ci_low`/`ci_high`). Ordered **ascending by `overlap_n`** — weakest evidence first — and
  capped, so the cap can never hide the worst-supported pair. The two existing counts stay
  as the authoritative totals.
- **`plot_correlation_matrix` owns its overlap threshold (#784).** `_MIN_CORR_OVERLAP` becomes
  a module-level constant with its own documented rationale (a pairwise degeneracy floor)
  instead of an alias for `_qc_shared._CANONICAL_MIN_SAMPLES_PER_TRAIT` (a per-trait
  completeness convention). **Same value (10), no behavior change** — this decouples two
  concepts that only coincidentally agree, so a future QC-side retune cannot silently move
  correlation's reporting boundary.
- **`locally_constant_trait_pairs` (new result field, #785).** The third and final blank-cell
  bucket, derived by elimination from the guarded `corr` matrix rather than recomputed — every
  `NaN` off-diagonal cell now falls into exactly one named bucket.
- **`heatmap_caveat` is deliberately NOT extended to the new bucket.** It exists to warn about
  the *masking mismatch* between the guarded JSON and the unguarded rendered PNG (#747). A
  locally-constant pair is `NaN` in the vendored delegate's independent computation too, so the
  image and the JSON already agree and there is no mismatch to warn about. See `design.md`
  Decision 4.
- Both new fields are stamped into the persisted run's manifest `params`, matching the existing
  uncapped-lists-in-manifest precedent.
- Module docstring updated: the two "tracked at #784 / #785" disclosure paragraphs become
  descriptions of shipped behavior.

## Impact

- Affected specs: `bloommcp-viz-tools` (2 ADDED requirements, 1 MODIFIED)
- Affected code:
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/plot_correlation_matrix.py`
  - `bloommcp/tests/tools/test_plot_correlation_matrix_tool.py`
- **Backward compatible**: additive result fields only; `strong_positive_correlations`,
  `strong_negative_correlations`, `zero_variance_traits`, `low_overlap_trait_pairs`, and
  `heatmap_caveat` all keep their current values and semantics.
- **Archive ordering**: this change's `bloommcp-viz-tools` delta builds on requirements that
  still live in the pending `converge-bloommcp-viz-tools` change (PR #683 merged
  2026-09-11; the change is not archived yet). It MUST be archived after that one — see
  `tasks.md` §5.
