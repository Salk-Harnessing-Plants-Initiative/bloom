## 1. Establish the render path (test-first)

- [ ] 1.1 **RED** — add `test_optional_plot_keys_render_and_commit` to
  `tests/tools/test_viz_snapshot.py`: drive `pca_analysis`/`umap_analysis`/`clustering` with
  `include_plots=True` through a `FakeReader.add_cleaned_version` + `FakeResultStore`
  commit-spy helper and assert all 8 `<catalog_key>.png` files land on disk. Fails first
  because the helper does not exist (the existing `_render_to_dir` seeds `add_experiment`,
  which these `require_clean=True` tools reject).
- [ ] 1.2 **GREEN** — add `_render_optional_to_dir(...)` next to `_render_to_dir`, differing
  only in the seeding call and in taking a params model. Do not refactor `_render_to_dir`.
  Cache the render **per tool** so `pca_analysis` is rendered once rather than once per each
  of its 4 parametrized keys (measured: umap 3.12s cold / ~0.4s warm, pca 0.28s, clustering
  0.15s — naive per-key rendering would cost ~7.7s instead of ~3.6s). Implement the cache as
  a module-level dict keyed by tool, with the capture directory from the session-scoped
  `tmp_path_factory` — **not** as a `@pytest.fixture(scope="module")`, which cannot be done
  here: the obvious dependency `viz_env` is function-scoped (it takes `monkeypatch`,
  `tmp_path`, and `fake_supabase_storage`), and pytest forbids a broader-scoped fixture
  depending on narrower-scoped ones. The new tests do not need `viz_env` at all — `FakeReader`
  bypasses the `TRAITS_DIR` read it sets up.
  Keep the capture directory under pytest's own tmp root (which `tmp_path_factory`
  guarantees), because CI's diff-artifact upload globs
  `/tmp/pytest-of-*/**/*-failed-diff.png`; a capture dir outside it would silently stop
  surfacing diagnostics on failure.
- [ ] 1.3 **RED→GREEN** — `test_baseline_set_matches_the_tool_catalogs`: compare the on-disk
  set `plot_baselines/create_*_turface_19_baseline.png` against the union of
  `_PCA_CATALOG_KEYS`/`_UMAP_CATALOG_KEYS`/`_CLUSTERING_CATALOG_KEYS` imported from the tool
  modules. Assert **set equality against files on disk**, not against this file's own
  parametrization table — a table-vs-table check would miss an orphaned baseline left behind
  when a key is removed. Discharges both scenarios of "Baseline Coverage For The Optional
  Plot Keys". Runs red until §2 commits the PNGs; land it in the same commit as §2.4.

## 2. Generate, inspect, and commit the baselines

- [ ] 2.1 Extend `scripts/gen_plot_snapshots_golden.py` with an `_OPTIONAL_TOOLS` table and
  an `_render_optional` helper mirroring `_render_converged`; keep `--yes`, the old-vs-new
  RMS print, and the all-or-nothing write semantics untouched. Note the two tables differ in
  arity (one tool → N figures, vs. the existing 4-tuple with its vestigial `_converged`
  field), so the destructuring cannot be shared as-is.
- [ ] 2.2 Extend `write_manifest()` to record what these renders actually depend on:
  `umap-learn`, `numba`, `llvmlite`, `scikit-learn`, `seaborn`, `adjustText`, `numpy`,
  matplotlib's bundled FreeType (`matplotlib.ft2font.__freetype_version__`), the SHA-256 of
  `turface_19_final_data.csv`, and the resolved seed per stochastic tool. Without these a
  cross-platform failure is undiagnosable — a scikit-learn `svd_flip` sign change alone
  mirrors the biplot for a scientifically meaningless reason.
- [ ] 2.3 Run the generator with **no** `--yes` first; confirm it reports 8 "new baseline"
  lines and 3 unchanged RMS≈0 lines for the existing ones. Then rerun with `--yes`. Paste
  both outputs into the PR body.
- [ ] 2.4 **Inspect all 8 PNGs before committing them.** A baseline generated from today's
  code enshrines today's output, bugs included — every JSON golden in
  `tests/fixtures/README.md` is careful to call itself "a drift gate, not scientific ground
  truth", and these must be too. Confirm per figure: axes and legend labelled; scree
  variance percentages consistent with `turface_19_pca_golden.json`; 2 visible clusters in
  the scatter and 2 bars in the barplot; UMAP not collapsed to a single blob; no blank or
  degenerate panel. Record the check in the PR body.
- [ ] 2.5 Record the known upstream defect this inspection surfaces:
  `create_feature_contribution_heatmap` is titled "Feature Loadings (Correlations)" with a
  "Loading (Correlation)" colorbar, but plots unit-norm eigenvector components, which are
  **not** correlations (the trait–PC correlation is `component × sqrt(eigenvalue)`; on this
  fixture that understates PC1 by ~2.7x and overstates PC5 by ~2x, so the error reverses
  direction across the columns a reader compares). Document it next to the baseline and file
  it upstream against `talmolab/sleap-roots-analyze`; do not silently canonize it.
- [ ] 2.6 Confirm each committed PNG is under the 500 KB `check-added-large-files` limit
  (measured: largest 214.1 KB, total 580.6 KB) and that `git check-attr` reports `binary` via
  `.gitattributes`' `*.png` rule.
- [ ] 2.7 Verify the 3 pre-existing baselines are byte-identical after the run — `git status`
  must show them unmodified. A change there means this PR altered coverage it does not own.
- [ ] 2.8 **Only now** extend `tests/scripts/test_gen_plot_snapshots_golden.py` to cover the
  new render path, so `test_build_writes_new_baselines_without_needing_yes` and the two
  `--yes` tests stop silently covering 3 of 11. Two constraints, both verified against the
  file: (a) this must follow §2.3, because `_dimension_matched_markers()` (line 106) reads
  `_REAL_BASELINES_DIR / baseline_name` for every table entry, so growing the table before
  the PNGs exist raises `FileNotFoundError` and takes the 3 existing generator tests down
  with it; (b) it must iterate the two tables **separately** rather than concatenating them —
  line 114 destructures a 4-tuple (`baseline_name, _tool_fn, _produced_name, _converged`) and
  `_OPTIONAL_TOOLS` has different arity, so `_TOOLS + _OPTIONAL_TOOLS` raises `ValueError`.

## 3. The comparison tests

- [ ] 3.1 **RED** — `test_optional_plot_matches_baseline_within_tolerance`, parametrized over
  all 8 keys with readable ids. Re-implement the `assert baseline.is_file(), "missing
  baseline ... run gen_plot_snapshots_golden.py --yes"` guard in the test body: it is **not**
  inherited from `_compare_or_fail`, which only handles the `ImageComparisonFailure`/`OSError`
  and RMS-string paths and whose message points at design.md Decision 3, never at the regen
  script. Delegate the comparison itself to `_compare_or_fail` so both of its failure paths
  and the cross-platform pointer are shared.
- [ ] 3.2 **GREEN** — with baselines committed from §2, all 8 pass.
- [ ] 3.3 **RED→GREEN** — `test_missing_optional_baseline_fails_with_an_actionable_message`:
  monkeypatch `_BASELINES` to a nonexistent dir and assert the message names the regen
  command, mirroring the existing `test_missing_baseline_fails_with_an_actionable_message`.
  A committed test, not the manual one-time check an earlier draft of this plan relied on —
  a `#### Scenario:` is not discharged by a step someone performed once.
- [ ] 3.4 **RED→GREEN** — `test_tolerance_catches_a_real_parameter_regression`, parametrized
  over the measured semantic perturbations rather than only a uniform dim (the weakest
  available probe — the module docstring itself notes a uniform shift's RMS is dominated by
  shared white background): `pca standardize=False` → scree RMS 110.2 and contribution plot
  100.6; `clustering n_clusters` changed → barplot 64.7-69.5; one perturbed loadings value →
  heatmap 40.1-63.8. This is the genuinely RED-first test: it fails on the missing baseline,
  then passes for the right reason once §2 lands.
- [ ] 3.5 **RED→GREEN** — three cheap boundary tests for `_compare_or_fail`, which 8 more
  keys now depend on and which has only ever been driven through its happy path (#713's own
  review found a real bug in this helper — the pointer was dead code for the RMS branch).
  All `tmp_path`, no render: (a) pixel-dimension mismatch → `ImageComparisonFailure` path
  attaches the pointer; (b) zero-length baseline; (c) truncated/corrupt PNG → `OSError` path
  attaches the pointer rather than surfacing a bare traceback.

## 4. Pin the measured blind spots (design.md Decision 2)

- [ ] 4.1 **RED→GREEN** — `test_cluster_scatter_pca_does_not_catch_a_cluster_count_change`.
  Pin `n_clusters=8` (the worst measured case, RMS 11.60, largest margin to `_TOL`). Make the
  assertion **two-sided and banded**, because a bare `assert compare_images(...) is None` on
  a *live re-render* is equally satisfied by a byte-identical render — so a refactor that
  silently ignored `n_clusters` would satisfy it vacuously while reading as coverage:
  assert `rms > 3.0` (the perturbation really happened), `compare_images(..., tol=_TOL) is
  None` (still not caught), and `rms < 13.0` (inside the measured 3.8-11.6 band). Wrap in
  `try/except ImageComparisonFailure` → `pytest.fail(... + _CROSS_PLATFORM_POINTER)`, since
  a Linux canvas-size difference would otherwise *error* rather than fail with the pointer.
- [ ] 4.2 **RED→GREEN** — `test_cluster_size_barplot_does_not_catch_a_same_k_membership_change`:
  `method="hierarchical"` yields the same k=2 as the baseline but different cluster sizes and
  scores RMS 13.5, under `_TOL`. Same two-sided banded shape as 4.1. Required by this
  change's own spec: a measured limit that is not pinned violates the requirement it adds.
- [ ] 4.3 Confirm no negative control is written for
  `create_feature_contribution_heatmap`. An earlier draft of this plan had one, based on a
  measurement taken through the **delegate** with 13 numeric columns rather than through the
  tool's 11 certified traits — the exact shortcut this change's own spec forbids. Re-measured
  correctly, a real single-cell loadings defect is **caught** (RMS 40.1-63.8); it is covered
  by 3.4's positive control instead. See design.md Decision 2.

## 5. Documentation

- [ ] 5.1 Update `test_viz_snapshot.py`'s module docstring: its "Scope" paragraph currently
  names these keys an explicit non-goal — the text #723's re-verification comment cites as
  proof the issue is unaddressed. Record the new coverage, `_TOL`'s re-derivation for these
  8, both pinned blind spots, and the drift-gate-not-correctness-oracle caveat.
- [ ] 5.2 Update `tests/fixtures/README.md`'s `plot_baselines/` section: the 8 new baselines,
  their provenance, the unchanged regeneration command, and the explicit statement that these
  pin rendering and not scientific correctness (matching how every JSON golden there is
  already described).
- [ ] 5.3 Document in the README what the baselines encode implicitly, so a future failure is
  read correctly rather than regenerated away: they pin `standardize=True`,
  `explained_variance_threshold=0.95`, `seed=42`, `method="kmeans"`, `n_clusters=None`(→2),
  and the `n_neighbors`/`min_dist` defaults; `create_umap_single_trait` colors by
  `trait_cols[0]` (`Maximum.Width.mm`), so a CSV column reorder surfaces as a rendering
  regression; and the fixture's SHA-256 now ties the baselines to the CSV that produced them.

## 6. Validation

- [ ] 6.1 `openspec validate --strict` across the repo, not just this change — §7 edits a
  sibling change's delta.
- [ ] 6.2 Full bloommcp sweep as CI runs it:
  `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and not live_smoke"`.
  The whole suite, not just the new file — #713's §9.4 found a regen-script global-state leak
  (`build()` mutates `eu.TRAITS_DIR`, `_manifest.list_prefix`, `_sc.list_prefix`) that broke
  30 unrelated tests and was invisible to a targeted run.
- [ ] 6.3 Record the suite's before/after runtime in design.md (baseline measured: 190.0s,
  1709 passed; estimated addition ~6-7s, ~3.5%, including ~2.5s from §2.8's generator tests
  now rendering 11 figures per `build()` call instead of 3).
- [ ] 6.4 `black --check` + `ruff check` on the touched Python files.
- [ ] 6.5 Confirm the new tests are unmarked, so `bloommcp-plot-snapshot-testing`'s existing
  "Runs Unmarked In Per-PR CI" requirement continues to hold for them.

## 7. Correct the sibling change's stale spec

- [ ] 7.1 Amend `openspec/changes/add-bloommcp-plot-snapshot-tests/specs/bloommcp-plot-snapshot-testing/spec.md`:
  retitle "Baseline Coverage For The **5** Plotting Tools" → 3, drop
  `heritability_turface_19_baseline.png` and `variance_decomposition_turface_19_baseline.png`
  (deleted by #462), and correct the regeneration command to
  `uv run --frozen --extra test python scripts/gen_plot_snapshots_golden.py --yes` — the
  current text asserts the script "overwrites every baseline PNG", which is false without
  `--yes` (it prints RMS values and exits 1). That change is unarchived, so both falsehoods
  would otherwise be frozen into `openspec/specs/` at archive time; PR #724 merged to staging
  on 2026-09-03, so the file is editable here.
- [ ] 7.2 Re-run `openspec validate add-bloommcp-plot-snapshot-tests --strict` after the edit.

## 8. Cross-platform verification (design.md Decision 3)

- [ ] 8.1 After opening the PR, read the `python-audit` job log directly
  (`gh api .../actions/jobs/<id>/logs`) — not just the green check — and confirm all 8
  comparisons passed on `ubuntu-latest` against the macOS-generated baselines. Pay particular
  attention to §4.3's live-geometry assertion and to `create_pca_biplot` (its `adjustText`
  label solver is the most layout-fragile of the 8).
- [ ] 8.2 If any failed, follow Decision 3's ordered fallback (diff artifact → identify
  content vs. canvas-size → regenerate that key from Linux → only then reconsider `_TOL`).
  If a single key is regenerated from Linux, the directory then holds baselines from two
  platforms under one `MANIFEST.json` — record the per-file platform stamp before doing so,
  not after.
- [ ] 8.3 Record the verified outcome in design.md either way, replacing Decision 3's
  accepted-risk wording with what actually happened, as #713 did.

## 9. Follow-ups to file (none of these issues exist today — verified via `gh issue list`)

- [ ] 9.1 `create_cluster_scatter_pca`'s semantic blind spot — a sibling to #768 and measured
  more severe (#768's single-cell case at least requires the defect to be small; here the
  plot's entire cluster assignment can change undetected). #768's precedent was measure +
  pin + **file**; this change does the first two, so the third is owed.
- [ ] 9.2 `outputs` key-order non-determinism: `list(frozenset)` in `pca_analysis.py:422` and
  `umap_analysis.py:649` vs `sorted(...)` in `clustering.py:644` and
  `heritability_analysis.py:594`.
- [ ] 9.3 Restore pixel coverage for `heritability_analysis`'s two `ResultStore`-persisted
  figures, lost in #462 — `tests/fixtures/README.md` already promises this is "a follow-up,
  not a silent loss".
- [ ] 9.4 The `create_feature_contribution_heatmap` "Loadings (Correlations)" mislabel (§2.5),
  filed upstream against `talmolab/sleap-roots-analyze`.
