> **TDD note:** write the RED contract tests for each tool before converting it, confirm they
> fail, then convert and confirm GREEN. Commit each tool's conversion + its new tests + the
> removal of that tool's now-incompatible old tests **in one atomic commit** — never leave
> `test_viz_tools.py`'s old standalone tests for an already-converted tool in the tree past that
> tool's own commit (they call the old flat `filename=`/`traits=` signature and will hard-fail
> the moment the tool's params become a Pydantic model). Never land a registration/registry
> change ahead of the tool it depends on.

## 1. Pre-work — confirm shared helpers cover the new shape

- [ ] 1.1 Confirm `_qc_shared._validate_experiment_name` / `_validate_trait_subset` /
      `_role_kwargs` need no changes to serve these 3 tools (they already serve `qc_clean`/
      `qc_inspect` identically shaped raw-frame reads). No new shared helper expected.
- [ ] 1.2 Confirm `ResultStore.commit` handles a multi-PNG output set correctly for a batched
      render (mirrors `pca_analysis`'s `include_plots` figure-dict handling) — no ResultStore
      change expected, just confirm the existing contract covers it.
- [ ] 1.3 Confirm `_ports.raw_source_for` / `frame.resolved_source` are usable from these 3
      tools exactly as `qc_inspect` uses them, for the `source_csv`/`source` provenance wiring
      each conversion below must include (design.md's "stamp source_csv/source" decision).

## 2. `plot_correlation_matrix` (simplest — no batching, no genotype requirement)

- [ ] 2.1 RED: add `tests/tools/test_plot_correlation_matrix_tool.py` wired with `FakeReader`/
      `FakeResultStore` via `_ports.configure` (mirrors `test_qc_inspect_tool.py`'s fixture).
      Write the delegation + numeric oracle test first (pin one off-diagonal correlation cell +
      the reported high-correlation counts against an independent `df.corr()` computation, as
      today's test already does) — confirm it fails (no contract-wrapped tool yet).
- [ ] 2.2 RED: schema round-trip (missing `experiment` → `invalid_input`; explicit empty
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
- [ ] 2.3 GREEN: convert `plot_correlation_matrix.py` — pin `matplotlib.use("Agg")` inline
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

- [ ] 3.1 RED: add `tests/tools/test_plot_trait_histograms_tool.py`. Delegation pinning
      (`create_trait_histograms` called exactly once below `TRAIT_BATCH_THRESHOLD`,
      `create_trait_histograms_batched` above it — reuse the existing synthetic wide-experiment
      fixture recipe), the boundary case (`== TRAIT_BATCH_THRESHOLD` unbatched, `+1` batched).
      Confirm RED.
- [ ] 3.2 RED: a batched render persists one output key + one `OutputLink` per page (assert
      `len(result.outputs) == expected_pages` and every page's bytes are non-empty via the fake
      store, and `result.n_pages`/`result.batched` reflect it) — confirm RED. Plus the same
      schema (including empty-`trait_columns`/zero-trait rejection)/path-safety/provenance
      (incl. `source_csv`/`source`)/links/error-envelope/figure-leak/staging-cleanup-on-failure/
      `tools/list` coverage as 2.2.
- [ ] 3.3 GREEN: convert `plot_trait_histograms.py` — pin `matplotlib.use("Agg")` inline before
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

- [ ] 4.1 RED: add `tests/tools/test_plot_trait_boxplots_tool.py` — same batching +
      contract-pattern coverage as `plot_trait_histograms`'s suite (3.1–3.2), plus: a `FakeReader`
      frame with no detectable genotype column raises `BloomMCPError(code="assumption_violated")`
      naming the experiment, and the delegate is never called / no run is persisted. Confirm RED.
- [ ] 4.2 GREEN: convert `plot_trait_boxplots.py` — pin `matplotlib.use("Agg")` inline before
      importing any `sleap_roots_analyze` delegate; `PlotTraitBoxplotsParams` /
      `PlotTraitBoxplotsResult(RunLinks)` (adds `genotype_column: str` to the histogram shape);
      persist under new tool class `"trait_boxplots"` with `source_csv`/`source` wired as in
      2.3; same staging-cleanup-on-failure wrapping. In the same commit: remove
      `plot_trait_boxplots` from `test_viz_tools.py`'s `_TOOLS`/`_TOOL_IDS` lists and delete its
      now-incompatible standalone tests (`test_plot_trait_boxplots_delegates_and_saves_png`,
      `test_plot_trait_boxplots_uses_batched_delegate_above_threshold`).

## 5. Update `tests/smoke/` for the 3 converted tools

- [ ] 5.1 Rewrite `test_plot_trait_histograms_smoke.py`, `test_plot_trait_boxplots_smoke.py`,
      `test_plot_correlation_matrix_smoke.py` from the `seeded_experiment`/`call_plot_tool`/
      `assert_plot_success` harness to the `db_experiment_id`/`call_tool` harness
      `test_qc_inspect_smoke.py` already uses; assert on the structured result
      (`result["run_ref"]`, `result["manifest_path"]`, `result["outputs"]`, etc.), not a success
      string.
- [ ] 5.2 Update `conftest.py`'s `FIXTURE_FILES`/`EXPERIMENT_ID_ENV_VARS` doc comments (lines
      ~46-50, ~56-62) to reflect the new split: 2 plot tools on `seeded_experiment` (filename),
      10 granular tools (the existing 7 + these 3) on `db_experiment_id` (numeric id).
- [ ] 5.3 Retarget `live_plot_tool_smoke.py` from `sleap_roots_plot_trait_histograms` to
      `sleap_roots_plot_heritability_bar` (or `plot_variance_decomposition`) — design.md's
      "retarget live_plot_tool_smoke.py" decision — since it exists specifically to prove a PNG
      lands on the real bind-mounted `PLOTS_DIR`, which no longer applies to
      `plot_trait_histograms` once converted. Update its docstring/comments accordingly (it
      currently states `plot_trait_histograms` is unaffected by bloom#551's DB-only rewrite —
      that statement becomes false for it and must move to describe whichever tool it now
      targets).

## 6. Registry updates — make the new runs discoverable

- [ ] 6.1 Add `"trait_histograms"`, `"trait_boxplots"`, `"correlation_matrix"` to
      `manifest.CANONICAL_TOOL_CLASSES` (`manifest/__init__.py`) and
      `list_existing_analyses.TOOL_CLASSES`, alongside the untouched, still-unclaimed `"viz"`
      entry. (Order-independent relative to sections 2-4: these constants are not imported by
      the tool modules themselves, only by the discovery-listing code.)
- [ ] 6.2 Add a direct membership assertion — mirrors
      `test_outliers_class_registered_in_discovery_and_canonical_registries` in
      `test_remove_outliers_tool.py` — that all 3 new tool-class strings appear in both
      `CANONICAL_TOOL_CLASSES` and `list_existing_analyses.TOOL_CLASSES`, so a typo in either
      tuple fails directly rather than only indirectly.
- [ ] 6.3 RED→GREEN: extend `tests/tools/test_list_existing_analyses_*` coverage confirming a
      committed run under each new class surfaces in `list_existing_analyses`'s response for
      that experiment (end-to-end, not just the membership check in 6.2).

## 7. Full-suite verification

- [ ] 7.1 `uv run pytest` (bloommcp) green, including the new + modified test files. Confirm no
      standalone test in `test_viz_tools.py` still calls any of the 3 converted tools with the
      old `filename=`/`traits=` signature.
- [ ] 7.2 `uv run ruff check` / `uv run black --check` clean on every touched file.
- [ ] 7.3 `openspec validate converge-bloommcp-viz-tools --strict` passes.
