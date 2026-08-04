## Why

`pca_analysis` (#377/#308) established the granular-consumer pattern for Tier 4: read a
*cleaned* experiment, delegate **all** math to `sleap_roots_analyze`, wrap the returned dict
into an upstream typed result, persist a versioned run, and return a summary + links. UMAP is
the other axis-reduction method scientists reach for alongside PCA, but there is no granular
`umap_analysis` tool today — only the legacy `run_dimensionality_reduction_workflow` (which
returns a raw dict, has no cleaned-input contract, and predates the typed-result convention).
`sleap_roots_analyze` now ships a serializable `UMAPResult` (0.1.0a5, closing
talmolab/sleap-roots-analyze#180) with no non-serializable `reducer`/`scaler` fields, so the
hard dependency this change was blocked on is already satisfied — `sleap-roots-analyze>=0.1.0a5`
is already the pin in `bloommcp/pyproject.toml` and no version bump is needed.

## What Changes

- **ADD** `umap_analysis` tool
  (`bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/umap_analysis.py`), a
  contract-wrapped Tier 4 consumer parallel to `pca_analysis`, in the `sleap_roots` section
  alongside its siblings (post-`devendor-bloommcp-analysis`, tools live in
  `sections/sleap_roots/analysis/*.py` and register centrally in
  `sections/sleap_roots/__init__.py` — there is no more per-tool `server.py` wiring):
  - Reads the cleaned run via `reader.load_experiment(experiment, require_clean=True)`
    (UMAP needs no-NaN numeric input, same precondition as PCA/clustering); a missing cleaned
    version raises `tool_error` with the "run qc_clean first" remedy.
  - Params: `experiment: str`, `trait_columns: list[str] | None`, `n_neighbors: int = 15`,
    `min_dist: float = 0.1`, `n_components: int = 2`, `seed: int = 42`,
    `include_plots: bool = False`, `plots: list[str] | None`, `user_label: str | None`.
  - Delegates to `sleap_roots_analyze.perform_umap_analysis`, forwarding the resolved
    `random_state`; wraps the returned dict via `UMAPResult.from_umap_dict(result_dict,
    random_state=random_state)`. Owns no UMAP math of its own.
  - **Stochastic**: declares a keyword-only `random_state: int` tool-function parameter (the
    `clustering` precedent, not `pca_analysis`'s `seed=None`) so the contract wrapper resolves
    `params.seed` and stamps the resolved int into `Provenance.seed` — UMAP's embedding
    genuinely depends on the seed, unlike PCA's inert one in this tool's regime.
  - **Edge case**: turns `perform_umap_analysis`'s silent `n_neighbors` → `n_samples - 1`
    clamp into a structured, pre-dispatch `assumption_violated` (see `design.md`'s Decision;
    talmolab/sleap-roots-analyze#67).
  - **Parameter bounds**: `n_neighbors`, `min_dist`, `n_components` get Pydantic field
    constraints (`ge=2` / `ge=0.0` / `ge=1, le=50`) so a caller mistake surfaces as
    `invalid_input` before the delegate is ever called, rather than being caught by the
    generic delegate-exception handler and mislabeled `assumption_violated` (mirrors
    `pca_analysis`'s `n_components: int | None = Field(default=None, ge=1)`). `n_neighbors`'s
    floor is `ge=2`, not the weaker `gt=0`: umap-learn hard-rejects `n_neighbors=1` for any
    data, independent of sample count (verified directly against the installed package).
    `n_components`'s `le=50` ceiling is a sanity bound, not a scientific one — UMAP has no
    natural clamp the way PCA does, and this is an LLM-driven, low-trust input surface where
    nothing else stops a request like `n_components=10_000_000` from risking the container's
    OOM-killer.
  - The delegate call's `except` clause is `(ValueError, KeyError, RuntimeError, TypeError)`
    — wider than `pca_analysis`'s single `ValueError` — because umap-learn's
    spectral-embedding eigensolver was found (verified directly) to raise a bare `TypeError`
    for a legitimate small-sample-count combination (`n_samples=3`, `n_neighbors=2`) near the
    `n_neighbors`/`n_samples` boundary; without it, that case would surface as an opaque
    `internal_error` instead of the intended `assumption_violated`. The internal,
    non-persisted `perform_pca_analysis` call (for `create_umap_colored_by_top_traits`, see
    below) gets the identical exception-tuple treatment for the same reason.
  - **Non-finite embedding guard**: `UMAPResult.to_json()` raises `ValueError` on a non-finite
    embedding value (`allow_nan=False`). Rather than letting that raise inside the
    `create_run`/`commit` region (which would leak an orphaned staging dir and surface as an
    unhandled `internal_error`), `umap_analysis` checks embedding finiteness immediately after
    the delegate call and raises a structured `assumption_violated` before any run is created.
  - Persists a versioned run under **`tool_class="umap"`**: `embedding_coords.csv` (with
    sample-identity columns prepended, mirroring `clustering`'s `labels.csv`) and the
    serialized `UMAPResult` (`umap_result.json`); `based_on_version` = the consumed cleaned
    version; not itself resolvable as a "cleaned" version by a downstream consumer. Returns a
    summary (shape, feature names, seed) + object-key links — the embedding is never inline.
- **ADD** optional plots via the existing `bloom_mcp.tools._plots` helper (already merged by
  #426; `validate_plot_keys` / `generate_figures` / `close_figures` reused **verbatim**, no
  changes to `_plots.py`). Catalog:
  - `create_umap_single_trait` — self-contained (embedding + df + one trait column).
  - `create_umap_colored_by_top_traits` — **requires a `pca_results: Dict`** (loadings +
    eigenvalues) to rank trait contributions. `umap_analysis` computes this via an internal,
    **non-persisted** call to `perform_pca_analysis` over the same trait selection, purely to
    feed the plotter's ranking — see `design.md` for the alternative considered (excluding
    this plot, mirroring PCA's exclusion of `create_variance_decomposition_plot`) and why an
    internal call was chosen instead.
  - Same validate-before-`create_run` / `try`-`finally` figure-cleanup structure as
    `pca_analysis`: unknown/duplicate/empty `plots` → `invalid_input` with no run committed;
    figures closed in `finally` regardless of outcome; PNGs merge into the existing `outputs`
    dict (no new result field); the lazy plotter import inside `_umap_plot_calls` avoids a
    *second*, redundant `matplotlib` import on the `include_plots=True` path — it does
    **not** keep matplotlib out of `sys.modules` on the default path, since this module's own
    top-level `sleap_roots_analyze` import already pulls matplotlib in transitively (same as
    `pca_analysis`/`clustering`; no Tier-0 import-clean guarantee is claimed for this tool).
- **ADD** `umap_analysis` registration in `sections/sleap_roots/__init__.py`'s `register(section,
  ...)` call, plus a `umap_analysis` mention in that module's docstring and in
  `server.py`'s section-summary docstring (the same touch-up `pca_analysis`/`clustering`
  each required on their own addition, adapted for the post-`devendor-bloommcp-analysis`
  sections/ layout — there is no more per-tool `server.py` registration call).
- **No dependency bump**: `sleap-roots-analyze>=0.1.0a5` already pinned and installed;
  `UMAPResult`, `perform_umap_analysis`, and both plotters are already importable.
- **No breaking change**: new tool, additive only. (At the time this proposal was first
  written, the legacy `run_dimensionality_reduction_workflow`/`run_clustering_workflow`
  Phase-1 workflow tools still coexisted alongside their granular siblings; both were
  retired outright by `devendor-bloommcp-analysis`, which landed on `staging` before this
  PR could merge — see the Impact section's rebase note. This tool touches none of that
  retirement; it is purely additive to the current `sections/sleap_roots/analysis/`
  layout.)

### Oracle

UMAP is not bit-reproducible cross-platform (numba backend), so there is **no golden
embedding** to pin against (contrast `pca_analysis`'s turface_19 golden). Instead:

- **Within-run determinism**: same `seed` on the same platform → identical embedding across
  repeated calls.
- **Structural**: embedding shape is `n_samples x n_components`; sample count is preserved
  (no silent row loss — same finite-guard and row-alignment check as `pca_analysis`/
  `clustering`); no non-finite values (checked before persistence — see the non-finite
  embedding guard above); `feature_names` recorded and matches the selected trait columns.
- **Provenance**: records an `int` seed (never `None` — UMAP is always stochastic here,
  unlike `clustering`'s deterministic `hierarchical` branch).
- **Plots**: real-PNG-bytes round-trip; unknown plot key → `invalid_input` with no run
  committed; figure cleanup verified via `matplotlib.pyplot.get_fignums() == []` on both
  success and failure paths.
- **Contract surface**: `tools/list` registration (namespaced `sleap_roots_umap_analysis`),
  schema round-trip, structured-error envelope (no leaked backend internals).
- **Live smoke**: `bloommcp/tests/smoke/test_umap_analysis_smoke.py`, mirroring every
  sibling analysis tool's real MCP-transport smoke test (`pytest.mark.live_smoke`, real
  `qc_clean` → real `umap_analysis` against the running dev stack) — the current
  post-`devendor-bloommcp-analysis` equivalent of the older `docs/local-validation.md`
  "Leg N" pattern this proposal originally described (that older pattern/doc no longer
  applies to this tool's location). A live Claude Desktop discoverability/schema check is
  still outstanding — see `tasks.md` 4.5.

## Impact

- Affected specs: `bloommcp-umap-analysis-tool` (new capability)
- Affected code:
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/umap_analysis.py` — new tool
    (models on `pca_analysis.py` for the plots/persistence shape, `clustering.py` for the
    stochastic-seed contract wiring)
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/__init__.py` — import + register
    `umap_analysis.umap_analysis`; update the module docstring's tool count/list
  - `bloommcp/src/bloom_mcp/server.py` — update the section-summary docstring's tool list
    (no registration call — that lives in `sections/sleap_roots/__init__.py`)
  - `bloommcp/tests/tools/test_umap_analysis_tool.py` — new test suite
  - `bloommcp/tests/test_sections_scaffold.py` — add `umap_analysis` to the expected
    namespaced-tools set
  - `bloommcp/tests/smoke/test_umap_analysis_smoke.py` — new live-smoke test (parity with
    every sibling analysis tool, each of which has one under `bloommcp/tests/smoke/`)
  - No changes to `bloommcp/src/bloom_mcp/tools/_plots.py` (consumed unmodified, as its own
    docstring anticipates)
- **Rebase note**: this proposal was originally written and implemented against the
  pre-`devendor-bloommcp-analysis` architecture (`bloommcp/src/bloom_mcp/tools/*_tool.py` +
  a per-tool `server.py` `.register(mcp)` call). That architecture was retired by
  `devendor-bloommcp-analysis` (merged to `staging` before this PR could land), which moved
  every sibling tool into `sections/sleap_roots/analysis/*.py` with centralized
  registration. This revision relocates `umap_analysis` into that layout; the tool's
  internal logic and its test suite carried over with no behavioral change beyond the
  Important-issue fixes listed below.
- Dependencies: `sleap-roots-analyze>=0.1.0a5` already satisfies the hard dependency
  (talmolab/sleap-roots-analyze#180, closed); no `pyproject.toml` change needed.
- Deferred / upstream-gated (not in scope for this change):
  - Openable (signed-URL) links depend on #388 Part 2 (`create_signed_url` on the
    `StorageBackend` seam, #389) — today's links are object keys, same caveat as
    `pca_analysis`/`clustering`.
  - Inherits the "latest cleaned version" resolution caveats from #419/#420.
- Rollback note: this is additive-only, so reverting the merge is sufficient to stop new
  `tool_class="umap"` runs from being created. It does **not** retroactively invalidate any
  `umap_analysis` runs already persisted by real users before a revert — the generic store
  API keeps serving them unchanged. Since `umap_analysis` results are not resolvable as a
  "cleaned" version by any downstream consumer, nothing else in the pipeline chains off a bad
  run; a data-remediation script would be a separate follow-up only if a persisted-data bug
  is ever found post-ship (the same systemic gap exists for `pca_analysis`/`clustering` today
  and is not unique to this change).
- Design decision finalized for this change (see `design.md`): `create_umap_colored_by_top_traits`
  is implemented via an internal, non-persisted `perform_pca_analysis` call (Decision #3 of
  three considered). This was an open question pending review sign-off; it is treated as
  resolved for this implementation and can be revisited if reviewer feedback prefers a
  different option.
- Sibling/template changes: `openspec/changes/add-bloommcp-clustering-tool/` (stochastic-seed
  precedent), `openspec/changes/add-pca-analysis-plots/` (plots precedent + the `_plots.py`
  module this change consumes unmodified).
- Branch: `egao28/bloommcp-umap-analysis-425`, targets `staging`.
