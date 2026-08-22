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

## 6. Post-merge validation (cannot complete before the PR's own CI runs)

- [ ] 6.1 Watch this PR's `python-audit` CI run. If `test_viz_snapshot.py` fails on
  `compare_images` RMS grounds (not a genuine content diff — check the named
  `*-failed-diff.png`), follow design.md Decision 3: regenerate baselines from a Linux
  environment rather than blindly loosening `_TOL`.
