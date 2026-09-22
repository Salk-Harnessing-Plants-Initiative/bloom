## 1. RED — tests that fail against today's code

Boxplot tests in `bloommcp/tests/tools/test_plot_trait_boxplots_tool.py`, histogram tests in
`test_plot_trait_histograms_tool.py`, shared-constant tests in `test_viz_shared.py`.

**Fixture notes for the implementer.** The shared `_wide_df` helper builds 12 rows over 3
genotypes — **n=4 per cell, below the new floor of 5** — so every existing batched test's render
becomes flagged once §2 lands (expected, not a regression), and `_wide_df` cannot serve the
"only one page has a thin group" test; §1.2.4 builds its own frame.
`FakeResultStore.commit` **rmtree's the staging dir**, so a committed CSV's bytes are unreachable
after `_run()`; §1.0.1 adds the commit-spy helper the CSV tests need.

- [x] 1.0 Write every test in §1 **first**, run them against unmodified code, and record that each
      fails for the expected reason (missing field / wrong bucket / no note), not an import error.
      §1b tests are exempt — they are green today by construction.
- [x] 1.0.1 Add a `_captured_outputs(store, monkeypatch)` helper to both test files: a `commit`
      spy that copies `run.staging_dir` contents before delegating (the pattern in
      `test_pca_analysis_tool.py` and `test_viz_snapshot._render_to_dir`), filtering by suffix so
      a bare `read_text` never hits a `.png`.

### 1.1 Boxplot per-group sample sizes

- [x] 1.1.1 `test_group_sample_sizes_match_hand_written_expectations` — a small frame with
      **literal expected counts written out by hand** (`{("t1","A"): 2, ("t1","B"): 0, …}`), not a
      recomputation of production's own `groupby().count()` expression. Where a larger frame needs
      a recount, use a structurally different oracle (a Python loop over
      `len(df[df[g]==k][t].dropna())`).
- [x] 1.1.2 `test_small_sample_groups_named_with_trait_genotype_and_n`.
- [x] 1.1.3 `test_small_sample_groups_ordered_and_capped` — parametrized at **cap−1, cap, cap+1**;
      non-decreasing in `n`, entry `[0]` is the global smallest finite-backed box, uncapped count
      true at every size.
- [x] 1.1.4 `test_small_sample_group_ordering_is_deterministic_on_ties` — more tied cells than the
      cap; two runs give identical lists; the secondary key is `(trait, genotype)` lexicographic.
- [x] 1.1.5 `test_box_n_summaries_exclude_absent_cells` — the `design.md` Decision 7 frame where
      **every drawn box has n=6** and many cells are absent: `box_n_min == box_n_median == 6`
      (never 0), `n_boxes_summarized` equals the drawn count, and
      `n_boxes_drawn + absent_genotype_group_count + no_data_trait_count × n_genotype_groups
      == n_traits × n_genotype_groups` (three-termed: the absent count excludes the cells of a
      wholly-dead trait, which collapse into `no_data_traits`).
- [x] 1.1.6 `test_absent_group_is_its_own_bucket` — a genotype all-null for one trait only: in
      `absent_genotype_groups`, not in `small_sample_groups`, excluded from the summaries.
- [x] 1.1.7 `test_all_null_trait_collapses_to_no_data_traits` — one dead trait over the 19-genotype
      fixture yields **one** `no_data_traits` entry and **zero** `absent_genotype_groups` entries,
      so it cannot exhaust the cap by itself.
- [x] 1.1.8 `test_every_group_cell_lands_in_exactly_one_bucket` — totality over a frame exercising
      no-data, absent, non-finite, small and healthy cells at once; none unexplained, none
      double-counted.
- [x] 1.1.9 `test_bucket_boundaries` — parametrized `n ∈ {0, 1, floor−1, floor}`: 0 is absent (not
      small), 1 is small (not absent), floor−1 is small, floor is unflagged.
- [x] 1.1.10 `test_rows_with_null_genotype_are_counted_and_excluded`.
- [x] 1.1.11 `test_max_nan_fraction_names_a_heavily_missing_cell_that_clears_the_floor`.
- [x] 1.1.12 `test_group_sample_sizes_csv_is_committed_and_complete` — via §1.0.1's spy: one row
      per (resolved trait × genotype group), every column present
      (`n_rows_in_group,n_plotted,n_finite,n_non_finite,n_missing,nan_fraction`), counts match the
      independent oracle, and it covers cells the capped lists truncated away.
- [x] 1.1.13 `test_new_boxplot_fields_and_genotype_column_stamped_into_manifest_params` — including
      `genotype_column`, which is data-dependent and recorded nowhere in the manifest today.
- [x] 1.1.14 `test_manifest_params_are_native_json_types` — stamp round-trips through
      `model_dump(mode="json")`; a raw `np.int64`/`np.float64` raises
      `PydanticSerializationError`, so this is a live hazard, not a hypothetical
      (`design.md` Decision 9).
- [x] 1.1.15 `test_result_and_manifest_are_strict_json` — both tools: round-trip result and
      persisted manifest bytes through `json.loads(..., parse_constant=_reject)` so a bare
      `NaN`/`Infinity` fails the test. Needed more here than next door: this change reports
      fractions whose denominator can be zero and medians whose population can be empty.
- [x] 1.1.16 `test_new_result_fields_stay_under_the_links_not_blobs_ceiling` — assert the
      5,000-char ceiling over **only the fields this change adds**, at cylinder width. Do **not**
      assert it over the whole result: `resolved_trait_columns` already measures ~24,862 chars for
      846 real trait names and `page_traits` about the same, a pre-existing overrun recorded in §7.

### 1.2 The rendered image

- [x] 1.2.1 `test_each_genotype_tick_label_carries_its_own_n` — parametrized over **2 genotypes
      (vertical, x-ticks) and 10 genotypes (horizontal, y-ticks)**: select the axis by
      `ax.get_title()`, filter `ax.get_visible()` (the delegate pads invisible blank-titled axes),
      and assert each label matches `"<genotype> (n=<count>)"` against the oracle. A test reading
      only `get_xticklabels()` at 10 genotypes would compare against the numeric scale and pass
      for the wrong reason.
- [x] 1.2.2 `test_unmatched_tick_labels_are_left_alone_and_reported` — monkeypatch the delegate to
      return a figure whose ticks are not genotype values: labels unchanged,
      `box_labels_annotated is False`, note still drawn.
- [x] 1.2.3 `test_note_is_drawn_on_every_render_including_unflagged` — assert against the **saved
      figure's** `fig.texts`, not a call spy, and **filter** rather than count: the delegate
      already puts a suptitle in `fig.texts` on the vertical unbatched path
      (`'Boxplot grouped by geno'`) and on every batched page.
- [x] 1.2.4 `test_note_is_drawn_before_savefig` — a `savefig` spy recording the matching
      `fig.texts` at call time; the spec says "before it is saved", which a call-order-blind spy
      cannot prove.
- [x] 1.2.5 `test_flagged_note_names_groups_and_reports_the_fraction`.
- [x] 1.2.6 `test_note_name_list_is_capped_with_remainder`.
- [x] 1.2.7 `test_paginated_notes_are_page_scoped` — purpose-built wide frame with the only thin
      group on one page: that page's text names it, no other page's does, each page's statistics
      match a recount restricted to its `page_traits`, the text identifies itself as page-scoped,
      and the per-page strings are NOT stamped into `params` (each is reconstructible from
      `page_traits` + the committed CSV; stamping them appends tens of KB of prose per version
      to a manifest re-validated on every subsequent run).
- [x] 1.2.8 `test_sample_size_note_recoverable_from_result_and_manifest` — non-empty, matches a
      regex carrying min/median/max plus the box/genotype/trait denominators (an empty string must
      not satisfy this), and equals the drawn text on an unbatched render.
- [x] 1.2.9 `test_tight_layout_called_only_when_unbatched` — spy `Figure.tight_layout`: called once
      for an unbatched render, never for a batched one (the batched delegate already calls it).
- [x] 1.2.10 `test_note_drawing_failure_cleans_staging_and_leaks_no_figures` — the existing render
      -failure test patches the *delegate*; the note is drawn at a different site and has its own
      failure path.

### 1.3 Histogram per-trait disclosure

- [x] 1.3.1 `test_per_trait_plotted_n_and_missingness_reported` (hand-written expectations, as 1.1.1).
- [x] 1.3.2 `test_all_null_trait_is_named_with_zero_plotted_n`.
- [x] 1.3.3 `test_low_sample_traits_ordered_capped_and_deterministic` — mirrors 1.1.3/1.1.4.
- [x] 1.3.4 `test_max_nan_fraction_names_the_worst_trait`.
- [x] 1.3.5 `test_trait_sample_sizes_csv_is_committed_and_complete` — via §1.0.1's spy.
- [x] 1.3.6 `test_new_histogram_fields_stamped_into_manifest_params`.

### 1.4 Non-finite values

- [x] 1.4.1 `test_histogram_over_non_finite_trait_is_assumption_violated_naming_it` — the RED
      assertions are that the **delegate spy records zero calls** and the **`create_run` spy
      records zero calls** (today the delegate raises *after* the run is created, and the existing
      `except` branch already rmtree's staging, so "no staging dir left behind" passes today).
- [x] 1.4.2 `test_histogram_non_finite_guard_fires_on_a_batched_selection_too` — >50 traits, guard
      still pre-`create_run`.
- [x] 1.4.3 `test_boxplot_over_non_finite_trait_renders_and_discloses` — trait in
      `non_finite_traits`, cell in `non_finite_groups`, CSV column populated, note warns.
- [x] 1.4.4 `test_all_inf_cell_is_not_reported_as_a_healthy_box` — a cell whose every value is
      `+inf`: `n_plotted > 0` but it is flagged non-finite, excluded from the `box_n_*` summaries,
      and never unflagged. Pins `design.md` Decision 5 — `count()` includes `±inf`.
- [x] 1.4.5 `test_boxplot_non_finite_warning_lands_on_the_offending_page` — batched.

### 1.5 Shared floor and empty populations

- [x] 1.5.1 `test_min_plotted_samples_is_owned_not_aliased` — `MIN_PLOTTED_SAMPLES == 5` **and**
      `"MIN_PLOTTED_SAMPLES" in _viz_shared.__dict__` (what "owned" means), **and** the module
      does not bind `_CANONICAL_MIN_SAMPLES_PER_TRAIT` (the regression is a future
      `from _qc_shared import ...`; `_viz_shared` already imports `_validate_trait_subset` from
      there, so the negative alone is weak). Deliberately does **not** assert any relationship to
      the QC constant — independence is the point.
- [x] 1.5.2 `test_both_plot_tools_flag_at_the_same_boundary` — parametrized at floor−1 and floor.
- [x] 1.5.3 `test_all_null_genotype_column_completes_with_null_summaries` — reachable today and
      **renders successfully**; after §2 it must still complete: summaries `None` (never `NaN`),
      `n_genotype_groups == 0`, `rows_missing_genotype == n_rows_read`, note says no box was drawn.
- [x] 1.5.4 `test_zero_row_frame_completes_with_null_summaries` — same for both tools.

### 1.6 Delegate-behavior pins (guard the decisions that depend on them)

- [x] 1.6.1 `test_delegate_titles_each_panel_with_its_n` — pins `f"{trait}\n(n={count})"` against
      the live delegate. `_titled_traits` splits the suffix off before asserting, so nothing fails
      today if it disappears — and Decision 4's "the histogram image needs no note" rests on it.
      Note the delegate titles the **bare trait name** for an all-NaN panel; assert the non-empty
      case.
- [x] 1.6.2 `test_delegate_draws_fliers_on_both_orientation_paths` — pins that neither path passes
      `showfliers=False`/`whis`, on the pandas `DataFrame.boxplot` path (≤8 genotypes) **and** the
      `Axes.boxplot` path (>8). `design.md` Decision 11's "nothing is hidden, so nothing to
      disclose" depends on defaults across two different APIs.
- [x] 1.6.3 `test_batched_delegate_calls_tight_layout` — pins the fact §2.6 relies on to justify
      not calling it there.

## 1b. Characterization guards (green today — must stay green)

Listed separately because they do **not** fail against today's code; §1.0's gate does not apply.

- [x] 1b.1 `test_histogram_render_gains_no_note` — `fig.texts` filtered (batched histograms carry
      their own suptitle), asserting no sample-size note is added.
- [x] 1b.2 `test_non_finite_values_are_not_stripped_before_rendering` — the frame reaching the
      boxplot delegate still contains the `±inf`.
- [x] 1b.3 `test_absent_group_renders_no_tick` — pins the delegate behavior `design.md`'s table
      records; true today.

## 2. GREEN — implementation

- [x] 2.1 `_viz_shared.py`: add `MIN_PLOTTED_SAMPLES = 5`, `MAX_FLAGGED_REPORTED = 20`,
      `MAX_NOTE_NAMES = 10` — public, matching the neighbouring `TRAIT_BATCH_THRESHOLD`, each with
      its own rationale comment. The floor's comment states the order-statistic argument and its
      explicit non-claim about whiskers/fliers.
- [x] 2.2 `_viz_shared.py`: the shared, vectorized counting helpers (per-trait counts,
      per-(trait, genotype) counts, the `isinf` mask via
      `to_numpy(dtype="float64", na_value=np.nan)` so a future nullable dtype does not raise), plus
      one `_native(...)` coercion used by both tools at the result/stamp boundary.
- [x] 2.3 `plot_trait_histograms.py`: non-finite pre-flight guard **before** `store.create_run`.
- [x] 2.4 `plot_trait_histograms.py`: new fields + `trait_sample_sizes.csv`; stamp into `params`.
- [x] 2.5 `plot_trait_boxplots.py`: new fields and buckets (classification on the **finite**
      count), `group_sample_sizes.csv`, stamp into `params` **including `genotype_column`**.
- [x] 2.6 `plot_trait_boxplots.py`: relabel genotype ticks by matching tick text; call
      `fig.tight_layout()` **only when not batched**; draw the page-scoped note via `Figure.text`
      before each `savefig`. Keep the existing `call_with_figure_cleanup` /
      `FIGURE_REGISTRY_LOCK` / staging-teardown structure intact, and place the new drawing inside
      the same figure lifecycle so a failure there still cleans up.
- [x] 2.7 Compute every summary **before** `store.create_run` — `Provenance` is stamped at run
      creation and `commit` cannot amend it, so anything computed during the render loop is too
      late to stamp.
- [x] 2.8 Keep the sample-size CSV out of `page_traits` and out of `n_pages` while still committing
      it with its own `OutputLink`.
- [x] 2.9 Retarget the two assertions the new output breaks:
      `test_plot_trait_boxplots_tool.py:161` and `test_plot_trait_histograms_tool.py:121-122`
      (`len(result.outputs) == expected` → page count **plus one**, and the same for
      `output_links`). Commit this together with 2.4/2.5 so no commit leaves the suite red for an
      unrelated reason.
- [x] 2.10 Field descriptions carry: the caps, the ordering and its tie-break, that `n_plotted`
      includes `±inf` and `n_non_finite` is a subset of it, the exact population behind each
      scalar, that the floor is a degeneracy bound and not a sufficiency claim, that
      `sample_size_note` is run-wide (and equals the drawn text only on a single-page render), and
      the histogram/boxplot denominator difference reconciled by `rows_missing_genotype`.

## 3. Docs

- [x] 3.1 `plot_trait_boxplots.py` docstring: replace the "tracked at #748" paragraph with the
      shipped disclosure — the buckets, the per-box labels and their fallback, the always-on
      page-scoped note, the CSV, and what the floor does and does not claim. Keep the honest note
      that a zero-variance trait still renders a degenerate box without a flag.
- [x] 3.2 `plot_trait_histograms.py` docstring: same treatment, plus the non-finite pre-flight
      guard, why the image is deliberately unchanged, and the delegate's hardcoded `bins=30`
      (a panel above the floor can still be visually degenerate).
- [x] 3.3 Record the assessed-but-unfixed outlier findings in **both** module docstrings, not only
      in `design.md` (which gets archived): matplotlib's `whis=1.5`/`showfliers=True` defaults mean
      nothing is hidden on the boxplot side, and `hist(..., bins=30)` with no `range=` clips
      nothing on the histogram side — but a single extreme value collapses every real observation
      into one bar while the `(n=…)` title still reads normally.
- [x] 3.4 Update both test modules' docstrings.
- [x] 3.5 Update `tests/tools/test_viz_snapshot.py`'s module docstring: its measured boxplot
      headroom ("RMS≈21.7-22.1 at a uniform 2% probe") describes a render that no longer exists,
      and the docstring itself instructs re-measurement after a layout change.
- [x] 3.6 Add the new fields to both smoke tests. Note `plot_trait_histograms`' disclosure is a
      **no-op on cylinder** (every trait has `n_plotted == n_rows`, zero NaN), so assert the
      fields' presence and internal consistency there rather than a non-trivial flag.

## 4. Snapshot baseline (deliberate render change)

- [x] 4.1 `git rm bloommcp/tests/fixtures/plot_baselines/boxplots_turface_19_baseline.png` **first**.
      `_report_regeneration` calls `compare_images`, which **raises** `ImageComparisonFailure` on a
      canvas-size change rather than returning an RMS, and it is called for all three baselines
      *before* anything is copied — so without this the script dies having written nothing.
      Removing the file takes its "new baseline, no prior version to diff against" branch.
- [x] 4.2 Harden `_report_regeneration` to catch `ImageComparisonFailure` and print the old/new
      dimensions instead, with a unit test in `tests/scripts/` — so the next deliberate render
      change does not hit the same wall.
- [x] 4.3 Run the generator, then restore `histograms_turface_19_baseline.png` and
      `correlation_matrix_turface_19_baseline.png` from git (the script rewrites all three by
      design; neither of those renders changes).
- [x] 4.4 Update `MANIFEST.json` if the recorded environment moved.
- [x] 4.5 Quote the **old/new canvas dimensions** and the reason in the PR description — not an
      RMS, which is undefined across a resize.

## 5. Verify

- [x] 5.1 `cd bloommcp && uv run --extra test pytest tests/tools/test_plot_trait_boxplots_tool.py
      tests/tools/test_plot_trait_histograms_tool.py tests/tools/test_viz_shared.py -q`.
- [x] 5.2 `cd bloommcp && uv run --extra test pytest tests/tools/ tests/scripts/ -q` — no sibling
      regression; `test_viz_snapshot.py` passes against the regenerated boxplot baseline and the
      untouched other two.
- [x] 5.3 `cd bloommcp && uv run black --check src tests scripts && uv run ruff check src tests scripts`.
- [x] 5.4 `openspec validate add-bloommcp-trait-plot-sample-disclosure --strict`.
- [x] 5.5 Commit `bloommcp/scripts/trait_plot_sample_disclosure_bench.py` reproducing `design.md`'s
      numbers (per-page render cost with/without relabelling and `tight_layout`, the cylinder-scale
      counting cost, the flier-artifact rates, and the quartile order-statistic table), so every
      figure in the design doc is re-checkable rather than asserted — the precedent PR #833 set.
- [ ] 5.6 **NOT DONE — needs a running dev stack, which this branch's author did not have.**
      Measure the **cylinder boxplot smoke wall clock** before and after
      (`live_smoke_slow`, run via `/pre-merge`, excluded from the `dev-stack-smoke` CI job so it
      is not a merge gate). It is already at ~109-111s against a 120s client timeout
      (`tests/smoke/conftest.py`), and the benchmark measures relabelling at **+5% per page**
      across 53 pages — so the expected landing point is ~115-117s, i.e. marginal but likely
      under. `tight_layout` is deliberately skipped on the batched path precisely because its
      measured +21% would not fit. If a real run lands above ~115s, raise that client timeout.
- [x] 5.7 Re-read the diff against `design.md`: no existing **result field** changed value, no
      delegate call's arguments changed, the histogram render is byte-identical, and every
      `±inf`-sensitive comparison uses the finite count.

## 6. PR #839 review round 1 (@eberrigan) — applied

- [x] 6.0.1 **B1** `_annotate_genotype_ticks` returned at the first unmatched panel, after
      mutating earlier ones: the reported flag depended on trait ordering, figures came back
      half-annotated while claiming otherwise, and one dead trait in an 846-trait run reported
      False for all 53 pages. Now per-panel, with the delegate's "No data" panel skipped rather
      than counted as a failure.
- [x] 6.0.2 **B2** A numeric genotype column (``accession`` matches on name, no dtype check) put
      the sample sizes on the trait VALUE axis and reported success. Three guards now: a
      ``FixedLocator`` on the axis, set EQUALITY with that trait's plotted genotypes, and
      exactly one matching axis.
- [x] 6.0.3 **B3** The note is unbounded in height and was drawn at a fixed offset, so a flagged
      render put it on the bottom row of boxes (reproduced: 21px into the axes, and the review
      measured ~145px on a longer note). The figure now grows and its axes translate up by the
      measured strip. Asserted as geometry against ``get_tightbbox()`` — the first fix cleared
      the axes rectangle but still landed on the bottom row's x-axis labels.
- [x] 6.0.4 **B4** Two tests were vacuous: the per-box label test gave every cell n=6 (so a
      label/count mispairing passed), and the ordering test tied every cell at n=1 (so a
      descending sort passed). Both now use distinct counts.
- [x] 6.0.5 Findings 1-10: ``page_traits`` descriptions no longer promise a total mapping over
      ``outputs``; the reconciliation identity is stated in its true three-term form in both
      spec and design; ``non_finite_groups`` is ordered worst-first; ``non_finite_trait_count``
      added; ``thin_box_count`` added so the exclusive buckets do not lose the thinness signal;
      the note's two "box(es)" populations are named separately; a cardinality guard bounds the
      (trait x genotype) table the server builds; ``max_nan_fraction`` excludes absent cells;
      per-page notes are no longer stamped into the manifest; the note is drawn with
      ``parse_math=False``.
- [x] 6.0.6 Suggestions applied: cap-precedent comment corrected (#833 is unmerged), requirement
      retitled to "Trait-Plot Tools", ``page_notes`` indexed rather than silently falling back,
      ``rows_missing_genotype`` and the flier caveat added to the note, "n rows per box" names
      its unit, dead ``use_finite`` parameter dropped, n=5's non-monotonicity recorded, the
      21%/1.95MB measurement disagreements reconciled, the benchmark moved to
      ``bloommcp/scripts/`` (lint scope) and made Windows-safe, smoke assertions made None-safe.

## 6b. PR #839 review round 2 (@eberrigan) — applied

- [x] 6b.1 The three-term reconciliation identity was corrected in spec.md but NOT in design.md
      or tasks.md, and the round-1 commit message claimed both were done. Root cause: an edit
      script aborted midway and three design.md edits were silently lost. All three are now
      applied and verified individually (identity, the page-notes reversal, and the
      thin_box_count decision, which had never been written at all).
- [x] 6b.2 Two genotype values that stringify alike (integer 1 vs string "1") are distinct
      groupby keys with identical tick text, so the n=3 group was confidently labelled with the
      n=7 group's count while reporting success — set equality does not catch it, because both
      sides collapse. Now skipped, with the guarantee ("never mislabel, only decline to label")
      enforced by test rather than argued. Not reachable via either ingestion path today.
- [x] 6b.3 `plot_trait_histograms` now draws its own note. The round-1 argument ("the delegate
      already titles each panel (n=…)") answered the sample-size question and left the
      missingness one open: a panel reading (n=12) is identical whether twelve plants were
      measured or 108 of 120 rows were lost — the sentence #748 opens with. Flags on missing
      FRACTION as well as count, and carries its own caveat about the delegate's fixed 30 bins.
- [x] 6b.4 The tick-label path now passes `parse_math=False`, like the note path. Verified that
      "$\frac{1}$", "$a__b$" and "$\badcmd$" each raise at savefig unguarded.
- [x] 6b.5 Suggestions: benchmark path corrected in design.md and tasks.md; the page-count
      mismatch guard has a test; `_viz_shared`'s flier-probability comment matches the corrected
      numbers; and the benchmark now ASSERTS the exact figures (33%, 8.6%, 21%) rather than
      printing them — which immediately caught that the "27-34%" band in my own prose is wrong
      at n=8 (0.266). Corrected to 26-34% in all three places.
- [x] 6b.6 Filed **#891**: a genotype literally named NA/null/None is swallowed by pandas'
      default missing-value parsing before any tool sees it, so `rows_missing_genotype` reports
      it as "null genotype" — truthful-looking and materially misleading. Pre-existing, in the
      read path.

## 7. Follow-up issues to file (not fixed here)

- [x] 7.1 Filed as **#837**: `resolved_trait_columns`/`page_traits` exceed the family's 5,000-char
      "links, not blobs" convention at cylinder width (~24,862 chars for 846 real trait names) —
      pre-existing, inherited by these tools, and not something this change should silently adopt
      as acceptable.
- [x] 7.2 Filed as **#838**: the residual on-image gaps this change leaves — a zero-variance trait still renders
      a degenerate box with no flag, and a `box_labels_annotated=False` render falls back to the
      note alone. Filing follows the precedent that made #785 exist: #466's review singled out the
      one disclosed gap that lacked a tracking issue.

## 8. Archive ordering (post-merge, not part of this PR)

- [ ] 8.1 Archive `converge-bloommcp-viz-tools` **first**. This change MODIFIES a requirement that
      still lives in that pending change, and `openspec validate --strict` passes without checking
      that a MODIFIED target exists.
- [ ] 8.2 Coordinate with `add-bloommcp-corr-pair-disclosure` (PR #833): disjoint requirements, so
      either order works between the two, but both must follow `converge-bloommcp-viz-tools`.
