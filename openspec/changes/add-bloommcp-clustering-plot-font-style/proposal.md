## Why

#661 gave `pca_analysis` and `umap_analysis` font-style overrides
(`plot_font_family`/`plot_font_size`); #662 added the mark-style fields on top
(`plot_alpha` for PCA; `plot_cmap`/`plot_point_size`/`plot_alpha` for UMAP). #601 (PR #668,
merged) then gave `clustering` its own `include_plots`/`plots` support, reusing the same
shared `bloom_mcp.tools._plots` helper — but it landed without the font-style fields its
siblings already have. The result is an inconsistency with no mechanism behind it:
`clustering` already calls `generate_figures`, the exact seam `apply_font_style` hooks into,
so the generic styling machinery is sitting there wired up and unreachable purely because two
fields were never added to `ClusteringParams`.

A scientist preparing a clustering figure for a paper or poster can set a serif family and a
22pt font on their PCA scree plot but not on the cluster scatter beside it in the same figure
panel — for no reason a caller can discover from the schema.

This change closes that one gap, and records the audit of every *other* plot-generating tool
(#680's second deliverable) so the next person doesn't re-derive which gaps are real, which
are blocked upstream, and which are already tracked elsewhere.

## What Changes

- **MODIFY** `ClusteringParams`: add `plot_font_family: str | None = None` and
  `plot_font_size: float | None = None`, mirroring `PCAAnalysisParams`/`UMAPAnalysisParams`
  field-for-field — same descriptions, same `json_schema_extra={"exclusiveMinimum": 0,
  "maximum": MAX_PLOT_FONT_SIZE}` discoverability shim, same ignored-when-`include_plots=False`
  policy.
- **MODIFY** the `clustering` tool body: call `check_plot_style_ceiling(params.plot_font_size,
  ...)` before any I/O — immediately *after* the existing `_reject_wrong_method_controls(params)`
  guard, so it is second in the body rather than first as in `pca_analysis` (see `design.md`
  for why, and for the two error-precedence consequences that follow). The ceiling is not
  optional polish: #721 measured `plot_font_size` in the low thousands costing several seconds
  and multiple GB per render, and omitting the check would newly introduce that cost on
  `clustering`'s LLM-driven input surface. Then forward both fields into the existing
  `generate_figures(...)` call as `font_family=`/`font_size=`.
- **No change to `bloom_mcp/tools/_plots.py`.** `apply_font_style`, `check_plot_style_ceiling`,
  `MAX_PLOT_FONT_SIZE`, and `generate_figures`'s font kwargs all already exist and are already
  exercised by two callers; this change adds a third caller, not a mechanism.
- **ADD** tool-level tests in `bloommcp/tests/tools/test_clustering_tool.py` mirroring the
  `test_pca_analysis_tool.py`/`test_umap_analysis_tool.py` font-style block: forwarded-and-applied
  across both catalog plots, no-op-by-default, ceiling rejection (including NaN/inf, regardless of
  `include_plots`, with the message naming value and ceiling), the two error-precedence orderings,
  at-ceiling and just-above-zero acceptance, ignored-when-`include_plots=False`, the
  unrecognized-family pass-through, provenance recording, and the JSON-schema discoverability guard.
- **ADD** one line to `bloommcp/docs/roadmap.md` recording the two untracked gaps this audit
  found, so they survive the archiving of this change.

## Audit: remaining plot-generating tools (#680)

Re-verified against `staging` at `ab779039` — **not** carried over from #680's text, which was
written before #466 and #462 landed and is stale in two of its four findings. Reasoning and
evidence for every verdict live in `design.md`; the table is here because it is #680's
deliverable.

**Scope of the audit: the font-style axis only.** #662 established that the mark-style fields
(`plot_cmap`/`plot_point_size`/`plot_alpha`) are per-tool work repeated as each tool is
tackled — they are not a uniform capability to audit for. The one mark-style question #680
does raise (do they apply to `clustering`?) is answered below.

| Tool | Reaches `generate_figures`? | Font style today | Verdict |
| --- | --- | --- | --- |
| `pca_analysis` | yes | yes (#661) | — |
| `umap_analysis` | yes | yes (#661) | — |
| `clustering` | yes | **no** | **closed by this change** |
| `heritability_analysis` | yes | **no** | real gap, **already tracked** — see below |
| `plot_trait_histograms` | no (`call_with_figure_cleanup` only) | no | out of scope, **untracked** |
| `plot_trait_boxplots` | no (`call_with_figure_cleanup` only) | no | out of scope, **untracked** |
| `plot_correlation_matrix` | no (`call_with_figure_cleanup` only) | no | out of scope, **untracked** |
| `qc_inspect` | no (`call_with_figure_cleanup` only) | no | out of scope, **untracked** |
| `remove_outliers` | no (`call_with_figure_cleanup` only) | no | out of scope, **untracked** |

- **`cmap`/`point_size`/`alpha` genuinely do not apply to clustering** — the upstream plotters
  accept no such kwarg. Confirmed against the installed `sleap_roots_analyze`; see `design.md`.
  Out of scope, as #680 states. The audit separately found a **pre-existing upstream colour
  defect** while checking this (clusters above 10 collapse onto duplicate colours) — see
  `design.md`; untracked, and not this change's to fix.
- **`heritability_analysis` has `clustering`'s gap, and it is already tracked** — as deferral
  **D9** and follow-up task **10.2** of `add-bloommcp-heritability-analysis-tool`, filed when
  #462 landed the tool. #680 never saw it (the tool postdates the issue), so the audit records
  it, but there is nothing new to file. Not bundled here: #680's "Scope (this issue)" names
  `ClusteringParams` only.
- **Five tools reach `_plots.py` for figure-registry cleanup only**, via
  `call_with_figure_cleanup`, and still `savefig()` inline — so `generate_figures`, and
  therefore `apply_font_style`, never sees their figures. Converging them is a refactor, not two
  fields. **As of 2026-09-21 no GitHub issue tracks it** — `gh issue list --search` finds none —
  and #808/PR #832 do *not* cover it (that is `FIGURE_REGISTRY_LOCK` around `plt.close`, a
  different axis). Recorded in `bloommcp/docs/roadmap.md` by this change; recommend filing an
  issue.
- **#680's own wording is stale in two places** — its "5 legacy string/URL-returning `plot_*`
  tools" no longer exist as such (#466 converged three, #462 retired two), and its
  "`qc_inspect`/`remove_outliers` don't route through `_plots.py` at all" stopped being true
  with #721/#726. Both conclusions survive on restated reasons; see `design.md`.

## Impact

- Affected specs: `bloommcp-clustering-tool`
- Affected code:
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/clustering.py` — two new
    `ClusteringParams` fields, one `check_plot_style_ceiling` call, two kwargs on the existing
    `generate_figures` call, one import line widened, module-docstring note
  - `bloommcp/tests/tools/test_clustering_tool.py` — new font-style test block
  - `bloommcp/docs/roadmap.md` — one line recording the audit's untracked gaps
- **Stacking note:** `openspec/specs/bloommcp-clustering-tool/` does not exist yet — the
  capability is still three unarchived deltas (`add-bloommcp-clustering-tool`,
  `-hierarchical`, `-plots`). This change adds a fourth. Both of its requirements are `ADDED`
  with names that collide with none of the other 15, so the composed spec is the same in any
  archive order.
- **Backward compatibility:** both fields default to `None`, and `apply_font_style` returns
  before touching a figure when both are `None` — so with them omitted **every generated figure
  is byte-identical to today**. The *manifest* is not: `@as_mcp_tool` stamps
  `Provenance.stamp(params=data.model_dump())`, so every clustering run — default calls
  included — gains `"plot_font_family": null, "plot_font_size": null` in its recorded params.
  That is additive, matches what `pca_analysis`/`umap_analysis` already record, and no consumer
  asserts an exact key set. Called out because "no behavior change" is otherwise easy to read as
  covering the persisted record too.
- **No conflict with #723** (pixel-snapshot baselines for clustering's two plot keys, open): the
  default path renders identically, so any baseline captured before or after this change matches.
- **No new dependency, and no `_plots.py` change** — this change is a third caller of an
  existing, already-tested mechanism.
