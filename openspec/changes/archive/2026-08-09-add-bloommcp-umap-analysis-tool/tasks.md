<!--
Commit plan (reconciled against clustering/#309 and pca-plots/#426's actual history —
neither ever pushes a RED-only commit; CI gates the PR HEAD, so a test file importing a
not-yet-created module would collect-error on any intermediate push):
  (1) feat: umap_analysis tool — core contract (params/result/dispatch/persistence) + its
      full test file, together (tests authored first locally, implementation follows until
      green, committed as one unit — mirrors clustering's e94bd01).
  (2) feat: create_umap_single_trait plot (no external dependency).
  (3) feat: create_umap_colored_by_top_traits plot (internal PCA call — see design.md;
      decided, not blocked, but kept as its own commit/PR-splittable unit in case review
      feedback prefers a different option for just this plot key).
  (4) docs: server.py docstring entry + tasks.md checkbox sync.
No golden-fixture generator needed (no cross-platform-reproducible embedding to pin).
-->

## 1. Design

- [x] 1.1 `design.md` — the internal-PCA-for-top-traits-plot decision (Decision #3) is
  resolved as implemented for this change, not blocking; still worth a second look from
  Elizabeth post-implementation (see design.md's Open Questions).

## 2. Tests (write red tests first — TDD)

- [x] 2.0 Add an `injected_ports` fixture mirroring `pca_analysis`/`clustering` exactly:
  `FakeReader` + `FakeResultStore` via `bloom_mcp.tools._ports.configure(...)`, seeding
  `turface_19_final_data.csv` as a cleaned version via `reader.add_cleaned_version(...)`
  (the same 8-trait selection recorded in `turface_19_pca_golden.json["trait_cols"]` — no new
  golden file needed), restoring the Supabase adapters in a `finally:`.
- [x] 2.1 `test_umap_through_the_tool` — call `_run()` with default params against the
  turface_19 fixture seeded as a cleaned version via `injected_ports` (a `FakeReader`/static-CSV
  fixture, not a live Supabase/qc_clean dependency); assert `embedding` shape
  `n_samples x n_components`, no NaN/inf, `feature_names` matches the certified selection.
  Uses the real `perform_umap_analysis` delegate (not marked `@pytest.mark.integration` — see
  design.md's "Real-delegate tests run in the fast lane").
- [x] 2.2 `test_same_seed_identical_embedding` — two calls with the same `seed` on the same
  platform produce bit-identical `embedding` (within-run determinism, no golden needed). Real
  delegate.
- [x] 2.3 `test_no_silent_sample_loss` — `n_samples` in the result equals the certified-clean
  row count.
- [x] 2.4 (superseded by §5.2 — see below) originally
  `test_umap_analysis_in_tools_list_and_workflow_preserved`: new tool appears in `tools/list`
  with a non-null input schema; legacy `run_dimensionality_reduction_workflow`
  (`method="umap"`) is still present and unchanged. That legacy workflow was retired by
  `devendor-bloommcp-analysis` before this PR could merge, so the test was replaced —
  `test_umap_analysis_registered_in_sleap_roots_section` now checks the namespaced
  `sleap_roots_umap_analysis` name instead.
- [x] 2.5 `test_delegates_once_and_never_computes_umap_itself` — monkeypatch
  `perform_umap_analysis`, assert called exactly once with the resolved `random_state` and the
  certified-clean trait selection.
- [x] 2.6 `test_provenance_records_resolved_seed` — `Provenance.seed` equals the resolved
  int, never `None` (contrast `pca_analysis`'s `test_provenance_seed_none`).
- [x] 2.7 `test_default_seed_is_42` — omitting `seed` resolves to 42 (matches
  `perform_umap_analysis`'s own default and the legacy workflow's `_RANDOM_STATE`).
- [x] 2.8 `test_n_neighbors_at_or_above_n_samples_is_assumption_violated` — `n_neighbors =
  n_samples` (and `> n_samples`) raise `BloomMCPError(assumption_violated)` naming the
  requested value and the max usable (`n_samples - 1`); assert `perform_umap_analysis` is
  never called (pre-dispatch guard) and no run is committed.
- [x] 2.8b `test_n_neighbors_non_positive_is_invalid_input` — `n_neighbors=0` raises
  `invalid_input` via the Pydantic `ge=2` field constraint; spy asserts the delegate is never
  called (distinct failure mode from 2.8 — a caller mistake, not a data-quality problem).
  `test_n_neighbors_one_is_invalid_input` covers the `n_neighbors=1` boundary separately
  (tightened from `gt=0` to `ge=2` — umap-learn hard-rejects `n_neighbors=1` for any data,
  independent of sample count, verified directly against the installed package).
- [x] 2.8c `test_min_dist_negative_is_invalid_input` — `min_dist=-0.1` raises `invalid_input`
  via a `ge=0.0` field constraint; delegate never called.
- [x] 2.8d `test_n_components_below_one_is_invalid_input` — `n_components=0` raises
  `invalid_input` via a `ge=1` field constraint; delegate never called (mirrors
  `pca_analysis`'s own `test_n_components_below_one_is_invalid_input`).
- [x] 2.8e `test_n_components_above_max_is_invalid_input` — `n_components=51` raises
  `invalid_input` via a new `le=50` field constraint (UMAP has no natural upper clamp the
  way PCA does; this is a sanity ceiling on an LLM-driven, low-trust input surface — see
  design.md).
- [x] 2.8f `test_degenerate_small_n_neighbors_eigensolver_failure_is_assumption_violated` —
  real delegate, `n_samples=3`/`n_neighbors=2` trips umap-learn's spectral-embedding
  eigensolver into a bare `TypeError` (verified directly); confirms the widened except
  clause (`ValueError, KeyError, RuntimeError, TypeError`) maps it to a structured,
  non-leaking `assumption_violated`.
- [x] 2.9 `test_n_neighbors_below_n_samples_boundary_succeeds` — `n_neighbors = n_samples -
  1` (the boundary) succeeds without clamping or error.
- [x] 2.9b `test_n_components_equals_one_succeeds` — `n_components=1` (the minimum useful
  value) produces a valid `n_samples x 1` embedding.
- [x] 2.9c `test_n_components_near_sample_count_is_handled_safely` — using the real delegate,
  request `n_components` close to/at a small fixture's `n_samples`; assert the outcome is
  EITHER a structured `assumption_violated` OR a successful finite embedding — never an
  unhandled `internal_error` and never a leaked backend message. Verified against the real
  delegate: on a 5-sample fixture with `n_components=4`, umap-learn succeeds with a finite
  embedding (no artificial upper-bound clamp needed — see design.md).
- [x] 2.10 `test_unknown_trait_column_is_invalid_input_naming_it` /
  `test_non_certified_numeric_column_is_rejected_not_dropped` /
  `test_empty_trait_columns_is_invalid_input` /
  `test_duplicate_trait_columns_is_invalid_input_naming_them` — same `_validate_trait_subset`
  contract as `pca_analysis`/`clustering`.
- [x] 2.11 `test_non_finite_certified_trait_is_rejected` — defense-in-depth finiteness guard
  on the *input* (same pattern as `pca_analysis`/`clustering`).
- [x] 2.12 `test_degenerate_fit_does_not_leak_backend_internals` — parametrized over all
  four exception types the delegate call's except clause names (`ValueError`, `KeyError`,
  `RuntimeError`, `TypeError`), each mapped to a structured `assumption_violated` with no
  raw exception text leaked — closes the review gap that only `ValueError` had a direct
  test. (A "real-delegate degenerate selection" test was considered but dropped: verified
  the real delegate does NOT raise on an all-constant selection — it produces a
  degenerate-but-finite embedding instead, so the leak-guard is only exercisable via a
  monkeypatched delegate, unlike PCA's eigendecomposition which genuinely raises.)
- [x] 2.13 `test_raw_only_experiment_is_rejected_with_qc_clean_remedy` /
  `test_consumes_cleaned_version_source` — same cleaned-input contract as siblings.
- [x] 2.14 `test_persists_embedding_and_returns_links_not_the_vector` — result never inlines
  `embedding`; `outputs` names `embedding_coords.csv` + `umap_result.json`.
- [x] 2.15 `test_embedding_csv_carries_sample_identity` — `embedding_coords.csv` prepends
  `frame.metadata_cols` (mirrors `clustering`'s `labels.csv` test).
- [x] 2.16 `test_second_run_increments_version` / `test_passes_source_csv_for_input_lineage`
  — same versioning/lineage pattern as siblings.
- [x] 2.17 `test_standardized_is_always_true` — `UMAPResult.standardized` reads `True` for
  every call through this tool (no `standardize` param exists — see design.md).
- [x] 2.18 `test_valid_input_output_round_trip` / schema validation smoke.
- [x] 2.25 `test_non_finite_embedding_is_assumption_violated_before_persistence` —
  monkeypatch `perform_umap_analysis` to return an otherwise-valid dict whose `embedding`
  contains a NaN/inf value; assert a structured `assumption_violated` is raised and no run is
  committed (mirrors `test_degenerate_fit_does_not_leak_backend_internals`'s no-leak shape,
  applied to the delegate's *output* instead of a raised exception — since the guard fires
  before `store.create_run()`, no staging dir is ever allocated, so there is nothing to leak).

### Plots

- [x] 2.19 `test_default_no_plots_outputs_unchanged` — `include_plots=False` (default): no
  PNG keys in `outputs`. `test_default_path_never_executes_an_import_matplotlib_statement`
  (renamed from `test_matplotlib_not_imported_on_default_path`) covers the narrower, accurate
  claim: matplotlib is already resident in `sys.modules` via this module's transitive
  `sleap_roots_analyze` import regardless of `include_plots` (the old name/claim was wrong —
  fixed per review, see design.md and the module/Field docstrings).
- [x] 2.20 `test_unknown_plot_key_invalid_input_no_run_committed` /
  `test_duplicate_plot_key_invalid_input_no_run_committed` /
  `test_empty_plots_list_is_invalid_input` /
  `test_include_plots_false_with_plots_param_is_silently_ignored` — reuse `_plots.py`'s
  existing validation contract verbatim (same test shapes as `test_pca_analysis_tool.py`).
- [x] 2.21 `test_umap_single_trait_plot_png_round_trip` — real PNG bytes
  (`content.startswith(b"\x89PNG")`).
- [x] 2.22 `test_umap_colored_by_top_traits_plot_png_round_trip` — asserts the internal,
  non-persisted `perform_pca_analysis` call happens (monkeypatch/spy) and produces a real
  PNG; asserts no second `tool_class="pca"` run is committed.
- [x] 2.22b `test_top_traits_plot_internal_pca_uses_same_trait_selection` — spy on
  `perform_pca_analysis`; assert its captured trait-column argument equals exactly the
  validated `trait_cols` passed to `perform_umap_analysis` (same set, same order) — not a
  broader or re-validated selection.
- [x] 2.22c `test_top_traits_internal_pca_failure_is_assumption_violated` — parametrized
  over `(ValueError, KeyError, RuntimeError, TypeError)`: the internal PCA call now gets
  the same widened exception-tuple translation as the main delegate call, closing the
  review gap where it previously only caught `(ValueError, KeyError)` and was untested for
  any failure path.
- [x] 2.23 `test_plots_subset_generates_only_requested`.
- [x] 2.24 `test_figure_cleanup_get_fignums_empty` — on success, on invalid key, and on
  partial plotter failure (three cases, mirroring `test_pca_analysis_tool.py`).

## 3. Implementation

- [x] 3.1 Create `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/umap_analysis.py`:
  - `UMAPAnalysisParams` (Pydantic): `experiment`, `trait_columns: list[str] | None`,
    `n_neighbors: int = Field(default=15, ge=2)`, `min_dist: float = Field(default=0.1,
    ge=0.0)`, `n_components: int = Field(default=2, ge=1, le=50)`, `seed: int = 42`,
    `include_plots: bool = False`, `plots: list[str] | None`, `user_label: str | None`.
  - `UMAPAnalysisResult(RunLinks)`: `experiment`, `source`, `n_samples`, `n_features`,
    `n_components`, `feature_names`, `n_neighbors`, `min_dist`, `standardized`, `seed`.
  - `umap_analysis(params, *, random_state: int, provenance: Provenance)` wrapped by
    `@as_mcp_tool(input_model=..., output_model=..., errors=(ExperimentReadError,))`.
  - Flow: `reader.load_experiment(require_clean=True)` → `CleanedVersionRequiredError` →
    `tool_error` remedy → `_validate_trait_subset(..., require_certified=True)` →
    finiteness guard (input) → **pre-dispatch `n_neighbors >= n_samples` guard** →
    `perform_umap_analysis(selected, feature_cols=trait_cols, n_neighbors=, min_dist=,
    n_components=, random_state=random_state)` (catch `ValueError`/`KeyError`/`RuntimeError`/
    `TypeError` → `assumption_violated`, logged server-side via `logger.debug` before
    translating, no leaked text) → **non-finite embedding guard (output)** →
    `UMAPResult.from_umap_dict(result_dict, random_state=random_state)`.
  - Persist: `store.create_run(tool_class="umap", ...)`; `embedding_coords.csv` (identity +
    `UMAP1..UMAPn` columns via `_build_output_frame`); `umap_result.json` via `.to_json()`.
- [x] 3.2 Register in `bloommcp/src/bloom_mcp/sections/sleap_roots/__init__.py`:
  - Import `umap_analysis` from `.analysis` and add `umap_analysis.umap_analysis` to the
    `register(section, ...)` call, alongside `pca_analysis`/`clustering`.
  - Update that module's docstring (tool count 5 → 6, name list) and `server.py`'s
    section-summary docstring to mention `umap_analysis`.
- [x] 3.3 `_umap_plot_calls(result_dict, frame, trait_cols)` local to
  `umap_analysis.py`: zero-arg lambdas for `create_umap_single_trait` (first selected
  trait column) and `create_umap_colored_by_top_traits` (needs the internal
  `perform_pca_analysis` call over the same `trait_cols` — only run when that key is
  requested, itself wrapped in the same widened `(ValueError, KeyError, RuntimeError,
  TypeError)` → `assumption_violated` translation as the main delegate call, logged
  server-side before translating); imports `validate_plot_keys` / `generate_figures` /
  `close_figures` from the existing `bloom_mcp.tools._plots` unmodified.
- [x] 3.4 Wire `include_plots`/`plots` into the same validate-before-`create_run`,
  `try/finally`-close persistence shape as `pca_analysis.py` (validate + generate before
  `create_run`; `close_figures` in `finally`; PNGs merge into `outputs`).

## 4. Validate

- [x] 4.1 `openspec validate add-bloommcp-umap-analysis-tool --strict` — passed
  (`npx -y -p @fission-ai/openspec openspec validate add-bloommcp-umap-analysis-tool --strict`
  → "Change 'add-bloommcp-umap-analysis-tool' is valid").
- [x] 4.2 Scoped pass: `cd bloommcp && .venv/bin/python -m pytest
  tests/tools/test_umap_analysis_tool.py -q --tb=short`. Full suite matching CI:
  `cd bloommcp && .venv/bin/python -m pytest tests/ -m "not integration" -q --tb=short` —
  no regressions (see §5 for the post-relocation counts).
- [x] 4.3 `bloommcp/README.md`, `DEV_SETUP.md`, and `bloommcp/docs/local-validation.md` need
  no changes (their tool-category phrasing is already non-exhaustive, confirmed by reading
  each — UMAP falls under the existing "dimensionality reduction" category). The
  post-relocation doc touch-ups are `sections/sleap_roots/__init__.py`'s docstring,
  `server.py`'s section-summary docstring (task 3.2), and `test_sections_scaffold.py`'s
  expected-tools set (§5).
- [x] 4.4 Lint: `uvx black --target-version py311` and `uvx ruff check` on all
  new/modified files (see §5 for the post-relocation run).
- [x] 4.5 Real (non-fake) end-to-end smoke, adapted for this environment: Docker is
  unavailable here, so the deployed `make bloommcp-smoke` (Supabase + MinIO) and the Claude
  Desktop dogfood check could not run. Instead ran a one-off script wiring the **real**
  `LocalReader` + real `SupabaseResultStore` (which transparently routes its five
  storage calls through `LocalStorageBackend` when `BLOOM_STORAGE_BACKEND=local` —
  `supabase_client.upload_file`/etc. resolve the active backend lazily, so this is not a
  mock) against real temp-directory input/output roots — no `FakeReader`/`FakeResultStore`
  anywhere. 11/11 checks passed: real `qc_clean` → real `umap_analysis(require_clean=True)`
  resolves the committed cleaned version (not raw); embedding shape, `n_components`, and the
  resolved `seed=42` are correct; `outputs` are exactly the two data artifacts *and* real
  files exist on disk at the recorded keys; the `create_umap_single_trait` plot produces a
  real PNG file with valid magic bytes; a second call advances the version rather than
  overwriting. Claude Desktop's interactive discoverability/schema-legibility check is still
  outstanding — that needs a real running server + client, which this script doesn't cover.
- [x] 4.6 Committed and pushed `egao28/bloommcp-umap-analysis-425`; PR #463 opened into
  `staging`.

## 5. PR #463 review round 2 — relocate + fix

`staging` merged `devendor-bloommcp-analysis` (moving every sibling tool from
`bloommcp/src/bloom_mcp/tools/*_tool.py` + per-tool `server.py` registration into
`bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/*.py` + centralized registration)
before PR #463 could land, making it unmergeable as-is. A second review pass also found
real gaps beyond the relocation. Addressed:

- [x] 5.1 **Relocate** (blocking): moved `umap_analysis_tool.py` →
  `sections/sleap_roots/analysis/umap_analysis.py`; dropped the file's own `register(mcp)`
  function (registration is now centralized, see 3.2); updated the test file's imports to
  `bloom_mcp.sections.sleap_roots.analysis.umap_analysis`.
- [x] 5.2 Replaced `test_umap_analysis_in_tools_list_and_workflow_preserved` (which asserted
  the now-retired `run_dimensionality_reduction_workflow` still existed) with
  `test_umap_analysis_registered_in_sleap_roots_section`, checking the new namespaced
  `sleap_roots_umap_analysis` tool name. Added `umap_analysis` to
  `test_sections_scaffold.py`'s `test_sleap_roots_section_exposes_the_expected_namespaced_tools`
  expected set (the authoritative registration-presence test for this architecture) and
  added `bloommcp/tests/smoke/test_umap_analysis_smoke.py` for parity with every sibling
  analysis tool's real-MCP-transport live smoke test.
- [x] 5.3 **Important — inconsistent exception handling for the internal PCA call**: widened
  `_top_traits()`'s except clause from `(ValueError, KeyError)` to
  `(ValueError, KeyError, RuntimeError, TypeError)`, matching the main delegate call. Added
  `test_top_traits_internal_pca_failure_is_assumption_violated` (parametrized over all four
  exception types) — this path was previously untested for any failure mode.
- [x] 5.4 **Important — no upper bound on n_components**: added `le=50` (see design.md's
  Decision). Added `test_n_components_above_max_is_invalid_input`.
- [x] 5.5 **Important — stale OpenSpec docs**: fixed `proposal.md`/`tasks.md`'s `gt=0` →
  `ge=2` for `n_neighbors` (the code has been `ge=2` since PR #463's first review round;
  only the docs lagged), and `design.md`'s real-delegate test count (was documented as 3,
  actually 5 — listed all five explicitly).
- [x] 5.6 **Important — factually wrong schema description**: `include_plots`'s Pydantic
  `Field(description=...)` claimed "matplotlib is never imported" on the default path —
  false, since `sleap_roots_analyze` imports it transitively regardless (the module
  docstring already said this correctly; the Field description was missed). Reworded.
  Renamed/reworded the corresponding test
  (`test_matplotlib_not_imported_on_default_path` → confirmed matplotlib is not imported by
  the default path).
- [x] 5.7 **Suggestion — log before translating**: added `logger.debug(...)` (module-level
  `logger = logging.getLogger(__name__)`) capturing the original exception type/message
  server-side, immediately before both delegate-exception → `assumption_violated`
  translations (main call and the internal PCA call) — makes a genuinely new upstream
  failure mode this except clause doesn't yet name distinguishable from ordinary degenerate
  data, without leaking anything to the caller.
- [x] 5.8 **Not fixed — orphaned staging-directory leak on partial write failure**: confirmed
  pre-existing and shared with `pca_analysis`/`clustering` (not a regression introduced
  here); documented as a cross-cutting follow-up in design.md's Risks rather than fixed in
  this PR, which would require touching all three tools' shared persistence shape.
- [x] 5.9 **Not fixed — `create_umap_single_trait`'s unguarded `trait_cols[0]`**: evaluated;
  `trait_cols` is guaranteed non-empty by the time plot generation runs (either the full
  certified-clean set, which `qc_clean` requires to be non-empty, or a caller-supplied
  subset already rejected as `invalid_input` if empty) — a defensive check here would be
  pure paranoia with no reachable failure case, so left as-is.
- [x] 5.10 Re-ran `openspec validate --strict`, full test suite, and `black`/`ruff` after
  the relocation + fixes — see the PR description for final counts.
- [x] 5.11 Force-pushed the rewritten branch history to `egao28/bloommcp-umap-analysis-425`
  (the original 3-commit history was rebuilt against current `staging` rather than rebased,
  since the branch's original fork point predates extensive, unrelated history on
  `staging`); PR #463 updated. `gh pr view --json mergeable` confirmed `MERGEABLE`
  (`mergeStateStatus: BLOCKED` only on the required-review branch-protection rule, not a
  real conflict).

## 6. PR #463 review round 3 — minor/non-blocking follow-ups

A second 5-lens review pass (after §5) confirmed every BLOCKING/Important item resolved
and approved, with five non-blocking follow-ups. All five addressed:

- [x] 6.1 Added `test_n_components_equals_max_succeeds` — `n_components=50` (the `le=50`
  ceiling's own boundary) must succeed; only 51 was previously tested as rejected and 1 as
  the lower-bound success case, leaving the upper boundary itself unverified.
- [x] 6.2 Added a spec.md scenario ("Internal PCA call failure is a structured,
  non-leaking assumption_violated") under "Top-traits plot consumes an internal,
  non-persisted PCA call" — the implementation and test
  (`test_top_traits_internal_pca_failure_is_assumption_violated`) already existed from §5.3
  but the spec had no corresponding scenario.
- [x] 6.3 Added `test_delegate_failure_logs_original_exception_at_debug_level` and
  `test_top_traits_internal_pca_failure_logs_original_exception` (via `caplog.at_level`) —
  the §5.7 `logger.debug(...)` additions had no dedicated coverage; these confirm the
  original exception type/message is actually captured, not just that the code calls a
  logging function.
- [x] 6.4 Fixed `bloommcp/tests/smoke/test_umap_analysis_smoke.py`'s docstring: it claimed
  coverage of only the turface_19 fixture, but `seeded_experiment`'s dependency on the
  session-wide `fixture_name` fixture (`params=["turface_19", "cylinder"]`) already
  parametrizes every test using it over both fixtures with no explicit mark needed. Added
  the reasoning for why neither fixture needs `live_smoke_slow` here (UMAP has no
  full-covariance step the way GMM does, and the default `n_neighbors=15` is comfortably
  below both fixtures' sample counts — verified: turface_19's raw fixture has 187 samples,
  cylinder's has 129).
- [x] 6.5 PR description/comment test-count mismatch: reconciled against the actual
  collected count (56, up from 43, reflecting parametrized cases — see the updated PR
  description).
