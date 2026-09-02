## Why

bloommcp exposes heritability only through **visualization**: `plot_heritability_bar` and
`plot_variance_decomposition` each call
`sleap_roots_analyze.statistics.calculate_heritability_estimates` internally and then discard
almost everything it returns — a PNG link plus one aggregate count. **No tool returns the
per-trait H² values as data**, so an agent can look at a bar chart but cannot query the numbers,
and nothing persists a versioned, provenance-stamped run for a heritability calculation the way
`pca_analysis` / `clustering` / `descriptive_stats` do.

Two further defects follow from the same shape (recorded in #462 from the adversarial review of
#438): neither plot tool passes `require_clean=True`, so both fit a mixed model on raw data where
the delegate's per-trait `dropna()` silently changes the analyzed sample count; and both duplicate
the same genotype/replicate/error-handling block, computing H² **twice, independently** for a user
who wants both figures, with nothing structurally preventing the two renderings from disagreeing.

Closes #462.

## What Changes

- **ADD** a granular `heritability_analysis` MCP tool in
  `sections/sleap_roots/analysis/heritability_analysis.py`, modeled on the
  `pca_analysis` / `clustering` / `descriptive_stats` contract pattern: `@as_mcp_tool`-wrapped
  Pydantic I/O, `ExperimentReader` with **`require_clean=True`**, certified-trait selection via
  the existing `_validate_trait_subset(..., require_certified=True)`, **all** heritability math
  delegated to `calculate_heritability_estimates` and wrapped into the upstream typed
  `HeritabilityResult`, and a versioned `ResultStore` run under tool class **`heritability`**
  (`heritability.csv` + `heritability_result.json`).
  The result returns per-trait H² inline, bounded to 50 traits with `truncated_in_summary` /
  `omitted_traits` (matching `descriptive_stats`; necessary at cylinder's 846-trait scale), with
  the full table always in the persisted CSV. Field-level detail lives in the spec delta and
  tasks.md §6.1 — not restated here.
- **BREAKING — RETIRE `plot_heritability_bar` and `plot_variance_decomposition` as standalone
  tools.** Both modules, both registrations, and both smoke tests are deleted; the two registered
  names `sleap_roots_plot_heritability_bar` and `sleap_roots_plot_variance_decomposition`
  disappear from `tools/list`, and any caller invoking them directly breaks. Their rendering is
  folded into `heritability_analysis` as `include_plots: bool = False` / `plots: list[str] | None`,
  mirroring how `pca_analysis` (#426/#447), `umap_analysis` (#425) and `clustering` (#601) grew
  optional plots. Catalog keys: `create_heritability_plot`, `create_variance_decomposition_plot`
  (the latter fed by `compare_trait_heritabilities`, computed only when that key is requested).

  **Migration.** The canonical, user-facing copy of this table lives in the tool's own
  `tools/list` description and in `bloommcp/docs/connecting-claude-code.md` — the two places a
  caller whose invocation just failed will actually look. It is reproduced here once as the
  rationale record; design.md and tasks.md link to it rather than restating it.

  | Retired call | Replacement |
  | --- | --- |
  | `sleap_roots_plot_heritability_bar(filename=X, threshold=T)` | `sleap_roots_heritability_analysis(experiment=X, threshold=T, include_plots=true, plots=["create_heritability_plot"])` |
  | `sleap_roots_plot_variance_decomposition(filename=X)` | `sleap_roots_heritability_analysis(experiment=X, include_plots=true, plots=["create_variance_decomposition_plot"])` |

  The replacement is **not** a drop-in: it requires a committed cleaned version (run `qc_clean`
  first) and returns structured JSON + `resource_link`s into a versioned run rather than a plain
  string carrying a static `/plots/<name>.png` URL.

  **No changelog entry or version bump is needed:** bloommcp is not a published package (the only
  `CHANGELOG.md` / `RELEASE_PROCESS.md` / PyPI trusted-publish workflow in this repo are
  `bloomcli`'s). bloommcp ships as a container built from `staging`/`main`, so the deployed tool
  surface *is* the release unit — which is exactly why the migration copy belongs in the connect
  guide and the tool description rather than in a changelog.
- **Plot/number consistency is structural.** One `calculate_heritability_estimates` call per
  invocation feeds the inline numbers, the persisted table, and both plotters; the caller's
  `threshold` is forwarded explicitly to all three consumers, including
  `create_variance_decomposition_plot`, whose own default is `0.3` rather than `0.5`. Ordering is
  the one documented exception — see design.md D1.
- **CHANGE (deliberate): a replicate column is no longer required.** Both retired tools rejected
  any experiment lacking one. `heritability_analysis` requires only a detected **genotype** column
  and passes `frame.replicate_col` through unchanged, `None` included. This is not a marginal
  loosening: `SupabaseReader` hard-codes `replicate_col=None` on every frame it produces, so it is
  the **only** way a DB-backed experiment can reach this tool at all. Rationale and evidence in
  design.md D3.
- **EXTEND** `bloom_mcp.tools._plots.generate_figures` to accept a plotter returning
  `list[Figure]`, expanding a list into `<key>_page<N>` entries. `create_heritability_plot`
  paginates above 50 traits; existing single-`Figure` callers are unaffected. Design.md D6.
- **ADD** `"heritability"` to `list_existing_analyses.TOOL_CLASSES` and its public-name map, so
  this tool's runs are discoverable. (`manifest.CANONICAL_TOOL_CLASSES` already carries it.)
- **ADD** a per-trait golden `tests/fixtures/turface_19_heritability_golden.json`, recorded via
  the delegate directly on the canonical-default cleaned turface_19 frame (158 samples / 19
  traits, the frame `turface_19_stats_golden.json` and `turface_19_outlier_golden.json` already
  use), labeled a characterization snapshot naming the `sleap-roots-analyze` version.
- **ADD** `tests/tools/test_heritability_analysis_tool.py` and
  `tests/smoke/test_heritability_analysis_smoke.py`, plus a `heritability_analysis` leg in
  `tests/smoke/live_persistence_smoke.py`. See tasks.md §2–§4 and §8.

## Explicitly out of scope

This section is the single source of truth for what is deferred; design.md's Non-Goals and
tasks.md §10 point here rather than re-listing.

- The **richer upstream heritability surface** the issue defers past v1 —
  `identify_high_heritability_traits`, `analyze_heritability_thresholds`, `extract_blup_table`,
  `diagnose_heritability_issues`, and `compare_trait_heritabilities` **as a first-class output**.
  Only `compare_trait_heritabilities` is called here, solely as the input shape
  `create_variance_decomposition_plot` requires.
- A **property/hypothesis-based test**. Issue #462's Oracle names "schema round-trip + provenance
  + property + error-envelope test patterns"; this change substitutes delegation-pinning for the
  property pattern in the main body, but keeps one cheap invariant check
  (`n_traits_requested == n_traits_reported + n_failed` over arbitrary valid trait subsets) so the
  substitution is partial and declared rather than silent. See tasks.md §3.15.
- `plot_font_family` / `plot_font_size` (#661) — deferred, not half-wired (design.md D9).
  `plot_cmap` / `plot_point_size` / `plot_alpha` (#662) — **inapplicable**: neither heritability
  plotter's upstream signature accepts them.
- `remove_low_h2=True` (the delegate's filtering mode). This tool is read-only; filtering traits
  by heritability belongs with the `qc_clean` / `remove_outliers` producer family.
- `force_method="anova_based"`. Exposing a second estimator is a separately scoped decision.
- The 3 surviving plotting tools. Their consolidation is #466's scope, already in flight as
  PR #683 — see the sequencing note below.

## Impact

- **Affected specs:**
  - `bloommcp-heritability-analysis-tool` (**new** capability);
  - `bloommcp-smoke-testing` (**MODIFIED** — smoke roster and the `live_smoke_slow` split);
  - `bloommcp-umap-analysis-tool` (**MODIFIED** — its "the 5 plotting tools" sibling assertion);
  - `bloommcp-packaging` (**MODIFIED** — its delegated-return-key scenario names the retired
    tools, and its zero-fill obligation moves to the replacement);
  - `development-environment` (**MODIFIED** — its "the 5 `sleap_roots` plotting tools always
    write to local disk" clause becomes 3, and `heritability_analysis` breaks the "plot tool ⇒
    writes to `PLOTS_DIR`" equivalence by persisting through `ResultStore`).
  - Builds on (does not modify) `bloommcp-tool-contract`, `bloommcp-experiment-read`,
    `bloommcp-result-store`, `bloommcp-qc-clean-tool`.
- **Pending-change spec collisions — archive ordering is load-bearing.** Two *unarchived*
  proposals carry ADDED requirements that mandate the existence of the tools this change deletes.
  Archiving either after this change would publish a live requirement for deleted modules:
  - `devendor-bloommcp-analysis` → `bloommcp-tool-sections`: its `sleap_roots Umbrella Section
    for Analysis Tools` requirement and two scenarios require "the five surviving plotting tools",
    naming both retired ones.
  - `fix-bloommcp-experiment-identifier-wording` → `bloommcp-experiment-identifier-wording`: a
    scenario enumerating "the five plotting tools", naming both.

  Handled by tasks.md §7.13, which amends those two sibling deltas in place (they are proposals,
  not published truth, so a corrective delta from this change cannot target them).
- **Affected code:** new `heritability_analysis.py`; deleted `plot_heritability_bar.py` and
  `plot_variance_decomposition.py`; edits to `sections/sleap_roots/__init__.py`, `_viz_shared.py`,
  `tools/_plots.py`, `sections/core/list_existing_analyses.py`, `server.py`; new/edited tests in
  `tests/tools/`, `tests/smoke/`, `tests/scripts/`, plus `tests/test_sections_scaffold.py`,
  `tests/test_devendor_invariants.py`, `tests/test_persistence_import_guard.py`,
  `tests/test_oracle.py` (comment only — its two heritability tests exercise the *library*
  delegate on `turface_19_final_data.csv`, not the retired wrappers, and stay valid); new golden
  fixture + `tests/fixtures/README.md`; docs in `bloommcp/docs/` and `_WIKI/BLOOMMCP/`.
  **tasks.md §5–§8 is the authoritative per-file list** — it is not duplicated here, because the
  duplicate is what drifts.
- **Dependencies:** none added. Every delegate is public in the pinned
  `sleap-roots-analyze>=0.1.0a5` (verified against `bloommcp/uv.lock`); no lockfile change.
- **Sequencing — three in-flight PRs touch this change's files.** This change should land **last**
  of the four:
  - **PR #724** (`egao28/bloommcp-plot-snapshot-tests-713`, open) adds pixel-diff snapshot tests
    that import **both retired modules at module level**, plus committed baseline PNGs for both.
    If #724 lands first and this change does not delete those, `python-audit` fails at *collection*
    — the whole bloommcp suite, not just the snapshot cases. Handled by tasks.md §7.14.
  - **PR #683** (`egao28/bloommcp-converge-viz-tools-466`, open) rewrites `test_viz_tools.py` and
    edits `_viz_shared.py`, `sections/sleap_roots/__init__.py`, `test_sections_scaffold.py`,
    `test_devendor_invariants.py`, `tests/smoke/conftest.py`, and `list_existing_analyses.py` —
    essentially this change's entire non-new-file footprint.
  - **PR #726** (`egao28/bloommcp-plot-guards-721`, open) edits `_plots.py` **and both retired
    modules**, producing modify/delete conflicts. Mitigation: extract this change's
    `generate_figures` extension into its own small PR first (tasks.md commit plan C3), so #726
    rebases onto a 10-line addition rather than the reverse.
- **Branch/PR:** branch `egao28/bloommcp-heritability-analysis-462`, cut from `origin/staging`;
  single PR targeting `staging`, titled
  `feat(bloommcp): add heritability_analysis, retire plot_heritability_bar/plot_variance_decomposition (#462)`,
  with a filled `## Breaking Changes` section and a bare `Closes #462` (the repo's
  staging-auto-close workflow matches only the un-bracketed form).
