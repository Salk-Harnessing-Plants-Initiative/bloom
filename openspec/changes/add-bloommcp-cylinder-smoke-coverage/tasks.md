## 1. Cylinder fixture

- [x] 1.1 Port `traits_11DAG_cleaned_qc_scanner_independent.csv` (raw) and
      `cylinder_final_data.csv` (post-QC) from the upstream `wheat_edpie` bundle into
      `bloommcp/tests/fixtures/` as `cylinder_raw_data.csv` / `cylinder_final_data.csv`.
- [x] 1.2 Port the upstream `expected/qc/cylinder/*` detail (removed-traits/samples,
      outlier-removal log, heritability diagnostics) into
      `cylinder_qc_golden.json` / `cylinder_outlier_golden.json`, following the same
      shape as the turface_19 equivalents. Recorded via real `fastmcp.Client` calls
      (`qc_clean` / `remove_outliers`) against the running dev stack, not
      hand-simulated.
- [x] 1.3 Derive `cylinder_qc_inspect_golden.json` using
      `sleap_roots_analyze.apply_data_cleanup_filters` at bloommcp's canonical defaults
      (matching how `turface_19_qc_inspect_golden.json` was produced). Also recorded
      via a real `qc_inspect` MCP call.
- [x] 1.4 Port the upstream `expected/viz/cylinder/viz_pca_metadata.json` independent
      PCA golden into `cylinder_pca_golden.json`. Per-PC `explained_variance_ratio`
      re-derived locally at the upstream pipeline's own 0.75 variance threshold
      (confirmed: sums to the independent cumulative value to 13 significant figures).
- [x] 1.5 Generate `cylinder_clustering_golden.json` (mirroring
      `scripts/gen_clustering_golden.py`'s pattern), documenting it as a
      characterization snapshot, not an independent oracle. The `gmm` entry carries an
      explicit `_note`: its hard-assignment metrics are bit-identical to `kmeans`'s
      (EM never moves off its k-means initialization at 588 traits vs. 123 samples) --
      recorded honestly as the ill-conditioning `design.md` predicts, not smoothed over.
- [x] 1.6 Add a `README.md` section for the cylinder bundle, documenting per-key
      provenance (independent vs. characterization) matching the turface_19 entries'
      convention, and noting the differing role-column names
      (`plant_qr_code`/`Geno`/`Rep`) and wider trait profile.
- [x] 1.7 Add dedicated cylinder oracle test functions to
      `bloommcp/tests/tools/test_qc_clean_tool.py`, `test_qc_inspect_tool.py`, and
      `test_remove_outliers_tool.py`, asserting each cylinder golden JSON from 1.2/1.3
      through the real tool call (fake-backed, no live stack) -- fast and unmarked, so
      they run in `python-audit`'s per-PR sweep. (Implementation note: added as
      standalone `test_cylinder_*` functions alongside the existing turface_19 tests
      rather than literally parametrizing every existing assertion -- the two
      fixtures' schemas differ too much, 20 vs. 846 trait columns, for a shared
      parametrize to stay readable. All 3 verified passing.)
- [x] 1.8 Add a `cylinder` fixture + a cylinder-parametrized PCA oracle test to
      `bloommcp/tests/test_oracle.py`, asserting `cylinder_pca_golden.json` the same
      way `test_external_library_pca_matches_recorded_oracle` asserts turface_19's (at
      the fixture's own 0.75 variance threshold, not turface_19's 0.95 -- see the
      golden's `_pca_evr_source`). (Implementation note: `test_oracle.py` does not
      contain a clustering assertion -- it was deleted by `devendor-bloommcp-analysis`;
      clustering's correctness oracle is determinism, asserted in
      `test_clustering_tool.py` per `tests/fixtures/README.md`. Task narrowed to PCA
      accordingly.) Verified passing, unmarked (matches the existing PCA test, which
      carries no `integration` marker).

## 2. Relocate existing smoke scripts

- [x] 2.1 `git mv bloommcp/scripts/live_persistence_smoke.py bloommcp/tests/smoke/`
- [x] 2.2 `git mv bloommcp/scripts/live_plot_tool_smoke.py bloommcp/tests/smoke/`
- [x] 2.3 Update the `bloommcp-smoke` Makefile target's invocation to
      `uv run python tests/smoke/live_persistence_smoke.py`.
- [x] 2.4 Update the `bloommcp-plot-smoke` Makefile target's invocation analogously.
      Also fixed both scripts' own `parents[N]`/prose self-references to the old path,
      and added the missing `make bloommcp-plot-smoke` line to the Makefile `help`
      target (pre-existing gap, drive-by fix).
- [x] 2.5 Updated `bloommcp/tests/scripts/test_live_persistence_smoke_logic.py`'s
      hardcoded `_DRIVER_PATH` to the new `tests/smoke/` location -- confirmed this
      module-level import breaks at pytest collection time if not fixed in the same
      commit as the `git mv`.
- [x] 2.6 Updated `bloommcp/docs/local-validation.md`'s relative link and prose to
      point at `tests/smoke/`.
- [x] 2.7 Ran `tests/unit/test_bloommcp_live_smoke_gate.py`: passes unmodified. Also
      ran both relocated scripts end-to-end (`make bloommcp-smoke`,
      `make bloommcp-plot-smoke`) against the live dev stack: both pass.

## 3. Granular tool smoke tests

- [x] 3.0 Added `live_smoke` and `live_smoke_slow` marker declarations to
      `bloommcp/pyproject.toml`, and narrowed `python-audit`'s pytest invocation to
      `-m "not integration and not live_smoke"` -- landed in the same commit as the
      test files below.
- [x] 3.1 Added `bloommcp/tests/smoke/conftest.py`: `call_tool`/`call_plot_tool`
      fixtures (sync wrappers around a real `fastmcp.Client`, confirmed the granular
      `sleap_roots_*` tools nest their payload under a `params` argument while the 5
      plot tools take flat kwargs -- two distinct helpers, not one), plus
      `fixture_name`/`seeded_experiment` fixtures parametrizing over
      `{turface_19, cylinder}`.
- [x] 3.2 Added `test_qc_clean_smoke.py`. Verified passing (both fixtures).
- [x] 3.3 Added `test_qc_inspect_smoke.py`. Verified passing (both fixtures).
- [x] 3.4 Added `test_remove_outliers_smoke.py`; cylinder case marked
      `live_smoke_slow`. Verified passing (both fixtures).
- [x] 3.5 Added `test_pca_analysis_smoke.py`. Verified passing (both fixtures).
- [x] 3.6 Added `test_clustering_smoke.py` (kmeans/hierarchical/gmm); gmm-on-cylinder
      marked `live_smoke_slow`, assertion only checks `"converged" in result` (not
      truthy) per the honesty note above. Verified passing (all 6 combinations).
- [x] 3.7 Added one smoke test file per plotting tool:
  - [x] 3.7a `test_plot_trait_histograms_smoke.py` -- passing.
  - [x] 3.7b `test_plot_trait_boxplots_smoke.py` -- passing.
  - [x] 3.7c `test_plot_correlation_matrix_smoke.py` -- cylinder marked
        `live_smoke_slow`; passing.
  - [x] 3.7d `test_plot_heritability_bar_smoke.py` -- both fixtures marked
        `live_smoke_slow`. **Cylinder case initially FAILED**: found a real,
        previously-latent bug (`create_heritability_plot` returns `list[Figure]` once
        trait count exceeds its internal pagination threshold of 50 -- turface_19's
        ~18 traits never crossed it, cylinder's 846 does -- and `save_plot`'s
        unconditional `fig.savefig()` crashed on the list). Fixed via a new
        `save_plot_or_plots` helper in `_viz_shared.py`. Now passing.
  - [x] 3.7e `test_plot_variance_decomposition_smoke.py` -- both fixtures marked
        `live_smoke_slow`; passing (this tool's delegate has no pagination path).
- [x] 3.8 Confirmed: all smoke tests call through the real MCP transport, and every
      client/fixture construction happens inside `conftest.py` fixtures or test
      bodies, never at module scope.

## 4. CI wiring for the live-smoke tiers

- [x] 4.1 Added a `dev-stack-smoke` step running
      `pytest tests/smoke/ -m "live_smoke and not live_smoke_slow"`.
- [x] 4.2 Added a `/pre-merge` step (4c) for the full `live_smoke` set, plus the
      drive-by fix adding `make bloommcp-plot-smoke` to the existing Step 4b.
- [x] 4.3 Added `tests/unit/test_bloommcp_smoke_marker_split.py`:
      `test_dev_stack_smoke_excludes_live_smoke_slow` (parallel to
      `test_bloommcp_live_smoke_gate.py`).
- [x] 4.4 Same file: `test_every_live_smoke_slow_test_file_also_declares_live_smoke`,
      a static regex check over `bloommcp/tests/smoke/*.py` (careful about
      `"live_smoke"` being a literal substring of `"live_smoke_slow"` -- verified the
      regex correctly discriminates the two on both a synthetic bad case and good
      case).

## 5. Validation

- [x] 5.1 `openspec validate add-bloommcp-cylinder-smoke-coverage --strict` -- valid.
- [x] 5.2 Full `bloommcp` unit suite (`-m "not integration and not live_smoke"`):
      535 passed.
- [x] 5.3 `cd bloommcp && uv run --extra test pytest tests/ -m integration`: 4 passed
      (includes the new cylinder PCA oracle test).
- [x] 5.4 `pytest tests/smoke/ -m live_smoke` end-to-end against the live dev stack:
      all 24 passed (turface_19 + cylinder, ~4 minutes total) -- this run is what
      surfaced and confirmed the fix for the `plot_heritability_bar` pagination bug
      (3.7d). Repo-root `tests/unit/`: 339 passed (includes both new regression
      guards + the existing gate test, all green together).
