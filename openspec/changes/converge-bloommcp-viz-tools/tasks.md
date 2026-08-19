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

Not chased (disclosed, non-blocking per the review itself): a delegate-internal figure leak if
`create_trait_histograms_batched`/`create_trait_boxplots_by_genotype_batched` raise partway
through creating a batch's figures — this tool's own `finally: for fig in figures: plt.close(fig)`
cannot reach figures the delegate created and abandoned before raising, since `figures` is never
assigned until the (all-or-nothing) delegate call returns. Pre-existing, delegate-internal, and
not unique to these 3 tools' wrapper.
