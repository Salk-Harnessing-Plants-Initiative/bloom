## 1. Baseline generation script + baseline PNGs

- [x] 1.1 Add `bloommcp/scripts/gen_plot_snapshots_golden.py`: fakes the versioned-manifest
  miss (`bloom_mcp.manifest.manifest.list_prefix` / `bloom_mcp.supabase_client.list_prefix`
  → `[]`, no Supabase env needed), points `TRAITS_DIR`/`PLOTS_DIR` at a temp dir seeded with
  `turface_19_final_data.csv`, and calls each of the 5 MCP tool functions directly (not
  their delegates) — the same fixture and call path `test_viz_tools.py`'s `viz_env` uses.
- [x] 1.2 Copy each tool's produced PNG into `tests/fixtures/plot_baselines/` under a
  `_baseline.png`-suffixed name; write a `MANIFEST.json` recording matplotlib/Pillow/
  sleap-roots-analyze versions and the generation platform (provenance only — not
  asserted by any test).
- [x] 1.3 Run the script locally and confirm all 5 baseline PNGs + `MANIFEST.json` are
  produced and look correct (visual spot-check).

## 2. Tolerance calibration (design.md Decision 2)

- [x] 2.1 Measure RMS via `compare_images` for a range of `PIL.ImageEnhance.Brightness`
  perturbations (2%/5%/10%/15% dim) against a real baseline PNG.
- [x] 2.2 Measure RMS for a same-machine matplotlib 3.7.5→3.10.8 version-bump re-render
  (stand-in for the dependency-bump scenario #713 is about) and for a deliberate content
  regression (recolored + rebinned bars), to confirm the chosen tolerance sits far below
  a genuine regression's RMS.
- [x] 2.3 Pick `_TOL = 15` from those measurements (documented in
  `test_viz_snapshot.py`'s module docstring and design.md, not just a bare constant).

## 3. Shared fixture refactor

- [x] 3.1 Create `bloommcp/tests/tools/conftest.py` holding the `viz_env` fixture (moved,
  not copied, from `test_viz_tools.py`).
- [x] 3.2 Remove the `viz_env` definition (and now-unused `shutil` import) from
  `test_viz_tools.py`; confirm its existing tests still pass unchanged.

## 4. Snapshot test file

- [x] 4.1 Add `bloommcp/tests/tools/test_viz_snapshot.py`: parametrized
  `test_plot_matches_baseline_within_tolerance` over the 5 tools, each rendering via
  `viz_env` and comparing to its baseline with `compare_images(tol=_TOL)`.
- [x] 4.2 Add `test_tolerance_catches_a_real_regression`: reproduces the 10%-dim
  perturbation live against a real baseline and asserts `compare_images` still flags it —
  proves `_TOL` isn't a rubber stamp.
- [x] 4.3 (post-review) Wrap the `compare_images` call in `_compare_or_fail`: a
  dimension-mismatch between baseline and actual raises `ImageComparisonFailure` (not a
  clean RMS result) — a plausible symptom of the same cross-platform `bbox_inches="tight"`
  risk `_TOL` accounts for — so convert it into an actionable `pytest.fail` instead of
  letting a raw exception obscure the diagnosis (`/review-openspec` finding).
- [x] 4.4 (post-review) Add `test_tolerance_catches_a_localized_regression`: proves `_TOL`
  also catches a spatially small (not just whole-image-uniform) regression, at the
  empirically-measured ~2%-of-image-area floor documented in design.md Decision 2
  (`/review-openspec` finding — a whole-image RMS check needs this distinct from the
  uniform-dim case above).
- [x] 4.5 (post-review) Add `test_missing_baseline_fails_with_an_actionable_message`,
  exercising the inline `assert baseline.is_file()` guard's message (previously asserted
  only by spec.md's scenario text, not a test — `/review-openspec` finding).
- [x] 4.6 Run `cd bloommcp && uv run --extra test pytest tests/tools/test_viz_snapshot.py
  tests/tools/test_viz_tools.py -v` — confirm green.
- [x] 4.7 Run the full per-PR sweep `uv run --frozen --extra test pytest tests/ -m "not
  integration and not live_smoke"` — confirm no regressions elsewhere from the conftest
  move.

## 5. Docs + validate

- [x] 5.1 Add a `plot_baselines/` section to `tests/fixtures/README.md` documenting
  provenance (rendered locally at authoring time; see design.md Decision 3 for the
  cross-platform caveat) and the regeneration command.
- [x] 5.2 `openspec validate add-bloommcp-plot-snapshot-tests --strict` passes.
- [x] 5.3 (post-review) Fix `test_viz_snapshot.py`'s module docstring, which incorrectly
  claimed the baselines were "rendered on Linux" — they were rendered on macOS
  (`/review-openspec` BLOCKING finding: this directly contradicted `MANIFEST.json` and
  design.md Decision 3's own honest disclosure). Also add the localized-regression
  finding (task 4.4) to the docstring and design.md Decision 2.
- [x] 5.4 (post-review) File a follow-up GitHub issue for the `pca_analysis`/
  `umap_analysis`/`clustering` optional-plot-key scope-down instead of leaving it as an
  undated prose promise (`/review-openspec` finding) — filed as **#723**; referenced from
  proposal.md and design.md Decision 4.
- [x] 5.5 (post-review) Make the baseline-regeneration command consistent across
  `gen_plot_snapshots_golden.py`'s docstring, `test_viz_snapshot.py`, and
  `tests/fixtures/README.md` (`uv run --frozen --extra test python
  scripts/gen_plot_snapshots_golden.py` everywhere — one location was missing
  `--extra test`).

## 6. Post-merge validation

- [x] 6.1 Watched PR #724's `python-audit` CI run (`ubuntu-latest`): all 8
  `test_viz_snapshot.py` tests passed, including the 5 baseline comparisons — the
  macOS-generated baselines (design.md Decision 3's accepted risk) held up against the
  real Linux render at `_TOL=15` with no `compare_images` RMS or dimension-mismatch
  failure. No baseline regeneration or tolerance adjustment was needed. Confirmed via
  the job log (`gh api repos/.../actions/jobs/<id>/logs`), not just the green checkmark.

## 7. Human-review response (PR #724, reviewer eberrigan)

- [x] 7.1 **Tolerance coverage was uneven across the 5 plot types** — `_TOL=15` had only
  been calibrated against `histograms`/`heritability_bar`. Measured the localized-regression
  floor (single fully-recolored square, increasing area %) against all 5 baselines: 4 of 5
  clear `_TOL=15` comfortably at 2% area, but `correlation_matrix` — the highest-stakes tool
  for a silent single-cell error — does **not** (RMS≈13.7 at 2%, crosses ~2.5%). Rather than
  filing a follow-up for this (the reviewer's suggested minimum), generalized
  `test_tolerance_catches_a_localized_regression` into a parametrized test covering both
  `heritability_bar` (~2%) and `correlation_matrix` (~3%, real margin above its measured
  crossing point).
  **Correction (see section 8): this was described as "resolved it directly" at the time,
  which was itself an overclaim.** A follow-up review measured `correlation_matrix`'s
  *actual* single-cell pixel footprint against the live delegate (`ax.get_window_extent()`)
  and found it's ~0.455% of the image — the 3% test case here is ~6-7x a real cell, so it
  never validated single-cell detection at all, only a several-cells-sized regression. See
  section 8.1 for the actual resolution (measuring the real size and being honest that
  single-cell detection remains an open, tracked, structural limitation — #768).
- [x] 7.2 **PR body claimed local and CI test counts matched exactly** ("1376 passed...
  matches CI's python-audit invocation exactly") when CI actually showed 1408 — most likely
  `staging` drift between the local run and CI, not a test defect. Corrected the PR
  description to stop claiming exact reproduction.
- [x] 7.3 **No safeguard against a baseline-regeneration PR silently laundering a real
  regression** — `gen_plot_snapshots_golden.py` overwrote existing baselines with no
  visibility into what changed. Added: when overwriting an existing baseline, the script
  now computes and prints the old-vs-new RMS via `compare_images(tol=0)`, and its module
  docstring states that a baseline-touching PR should say what changed and why (design.md
  Decision 5). Verified locally: regenerating without any tool-code change reports
  `RMS=0.0` for all 5, and the PNGs come out byte-identical (`git status` shows no diff).
  **Correction (see section 8.2): calling this a "safeguard" overclaimed what it is** — a
  visibility print with no enforcement (`shutil.copy` still runs regardless of the RMS,
  nothing in CI reads the output). Reframed as a "visibility aid" throughout, and its logic
  extracted into a tested `_report_regeneration` helper (section 8.4).
- [x] 7.4 **Minor: `tasks.md` vs. PR body inconsistency** on whether task 6.1 was done —
  synced the PR body's checklist to match `tasks.md`.
- [x] 7.5 **Minor: localized-regression test's discriminating power is tied to today's
  exact baseline pixel content** and could need re-measuring after a routine baseline
  regen — acknowledged explicitly in the docstring table's closing note ("re-measure...
  after any change to a plot's own color density or layout") rather than left implicit.
- [x] 7.6 **Suggestion: narrow `_compare_or_fail`'s `except Exception`** to
  `except (ImageComparisonFailure, ValueError)` — done.
- [x] 7.7 **Suggestion: tie `_TOL` more directly to the design.md regen protocol** — added
  an inline comment at the `_TOL = 15` definition site pointing to design.md Decisions 2 & 3
  (previously only reachable via the module docstring).
- [x] 7.8 Reran `cd bloommcp && uv run --extra test pytest tests/tools/test_viz_snapshot.py
  tests/tools/test_viz_tools.py -v` (9 tests, up from 8) and the full per-PR sweep —
  confirm green before pushing the fix commit.

## 8. Second human-review response (5-subagent parallel review of the section-7 fix commit)

Found that section 7's own "resolved it directly" claim didn't hold up: the
`correlation_matrix` negative-control test simulated a defect ~6x the size of a real
heatmap cell, so it never actually validated single-cell detection for the tool the whole
fix was about. Also found the narrowed exception handler's diagnostic pointer was
unreachable in the common failure path, and that "safeguard"/"per-plot tolerance" language
oversold what changed.

- [x] 8.1 **BLOCKING: measure `correlation_matrix`'s real single-cell size, not an assumed
  area fraction.** Rendered `create_correlation_heatmap` against the live `turface_19`
  fixture (11 real trait columns → 55 lower-triangle cells) and measured the axes geometry
  directly via `ax.get_window_extent()` after `fig.canvas.draw()`: one real cell is
  ~108.8×108.8px ≈ 11,836px² ≈ **0.455%** of the 1690×1539 baseline — not the ~3% the
  previous test used (~6-7x too large). Recoloring exactly that real footprint to the
  most-detectable-possible color (opaque orange) scores **RMS≈5.2**, nowhere near
  `_TOL=15`. Added `test_realistic_single_cell_defect_in_correlation_matrix_is_not_caught`:
  a pinning/characterization test that honestly asserts the current (negative) result
  rather than a rubber-stamped "resolved". Also verified `heritability_bar`'s existing 2%
  figure the same rigorous way (real per-bar area via each bar patch's
  `get_window_extent()`: range ~0%-2.89%, median ~2.41%) — it held up; only
  `correlation_matrix`'s claim was wrong. Relabeled `test_tolerance_catches_a_localized_regression`'s
  `correlation_matrix` case honestly as validating a several-cells-sized (~6-7 cells)
  regression, not a single-cell one.
- [x] 8.2 **Filed #768** ("correlation_matrix snapshot test cannot catch a real single-cell
  defect") instead of leaving this as an implicit assumption or re-attempting a fix that
  can't actually work: a `_TOL` low enough to catch a ~5 RMS single-cell signal would also
  flag ordinary cross-platform noise (design.md Decision 7 explains why a per-plot `_TOL`
  wouldn't help either). Referenced from `test_viz_snapshot.py`'s docstring, design.md
  Decisions 2 & 7, and this file.
- [x] 8.3 **IMPORTANT: `_compare_or_fail`'s design.md pointer was unreachable in the
  ordinary failure case.** `compare_images` returns a plain string (not an exception) for
  an RMS-over-`_TOL` mismatch — the exact scenario `_TOL` is calibrated against — so the
  pointer text inside the `except` block never fired for it. Restructured `_compare_or_fail`
  to attach the same pointer (`_CROSS_PLATFORM_POINTER`) to both the exception path AND the
  returned-string path via one shared `pytest.fail` call.
- [x] 8.4 **IMPORTANT: "per-plot tolerance" and "safeguard" language oversold the actual
  changes.** `_TOL=15` remains one global constant in the only comparison that runs at
  CI time (`test_plot_matches_baseline_within_tolerance`/`_compare_or_fail`) — what
  became per-plot was the negative-control test's synthetic perturbation *size*, not `_TOL`
  itself (design.md Decision 7 explains why a real per-plot `_TOL` wouldn't help for
  `correlation_matrix` anyway). The regen script's RMS print was called a "safeguard";
  reframed as a "visibility aid" throughout (script docstring, design.md Decision 5) since
  nothing enforces it. Its logic extracted into `_report_regeneration`, now covered by
  `tests/scripts/test_gen_plot_snapshots_golden.py` (previously untested, a cheap gap a
  reviewer flagged).
- [x] 8.5 **SUGGESTION: narrow `_compare_or_fail`'s exception tuple correctly.** The
  previous `except (ImageComparisonFailure, ValueError)` included a `ValueError` no actual
  `matplotlib.testing.compare` code path raises (verified by reading `_load_image`/
  `calculate_rms`/`crop_to_same` source) and omitted `OSError`, the real type
  `PIL.UnidentifiedImageError` (a corrupted/truncated baseline PNG) would raise. Swapped to
  `except (ImageComparisonFailure, OSError)`.
- [x] 8.6 **SUGGESTION**: added a comment next to `_SNAPSHOT_TOOLS` explicitly stating that
  `histograms`/`boxplots`/`variance_decomposition` are documented but not independently
  test-enforced for the localized-regression floor (only `heritability_bar` and
  `correlation_matrix` get a dedicated case).
- [x] 8.7 **SUGGESTION**: design.md Decision 3 now cites that the section-7 fix commit's
  own CI run cross-platform-reverified the new `correlation_matrix`-parametrized case, not
  just the original 5 baseline comparisons.
- [x] 8.8 Ran `cd bloommcp && uv run --extra test pytest tests/tools/test_viz_snapshot.py
  tests/scripts/test_gen_plot_snapshots_golden.py -v` and the full per-PR sweep — confirm
  green before pushing.

## 9. Third human-review response (5-subagent review of the section-8 fix commit)

No blocking issues — the honestly-pinned `correlation_matrix` limitation (#768) held up
under independent re-derivation of the geometry math. Two important gaps closed: a
"measured" constant that no code actually re-measured, and a noise-floor calibration
verified on only one of the 5 plot types.

- [x] 9.1 **IMPORTANT: `_REAL_CORRELATION_CELL_AREA_FRACTION` was a hardcoded literal
  documented as "measured," with nothing re-verifying it.** Exactly the same class of
  mistake that produced the original 3%-vs-0.455% error (section 8's whole subject) — a
  number a human is trusted to re-derive by hand rather than something self-checking.
  Added `test_real_correlation_cell_area_fraction_matches_measured_geometry`: renders the
  live delegate call, measures `ax.get_window_extent()` itself
  (`_measure_live_correlation_cell_area_fraction`), and asserts the hardcoded constant is
  still within 5% relative of that live measurement — a future fixture/delegate geometry
  change now fails this test loudly instead of leaving the constant stale. Design.md
  Decision 8.
- [x] 9.2 **IMPORTANT: `_TOL=15`'s noise-floor calibration (the 5.6/12.2/24.4/36.6 RMS
  table) was measured against `histograms` only, then applied to all 5 plot types without
  checking.** Re-measured the same 2%/5%/10%/15%-dim perturbation against all 5 baselines:
  they agree within ~0.3 RMS at every level (the 5 baselines: 5.4-5.7 / 11.7-12.5 /
  23.4-24.9 / 35.0-37.4) — because a *uniform* shift's RMS is dominated by the large shared
  white-background area, not each plot's distinct foreground, unlike the *localized*
  perturbation case (section 7/8) whose RMS genuinely does vary a lot by plot type. Added
  the full table + this explanation to `test_viz_snapshot.py`'s docstring and design.md
  Decision 2, replacing the single-baseline citation.
- [x] 9.3 **IMPORTANT: a real CI failure's diff image never survived the CI run.**
  `compare_images` names a local `*-failed-diff.png` on failure, but nothing uploaded it —
  a non-matplotlib-expert reviewer had no way to actually see what changed. Added an
  `actions/upload-artifact` step (`if: failure()`) to the `python-audit` job in
  `.github/workflows/pr-checks.yml`, uploading `/tmp/pytest-of-*/**/*-failed-diff.png`.
  Verified the existing CI-structure guard tests (`test_bloommcp_wheel_import_gate.py`,
  `test_ci_workflow_uv_conventions.py`, `test_pr_checks_workflow_shape.py`,
  `test_ci_dev_stack_smoke.py` — all match by step *presence*, not index) still pass.
  Design.md Decision 9.
- [x] 9.4 **IMPORTANT: nothing backstopped a human ignoring the regen script's printed
  RMS.** Decision 5 already deliberately chose not to build CI-side enforcement — but
  `gen_plot_snapshots_golden.py` now requires an explicit `--yes` flag before overwriting
  any *existing* baseline: without it, prints every file's RMS and exits 1 without writing
  anything (all-or-nothing, not a per-file mix); with it, proceeds as before. A local,
  opt-in speed bump, not a CI gate. Design.md Decision 10. Updated the two literal
  regen-command citations (`tests/fixtures/README.md`, the "missing baseline" test failure
  message) to include `--yes`.
  **Caught during this fix's own test-writing**: the new `build()`-gate tests initially
  leaked global state (`_manifest.list_prefix`/`_sc.list_prefix`, permanently reassigned by
  `build()` via plain assignment, not `monkeypatch.setattr`) into 30 unrelated tests
  elsewhere in the suite (`tests/test_storage_backend.py`,
  `tests/tools/test_list_existing_analyses_staleness.py`) when run as part of the full
  sweep — caught by actually running the full per-PR sweep before committing, not just the
  new/touched test files. Fixed by registering those two globals (alongside
  `TRAITS_DIR`/`PLOTS_DIR`) for `monkeypatch` auto-revert before calling `build()` in tests.
- [x] 9.5 **SUGGESTION: design.md's Decision 7 was physically ordered before Decision 6.**
  Reordered so the numbering matches file order.
- [x] 9.6 **SUGGESTION: design.md cited a stale baseline platform string
  (`macOS-14.8.2-arm64`) that had drifted from the currently-committed `MANIFEST.json`
  (`macOS-26.5.1-arm64-arm-64bit`) after an OS update on the authoring machine** — the PNG
  bytes were confirmed byte-identical across commits, so this was doc-only drift, not a
  silent re-render. Removed the hardcoded platform string from design.md's prose entirely
  (pointing to `MANIFEST.json` instead) rather than just updating today's number, since a
  hardcoded citation would go stale again the next time the authoring machine's OS updates.
- [x] 9.7 **SUGGESTION: no pointer to #768 existed in `plot_correlation_matrix.py` itself**
  — a maintainer starting from production code had no breadcrumb to the test-coverage gap.
  Added a short module-docstring note.
- [x] 9.8 Ran `cd bloommcp && uv run --extra test pytest tests/tools/test_viz_snapshot.py
  tests/scripts/test_gen_plot_snapshots_golden.py -v` (11 + 6 tests) and the **full per-PR
  sweep** (`uv run --frozen --extra test pytest tests/ -m "not integration and not
  live_smoke"`, 1385 passed) — confirmed green, including the previously-leaking tests,
  before pushing.
