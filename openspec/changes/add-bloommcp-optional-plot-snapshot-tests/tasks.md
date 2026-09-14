## 1. Establish the render path (test-first)

- [ ] 1.1 **RED** — add `test_optional_plot_keys_render_and_commit` to
  `tests/tools/test_viz_snapshot.py`: drive `pca_analysis`/`umap_analysis`/`clustering` with
  `include_plots=True` through a `FakeReader.add_cleaned_version` + `FakeResultStore`
  commit-spy helper and assert all 8 `<catalog_key>.png` files land on disk. Fails first
  because the helper does not exist (the existing `_render_to_dir` seeds `add_experiment`,
  which these `require_clean=True` tools reject).
- [ ] 1.2 **GREEN** — add `_render_optional_to_dir(...)` next to `_render_to_dir`, differing
  only in the seeding call and in taking a params model. Do not refactor `_render_to_dir`.
- [ ] 1.3 **RED→GREEN** — `test_baseline_set_covers_every_catalog_key`: import
  `_PCA_CATALOG_KEYS`/`_UMAP_CATALOG_KEYS`/`_CLUSTERING_CATALOG_KEYS` and assert their union
  maps 1:1 onto the parametrization table, so a future catalog key with no baseline fails
  here rather than shipping uncovered. Verifies the second scenario of "Baseline Coverage
  For The Optional Plot Keys".

## 2. Generate and commit the baselines

- [ ] 2.1 Extend `scripts/gen_plot_snapshots_golden.py` with an `_OPTIONAL_TOOLS` table and
  an `_render_optional` helper mirroring `_render_converged`; keep `--yes`, the old-vs-new
  RMS print, and the all-or-nothing write semantics untouched.
- [ ] 2.2 **RED** — extend `tests/scripts/test_gen_plot_snapshots_golden.py` to cover the new
  render path: a first-time write needs no `--yes`, and an overwrite without `--yes` writes
  nothing and exits non-zero (the existing behaviour, now over 11 files not 3).
- [ ] 2.3 Run the generator with no `--yes` first and confirm it reports 8 "new baseline"
  lines and 3 unchanged RMS≈0 lines; then rerun with `--yes`. Record both outputs.
- [ ] 2.4 Confirm each committed PNG is under the 500 KB `check-added-large-files` limit
  (measured: largest 214.1 KB, total 580.6 KB) and that `git check-attr` reports `binary` for
  them via `.gitattributes`' `*.png` rule.
- [ ] 2.5 Verify the 3 pre-existing baselines are byte-identical after the run —
  `git status` must show them unmodified. A change there means this PR altered coverage it
  does not own.

## 3. The comparison tests

- [ ] 3.1 **RED** — `test_optional_plot_matches_baseline_within_tolerance`, parametrized over
  all 8 keys with readable ids, comparing via the existing `_compare_or_fail` (so the
  cross-platform pointer and both failure paths are inherited, not re-implemented). Run it
  with one baseline temporarily removed to confirm the actionable missing-baseline message
  fires before making it pass.
- [ ] 3.2 **GREEN** — with baselines committed from §2, all 8 pass.
- [ ] 3.3 **RED→GREEN** — `test_tolerance_catches_a_real_regression_in_an_optional_plot`:
  reproduce the 10%-dim case live (not hardcoded) against one optional baseline, asserting
  `compare_images` returns non-`None`. Mirrors the existing test for the dedicated tools.

## 4. Pin the measured blind spots (design.md Decision 2)

- [ ] 4.1 **RED→GREEN** — `test_clustering_scatter_does_not_catch_a_cluster_count_change`:
  re-render `clustering` with a different `n_clusters` than the baseline's auto-selected
  k=2 and assert `compare_images(..., tol=_TOL)` returns `None`. Docstring must state the
  dilution cause, quote the measured range (RMS 3.8-11.6 across k=3/4/5/8, gmm, hierarchical),
  and point at `test_clustering_tool.py` for the numeric coverage that does hold.
- [ ] 4.2 **RED→GREEN** — `test_single_cell_defect_in_loadings_heatmap_is_not_caught`:
  recolor one measured-cell-sized region of the heatmap baseline and assert `None`.
- [ ] 4.3 **RED→GREEN** — `test_loadings_heatmap_cell_geometry_matches_measured_value`:
  measure the live cell footprint via `ax.get_window_extent()` after `fig.canvas.draw()` and
  assert the constant 4.2 depends on is still within `rel=0.05` of it. Follows #713's
  Decision 8 — the constant that documents a limitation must be re-measured by code, since
  a hand-derived "measured" number that nothing re-checks is exactly how #713's original
  3%-vs-0.455% error happened.

## 5. Documentation

- [ ] 5.1 Update `test_viz_snapshot.py`'s module docstring: its "Scope" paragraph currently
  names these keys an explicit non-goal. Record the new coverage, `_TOL`'s re-derivation for
  these 8, and both pinned blind spots.
- [ ] 5.2 Update `tests/fixtures/README.md`'s `plot_baselines/` section: list the 8 new
  baselines, their provenance, and the unchanged regeneration command.
- [ ] 5.3 Note in the README that `MANIFEST.json` still records environment provenance only
  (no filenames), so it needed no structural edit — only the regeneration stamp.

## 6. Validation

- [ ] 6.1 `openspec validate add-bloommcp-optional-plot-snapshot-tests --strict`.
- [ ] 6.2 Full bloommcp sweep as CI runs it:
  `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and not live_smoke"`.
  The whole suite, not just the new file — #713's §9.4 found a regen-script global-state leak
  that broke 30 unrelated tests and was invisible to a targeted run.
- [ ] 6.3 `black --check` + `ruff check` on the touched Python files.
- [ ] 6.4 Confirm the new tests are unmarked, so `bloommcp-plot-snapshot-testing`'s existing
  "Runs Unmarked In Per-PR CI" requirement continues to hold for them.

## 7. Cross-platform verification (design.md Decision 3)

- [ ] 7.1 After opening the PR, read the `python-audit` job log directly
  (`gh api .../actions/jobs/<id>/logs`) — not just the green check — and confirm all 8
  comparisons passed on `ubuntu-latest` against the macOS-generated baselines.
- [ ] 7.2 If any failed, follow Decision 3's ordered fallback (diff artifact → identify
  content vs. canvas-size → regenerate that key from Linux → only then reconsider `_TOL`)
  and record the outcome in design.md, replacing its accepted-risk wording with what happened.
- [ ] 7.3 Record the verified outcome in design.md either way, as #713 did.
