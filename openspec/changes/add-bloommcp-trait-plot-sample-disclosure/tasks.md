## 1. RED — tests that fail against today's code

Boxplot tests in `bloommcp/tests/tools/test_plot_trait_boxplots_tool.py`, histogram tests in
`test_plot_trait_histograms_tool.py`, shared-constant tests in `test_viz_shared.py` — all using
each file's existing `injected_ports` fixture (`FakeReader` + `FakeResultStore`) and `_run(...)`
helper.

- [ ] 1.0 Write every test below **first**, run them against unmodified code, and record that
      each fails for the expected reason (missing field / wrong bucket / no footer), not an
      import error. The TDD gate is this checkbox, not the prose.

### 1.1 Boxplot per-group sample sizes (#748 core)

- [ ] 1.1.1 `test_group_sample_sizes_match_an_independent_recount` — a frame with deliberately
      uneven per-(trait, genotype) missingness. Every reported count matches an independent
      `df.groupby(geno)[trait].count()` recomputation, and the scalars match
      `min`/`median`/`max` over that same table.
- [ ] 1.1.2 `test_small_sample_groups_named_with_trait_genotype_and_n` — a group with
      `0 < n < _MIN_PLOTTED_SAMPLES` for one trait only: it is named with its trait, genotype and
      count, and the unaffected traits' groups are not.
- [ ] 1.1.3 `test_small_sample_groups_ordered_ascending_and_capped` — more flagged groups than
      the cap: the list is non-decreasing in `n`, its length equals the cap, entry `[0]` is the
      global smallest non-empty box, and `small_sample_group_count` reports the true total.
- [ ] 1.1.4 `test_group_n_summaries_are_uncapped` — same over-cap frame: `group_n_min`/`_median`/
      `_max` match an independent computation over **every** (trait, genotype) cell, including
      the zeros, not just the reported sample.
- [ ] 1.1.5 `test_absent_group_is_its_own_bucket_and_renders_no_tick` — a genotype that is
      entirely null for one trait: it is in `absent_genotype_groups`, **not** in
      `small_sample_groups`, and a spy on the delegate's returned figure confirms that trait's
      panel carries no tick for it (pins the verified delegate behavior `design.md` documents).
- [ ] 1.1.6 `test_every_group_cell_lands_in_exactly_one_bucket` — taxonomy totality over a frame
      exercising absent, small and healthy groups at once: no cell unexplained, none
      double-counted (mirrors `test_every_nan_cell_has_exactly_one_reason` next door).
- [ ] 1.1.7 `test_rows_with_null_genotype_are_counted_and_excluded` — rows whose genotype value
      is null are reported in `rows_missing_genotype` and appear in no group's count.
- [ ] 1.1.8 `test_group_sample_sizes_csv_is_committed_and_complete` — the committed CSV has one
      row per (resolved trait × genotype group), its counts match the independent recount, it
      carries an `OutputLink`, and it covers groups the capped lists truncated away.
- [ ] 1.1.9 `test_new_boxplot_fields_stamped_into_manifest_params` — every new field is
      recoverable from the persisted manifest, matching the precedent set by
      `resolved_trait_columns`.
- [ ] 1.1.10 `test_boxplot_result_stays_links_not_blobs` — port
      `test_provenance_stamped_seed_none_and_links_returned`'s 5,000-char-per-field assertion
      into this tool's file and exercise it on a wide frame, so the caps are enforced by a test
      rather than by intent (`design.md` Decision 2).

### 1.2 Boxplot figure footer

- [ ] 1.2.1 `test_sample_size_note_is_drawn_on_every_render` — a healthy frame: a `Figure.text`
      spy shows exactly one note containing the min/median/max and the group count, with no
      warning marker. Deliberately the *unflagged* path, which today draws nothing.
- [ ] 1.2.2 `test_flagged_note_names_the_affected_groups` — a frame with a small group and an
      absent group: the drawn text carries the warning marker, names both groups, and points at
      the committed table.
- [ ] 1.2.3 `test_note_name_list_is_capped_with_remainder` — more flagged groups than the
      footer's name cap: at most the cap are named, and a `+N more` summary covers the rest.
- [ ] 1.2.4 `test_paginated_notes_are_page_scoped` — a batched render where the only thin group
      sits on one page: that page's drawn text names it, every other page's does not, and each
      page's statistics match a recount restricted to that page's `page_traits`.
- [ ] 1.2.5 `test_sample_size_note_recoverable_from_result_and_manifest` — `sample_size_note`
      equals the drawn text for an unbatched render and is stamped into `params`.
- [ ] 1.2.6 `test_histogram_render_gains_no_note` — a `Figure.text` spy over
      `plot_trait_histograms` records no added note (`design.md` Decision 4).

### 1.3 Histogram per-trait disclosure

- [ ] 1.3.1 `test_per_trait_plotted_n_and_missingness_reported` — counts and NaN fractions match
      an independent recount; the scalars cover every resolved trait.
- [ ] 1.3.2 `test_all_null_trait_is_named_with_zero_plotted_n` — the "No data" panel's trait is
      in `low_sample_traits` with `plotted_n == 0` (today it is discoverable only by opening the
      image).
- [ ] 1.3.3 `test_low_sample_traits_ordered_ascending_and_capped_with_true_count` — mirrors
      1.1.3 at trait granularity.
- [ ] 1.3.4 `test_trait_sample_sizes_csv_is_committed_and_complete` — mirrors 1.1.8.
- [ ] 1.3.5 `test_new_histogram_fields_stamped_into_manifest_params` — mirrors 1.1.9.
- [ ] 1.3.6 `test_delegate_titles_each_panel_with_its_n` — pins the delegate's
      `f"{trait}\n(n={count})"` titling directly against the live delegate. The existing
      `_titled_traits` helper splits that suffix off before asserting, so nothing currently fails
      if it disappears — and it is the entire reason this tool's image needs no footer
      (`design.md` Decision 4 / Open Questions).

### 1.4 Non-finite values

- [ ] 1.4.1 `test_histogram_over_non_finite_trait_is_assumption_violated_naming_it` — a trait
      carrying `+inf`: `BloomMCPError(code="assumption_violated")` naming the trait, with a
      remedy. Assert the delegate was **never called** (spy) and no run was created, so the
      detection is a pre-flight, not a rescued delegate failure.
- [ ] 1.4.2 `test_histogram_non_finite_guard_leaves_no_staging_dir` — a `create_run` spy confirms
      no staging directory is created or left behind for that call.
- [ ] 1.4.3 `test_boxplot_over_non_finite_trait_renders_and_discloses` — the run completes, the
      trait is in `non_finite_traits`, the CSV's `n_non_finite` column reports the affected
      (trait, genotype) pair, and the drawn note warns about it.
- [ ] 1.4.4 `test_non_finite_values_are_not_stripped_before_rendering` — the frame handed to the
      boxplot delegate still contains the non-finite value (`design.md` Decision 5 — the wrapper
      must not quietly alter a pre-clean EDA view).

### 1.5 Shared floor

- [ ] 1.5.1 `test_min_plotted_samples_is_owned_not_aliased` — `_viz_shared._MIN_PLOTTED_SAMPLES`
      is 5 and `_viz_shared` does not bind `_CANONICAL_MIN_SAMPLES_PER_TRAIT`, so the alias
      #784 removed next door cannot be introduced here. Deliberately does **not** assert any
      relationship to the QC constant — independence is the point.
- [ ] 1.5.2 `test_both_plot_tools_use_the_shared_floor` — both tools flag at the same boundary,
      pinned parametrically at `n = floor - 1` and `n = floor`.

## 2. GREEN — implementation

- [ ] 2.1 `_viz_shared.py`: add `_MIN_PLOTTED_SAMPLES = 5` with its own rationale comment (a
      quartile-degeneracy floor, owned here, explicitly not the QC per-trait completeness
      convention, and honest that it is not a sufficiency threshold).
- [ ] 2.2 `_viz_shared.py`: add the shared, vectorized count helper(s) the two tools call —
      per-trait non-null counts, per-(trait, genotype) non-null counts, and the `isinf` mask —
      so the two files cannot drift on how a "plotted observation" is defined. No Python loop
      over the trait × genotype grid; only over the flagged tail.
- [ ] 2.3 `plot_trait_histograms.py`: pre-flight non-finite guard **before** `store.create_run`,
      raising `assumption_violated` naming the offending traits (capped) with a remedy pointing
      at `qc_clean`/`remove_outliers`.
- [ ] 2.4 `plot_trait_histograms.py`: add `n_rows_read`, `plotted_n_min`/`_median`/`_max`,
      `low_sample_traits` (a small Pydantic model: trait, `plotted_n`, `nan_fraction`),
      `low_sample_trait_count`; commit `trait_sample_sizes.csv`; stamp everything into `params`.
- [ ] 2.5 `plot_trait_boxplots.py`: add `n_rows_read`, `n_genotype_groups`,
      `rows_missing_genotype`, `group_n_min`/`_median`/`_max`, `small_sample_groups` +
      `small_sample_group_count`, `absent_genotype_groups` + `absent_genotype_group_count`,
      `non_finite_traits` + `non_finite_trait_count`, `sample_size_note`; commit
      `group_sample_sizes.csv`; stamp everything into `params`.
- [ ] 2.6 `plot_trait_boxplots.py`: draw the page-scoped note via `Figure.text(...)` before each
      `savefig`, inside the existing figure-lifecycle block — neutral when nothing is flagged,
      `⚠`-prefixed and dark red when something is. Keep the existing
      `call_with_figure_cleanup` / `FIGURE_REGISTRY_LOCK` / staging-teardown structure untouched.
- [ ] 2.7 Keep the sample-size CSV out of `page_traits` and out of `n_pages` (the MODIFIED
      pagination requirement), while still committing it with its own `OutputLink`.
- [ ] 2.8 Field descriptions carry the caps, the ascending ordering, the uncapped-scalar
      rationale, and the fact that the floor is a degeneracy bound rather than a sufficiency
      claim — the same standard the sibling correlation fields are held to.

## 3. Docs

- [ ] 3.1 `plot_trait_boxplots.py` module docstring: replace the "**Unlike
      `plot_correlation_matrix` … tracked at #748**" paragraph with a description of the shipped
      disclosure — the three buckets, the always-on page-scoped footer, the committed CSV, and
      what the floor does and does not claim. Keep the honest note that a zero-variance trait
      still renders a degenerate box without a flag (that is not what #748 asked for, and it is
      not silently misleading now that every box's `n` is reported).
- [ ] 3.2 `plot_trait_histograms.py` module docstring: same treatment, plus the non-finite
      pre-flight guard and why the rendered image is deliberately unchanged.
- [ ] 3.3 Update both test modules' docstrings to cover the new surface.
- [ ] 3.4 Add the new fields to `tests/smoke/test_plot_trait_boxplots_smoke.py` and
      `test_plot_trait_histograms_smoke.py` — the only tests exercising this surface through the
      real MCP server, and cylinder is exactly the scale the caps exist for.

## 4. Snapshot baseline (deliberate render change)

- [ ] 4.1 Run `uv run --frozen --extra test python scripts/gen_plot_snapshots_golden.py` with no
      `--yes` first and record the printed old-vs-new RMS for all three baselines.
- [ ] 4.2 Rerun with `--yes`, then restore `histograms_turface_19_baseline.png` and
      `correlation_matrix_turface_19_baseline.png` from git — neither render changes, and the
      script rewrites all three by design.
- [ ] 4.3 Update `tests/fixtures/plot_baselines/MANIFEST.json` if the recorded environment moved.
- [ ] 4.4 Quote the boxplot RMS and the reason for the change in the PR description, per the
      generator script's review convention.

## 5. Verify

- [ ] 5.1 `cd bloommcp && uv run --extra test pytest tests/tools/test_plot_trait_boxplots_tool.py
      tests/tools/test_plot_trait_histograms_tool.py tests/tools/test_viz_shared.py -q`.
- [ ] 5.2 `cd bloommcp && uv run --extra test pytest tests/tools/ -q` — no sibling regression, in
      particular `test_viz_snapshot.py` (boxplots against the regenerated baseline; histograms
      and correlation_matrix must still match their untouched ones) and
      `test_devendor_invariants.py`.
- [ ] 5.3 `cd bloommcp && uv run black --check src tests scripts && uv run ruff check src tests
      scripts`.
- [ ] 5.4 `openspec validate add-bloommcp-trait-plot-sample-disclosure --strict`.
- [ ] 5.5 Measure the added cost at cylinder width (846 traits) — the two vectorized passes and
      the CSV write — and record the numbers in `design.md`'s Risks section rather than leaving
      "vectorized, therefore fine" as an assertion.
- [ ] 5.6 Re-read the diff against `design.md`: no existing field's value changed, no delegate
      call's arguments changed, and the histogram render is byte-identical.

## 6. Archive ordering (post-merge, not part of this PR)

- [ ] 6.1 Archive `converge-bloommcp-viz-tools` **first**. This change MODIFIES a requirement
      that still lives in that pending change, and `openspec validate --strict` passes without
      checking that a MODIFIED target exists — archiving in the wrong order silently drops it.
- [ ] 6.2 Coordinate with `add-bloommcp-corr-pair-disclosure` (PR #833): it MODIFIES disjoint
      requirements of the same capability, so either order works between the two, but both must
      follow `converge-bloommcp-viz-tools`.
