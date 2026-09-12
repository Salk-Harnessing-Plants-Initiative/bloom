## Why

[#748](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/748) — `plot_trait_boxplots`
discloses **no sample size anywhere**, and neither it nor `plot_trait_histograms` discloses how
much of a trait's raw missingness was dropped before rendering. Both read **raw, uncleaned** data
by design (the same pre-clean EDA posture as `qc_inspect`) and delegate rendering to
`sleap_roots_analyze.visualization`, whose handling of missing and non-finite values is silent.

Three concrete, verified gaps (each probed against the live delegate, not assumed):

- **A box built from 2 points is visually indistinguishable from one built from 200.**
  `create_trait_boxplots_by_genotype` titles each subplot `f"{trait}"` — no `n`, unlike
  `create_trait_histograms`, which titles each panel `f"{trait}\n(n={count})"`. A genotype group
  reduced to a handful of rows by disjoint per-trait missingness renders as a normal-looking box.
- **A genotype group with zero non-null rows for a trait vanishes from the panel entirely.** The
  delegate does `df[[trait, genotype_col]].dropna()` and takes its tick labels from what survives,
  so the group gets no tick, no box, and no gap — a reader cannot tell the genotype was ever
  there. Confirmed: a 2-genotype frame with one all-null group renders ticks `['B']`.
- **A trait carrying `±inf` corrupts the affected box silently.** Its quartiles come back `NaN`
  (confirmed: the box patch's upper edge is literally `nan`), so the box renders broken, with no
  error and no flag. The same value makes `plot_trait_histograms` *fail the whole run* with the
  delegate's own `ValueError: supplied range of [1.0, inf] is not finite` — redacted by the error
  path into a message that names no trait and offers no remedy.

This is the same "silently misleading, no error" category #466 already fixed once for
`plot_correlation_matrix` (`zero_variance_traits`, `low_overlap_trait_pairs`, `heatmap_caveat`)
and #784/#785 extended with per-pair evidence. Those changes established the pattern this one
reuses: compute the disclosure in bloommcp from the same selected frame, report it in the result
*and* the persisted manifest, and put a signal on the image itself for the caller who only ever
opens the PNG.

**Why both tools in one change.** They are the same wrapper shape, the same `_viz_shared` helpers,
the same batching/pagination path, the same persistence flow, and the same missingness question
asked at two different granularities (per trait vs per trait-per-genotype). The non-finite case
spans both and has to be settled once, since the two tools' delegates react to it differently
(one raises, one corrupts silently). Splitting would mean two rounds of schema churn and two
passes at the same cylinder-scale (846-trait) cost question.

Sibling issues [#747](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/747)
(heatmap not masked per-cell) and
[#768](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/768) (snapshot test
can't catch a single-cell defect) stay **out of scope** — see `design.md` Decision 8.

## What Changes

### `plot_trait_boxplots`

- **Per-group sample sizes, computed here and disclosed three ways.** One vectorized
  `groupby(genotype)[traits].count()` yields every (trait × genotype) box's `n`. From it:
  - `group_n_min`/`_median`/`_max` — uncapped scalars over **every** box in the run.
  - `small_sample_groups` — boxes with `0 < n < _MIN_PLOTTED_SAMPLES`, ascending by `n` (weakest
    first, so the cap truncates the best-supported end), capped at 20, with an uncapped
    `small_sample_group_count`.
  - `absent_genotype_groups` — boxes with `n == 0`, which are **not drawn at all**; capped at 20
    with an uncapped `absent_genotype_group_count`.
  - `group_sample_sizes.csv` — a committed run output carrying every (trait, genotype) pair's
    `n_plotted` and `n_non_finite`, uncapped. Links, not blobs: the complete table is a download,
    the response carries only the flagged tail and the scalars.
- **A sample-size footer is drawn on every rendered page** (`sample_size_note`), not only when
  something is flagged — this is the only signal a PNG-only reader gets, and "min 2, median 8,
  max 10 across 57 groups" is exactly the context the image otherwise withholds. On a paginated
  render the footer is **page-scoped** (derived from that page's `page_traits`), so it never
  names a trait the viewer cannot see. It escalates to a `⚠`-prefixed dark-red warning naming the
  flagged groups (capped at 10, `+N more`) when any is small, absent, or non-finite.
- `rows_missing_genotype`, `n_genotype_groups`, `n_rows_read` — the denominators behind the
  above; rows whose genotype value itself is null appear in no box at all.
- `non_finite_traits` (capped) + `non_finite_trait_count` — traits whose `±inf` values make the
  affected group's quartiles `NaN`.

### `plot_trait_histograms`

- **Per-trait plotted `n` and missingness**, mirroring `qc_inspect`'s
  `per_trait_nan_fraction`: `plotted_n_min`/`_median`/`_max` (uncapped scalars),
  `low_sample_traits` (traits under the floor, with each one's `plotted_n` and `nan_fraction`,
  ascending, capped at 20) + `low_sample_trait_count`, and a committed
  `trait_sample_sizes.csv` with every trait's `n_plotted`/`n_missing`/`nan_fraction`.
  `plotted_n == 0` is the "No data" panel, named in the result rather than only in the image.
- **A non-finite trait now fails with a structured error that names it.** The render cannot
  succeed (matplotlib raises on a non-finite histogram range), so this replaces an opaque,
  redacted delegate `ValueError` with `BloomMCPError(code="assumption_violated")` naming the
  offending traits and pointing at `qc_clean`/`remove_outliers`. Detected **before** any run is
  created, so no staging directory is written and torn down.
- **The rendered image is unchanged** — the delegate already titles every panel `(n=…)`, so the
  PNG-only gap this change closes for boxplots does not exist here (`design.md` Decision 4).

### Shared

- `_viz_shared._MIN_PLOTTED_SAMPLES = 5` — the viz family's own degeneracy floor, deliberately
  **not** aliased to `_qc_shared._CANONICAL_MIN_SAMPLES_PER_TRAIT` (10), following the ownership
  decoupling #784 applied to `_MIN_CORR_OVERLAP`. Below 5 points a box's quartiles interpolate
  between at most 4 observations and its whiskers/outlier dots are artifacts of individual
  values. Like that constant, it is a degeneracy floor, **not** a sufficiency threshold
  (`design.md` Decision 3).
- Every new field is stamped into the persisted run's `params`, so a later manifest read
  recovers the same signal a live call got.

## Impact

- Affected specs: `bloommcp-viz-tools` (4 ADDED requirements, 1 MODIFIED)
- Affected code:
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/plot_trait_boxplots.py`
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/plot_trait_histograms.py`
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/_viz_shared.py`
  - `bloommcp/tests/tools/test_plot_trait_boxplots_tool.py`,
    `test_plot_trait_histograms_tool.py`, `test_viz_shared.py`
  - `bloommcp/tests/smoke/test_plot_trait_boxplots_smoke.py`,
    `test_plot_trait_histograms_smoke.py`
  - `bloommcp/tests/fixtures/plot_baselines/boxplots_turface_19_baseline.png` + `MANIFEST.json`
    — **the boxplot render changes on purpose**, so its snapshot baseline is regenerated via
    `scripts/gen_plot_snapshots_golden.py` and the printed RMS is quoted in the PR, per that
    script's review convention. The histogram and correlation baselines are untouched.
- **Backward compatible except for one deliberate error-path change**: all result fields are
  additive and every existing field keeps its value. The exception is a histogram run over a
  trait carrying `±inf`, which already failed and now fails earlier with a better-labelled
  structured error (`design.md` Decision 5).
- **Archive ordering**: these deltas ADD to and MODIFY requirements that still live in the
  pending `converge-bloommcp-viz-tools` change (PR #683, merged 2026-09-11, not yet archived).
  `openspec validate --strict` does not catch a dangling MODIFIED target, so the ordering is a
  real hazard — see `tasks.md` §6. The same note applies to the in-flight
  `add-bloommcp-corr-pair-disclosure` change (PR #833), which touches disjoint requirements of
  the same capability.
