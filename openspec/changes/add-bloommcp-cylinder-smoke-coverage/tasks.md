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
  - [x] 3.7a `test_plot_trait_histograms_smoke.py` -- cylinder marked
        `live_smoke_slow` **(added post-PR-review, see below)**; passing.
  - [x] 3.7b `test_plot_trait_boxplots_smoke.py` -- cylinder marked `live_smoke_slow`
        **(added post-PR-review, see below)**; passing.
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
- [x] 5.5 **Post-PR-review fix**: PR #507's first real `dev-stack-smoke` CI run
      (not reproducible locally against `python-audit` -- it needs the actual live
      stack) failed: `test_plot_trait_boxplots_smoke[cylinder]` exceeded the 120s
      client timeout in `conftest.py`. Investigated: neither
      `create_trait_histograms` nor `create_trait_boxplots_by_genotype` paginates
      (unlike `create_heritability_plot`), so both render all 846 cylinder traits
      into one figure -- genuinely slow and, per repeated local timing (histograms
      46-86s, boxplots 109s locally vs. >120s in CI), too variable to be "bounded
      time" the way design.md originally assumed for rendering-only tools. Fixed by
      marking both cylinder cases `live_smoke_slow` (matching the reviewer's
      preferred fix over raising the timeout) and updating design.md/spec.md's
      enumeration accordingly. Re-verified: CI-safe subset now 15 tests (was 17),
      passes in ~29s; both updated test files pass in full (all 4 parametrizations);
      both regression guards + the existing gate test still pass.
- [x] 5.6 **Second round of PR review fixes**: a reviewer correctly pushed back that
      5.5's `live_smoke_slow` marking alone "hides the hang from CI, it doesn't fix the
      tool for a real user calling it on a wide dataset." Investigated:
      `sleap_roots_analyze.visualization` already ships
      `create_trait_histograms_batched` / `create_trait_boxplots_by_genotype_batched`
      counterparts (unused until now). Both `plot_trait_histograms.py` and
      `plot_trait_boxplots.py` now route through the batched delegate + the existing
      `save_plot_or_plots` helper once trait count exceeds a new
      `TRAIT_BATCH_THRESHOLD` (50, matching `create_heritability_plot`'s own default)
      -- fixing the same class of bug as 3.7d for real users, not just hiding it from
      CI. Confirmed empirically that batching does NOT meaningfully reduce wall-clock
      time (still ~63-111s locally for cylinder), so the `live_smoke_slow` marking from
      5.5 stays correct on its own "genuinely slow at this scale" merits. Also, per the
      same review: added 2 fast unit tests directly exercising `save_plot_or_plots`
      (the exact code path that crashed) and 2 more confirming the batched-delegate
      routing decision with a synthetic wide fixture (no live stack needed -- this
      closes the gap that the pagination code was previously only exercised by
      `live_smoke_slow`-tier tests); strengthened all 5 plot-tool smoke tests from a
      bare `"Plot saved:" in text` substring check to a real file-existence assertion
      (`conftest.py`'s new `assert_plot_success`, which parses every URL out of a
      single- or multi-page success summary and checks the corresponding file exists
      with nonzero size on the host-side bind-mounted `PLOTS_DIR`); and corrected
      `tests/fixtures/README.md`'s cylinder metadata-column claim (`plant_name` /
      `species_name` are silently dropped by upstream dtype filtering before reaching
      bloommcp's role-matching, unlike `scan_id` / `plant_id` / `wave_number`, which are
      recognized and reported back via `excluded_from_traits` even though also not
      persisted). Two reviewer claims were investigated and found factually incorrect
      (not fixed, pushed back on with evidence): `conftest.py`'s "gitignored" claim
      (`bloommcp/data/` — covering both `SLEAP_OUT_CSV` and `PLOTS_DIR` — is genuinely
      in `.gitignore`, confirmed via `git check-ignore -v`), and the claim that a
      server-side tool error surfaces as an opaque `KeyError` (`as_mcp_tool` always
      raises `BloomMCPError` → a real `fastmcp.exceptions.ToolError`, confirmed by
      reading `contract/wrap.py` — it never returns an error disguised as a
      successful dict). Re-verified: 539 fast tests pass (was 535), all 24 live_smoke
      tests pass end-to-end against the live stack, `openspec validate --strict` clean.
- [x] 5.7 **Round 3 (re-review of commits 6e02f69 + 3b8a21f)**: reviewer confirmed the
      pagination fix and stronger assertions were correct, retracted the prior round's
      `.gitignore` finding, but flagged two real blockers and one causal-attribution
      error:
  - [x] **Merge conflict with `staging`** — `staging` had independently renamed
        `bloommcp/data/SLEAP_OUT_CSV` → `TRAITS_DIR` (issue #477, landed after this
        branch diverged). Merged `origin/staging`; git auto-resolved the 3 files with
        actual line overlap, but two things needed a manual follow-up fix since they
        were new-on-this-branch content with no conflicting lines for git to flag:
        `tests/smoke/conftest.py`'s `TRAITS_DIR` constant (still hardcoded
        `SLEAP_OUT_CSV`) and `tests/unit/test_bloommcp_data_mount_rename.py`'s
        `RENAMED_FILES` list (still pointed at the pre-relocation
        `bloommcp/scripts/live_plot_tool_smoke.py` path). Re-verified against the live
        stack after recreating the `bloommcp` container to pick up the renamed
        bind-mount.
  - [x] **CI had not actually re-run** on either fix commit — confirmed via
        `gh api .../actions/runs?branch=...`: the only `pr-checks.yml` run on this
        branch was still the original failing one on `d3a532c`; the checks
        `gh pr checks` showed passing were an unrelated CodeQL/default-setup scan
        (`event: dynamic`, not `pull_request`), not `pr-checks.yml`. Root cause
        undetermined; the merge commit above produces a fresh HEAD/push regardless,
        which triggers a new `synchronize` run.
  - [x] **README causal-attribution fix**: the prior round's wording blamed
        `plant_name`/`species_name`'s invisibility in `excluded_from_traits` on
        upstream `get_trait_columns`'s "dtype filtering" — reviewer correctly traced
        this to bloommcp's own `resolve_columns()` (`data_access/columns.py`), whose
        `excluded_cols` computation only includes a non-trait column if it is
        numeric-dtype or explicitly deny-listed; `get_trait_columns` itself excludes
        both columns via substring-pattern matching (`"species_"`, `"plant_name"`),
        not a dtype check. Verified by reading both functions' source directly, then
        rewrote the README paragraph to attribute each mechanism to the correct layer.
  - [x] Also from this round: clarified `TRAIT_BATCH_THRESHOLD`'s docstring (it only
        decides whether to batch; actual page size is each `*_batched` delegate's own
        independent `batch_size=16`, so cylinder's 846 traits make 53 pages, not "50
        traits/page"); added a fast unit test asserting `TRAIT_BATCH_THRESHOLD` against
        `create_heritability_plot`'s live `traits_per_page` default (guards silent
        desync on a future `sleap-roots-analyze` bump, since the pin is open-ended
        `>=`); tightened the two batching-decision tests to assert the full expected
        page count (was: only page 1/2 of 4); added an explicit boundary test at
        exactly 50 vs. 51 traits (50 must NOT batch, matching
        `create_heritability_plot`'s own `<=`/`>` semantics, confirmed by reading its
        source).
  - Re-verified: `openspec validate --strict` clean; bloommcp fast suite passes;
    `test_bloommcp_data_mount_rename.py` passes; `test_viz_tools.py` (27 tests, up
    from 24) passes; CI-safe smoke subset (15/15) passes against the recreated,
    renamed-bind-mount dev stack.
