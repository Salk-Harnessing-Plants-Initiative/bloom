## 1. Shared test scaffolding

- [ ] 1.1 Move `_render_to_dir` **and** `_TOL` from `tests/tools/test_viz_snapshot.py` into
      `tests/tools/conftest.py` (same single-sourcing precedent as `viz_env` in
      `add-bloommcp-plot-snapshot-tests` Decision 6), keeping `_render_to_dir`'s
      `ResultStore`-commit-spy behavior byte-identical
- [ ] 1.2 Update `test_viz_snapshot.py` to import both from the conftest. **Land 1.1 and 1.2 in
      one commit** — split across two, the suite is red in between
- [ ] 1.3 Confirm `test_viz_snapshot.py`'s existing tests pass unchanged

## 2. The per-cell oracle — artist state

- [ ] 2.1 Add `tests/tools/test_viz_cell_oracle.py` with a module docstring recording the
      measured numbers (noise floor 0.00196, `atol=0.01`, the RMS-vs-per-cell table, both
      measurement methods) and the division of labor against `test_viz_snapshot.py`
- [ ] 2.2 Oracle helper: recompute `df[trait_cols].corr()` from the raw fixture and derive the
      drawn-cell set as `~(np.triu(ones_like, dtype=bool) | ~np.isfinite(corr))`, deriving grid
      size from `len(trait_cols)` — no hardcoded 11 or 55 (design.md Decision 5)
- [ ] 2.3 `test_drawn_cell_set_is_the_finite_lower_triangle` — QuadMesh mask equals the derived
      set; unmasked values equal the recomputed matrix; assert the drawn set **and** its
      complement are non-empty so it cannot pass vacuously
- [ ] 2.4 `test_color_scale_spans_exactly_the_drawn_cells` — `norm.vmin`/`vmax` against the
      recomputed min/max of the drawn values
- [ ] 2.5 `test_every_drawn_cell_annotation_matches_its_own_value` — one annotation per drawn
      cell, count pinned exactly, each keyed by its own grid position
- [ ] 2.6 `test_every_drawn_cell_is_colored_by_its_own_value` — facecolors vs `cmap(norm(expected))`
- [ ] 2.7 `test_cells_outside_the_drawn_set_are_not_painted` — RGBA `(0,0,0,0)`
- [ ] 2.8 `test_axis_labels_are_the_resolved_trait_columns_in_order` — both axes, label list
      asserted non-empty
- [ ] 2.9 Annotation precondition: read the threshold from
      `inspect.signature(create_correlation_heatmap).parameters["annot_threshold"].default`
      rather than duplicating the literal 50, and guard on `n_traits > threshold` (the delegate
      is `show_annot = n_traits <= annot_threshold`, so annotations stop *above* it)
- [ ] 2.10 `test_annotation_precondition_fails_loudly_above_the_threshold` — exercise the guard's
      failure path directly against a synthetic count, since the 11-trait fixture never can

## 3. The per-cell oracle — saved PNG

- [ ] 3.1 Cell-geometry helper: map a cell's data coords to saved-PNG pixels through
      `fig.get_tightbbox(renderer)` minus savefig's `pad_inches`, with the dpi rescale and
      vertical flip
- [ ] 3.2 Median-over-inset cell sampler (median, not mean, so annotation glyphs cannot bias it)
- [ ] 3.3 `test_saved_png_cell_pixels_match_their_correlation_value` — all drawn cells at the
      measured `atol=0.01`
- [ ] 3.4 `test_cell_sampling_is_unbiased_by_annotation_glyphs` — sample each cell over a
      text-including wide inset and a text-free narrow inset; assert both agree with each other
      and with the expected color (demonstrates the property rather than asserting the technique)
- [ ] 3.5 `test_the_oracles_subject_is_the_tools_committed_png` — the test's own render vs the
      PNG the real tool commits, at `tol=0`, via the conftest `_render_to_dir`

## 4. The negative control (#768's actual point)

- [ ] 4.1 Doctored-matrix helper: render via the real `create_correlation_heatmap` with
      `pd.DataFrame.corr` patched for the duration so exactly one cell carries a shifted value,
      restoring it in a `finally`. **Not** a post-hoc recolor of the saved PNG — that is
      invisible to every artist-state assertion (design.md Decision 2 / spec Requirement 3)
- [ ] 4.2 `test_single_cell_defect_rms_misses_but_cell_oracle_catches` — parametrized over the
      measured shift magnitudes; assert `compare_images` at `_TOL` misses **and** that the
      array, annotation, and facecolor layers each independently catch it, asserted separately
      so no one layer can carry the test
- [ ] 4.3 Failure messages on both directions: RMS gaining sensitivity, or any per-cell layer
      losing it, must name what changed and point at design.md, `test_viz_snapshot.py`'s
      known-limitation prose, and the RMS layer's negative pin

## 5. De-staling the prose that says #768 is open

- [ ] 5.1 `test_viz_snapshot.py` module docstring "Known limitation" — keep the measurement, add
      that the gap is now covered by `test_viz_cell_oracle.py`
- [ ] 5.2 `test_realistic_single_cell_defect_in_correlation_matrix_is_not_caught` docstring —
      reframe as a pin on the RMS layer's blind spot, cross-referencing the oracle, so it is not
      misread as an open gap
- [ ] 5.3 `plot_correlation_matrix.py`'s "Known test-coverage gap (#768...)" paragraph — update
      to reflect the closed gap, keeping the #747 disclosure it sits beside intact
- [ ] 5.4 `openspec/changes/add-bloommcp-plot-snapshot-tests/design.md` Decision 7 ("why #768 is
      tracked, not fixed here") — note what closed it. That change is unarchived and
      `plot_correlation_matrix.py` points readers straight at it, so skipping this leaves the
      production docstring citing a live document that contradicts it

## 6. Verification

- [ ] 6.1 `uv run --frozen --extra test pytest tests/tools/test_viz_cell_oracle.py -v` green
- [ ] 6.2 **Full per-PR sweep** green: `uv run --frozen --extra test pytest tests/ -m "not
      integration and not live_smoke"` — not just the touched files. The precedent change's own
      section 9.4 records new tests leaking global state into 30 unrelated tests, caught only by
      running the whole sweep; `_render_to_dir` mutates global port config, so this is the
      specific risk here too
- [ ] 6.3 Confirm the new tests are collected by that marker filter (spec Requirement 4)
- [ ] 6.4 `black --check` / `ruff check` clean on the touched files
- [ ] 6.5 `openspec validate add-bloommcp-correlation-cell-oracle --strict` passes
- [ ] 6.6 After the PR opens, read the `python-audit` job log directly (not just the green tick)
      to confirm the suite passes on `ubuntu-latest`. The `tol=0` assertion in 3.5 is the
      strictest in the suite and has never run cross-platform; same posture as the sibling
      change's Decision 3
