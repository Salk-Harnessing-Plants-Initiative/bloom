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
  filing a follow-up for this (the reviewer's suggested minimum), resolved it directly:
  generalized `test_tolerance_catches_a_localized_regression` into a parametrized test
  covering both `heritability_bar` (~2%) and `correlation_matrix` (~3%, real margin above
  its measured crossing point), and rewrote the "Known limitation" section in
  `test_viz_snapshot.py`'s docstring and design.md Decision 2 with the full 5-baseline
  table instead of a single-baseline extrapolation.
- [x] 7.2 **PR body claimed local and CI test counts matched exactly** ("1376 passed...
  matches CI's python-audit invocation exactly") when CI actually showed 1408 — most likely
  `staging` drift between the local run and CI, not a test defect. Corrected the PR
  description to stop claiming exact reproduction.
- [x] 7.3 **No safeguard against a baseline-regeneration PR silently laundering a real
  regression** — `gen_plot_snapshots_golden.py` overwrote existing baselines with no
  visibility into what changed. Added: when overwriting an existing baseline, the script
  now computes and prints the old-vs-new RMS via `compare_images(tol=0)`, and its module
  docstring states that a baseline-touching PR must say what changed and why (design.md
  Decision 5). Verified locally: regenerating without any tool-code change reports
  `RMS=0.0` for all 5, and the PNGs come out byte-identical (`git status` shows no diff).
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
