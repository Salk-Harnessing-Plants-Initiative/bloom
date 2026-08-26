> **TDD note:** write the RED contract tests for each tool before converting it, confirm they
> fail, then convert and confirm GREEN. Commit each tool's conversion + its new tests + the
> removal of that tool's now-incompatible old tests **in one atomic commit** — never leave
> `test_viz_tools.py`'s old standalone tests for an already-converted tool in the tree past that
> tool's own commit (they call the old flat `filename=`/`traits=` signature and will hard-fail
> the moment the tool's params become a Pydantic model). Never land a registration/registry
> change ahead of the tool it depends on.

## 1. Pre-work — confirm shared helpers cover the new shape

- [x] 1.1 Confirm `_qc_shared._validate_experiment_name` / `_validate_trait_subset` /
      `_role_kwargs` need no changes to serve these 3 tools (they already serve `qc_clean`/
      `qc_inspect` identically shaped raw-frame reads). No new shared helper expected.
- [x] 1.2 Confirm `ResultStore.commit` handles a multi-PNG output set correctly for a batched
      render (mirrors `pca_analysis`'s `include_plots` figure-dict handling) — no ResultStore
      change expected, just confirm the existing contract covers it.
- [x] 1.3 Confirm `_ports.raw_source_for` / `frame.resolved_source` are usable from these 3
      tools exactly as `qc_inspect` uses them, for the `source_csv`/`source` provenance wiring
      each conversion below must include (design.md's "stamp source_csv/source" decision).

## 2. `plot_correlation_matrix` (simplest — no batching, no genotype requirement)

- [x] 2.1 RED: add `tests/tools/test_plot_correlation_matrix_tool.py` wired with `FakeReader`/
      `FakeResultStore` via `_ports.configure` (mirrors `test_qc_inspect_tool.py`'s fixture).
      Write the delegation + numeric oracle test first (pin one off-diagonal correlation cell +
      the reported high-correlation counts against an independent `df.corr()` computation, as
      today's test already does) — confirm it fails (no contract-wrapped tool yet).
- [x] 2.2 RED: schema round-trip (missing `experiment` → `invalid_input`; explicit empty
      `trait_columns=[]` → `invalid_input`; zero-numeric-trait-column frame → `invalid_input`),
      path-safety (traversal/absolute filename rejected via `_validate_experiment_name` before
      any read — the delegate is never called), provenance (`seed=None`, tool name stamped,
      `based_on_version`/`source` recorded and `input_sha256` content-addresses the read frame —
      mirrors `test_qc_inspect_tool.py`'s provenance+links test), links
      (`run_ref`/`version_dir`/`manifest_path`/`outputs`/`output_links` populated, the PNG is
      never inlined), error envelope (an unexpected delegate exception → structured
      `internal_error`, no raw traceback/path leaked), figure-handle-leak
      (`plt.get_fignums()` returns to its pre-call baseline on both success and failure),
      staging-cleanup-on-failure (a `ResultStore.commit` failure leaves no discoverable run and
      no leaked staging dir — mirrors `qc_inspect`'s `try/except: rmtree(...); raise`), and
      `tools/list` presence. Confirm RED.
- [x] 2.3 GREEN: convert `plot_correlation_matrix.py` — pin `matplotlib.use("Agg")` inline
      before importing any `sleap_roots_analyze` delegate (design.md's matplotlib Decision);
      `PlotCorrelationMatrixParams` (`experiment: str`, `trait_columns: Optional[list[str]] =
      None`, `user_label: Optional[str] = None`) / `PlotCorrelationMatrixResult(RunLinks)`
      (`experiment`, `source`, `n_traits`, `strong_positive_correlations: int`,
      `strong_negative_correlations: int`); `@as_mcp_tool(errors=(ExperimentReadError,))`; read
      via `reader.load_experiment(experiment, version="raw")`; persist under new tool class
      `"correlation_matrix"` with `source_csv=_ports.raw_source_for(experiment)` and
      `source=frame.resolved_source` on `create_run`; wrap render+commit in
      `try/except: rmtree(run.staging_dir, ignore_errors=True); raise`. Preserve the existing
      hand-rolled strong-correlation-count computation unchanged (not delegated — see design.md).
      In the same commit: remove `plot_correlation_matrix` from `test_viz_tools.py`'s
      `_TOOLS`/`_TOOL_IDS` parametrized lists and delete its now-incompatible standalone test
      (`test_plot_correlation_matrix_pins_one_off_diagonal_cell`).

## 3. `plot_trait_histograms` (adds batching/pagination)

- [x] 3.1 RED: add `tests/tools/test_plot_trait_histograms_tool.py`. Delegation pinning
      (`create_trait_histograms` called exactly once below `TRAIT_BATCH_THRESHOLD`,
      `create_trait_histograms_batched` above it — reuse the existing synthetic wide-experiment
      fixture recipe), the boundary case (`== TRAIT_BATCH_THRESHOLD` unbatched, `+1` batched).
      Confirm RED.
- [x] 3.2 RED: a batched render persists one output key + one `OutputLink` per page (assert
      `len(result.outputs) == expected_pages` and every page's bytes are non-empty via the fake
      store, and `result.n_pages`/`result.batched` reflect it) — confirm RED. Plus the same
      schema (including empty-`trait_columns`/zero-trait rejection)/path-safety/provenance
      (incl. `source_csv`/`source`)/links/error-envelope/figure-leak/staging-cleanup-on-failure/
      `tools/list` coverage as 2.2.
- [x] 3.3 GREEN: convert `plot_trait_histograms.py` — pin `matplotlib.use("Agg")` inline before
      importing any `sleap_roots_analyze` delegate; `PlotTraitHistogramsParams` (`experiment`,
      `trait_columns`, `user_label`) / `PlotTraitHistogramsResult(RunLinks)` (`experiment`,
      `source`, `n_traits_plotted`, `batched: bool`, `n_pages: int`); persist under new tool
      class `"trait_histograms"` with `source_csv`/`source` wired as in 2.3; one committed
      output per page when batched; same staging-cleanup-on-failure wrapping. In the same
      commit: remove `plot_trait_histograms` from `test_viz_tools.py`'s `_TOOLS`/`_TOOL_IDS`
      lists and delete its now-incompatible standalone tests
      (`test_plot_trait_histograms_delegates_and_saves_png`,
      `test_plot_trait_histograms_uses_batched_delegate_above_threshold`,
      `test_plot_trait_histograms_batching_boundary`).

## 4. `plot_trait_boxplots` (adds the genotype-required guard)

- [x] 4.1 RED: add `tests/tools/test_plot_trait_boxplots_tool.py` — same batching +
      contract-pattern coverage as `plot_trait_histograms`'s suite (3.1–3.2), plus: a `FakeReader`
      frame with no detectable genotype column raises `BloomMCPError(code="assumption_violated")`
      naming the experiment, and the delegate is never called / no run is persisted. Confirm RED.
- [x] 4.2 GREEN: convert `plot_trait_boxplots.py` — pin `matplotlib.use("Agg")` inline before
      importing any `sleap_roots_analyze` delegate; `PlotTraitBoxplotsParams` /
      `PlotTraitBoxplotsResult(RunLinks)` (adds `genotype_column: str` to the histogram shape);
      persist under new tool class `"trait_boxplots"` with `source_csv`/`source` wired as in
      2.3; same staging-cleanup-on-failure wrapping. In the same commit: remove
      `plot_trait_boxplots` from `test_viz_tools.py`'s `_TOOLS`/`_TOOL_IDS` lists and delete its
      now-incompatible standalone tests (`test_plot_trait_boxplots_delegates_and_saves_png`,
      `test_plot_trait_boxplots_uses_batched_delegate_above_threshold`).

## 5. Update `tests/smoke/` for the 3 converted tools

- [x] 5.1 Rewrite `test_plot_trait_histograms_smoke.py`, `test_plot_trait_boxplots_smoke.py`,
      `test_plot_correlation_matrix_smoke.py` from the `seeded_experiment`/`call_plot_tool`/
      `assert_plot_success` harness to the `db_experiment_id`/`call_tool` harness
      `test_qc_inspect_smoke.py` already uses; assert on the structured result
      (`result["run_ref"]`, `result["manifest_path"]`, `result["outputs"]`, etc.), not a success
      string.
- [x] 5.2 Update `conftest.py`'s `FIXTURE_FILES`/`EXPERIMENT_ID_ENV_VARS` doc comments (lines
      ~46-50, ~56-62) to reflect the new split: 2 plot tools on `seeded_experiment` (filename),
      10 granular tools (the existing 7 + these 3) on `db_experiment_id` (numeric id).
- [x] 5.3 Retarget `live_plot_tool_smoke.py` from `sleap_roots_plot_trait_histograms` to
      `sleap_roots_plot_heritability_bar` (or `plot_variance_decomposition`) — design.md's
      "retarget live_plot_tool_smoke.py" decision — since it exists specifically to prove a PNG
      lands on the real bind-mounted `PLOTS_DIR`, which no longer applies to
      `plot_trait_histograms` once converted. Update its docstring/comments accordingly (it
      currently states `plot_trait_histograms` is unaffected by bloom#551's DB-only rewrite —
      that statement becomes false for it and must move to describe whichever tool it now
      targets).

## 6. Registry updates — make the new runs discoverable

- [x] 6.1 Add `"trait_histograms"`, `"trait_boxplots"`, `"correlation_matrix"` to
      `manifest.CANONICAL_TOOL_CLASSES` (`manifest/__init__.py`) and
      `list_existing_analyses.TOOL_CLASSES`, alongside the untouched, still-unclaimed `"viz"`
      entry. (Order-independent relative to sections 2-4: these constants are not imported by
      the tool modules themselves, only by the discovery-listing code.)
- [x] 6.2 Add a direct membership assertion — mirrors
      `test_outliers_class_registered_in_discovery_and_canonical_registries` in
      `test_remove_outliers_tool.py` — that all 3 new tool-class strings appear in both
      `CANONICAL_TOOL_CLASSES` and `list_existing_analyses.TOOL_CLASSES`, so a typo in either
      tuple fails directly rather than only indirectly.
- [x] 6.3 RED→GREEN: extend `tests/tools/test_list_existing_analyses_*` coverage confirming a
      committed run under each new class surfaces in `list_existing_analyses`'s response for
      that experiment (end-to-end, not just the membership check in 6.2).

## 7. Full-suite verification

- [x] 7.1 `uv run pytest` (bloommcp) green, including the new + modified test files. Confirm no
      standalone test in `test_viz_tools.py` still calls any of the 3 converted tools with the
      old `filename=`/`traits=` signature.
- [x] 7.2 `uv run ruff check` / `uv run black --check` clean on every touched file.
- [x] 7.3 `openspec validate converge-bloommcp-viz-tools --strict` passes.

## 8. PR review fixes

Branch was rebased onto `origin/staging` (this repo's actual active integration branch for
bloommcp — confirmed by 14/15 recent merged PRs — not `origin/main`, which the branch was
originally, incorrectly created from). PR review then found the 3 new tools didn't match
`staging`'s current sibling shape, plus several test/behavior gaps:

- [x] 8.1 **Blocking**: declare `errors=(ExperimentReadError, CommitFailedError,
      ManifestReadError)` on all 3 tools, matching every sibling's post-#640 shape (staging had
      landed #660's fix while this branch was based on stale `main`). Add
      `test_commit_failure_surfaces_as_tool_error` / `test_manifest_read_failure_surfaces_as_
      tool_error` per tool file, mirroring `qc_inspect`'s regression tests.
- [x] 8.2 Register all 3 new tool classes in `list_existing_analyses._TOOL_CLASS_TO_PUBLIC_NAME`
      (bloom#671, landed on staging while this branch was based on stale `main`) — without this,
      `test_every_non_legacy_tool_class_has_a_public_name_mapping` fails and a `list_runs` failure
      for one of these 3 leaks its raw tool_class string.
- [x] 8.3 Extract the 3 tools' duplicated `_resolve_trait_cols` into one shared
      `_viz_shared.resolve_trait_columns`, additionally rejecting duplicate trait names (silent
      miscounting risk for `plot_correlation_matrix`'s permanently-stored strong-correlation
      counts). Add `test_duplicate_trait_columns_is_invalid_input` per tool file.
- [x] 8.4 `plot_correlation_matrix` reports `zero_variance_traits` (constant/all-NaN selected
      traits, whose Pearson correlation is `NaN` and so silently doesn't count toward either
      strong-correlation field). Add `test_zero_variance_trait_excluded_from_counts_and_reported`.
- [x] 8.5 Disclose raw/uncleaned data explicitly in each tool's function docstring (mirrors
      `qc_inspect`'s "Inspect raw experiment missingness" framing) — previously only discoverable
      post-hoc via `result.source == "raw"`.
- [x] 8.6 Fix the tautological `assert stored.input_validation is None or True` in
      `test_plot_correlation_matrix_tool.py` (the `or True` made it always pass regardless of the
      left side) to a real assertion.
- [x] 8.7 Close `plot_trait_boxplots`'s test-parity gap with `plot_trait_histograms`: add the
      exact batching-boundary test, the non-numeric-trait-column test, and the
      reads-raw-despite-cleaned-version test (tasks.md §4.1 had checked off parity that wasn't
      actually written).
- [x] 8.8 Align the path-traversal payload list across all 3 test files to the same 8 cases
      (histograms/boxplots were missing the `sub\dir\x.csv` variant `correlation_matrix` had).
- [x] 8.9 Strengthen `test_committed_runs_from_all_3_tools_are_discoverable` with an interleaved
      cross-tool-class test proving independent per-class version-lineage progression, not just
      that each starts at v1.
- [x] 8.10 Add a staging-dir-removed assertion to each tool's `test_commit_failure_cleans_
      staging_and_commits_nothing` (previously only the render-failure test checked this).
- [x] 8.11 design.md: document the `errors=` fix, the `_TOOL_CLASS_TO_PUBLIC_NAME` addition, the
      `resolve_trait_columns` extraction + duplicate rejection, `zero_variance_traits`, and an
      explicit deferred note that `source_id`/`run_id` pinning (bloom#626, landed on staging
      mid-flight) is intentionally not threaded through these 3 tools.
- [x] 8.12 Re-run the full suite + ruff/black + `openspec validate --strict` against the
      rebased-onto-staging tree.

Not fixed inline, tracked instead as
[#725](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/725): a
delegate-internal figure leak if `create_trait_histograms_batched`/
`create_trait_boxplots_by_genotype_batched` raise partway through creating a batch's figures —
this tool's own `finally: for fig in figures: plt.close(fig)` cannot reach figures the delegate
created and abandoned before raising, since `figures` is never assigned until the (all-or-nothing)
delegate call returns. Pre-existing and delegate-internal — but, per a second review pass (§9),
**not** equivalent to `pca_analysis`'s situation: `pca_analysis` is actually safer here via
`tools/_plots.py::generate_figures`'s incremental population, which these 3 tools can't reuse
because the vendored batch delegates expose no per-page hook (see design.md's corrected Risk).

## 9. Second PR review pass — description accuracy + scientific-rigor gaps

- [x] 9.1 **Blocking (description accuracy)**: re-verified every numeric/process claim in the PR
      description against ground truth rather than repeating unverified figures. Corrected:
      `test_viz_tool_classes_discovery.py` has 3 test functions, not 14 (the "14" conflated a
      cross-file total with a single file); the 3 tools' conversion, their new tests, and the
      `test_viz_tools.py` trim landed in one combined commit (`git log`), not tool-by-tool atomic
      commits as the description implied — corrected the PR description to describe what actually
      happened (a per-tool RED→GREEN *development* cycle, landed as one commit) rather than
      overstating commit-level atomicity tasks.md's own TDD note calls for but this iteration
      didn't follow to the letter.
- [x] 9.2 **Important**: `plot_correlation_matrix` now rejects a resolved selection of fewer than
      2 trait columns as `invalid_input` before any run is persisted (was: silently committed a
      degenerate 1×1 masked heatmap as a normal artifact). Lives in the tool itself, not the
      shared `resolve_trait_columns` (a 1-trait selection is valid for the other 2 tools).
- [x] 9.3 **Important**: `plot_correlation_matrix.corr()` now takes
      `min_periods=_CANONICAL_MIN_SAMPLES_PER_TRAIT` and the result reports
      `low_overlap_trait_pairs` — closes the spurious-±1.0-from-a-near-empty-overlap gap (the
      same silent-mislead class already fixed for duplicate/zero-variance traits, via disjoint
      missingness instead). Test seeds two traits overlapping in exactly 2 non-null rows.
- [x] 9.4 **Important**: corrected the matplotlib-figure-leak disclosure's "not unique to these 3
      tools' wrapper" framing (design.md Risks + this file, above) and filed
      [#725](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/725) to track the
      gap itself, separately from this PR.
- [x] 9.5 **Important**: design.md Risks now names the `ResultStore`-persistence blast-radius
      increase (permanent, cross-caller-discoverable outputs replacing ephemeral local PNGs, for
      the highest-call-volume tool family) as an existing, architecture-wide characteristic this
      change meaningfully expands the surface of — not a new gap, but no longer left implicit.
- [x] 9.6 **Important**: added a tool-layer test per batched tool
      (`test_batched_commit_failing_partway_through_persists_nothing`) exercising "commit fails
      partway through a multi-page batched persist" directly — previously only covered
      transitively via `test_store_parity.py`'s generic store-level coverage, despite the spec
      scenario's own wording implying tool-level coverage.
- [x] 9.7 **Important**: added a `bloommcp-smoke-testing` spec delta (this change's `specs/`
      directory) documenting the `seeded_experiment`/`db_experiment_id` harness split explicitly,
      so it isn't left for someone to rediscover from the smoke-test diff alone.
- [x] 9.8 **Important**: strengthened the `source_id`/`run_id` deferred note (design.md) to name
      the concrete consequence the review raised — no `source_note`-style advisory for a
      multi-source experiment — while keeping the deferral itself (matches bloom#626's own
      one-tool-family-at-a-time migration precedent).
- [x] 9.9 **Suggestion**: fixed the two remaining stale "5 surviving plots" comments
      (`test_devendor_invariants.py`, `test_sections_scaffold.py`).
- [x] 9.10 **Suggestion**: `_viz_shared.resolve_trait_columns`'s duplicate check is now O(n) via
      `collections.Counter`, not O(n²) via a `.count()`-per-element comprehension (matters at
      cylinder's ~846-trait scale).
- [x] 9.11 **Suggestion**: added direct unit coverage for `resolve_trait_columns` itself
      (`test_viz_tools.py`) — previously only exercised indirectly through the 3 tools' contract
      tests.
- [x] 9.12 Re-ran the full suite + ruff/black + `openspec validate --strict`.

## 10. Third PR review pass — genuine data-integrity gap that survived 2 review rounds

- [x] 10.1 **Blocking**: `plot_correlation_matrix`'s guarded correlation (`min_periods`) fed
      only the JSON summary; the persisted PNG is rendered by a *separate* call to the vendored
      `create_correlation_heatmap`, which runs its own unguarded, unmaskable correlation — a
      flagged pair could still render as a solid, confidently-colored ±1.0 square in the image
      itself, the exact artifact researchers actually look at. Fixed by disclosure (a new
      `heatmap_caveat` result field, populated whenever `zero_variance_traits`/
      `low_overlap_trait_pairs` is non-empty) rather than a silent, undisclosed gap; filed
      [#747](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/747) to track a
      real fix (patching the vendored delegate, or a bloommcp-side render — the latter against
      this tool's own no-vendored-plotting-logic principle). Added
      `test_heatmap_still_renders_from_the_full_unmasked_frame` (proves the disclosure is
      honest: the delegate genuinely receives the unmasked selection) and
      `test_heatmap_caveat_is_none_when_nothing_is_flagged`.
- [x] 10.2 **Important**: `resolved_trait_columns` — the exact trait list used to
      render/persist a run — is now recorded on all 3 tools, both in the result and stamped into
      the persisted run's `params` (via `provenance.model_copy` extending the existing `params`
      dict, mirroring how `input_validation` is already merged in post-hoc). Previously only a
      count (`n_traits`/`n_traits_plotted`) was recorded anywhere, so a manifest read months
      later couldn't answer "exactly which traits produced this artifact" if auto-detection
      (data-dependent) would resolve differently against drifted source columns today.
- [x] 10.3 **Important**: `plot_trait_histograms`/`plot_trait_boxplots` now report `page_traits`
      — a mapping from each committed output filename to the trait columns rendered on that
      page — computed from `trait_cols` chunked by `_DELEGATE_BATCH_SIZE` (pinned against the
      live delegate signature by a new `test_delegate_batch_size_matches_live_default`, mirroring
      `TRAIT_BATCH_THRESHOLD`'s own existing live-signature pin). Previously only discoverable by
      opening an image (up to ~53 pages at cylinder scale) and reading its axis labels.
- [x] 10.4 **Important**: the `<2`-column guard only counted columns, not variance —
      `plot_correlation_matrix` now also requires at least 2 *non-constant* trait columns
      (`assumption_violated`, since it's discovered only after reading the data), closing the
      all-zero-variance edge case that could otherwise commit a meaningless all-`NaN` heatmap as
      a permanent artifact. Added `test_all_selected_traits_zero_variance_is_assumption_violated`.
- [x] 10.5 **Important**: `test_source_content_addressed_in_manifest`'s name overclaimed what it
      verified (only output hashing, not `based_on_version`/source content-addressing) — fixed
      to actually assert `stored.based_on_version == result.source` and
      `stored.params["resolved_trait_columns"] == result.resolved_trait_columns`, rather than
      just renaming it to something more modest.
- [x] 10.6 **Suggestion**: backported the O(n) `Counter`-based duplicate check (added in round 2
      for `_viz_shared.resolve_trait_columns`) to `_qc_shared._validate_trait_subset`'s
      `require_certified=True` branch (`pca_analysis`/`clustering`) — same cylinder-scale
      motivation, behavior-preserving, covered by their existing duplicate-rejection tests.
- [x] 10.7 **Suggestion**: filed
      [#748](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/748) tracking
      `plot_trait_histograms`/`plot_trait_boxplots`'s delegate-silent raw-data NaN/outlier
      handling (asymmetric with the rigor now applied to `plot_correlation_matrix`) — a
      suggestion-tier follow-up, not fixed in this PR.
- [x] 10.8 Re-ran the full suite (1464 passed — reconciled against 1452 + exactly the 12 tests
      this round added) + `openspec validate --strict`.
