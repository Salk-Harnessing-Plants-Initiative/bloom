## 1. Tests (write first — but see 1.11 on which are genuinely red)

All new tests go in `bloommcp/tests/tools/test_clustering_tool.py`, in a new
`# ── Font-style override (#680) ──` block at the end, mirroring the equivalent block in
`test_pca_analysis_tool.py`/`test_umap_analysis_tool.py`. Reuse the file's existing
`_run(**overrides)` helper (line 73) and `injected_ports` fixture — do not introduce a second
harness.

**Harness note that applies to 1.1, 1.2, 1.5, 1.8 and 1.12.** Patch
`clustering_tool._clustering_plot_calls`, whose real signature is keyword-only:
`_clustering_plot_calls(result_dict, result, *, scatter_pca_result_dict)`
(`clustering.py:391`). The in-file precedent is
`test_figure_cleanup_on_partial_plotter_failure_no_run_committed` (line 1262), **not**
`test_plotters_invoked_once_with_correct_args` (line 1127), which patches the upstream plotters
on `sleap_roots_analyze` instead and cannot capture the styled figure — styling happens inside
`generate_figures`, after the plotter returns. A fake copied from PCA
(`def _fake_calls(result_dict, pca, frame, threshold, **kwargs)`) will `TypeError`; use
`def _fake_calls(result_dict, result, **kwargs)`.

- [x] 1.0 `test_plot_font_style_fields_exist` — `assert {"plot_font_family", "plot_font_size"} <=
  set(ClusteringParams.model_fields)`, mirroring `test_version_field_exists` (line 985). This is
  the **red anchor** for 1.6/1.7/1.8/1.10/1.10b: `ClusteringParams` declares no `model_config`, so
  Pydantic v2's default `extra="ignore"` silently *drops* unknown kwargs — without this test those
  tests pass today for the wrong reason.
- [x] 1.1 `test_plot_font_family_and_size_forwarded_and_applied` — fake `_clustering_plot_calls`
  returning **two** zero-arg callables (one per catalog key), each building a real `Figure` with a
  title, an x-label, and — on the `create_cluster_scatter_pca` stand-in — a legend created with an
  explicit title (`ax.legend(title="Cluster")`), captured in a closure. Call
  `_run(method="kmeans", n_clusters=3, include_plots=True, plots=None, plot_font_family="serif",
  plot_font_size=22)`. Assert on **both** captured figures: `title.get_fontfamily() == ["serif"]`,
  `title.get_fontsize() == 22`, `xaxis.label.get_fontfamily() == ["serif"]`, and on the legend
  figure that both `legend.get_texts()` entries and `legend.get_title()` carry the override. Also
  assert both PNG keys are in `result.outputs`, `len(store.list_runs(_EXPERIMENT, "clustering"))
  == 1`, and `plt.get_fignums() == []`. Two figures, not one, is what proves the override is
  applied per-iteration; the legend is what pins the spec's "legend text and title" clause. Note
  `plots=None` means the real `_compute_scatter_pca` fit runs (as in
  `test_both_plots_png_round_trip`) — a few hundred ms, acceptable.
- [x] 1.2 `test_plot_font_style_applies_for_every_method` — parametrize over `_METHOD_CALLS`
  (defined at line 1017, used as `@pytest.mark.parametrize("method_kwargs",
  _METHOD_CALLS.values(), ids=_METHOD_CALLS)` at lines 1040/1082); single-figure fake, request
  only `plots=["create_cluster_size_barplot"]` to keep the real PCA fit out; assert the override
  lands for each method.
- [x] 1.3 `test_plot_font_size_out_of_range_is_invalid_input_regardless_of_include_plots` —
  parametrized over `value in [0, -1, 101, float("inf"), float("nan")]` × `include_plots in
  [True, False]`. Assert `exc.value.code == "invalid_input"`, **and that the message names both
  the field/value and the ceiling** (`"plot_font_size" in exc.value.message` and `"100" in
  exc.value.message` — `check_plot_style_ceiling` renders `plot_font_size=101 must be greater
  than 0 and at most 100.`). The message assertion is not optional polish: naming the value is
  the entire reason the check is a tool-body call rather than a `Field` constraint, and without
  it a refactor back to `Field(gt=0, le=100)` keeps every other test green. Also assert
  `store.list_runs(_EXPERIMENT, "clustering") == []` and `plt.get_fignums() == []`.
- [x] 1.4 `test_plot_font_size_rejected_before_the_experiment_is_read` — **spy, do not raise.**
  `reader.load_experiment = MagicMock(wraps=reader.load_experiment)` (the pattern
  `test_omitting_version_preserves_todays_exact_call` at line 989 already uses; `MagicMock` is
  imported at line 25). Call with `plot_font_size=101`; assert `exc.value.code ==
  "invalid_input"`, `reader.load_experiment.call_count == 0`, and no run committed. A raising
  sentinel does **not** work: `as_mcp_tool` routes any non-`BloomMCPError` through
  `BloomMCPError.from_exception` (`contract/wrap.py:148-152`), so an `AssertionError` surfaces as
  an opaque `internal_error` whose message is a correlation id.
- [x] 1.5 `test_plot_font_size_at_ceiling_is_accepted` — `plot_font_size=100` with a captured real
  figure; assert `title.get_fontsize() == 100` (reaching the figure, not merely passing
  construction), **and** that the run committed with its PNG key — otherwise a regression that
  styles then aborts still passes.
- [x] 1.6 `test_plot_font_size_just_above_zero_is_accepted` — `_run(include_plots=False,
  plot_font_size=0.01)`; assert success and no PNG outputs. (Do not assert the *applied* size
  below 1pt: matplotlib clamps sub-1pt to `1.0`.)
- [x] 1.7 `test_plot_font_fields_ignored_when_include_plots_false` — `_run(include_plots=False,
  plot_font_family="serif", plot_font_size=22)`; assert success and no PNG keys in `outputs`.
- [x] 1.8 `test_unresolvable_plot_font_family_is_not_rejected` — parametrize over
  `["NotARealFont-12345", ""]`; assert the call succeeds, the PNG output is present, **and** the
  captured figure's title reports `get_fontfamily() == [<the submitted value>]` — matplotlib
  stores the unresolved name and falls back only at render, so without that third assertion the
  test passes today for the wrong reason. No warning risk: the fallback is logged via
  matplotlib's logger, not `warnings.warn`, and no `filterwarnings` is configured anywhere in the
  repo.
- [x] 1.9 `test_plot_font_size_ceiling_is_in_the_json_schema` — assert
  `ClusteringParams.model_json_schema()["properties"]["plot_font_size"]` carries `maximum == 100`
  and `exclusiveMinimum == 0`, **and** that `ClusteringParams(experiment="x.csv", n_clusters=3,
  plot_font_size=99999)` does not raise.
- [x] 1.10 `test_method_control_conflict_is_reported_before_the_font_size_ceiling` — reuse
  `test_gmm_control_on_kmeans_is_rejected`'s shape (line 347): `_run(method="kmeans",
  n_clusters=3, n_components=2, plot_font_size=101)`; assert `invalid_input`, `"n_components" in
  exc.value.message`, and `"plot_font_size" not in exc.value.message`.
- [x] 1.10b `test_font_size_ceiling_is_reported_before_plot_key_validation` — `_run(method=
  "kmeans", n_clusters=3, include_plots=True, plots=["not_a_real_plot"], plot_font_size=101)`
  with `reader.load_experiment` spied as in 1.4; assert the message names `plot_font_size`, not
  the bad key, and that the reader was never called. This ordering is a genuine contract change:
  `validate_plot_keys` runs at `clustering.py:640`, *after* `load_experiment` at `:448`, so
  inserting the ceiling check at `:440` flips which error this input reports (design.md).
- [x] 1.11 Run the new tests and confirm the expected split — **measured against `HEAD`, do not
  assume all are red.** Actual result: **22 red, 3 green.** RED before implementation: 1.0, 1.1,
  1.2, 1.3 (all 10 parametrizations), 1.4, 1.5, 1.8, 1.10b, 1.13, and 1.9's first assertion.
  GREEN before implementation **by construction, not by mistake**: 1.6, 1.7, 1.10, 1.12, and
  1.9's `plot_font_size=99999` assertion — Pydantic's `extra="ignore"` drops the unknown field,
  so the no-error/no-PNG outcome is identical pre- and post-change, and 1.12 asserts the
  *absence* of styling, which already holds. Those are regression pins gated by 1.0; do not
  "fix" them by asserting a Pydantic rejection that `extra="ignore"` will never produce.
  (1.8 came out red rather than green as first predicted, because its third assertion — the
  figure actually carries the unresolved family — is not satisfiable until the field exists.)
- [x] 1.12 `test_default_call_leaves_upstream_plotter_styling_untouched` — same fake harness, but
  the fake sets a deliberately non-default style on its figure (`ax.set_title("t",
  fontfamily="monospace", fontsize=7)`); call `_run(include_plots=True,
  plots=["create_cluster_size_barplot"])` with **both** font fields omitted; assert the captured
  title still reports `["monospace"]` / `7`. Pins the spec's "A default call applies no styling"
  scenario as a test rather than leaving the change's central no-regression claim to review gate
  4.2.
- [x] 1.13 `test_font_style_fields_are_recorded_in_provenance_params` — spy on `store.create_run`
  and capture `kwargs["provenance"].params`; assert both keys carry the submitted values on a
  styled call, and are present-as-`None` on a default call. Pins the manifest-shape change
  `Provenance.stamp(params=data.model_dump())` (`contract/wrap.py:137-139`) makes for *every*
  clustering run, and the spec's "records the requested style, applied or not" scenario.

## 2. Implementation

- [x] 2.1 `clustering.py` — widen the `_plots` import to
  `from bloom_mcp.tools._plots import (MAX_PLOT_FONT_SIZE, check_plot_style_ceiling,
  close_figures, generate_figures, validate_plot_keys)`.
- [x] 2.2 `clustering.py` — add `plot_font_family: str | None = Field(default=None, ...)` and
  `plot_font_size: float | None = Field(default=None, json_schema_extra={"exclusiveMinimum": 0,
  "maximum": MAX_PLOT_FONT_SIZE}, ...)` to `ClusteringParams`, directly after `plots`. Copy the
  two `Field` descriptions from `PCAAnalysisParams` (`pca_analysis.py:147-167`) verbatim — they
  name no PCA-specific plots, so no adaptation is needed there. (The per-tool text that *does*
  enumerate plotters is the module docstring, which task 2.6 handles.)
- [x] 2.3 `clustering.py` — add a short comment above the fields pointing at
  `MAX_PLOT_FONT_SIZE`'s single-sourcing in `_plots.py`, matching the equivalent comment in
  `pca_analysis.py`/`umap_analysis.py`.
- [x] 2.4 `clustering.py` — in the `clustering` body, call
  `check_plot_style_ceiling(params.plot_font_size, field_name="plot_font_size",
  max_value=MAX_PLOT_FONT_SIZE)` immediately after `_reject_wrong_method_controls(params)`
  (line 439) and before the `reader.load_experiment` block. Comment it with both the
  `pca_analysis` rationale (why a tool-body check, not a `Field` constraint) **and** the
  placement divergence (why second, not first — and that it therefore precedes
  `validate_plot_keys`). See design.md.
- [x] 2.5 `clustering.py` — forward `font_family=params.plot_font_family,
  font_size=params.plot_font_size` into the existing
  `generate_figures({k: calls[k] for k in keys_to_generate}, figures)` call (line 660).
- [x] 2.6 `clustering.py` — add an "**Optional font-style override (#680).**" paragraph to the
  module docstring, mirroring `pca_analysis.py`/`umap_analysis.py`, stating that
  `plot_cmap`/`plot_point_size`/`plot_alpha` are **not** offered here because the upstream
  cluster plotters accept no such kwargs (so the omission reads as deliberate), and that an
  unresolvable family is recorded in provenance as requested but rendered as matplotlib's
  fallback.
- [x] 2.7 `bloommcp/docs/roadmap.md` — add one line recording the audit's two untracked gaps
  (converging the five `call_with_figure_cleanup`-only tools onto `generate_figures`; the two
  pre-existing plot defects in design.md's "Defects the audit exposed"). The change docs archive;
  the roadmap doesn't.
- [x] 2.8 Re-run the tests from section 1; all green.

## 3. Verification

- [x] 3.1 `uv run --extra test pytest tests/tools/test_clustering_tool.py -q` from `bloommcp/` —
  full file green, no pre-existing test modified.
- [x] 3.2 `uv run --extra test pytest tests/tools/ -q` from `bloommcp/` — no collateral damage to
  `test_pca_analysis_tool.py`, `test_umap_analysis_tool.py`, or `test_plots_helpers.py`.
- [x] 3.3 `uv run black --check src tests && uv run ruff check src tests` from `bloommcp/`.
- [x] 3.4 `openspec validate add-bloommcp-clustering-plot-font-style --strict`.
- [x] 3.5 Commit safety: the test commit may land red — `pr-checks.yml` triggers on
  `pull_request` (`branches: [main, staging]`), so CI evaluates the PR head, not each commit.
  Noted so a future `git bisect` isn't surprised.

## 4. Review gates

- [x] 4.1 Diff `ClusteringParams`'s two new fields against `PCAAnalysisParams`'s — confirm the
  only differences are description prose. **This gate covers the fields only**; the call-site
  placement is deliberately asymmetric (2.4) and is pinned by 1.10/1.10b instead.
- [x] 4.2 Confirm the default path is untouched: with both fields `None`, `apply_font_style`
  returns before touching the figure, so `include_plots=True` figure output is byte-identical to
  before. (Provenance is *not* byte-identical — it gains two `null` keys; that is intended and
  pinned by 1.13.)
- [x] 4.3 **Confirm the diff makes no behavior change to
  `bloommcp/src/bloom_mcp/tools/_plots.py`.** If it does, the change's central premise is wrong —
  stop and re-read `design.md`.
- [x] 4.4 Confirm no other tool file is touched — in particular `heritability_analysis.py`, whose
  identical gap is already tracked as that change's D9/10.2 and is deliberately not fixed here.
