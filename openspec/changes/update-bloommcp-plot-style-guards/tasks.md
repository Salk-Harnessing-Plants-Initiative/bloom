Commit granularity: one commit per numbered section below (tests + wiring together, mirroring
how #661 landed), not a separate red-only commit — see the review's git-workflow findings.

## 1. UMAP `plot_font_size`/`plot_point_size` ceilings — tests first (red), then wiring (green)

- [x] 1.1 In `test_umap_analysis_tool.py`, extend the existing
      `test_out_of_range_plot_style_field_is_invalid_input_regardless_of_include_plots`
      parametrize list (currently `("plot_point_size", 0), ("plot_point_size", -1)`, plus its
      `plot_alpha` cases) with `("plot_point_size", 10001)`, `("plot_font_size", 101)`, and
      `("plot_font_size", float("inf"))` — asserting `invalid_input`, raised regardless of
      `include_plots` (the existing test already parametrizes `include_plots` over
      `[True, False]`; confirm the new cases are covered by that same parametrization, not
      just one value). Confirm red (neither field has an upper bound yet).
- [x] 1.2 Add `test_plot_font_size_at_ceiling_is_accepted` and
      `test_plot_point_size_at_ceiling_is_accepted`, following the _end-to-end_
      `test_plot_alpha_boundary_values_accepted` pattern (not the Pydantic-construction-only
      `test_plot_font_size_just_above_zero_is_accepted` pattern, which doesn't confirm the
      value actually reaches the plotter): spy-and-delegate on
      `sleap_roots_analyze.create_umap_single_trait`, run with `plot_font_size=100` (resp.
      `plot_point_size=10000`) and `include_plots=True`,
      `plots=["create_umap_single_trait"]`, assert the call succeeds and the captured kwargs
      show the boundary value reached the plotter unchanged.
- [x] 1.3 Add `le=100` to `UMAPAnalysisParams.plot_font_size` and `le=10000` to
      `plot_point_size` (`umap_analysis.py:177-198`), updating both field descriptions to
      state the new ceiling and its rationale (mirroring `n_components`'s
      `_MAX_N_COMPONENTS` description style).
- [x] 1.4 Run the section-1 tests; confirm 1.1's rejection cases and 1.2's boundary-accepted
      cases are all green.

## 2. PCA `plot_font_size` ceiling — tests first (red), then wiring (green)

- [x] 2.1 In `test_pca_analysis_tool.py`, add
      `test_plot_font_size_above_ceiling_is_invalid_input_regardless_of_include_plots`,
      matching UMAP's 1.1 structure exactly (not the narrower existing
      `test_plot_font_size_non_positive_is_invalid_input` shape): parametrize over
      `include_plots in [True, False]` and over `plot_font_size in [101, float("inf")]`,
      asserting `invalid_input` in all four combinations. Confirm red.
- [x] 2.2 Add `test_plot_font_size_at_ceiling_is_accepted`, following the _end-to-end_
      `test_plot_alpha_boundary_values_accepted` pattern (spy-and-delegate on
      `sleap_roots_analyze.create_pca_biplot`, `include_plots=True`,
      `plots=["create_pca_biplot"]`, `plot_font_size=100`), asserting success and that the
      figure's font size was actually set to `100` (reuse
      `test_plot_font_family_and_size_forwarded_and_applied`'s assertion style, ~line 851, at
      the boundary value specifically) — not just that `PCAAnalysisParams` construction
      doesn't raise.
- [x] 2.3 Add `le=100` to `PCAAnalysisParams.plot_font_size` (`pca_analysis.py:147-153`),
      matching the field-description update from 1.3.
- [x] 2.4 Run the section-2 tests; confirm green.

## 3. UMAP `plot_cmap` allowlist — tests first (red), then wiring (green)

- [x] 3.1 In `test_umap_analysis_tool.py`, write failing tests for the new tool-body check:
  - [x] 3.1.1 `plot_cmap="virdis"` (misspelling), parametrized over
        `include_plots in [True, False]` → `invalid_input` naming `"virdis"` in the message
        in both cases; patch `perform_umap_analysis` with a spy and assert it is never called
        in either case (this is the "regardless of include_plots" scenario — cover both
        values here, not only in a separate `include_plots=False` case)
  - [x] 3.1.2 `plot_cmap="hsv"` and `plot_cmap="tab10"` (valid matplotlib names, excluded by
        the allowlist) → `invalid_input`, same non-invocation assertion (default
        `include_plots`, i.e. `False`, is sufficient here — 3.1.1 already covers the
        `include_plots=True` case for the rejection path generally)
  - [x] 3.1.3 `plot_cmap="viridis"` and `plot_cmap="RdBu"` (allowed sequential/diverging
        names) → accepted; extend the `test_plot_alpha_boundary_values_accepted` pattern
        (spy-and-delegate on `sleap_roots_analyze.create_umap_single_trait`) to assert
        `cmap="viridis"` / `cmap="RdBu"` reaches the plotter unchanged
  - [x] 3.1.4 `plot_cmap=None` (default) → unchanged behavior, no check performed (construct
        `UMAPAnalysisParams` with no `plot_cmap` and confirm no `invalid_input` path is taken
        purely from omitting it)
- [x] 3.2 Confirm 3.1.1/3.1.2 are red (today `plot_cmap` has no validation — these currently
      either succeed or fail late with a raw matplotlib `ValueError`, never `invalid_input`).
- [x] 3.3 Add a module-level `_ALLOWED_CMAPS: frozenset[str]` to `umap_analysis.py` near
      `_MAX_N_COMPONENTS`, hand-authored from matplotlib's documented Perceptually Uniform
      Sequential + Sequential + Sequential (2) + Diverging colormap names, including each
      base name's `_r` reversed variant. Document the list's provenance and drift risk in a
      comment (design.md Decision 3).
- [x] 3.3.1 Add a cheap regression test asserting every name in `_ALLOWED_CMAPS` is still a
      real matplotlib colormap in the pinned/installed version
      (`_ALLOWED_CMAPS <= set(plt.colormaps())`) — catches a future matplotlib release
      renaming or removing one of these names, which nothing else in this test plan would
      notice.
- [x] 3.4 In `umap_analysis`'s tool body, add a check alongside the existing
      `n_neighbors >= n_samples` check (`umap_analysis.py:367-381`, before the
      `perform_umap_analysis` call at ~line 392): if `params.plot_cmap is not None and
params.plot_cmap not in _ALLOWED_CMAPS`, raise `BloomMCPError(code="invalid_input",
...)` naming the invalid value — **not** a Pydantic `@field_validator`, per design.md
      Decision 3 (`qc_clean.py:204-212` precedent: a validator's `ValueError` loses its
      message text at the contract layer).
- [x] 3.5 Replace the now-false sentence in `plot_cmap`'s field description — "Not validated
      against matplotlib's colormap registry — an unknown name raises ValueError from
      matplotlib itself at figure-generation time" becomes untrue once 3.4 ships — with
      wording describing the allowlist restriction, e.g.: "Restricted to matplotlib's
      documented sequential and diverging colormap names (plus each name's `_r` reversed
      variant); an unrecognized or excluded name (e.g. hsv, tab10) is rejected as
      invalid_input naming the value, before any computation runs." Do not leave the old
      sentence in place alongside the new one — the two directly contradict each other.
- [x] 3.6 Run the section-3 tests; confirm all green.

## 4. Figure cleanup survives an allocate-then-raise plotter call — tests first (red), then wiring (green)

- [x] 4.0 Verified `test_umap_analysis_tool.py` already has an equivalent of PCA's
      `test_matplotlib_not_imported_on_default_path` — it's
      `test_default_path_never_executes_an_import_matplotlib_statement` (~line 731), just
      under a different name. No new test needed here; both are re-run in 4.4 to confirm
      the Tier-0 guarantee still holds after 4.3.
- [x] 4.1 In `test_plots_helpers.py`, add a stand-in that — unlike the existing `_boom`
      (~line 106-107) — actually calls `plt.figure()` and then raises, and assert
      `plt.get_fignums() == []` after `generate_figures`/`close_figures` run; mirror
      `test_generate_figures_partial_failure_then_close_leaves_no_open_figures` (~line 96)
      but exercise the allocate-then-raise path specifically. Confirm red (today's
      `figures[key] = fn()` never assigns on this path, so `close_figures` can't reach it).
- [x] 4.2 In `test_umap_analysis_tool.py` and `test_pca_analysis_tool.py`, add an
      allocate-then-raise variant of each file's own
      `test_figure_cleanup_get_fignums_empty_on_partial_plotter_failure` (UMAP ~line 1017,
      PCA ~line 802): the `_boom`-equivalent stand-in calls `plt.figure()` (or
      `plt.subplots()`) before raising `RuntimeError`. Confirm red.
- [x] 4.3 In `bloom_mcp/tools/_plots.py`, change `generate_figures` to snapshot
      `plt.get_fignums()` before each `fn()` call; on an exception from `fn()`, close every
      figure number present in the post-call snapshot but not the pre-call one, then
      re-raise. Import `matplotlib.pyplot` as a local import inside `generate_figures`'s own
      body (mirroring `close_figures`'s existing lazy-import pattern) — not a module-level
      import — so the Tier-0 "no matplotlib import on the default no-plots path" guarantee
      (verified by 4.0's new test and the existing PCA one) is preserved: `generate_figures`
      is only ever reached from the `include_plots=True` path already, but a module-level
      import would still execute at import time regardless of that guard.
- [x] 4.4 Run the section-4 tests; confirm all green.

## 5. Validation

- [x] 5.1 `openspec validate update-bloommcp-plot-style-guards --strict` passes
- [x] 5.2 Full `bloommcp` test suite passes: `cd bloommcp && uv run --frozen --extra test
pytest tests/ -m "not integration and not live_smoke" -v --tb=short` (the exact
      invocation `pr-checks.yml`'s `python-audit` job runs) — 1424 passed, 33 deselected.
- [x] 5.3 Lint/format clean on every changed file, via the repo's actual pre-commit hooks
      (`uvx pre-commit run black|ruff|ruff-format --files <file>`, since `ruff`/`black`
      aren't in bloommcp's own `uv` environment — they run through pre-commit's isolated
      tool envs per `.pre-commit-config.yaml`): black reformatted one line in
      `test_plots_helpers.py`; ruff/ruff-format clean on every file from the start.

## 6. Post-review fixes (PR #726 review)

- [x] 6.1 **Blocking**: dropped `berlin`/`managua`/`vanimo` from `_ALLOWED_CMAP_BASE_NAMES` —
      confirmed (matplotlib's own 3.10.0 release notes) these were added in matplotlib
      3.10.0, but `bloommcp/pyproject.toml` pins `matplotlib>=3.7.0` with no upper bound, so
      an install resolving below 3.10 would pass the allowlist check for these three names
      and then hit the exact opaque render-time error this change exists to eliminate. Added
      a comment tying the allowlist's provenance to the declared dependency floor so a future
      `pyproject.toml` bump (in either direction) doesn't silently desync again.
- [x] 6.2 Fixed `plot_cmap`/`plot_point_size` (UMAP) and `plot_font_size` (UMAP + PCA)
      field descriptions: each previously ended with "Ignored (not rejected) when
      include*plots=False," which is only true for a \_valid* value (nothing to render) —
      an _out-of-range/invalid_ value is rejected regardless of `include_plots`, and for
      `plot_cmap` specifically this read as directly self-contradicting the sentence just
      before it describing that same rejection. Reworded all three to state both halves
      unambiguously.
- [x] 6.3 Hoisted the `100`/`10000` ceilings into named module-level constants
      (`_MAX_PLOT_FONT_SIZE` in both files, `_MAX_PLOT_POINT_SIZE` in `umap_analysis.py`),
      matching the existing `_MAX_N_COMPONENTS` precedent — previously inline literals
      duplicated across each `Field(...)` call and its own prose description.
- [x] 6.4 Added missing test-coverage parity: `plot_point_size` now has an `inf` rejection
      case alongside `plot_font_size`'s; the `hsv`/`tab10` excluded-cmap rejection test is
      now parametrized over `include_plots` like the misspelling case already was.
- [x] 6.5 Documented GitHub issue #721's findings 6-8 (posted as a follow-up comment the day
      before this change's implementation commits, after the proposal was already scaffolded
      and approved) as explicit Non-Goals in `design.md`, with the reasoning for leaving each
      out of this change's scope rather than silently ignoring them.
