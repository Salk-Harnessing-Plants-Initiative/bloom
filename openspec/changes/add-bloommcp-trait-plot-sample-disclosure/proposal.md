## Why

[#748](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/748) — `plot_trait_boxplots`
discloses **no sample size anywhere**, and neither it nor `plot_trait_histograms` discloses how
much of a trait's raw missingness was dropped before rendering. Both read **raw, uncleaned** data
by design (the same pre-clean EDA posture as `qc_inspect`) and delegate rendering to
`sleap_roots_analyze.visualization`, whose handling of missing and non-finite values is silent.

Four gaps, each probed against the resolved delegate rather than assumed:

- **A box built from 2 points is pixel-identical to one built from 200.**
  `create_trait_boxplots_by_genotype` titles each subplot `f"{trait}"` — no `n`, unlike
  `create_trait_histograms`, which titles each panel `f"{trait}\n(n={count})"`.
- **A genotype group with zero non-null rows for a trait vanishes from the panel entirely** — the
  delegate takes its tick labels from what survives `dropna()`, so the group gets no tick, no box
  and no gap. Confirmed: a 2-genotype frame with one all-null group renders ticks `['B']`.
- **A trait carrying `±inf` corrupts the affected box without erroring.** On `[1, 2, inf, 4, 5]`
  the delegate draws `q1=2.0`, `median=4.0`, `q3=NaN` — the finite median is 3.0. The reader sees
  a confidently drawn median in the wrong place and an open top, not something that reads as
  corrupt data. The same value makes `plot_trait_histograms` fail the whole run with the
  delegate's `ValueError: supplied range of [1.0, inf] is not finite`, redacted by the error path
  into a message naming no trait and offering no remedy.
- **Rows whose genotype value is null are dropped from every box**, uncounted.

This is the "silently misleading, no error" category #466 already fixed once for
`plot_correlation_matrix` (`zero_variance_traits`, `low_overlap_trait_pairs`, `heatmap_caveat`).
That work established the pattern reused here: compute the disclosure in bloommcp from the same
selected frame, report a capped worst-first tail with uncapped scalars beside it, stamp it into
the manifest, and put a signal on the image for the caller who only opens the PNG.

**Why both tools in one change.** Same wrapper shape, same `_viz_shared` helpers, same
pagination path, same persistence flow, and the same missingness question at two granularities.
The non-finite case spans both and has to be settled once, since the delegates react to it
differently.

**On scope.** #748's round-6 comment calls the fix "cheap" and asks for a "fast follow". This
change is larger than that framing: it adds an image change, a committed table per tool, a
histogram error-path change, and a regenerated snapshot baseline. Two things drove the growth,
both from the issue's own text — "consider whether per-group sample counts should also land in
the rendered image itself", and "mirroring `qc_inspect`'s `per_trait_nan_fraction`/
`per_trait_inf_count`", which cannot be done honestly without confronting that `count()` treats
`±inf` as present. The severable pieces are named in `design.md` Decision 11 so a reviewer can
cut rather than guess.

Sibling issues [#747](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/747) and
[#768](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/768) are
`plot_correlation_matrix`-only and stay out of scope (`design.md` Decision 11).

## What Changes

### `plot_trait_boxplots`

- **Per-(trait, genotype) sample sizes**, from one vectorized `groupby(genotype)[traits].count()`:
  - `box_n_min`/`_median`/`_max` — uncapped scalars over every cell carrying at least one
    **finite** observation, with `n_boxes_summarized`/`n_boxes_drawn` naming that population
    explicitly. Absent cells are excluded: including them prints `median=0` on a run where every
    drawn box is healthy (`design.md` Decision 7).
  - Four mutually exclusive, exhaustive buckets (`design.md` Decision 5): `no_data_traits`,
    `absent_genotype_groups`, `non_finite_groups`, `small_sample_groups` — each capped at
    `MAX_FLAGGED_REPORTED = 20`, ordered **ascending by count then `(trait, genotype)`** so the cap
    is deterministic under the ties a replicated design guarantees, each with an uncapped count.
  - `group_sample_sizes.csv` — a committed output carrying every cell's
    `n_rows_in_group,n_plotted,n_finite,n_non_finite,n_missing,nan_fraction`, uncapped.
- **The image carries the disclosure two ways** (`design.md` Decision 4):
  - each genotype tick label gains its own ` (n=…)`, matched by tick text against the count table
    — no geometry, no orientation branch, self-checking, with `box_labels_annotated` reporting
    whether the match succeeded;
  - an **always-on** note below the axes gives min/median/max, every denominator, and its own
    page scope, escalating to a `⚠` clause naming the flagged groups **as a fraction**
    (`846 of 16,074 box(es) below n=5`) when any is flagged.
  - `fig.tight_layout()` is called on unbatched renders only — the batched delegate already does
    it, and paying for it 53 times is what would break the cylinder smoke timeout.
- `n_rows_read`, `n_genotype_groups`, `rows_missing_genotype`, `max_nan_fraction` +
  `max_nan_fraction_group`, `non_finite_traits`.

### `plot_trait_histograms`

- `trait_n_min`/`_median`/`_max`, `low_sample_traits` (capped, ascending, with each trait's
  binned count and missing fraction) + `low_sample_trait_count`, `max_nan_fraction` +
  `max_nan_fraction_trait`, `n_rows_read`, and a committed `trait_sample_sizes.csv`
  (`trait,n_plotted,n_missing,nan_fraction`). A trait with `n_plotted == 0` — the "No data" panel
  — is named in the result rather than only in the image.
- **A non-finite trait now fails with a structured error that names it**, raised *before* the run
  is created so no staging directory is written and torn down. The render already cannot succeed.
- **The rendered image is unchanged** — the delegate already titles each panel `(n=…)`, so the
  PNG-only gap does not exist here (`design.md` Decision 4).

### Shared

- `_viz_shared.MIN_PLOTTED_SAMPLES = 5` (public, like the neighbouring `TRAIT_BATCH_THRESHOLD`),
  owned here rather than aliased to `_qc_shared._CANONICAL_MIN_SAMPLES_PER_TRAIT`. n=5 is the
  smallest sample above 1 at which Q1, the median and Q3 all land on actual order statistics.
  It is a degeneracy floor and makes **no** claim about whiskers or fliers, which stay
  artifact-prone well above it (`design.md` Decision 3).
- `MAX_FLAGGED_REPORTED = 20`, `MAX_NOTE_NAMES = 10`, and the shared counting helpers.
- Every scalar is `Optional` and `None` on an empty population — an all-null genotype column
  renders fine today and must not start crashing (`design.md` Decision 8) — and every stamped
  value is coerced to a native Python type, because `np.int64` in `params` raises
  `PydanticSerializationError` at the manifest write (`design.md` Decision 9).

## Impact

- Affected specs: `bloommcp-viz-tools` (5 ADDED requirements, 1 MODIFIED)
- Affected code:
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/plot_trait_boxplots.py`,
    `plot_trait_histograms.py`, `_viz_shared.py`
  - `bloommcp/tests/tools/test_plot_trait_boxplots_tool.py`,
    `test_plot_trait_histograms_tool.py`, `test_viz_shared.py`
  - `bloommcp/tests/tools/test_viz_snapshot.py` — its module docstring carries measured,
    load-bearing headroom numbers and instructs re-measurement after a layout change
  - `bloommcp/scripts/gen_plot_snapshots_golden.py` + `bloommcp/tests/scripts/` —
    `_report_regeneration` calls `compare_images` with no guard, and that **raises** on a canvas
    resize before anything is copied (`design.md` Decision 10)
  - `bloommcp/tests/fixtures/plot_baselines/boxplots_turface_19_baseline.png` + `MANIFEST.json`
  - `bloommcp/tests/smoke/test_plot_trait_boxplots_smoke.py`, `test_plot_trait_histograms_smoke.py`
- **Not backward compatible in three named ways**, all deliberate:
  1. `outputs`/`output_links` gain the committed sample-size table, so
     `len(result.outputs) == n_pages` no longer holds. Two currently-green assertions
     (`test_plot_trait_boxplots_tool.py:161`, `test_plot_trait_histograms_tool.py:121-122`) are
     retargeted in tasks §2.9.
  2. The boxplot render changes (labels, note, unbatched layout), so its snapshot baseline is
     regenerated.
  3. A histogram run over a `±inf` trait fails earlier, with a better-labelled structured error.
     It already failed.
  Every pre-existing **result field** keeps its value and meaning.
- **Archive ordering**: these deltas ADD to and MODIFY requirements that still live in the pending
  `converge-bloommcp-viz-tools` change (PR #683, merged 2026-09-11, not archived).
  `openspec validate --strict` does not catch a dangling MODIFIED target — see `tasks.md` §8.
  The in-flight `add-bloommcp-corr-pair-disclosure` (PR #833) touches disjoint requirements of the
  same capability; its **code** is also not in this branch's tree, so nothing here references its
  symbols. PR #832 (#808) edits `bloom_mcp/tools/_plots.py`, the home of the figure-lifecycle
  helpers this change draws inside — no file overlap today, but worth a rebase check.

Closes #748.
