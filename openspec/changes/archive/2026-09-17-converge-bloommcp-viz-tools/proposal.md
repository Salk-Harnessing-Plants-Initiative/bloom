## Why

Adversarial review of #438 (`devendor-bloommcp-analysis`) found that all 5 relocated viz tools
(`sections/sleap_roots/analysis/`) register via bare `mcp.tool()` instead of the `@as_mcp_tool`
contract wrapper every other tool in that folder uses, and take a raw `filename` straight into
`load_experiment_data` with no path-safety guard (unlike every sibling tool). #438 applied an
immediate path-safety + no-raw-exception-leak patch to all 5 as a stopgap, not the full
convergence (see `_viz_shared.validate_filename`'s own docstring, which still says so today).

`plot_heritability_bar` and `plot_variance_decomposition` are **out of scope here** — they are
being retired entirely and folded into a new `heritability_analysis` compute tool (#462), since
they duplicate heritability computation and don't require clean data the way `pca_analysis`/
`clustering` do. This issue (#466) covers only the 3 tools that remain genuinely standalone
visualizations with no backing "compute" tool to fold into:

- `plot_trait_histograms`
- `plot_trait_boxplots`
- `plot_correlation_matrix`

These are legitimately pre-clean-appropriate EDA tools (same category as `qc_inspect`), so
`require_clean=True` is **not** the fix — just the architectural convergence onto the same
contract (`@as_mcp_tool`, Pydantic I/O, structured `BloomMCPError`, one stamped `Provenance`,
versioned `ResultStore` persistence) every other tool in this folder already has.

Adjacent, non-conflicting context: #669 (filed the same day as this proposal) notes that
`list_existing_analyses.TOOL_CLASSES` already fails to enumerate 3 existing tool classes
(`pca`, `umap`, `qc_inspect`). This change adds 3 correctly-named new entries alongside that
gap; it does not fix #669's pre-existing omissions, which stay a separate follow-up.

## What Changes

- **Convert all 3 tools onto `@as_mcp_tool`** with Pydantic input/output models, matching the
  pattern `pca_analysis`/`qc_clean`/`qc_inspect` already use in the same folder. All figure
  *rendering* is already 100% delegated to `sleap_roots_analyze` (verified during #438's review
  — no vendored/duplicated plotting logic), so no rendering logic changes. One exception,
  preserved exactly as-is: `plot_correlation_matrix`'s reported strong-positive/negative
  correlation counts are today computed in bloommcp itself (`df[selected].corr()` + `np.triu`),
  not via a delegate call — this small, pre-existing summary computation carries over unchanged,
  it is not new analysis logic introduced by this change.
- **Read the raw frame via the `ExperimentReader` port, no `require_clean`** — mirrors
  `qc_inspect` exactly (pre-clean EDA, not a cleaned-data consumer).
- **`trait_columns: Optional[list[str]]` replaces the ad hoc `traits: str` comma-parsed
  parameter**, validated via the existing `_qc_shared._validate_trait_subset` (existence +
  numeric) so an unknown/non-numeric name is now a structured `invalid_input` rather than being
  silently dropped by `_viz_shared.parse_traits`.
- **The bare-filename guard moves from `_viz_shared.validate_filename`'s string return to
  `_qc_shared._validate_experiment_name`'s raised `BloomMCPError`** — the string-returning guard
  was a deliberate #438 stopgap for tools with nothing to catch a raised exception; once wrapped
  by `@as_mcp_tool`, a raised `BloomMCPError` is exactly what the contract expects and reports.
- **Persist a versioned run per tool via the `ResultStore` port**, each under its **own new**
  tool class (`trait_histograms` / `trait_boxplots` / `correlation_matrix`) rather than the
  legacy shared `viz` bucket — see `design.md` for why. The rendered PNG(s) become committed run
  outputs (signed/served `output_links`), replacing today's direct write to
  `BLOOM_PLOTS_DIR`/`BLOOM_PLOTS_URL`. A batched (paginated) render — above
  `_viz_shared.TRAIT_BATCH_THRESHOLD` traits — persists one output entry per page (mirrors
  `pca_analysis`'s `include_plots` multi-figure handling) instead of today's `"N pages: url1,
  url2, ..."` string.
- **Register the 3 new tool classes** in `manifest.CANONICAL_TOOL_CLASSES` and
  `list_existing_analyses.TOOL_CLASSES` so these tools' runs become discoverable via
  `list_existing_analyses` — today they produce zero discoverable history.
- **Structured `RunLinks`-based result models replace the plain formatted-string return, AND
  the call moves from a flat `filename`/`traits` kwarg pair to one `params` object with
  `experiment`/`trait_columns` fields** — the same two-part shape change every other
  `@as_mcp_tool` tool in this folder already has relative to a bare `mcp.tool()` function.
  **BREAKING** on both axes. See `design.md`'s "Read Path Migration" decision for what else
  this implies (it is not purely cosmetic).
- **Reading via the `ExperimentReader` port (instead of `experiment_utils.load_experiment_data`)
  is itself a behavior change, not just a wrapper change** — see `design.md`. In the default
  Supabase deployment this means these 3 tools move from "any local CSV in `BLOOM_TRAITS_DIR`"
  to "a DB-registered experiment", mirroring the exact migration the other 7 granular tools
  (`qc_clean`, `qc_inspect`, `remove_outliers`, `pca_analysis`, `clustering`,
  `descriptive_stats`, `umap_analysis`) already underwent. `tests/smoke/conftest.py` already
  documents this exact split (`seeded_experiment`/filename fixture for the 5 plot tools vs.
  `db_experiment_id`/numeric-id fixture for the 7 granular ones) — after this change that split
  becomes 2 plot tools vs. 10 granular ones.
- **Rewrite `tests/tools/test_viz_tools.py`**: split the cross-tool parametrized tests
  (path-safety, no-raw-exception-leak, missing-file) so `plot_heritability_bar`/
  `plot_variance_decomposition` (unchanged, still bare `mcp.tool()`) keep their existing
  string-based assertions, while the 3 converged tools get their own `FakeReader`/
  `FakeResultStore`-backed contract tests (tools/list presence, schema round-trip,
  provenance + links, delegation pinning, error envelope, staging-cleanup-on-failure,
  figure-handle-leak) mirroring `test_qc_inspect_tool.py`. Each tool's old, now-incompatible
  standalone tests in `test_viz_tools.py` are removed in the **same commit** that converts it
  (not deferred to a final cleanup pass) — see `tasks.md`.
- **Update `tests/smoke/`**: `test_plot_trait_histograms_smoke.py`,
  `test_plot_trait_boxplots_smoke.py`, and `test_plot_correlation_matrix_smoke.py` move from
  the `seeded_experiment`/`call_plot_tool`/`assert_plot_success` harness to the
  `db_experiment_id`/`call_tool` harness `test_qc_inspect_smoke.py` already uses, asserting on
  the structured result instead of a success string. `conftest.py`'s comments/fixture-group
  docs update to reflect the new 2-vs-10 split. `live_plot_tool_smoke.py` — which exists
  specifically to prove a plotting tool's PNG lands on the real bind-mounted `PLOTS_DIR`
  (issue #472) — retargets to one of the 2 **remaining** bare-`mcp.tool()` tools (e.g.
  `plot_heritability_bar`), since that bind-mount write path no longer exists for the 3
  converted tools (their PNGs are now `ResultStore` outputs, not `BLOOM_PLOTS_DIR` writes).
  **No prior claim that "no in-repo consumer depends on the old shape" holds** — it does not;
  these smoke-test files are real, if infrequently-run (`live_smoke`-marked, dev-stack-only),
  consumers, and are explicitly in scope here.
- `_viz_shared.py` keeps `parse_traits`/`TRAIT_BATCH_THRESHOLD`/`save_plot`/`save_plot_or_plots`/
  `validate_filename` — still used by the 2 remaining bare-`mcp.tool()` tools — no dead code
  removed by this change.

## Impact

- **Affected specs:** `bloommcp-viz-tools` (new capability).
- **Affected code:**
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/plot_trait_histograms.py`
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/plot_trait_boxplots.py`
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/plot_correlation_matrix.py`
  - `bloommcp/src/bloom_mcp/manifest/__init__.py` (3 new `CANONICAL_TOOL_CLASSES` entries)
  - `bloommcp/src/bloom_mcp/sections/core/list_existing_analyses.py` (3 new `TOOL_CLASSES`
    entries)
  - `bloommcp/tests/tools/test_viz_tools.py` (split; new contract tests for the 3 tools)
  - `bloommcp/tests/smoke/test_plot_trait_histograms_smoke.py`,
    `test_plot_trait_boxplots_smoke.py`, `test_plot_correlation_matrix_smoke.py` (rewritten to
    the `db_experiment_id` harness)
  - `bloommcp/tests/smoke/conftest.py` (fixture-group doc comments)
  - `bloommcp/tests/smoke/live_plot_tool_smoke.py` (retargeted to a non-converted tool)
- **Not touched:** `plot_heritability_bar.py`, `plot_variance_decomposition.py`,
  `_viz_shared.py`'s existing helpers, any `sleap_roots_analyze` delegate,
  `tests/smoke/live_persistence_smoke.py` (its deep, tool-specific `ResultStore` round-trip
  coverage is a separate, larger undertaking than this change and is explicitly a follow-up,
  not a blocker — see `design.md` Non-Goals).
- **Breaking:** the 3 tools' MCP request shape (`filename`/`traits` → one `params` object) and
  response shape (plain string → structured object) both change; and, in the default Supabase
  deployment, the underlying read moves from "any local CSV" to "a DB-registered experiment"
  (see above). Every in-repo caller of the old shape (the 3 smoke-test files + `conftest.py` +
  `live_plot_tool_smoke.py`) is updated in this change; no consumer outside `bloommcp/` was
  found (`langchain/`, `apps/` checked).
