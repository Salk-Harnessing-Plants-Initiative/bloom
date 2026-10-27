## Context

**Note on #661**: this design is written by contrast with the sibling proposal
`add-bloommcp-plot-font-style` (issue #661), which adds `plot_font_family`/`plot_font_size` as
a **generic** post-processing step inside `bloom_mcp.tools._plots.generate_figures`. As of this
writing #661 is implemented only on its own branch (`egao28/bloommcp-plot-font-style-override-661`,
open PR #665) — **not yet merged to `main`**, and not present in this change's own base. Every
"#661" reference below describes that sibling proposal's shape for comparison, not code that
exists on this branch; see `proposal.md`'s Impact section for the resulting merge-order note.

Font styling (`Text.set_fontfamily`/`set_fontsize`) applies uniformly to any `Figure`'s axes
regardless of which upstream plotter drew it, which is why #661's shape works as a generic
hook. `cmap`/`point_size`/`alpha` are different in kind: they are **constructor kwargs** the
upstream plotter itself consumes while drawing (e.g. `ax.scatter(..., c=values, cmap=cmap,
s=point_size, alpha=alpha)`), not a property of an already-built `Figure` that can be
overridden after the fact — there is no post-hoc "change the colormap of a `PathCollection`
that's already been colored" equivalent to `text.set_fontfamily(...)`. So this change wires
these three kwargs at the call site (`_umap_plot_calls`/`_pca_plot_calls`), not inside
`_plots.py`.

## Goals / Non-Goals

- Goals: let callers override `cmap`/`point_size`/`alpha` on the specific catalog plotters
  that already support them upstream; zero impact on callers who don't pass the new fields;
  no upstream (`sleap-roots-analyze`) change required.
- Non-Goals: making `cmap`/`point_size`/`alpha` available on plotters whose current upstream
  signature doesn't accept them (`create_umap_colored_by_top_traits`,
  `create_pca_scree_plot`, `create_feature_contribution_plot`,
  `create_feature_contribution_heatmap`) — adding that support belongs in
  `sleap-roots-analyze`, not here; a generic `_plots.py` post-processing hook (rejected, see
  Decisions).

## Decisions

### Per-plotter forwarding at the call site, not a `_plots.py` hook

Considered mirroring #661's `generate_figures(..., font_family=..., font_size=...)` shape —
i.e. a single `cmap`/`point_size`/`alpha` triple threaded generically into every plot key's
call. Rejected: the table in `proposal.md` shows support is **not** uniform across catalog
plotters (only 2 of 6 accept `alpha`; only 1 accepts `cmap`/`point_size`). A generic hook would
either (a) silently swallow a `TypeError: unexpected keyword argument` for every plotter that
doesn't accept the kwarg — requiring `_plots.py` to special-case which kwargs each key
accepts, which re-introduces per-plotter knowledge into a module explicitly designed to be
plotter-agnostic (`bloommcp-pca-analysis-tool` spec's "Shared, Tool-Agnostic Module"
requirement) — or (b) reject caller values based on which plot keys were requested, which
couples plot-key validation to style-kwarg validation. Instead, each tool's own
`_umap_plot_calls`/`_pca_plot_calls` — which already hard-codes each plotter's bespoke args
(e.g. `pca_analysis`'s `create_feature_contribution_heatmap` call passing
`plot_type="loadings"`) — is the natural, already-established place to also hard-code *which*
new kwargs a given call forwards.

### Forward only non-`None` fields, one dict-building line each

```python
def _create_umap_single_trait_call() -> Figure:
    kwargs = {}
    if params.plot_cmap is not None:
        kwargs["cmap"] = params.plot_cmap
    if params.plot_point_size is not None:
        kwargs["point_size"] = params.plot_point_size
    if params.plot_alpha is not None:
        kwargs["alpha"] = params.plot_alpha
    return create_umap_single_trait(result_dict, frame.df, trait_cols[0], **kwargs)
```

Mirrors #661's "no-op when unset" guarantee: a caller who never sets these fields gets
byte-identical figures to today (the plotter's own hardcoded defaults —
`cmap="viridis", point_size=30, alpha=0.7` for `create_umap_single_trait`), since no kwarg is
passed at all rather than passing back the plotter's own default value.

### `PCAAnalysisParams` gets `plot_alpha` only — no `plot_cmap`/`plot_point_size`

Adding fields with no plotter to reach them would be a param that always no-ops — worse than
omitting it, since a caller setting `plot_cmap` on a `pca_analysis` call would reasonably
expect it to affect `create_pca_biplot`'s scatter coloring the same way it affects UMAP's, and
silently doing nothing is a confusing trap. Omitting the fields entirely means an unsupported
kwarg is a normal Pydantic "unknown field" error (or simply not offered in the tool schema)
instead of a silent no-op. If a future `sleap-roots-analyze` release adds `cmap`/`point_size`
to a PCA plotter, that's a small follow-up mirroring this one's `plot_alpha` wiring — not a
reason to speculatively add the fields now.

### Field bounds match #661's precedent

- `plot_alpha: float | None = Field(default=None, ge=0.0, le=1.0)` — matplotlib's `alpha`
  range is a hard domain constraint (0=fully transparent, 1=fully opaque; values outside are
  either clamped or raise deep inside rendering), same rationale as `PCAAnalysisParams.
  explained_variance_threshold`'s existing `ge=0.0, le=1.0`.
- `plot_point_size: float | None = Field(default=None, gt=0)` — a non-positive marker size is
  always degenerate (invisible or an error), the same *shape* of bound as #661's sibling
  `plot_font_size: gt=0` field. Unlike `UMAPAnalysisParams.n_components` (`le=_MAX_N_COMPONENTS`),
  no upper bound is added: `n_components`'s ceiling exists specifically because an
  unbounded value risks the OS OOM-killer (a huge embedding dimensionality multiplies memory
  across the whole UMAP computation). An arbitrarily large `point_size` has no analogous
  resource-exhaustion path — matplotlib just draws bigger, overlapping circles on a
  fixed-size canvas — so a defensive ceiling here would be guarding against a purely
  cosmetic, self-correcting mistake, not a stability risk.
- `plot_cmap: str | None = None` — no format validation, the same rationale as #661's
  `font_family`: `matplotlib.cm.get_cmap` raises its own `ValueError` for an unknown colormap
  name at draw time; re-validating the colormap registry here would duplicate matplotlib's
  own check and could drift from the installed matplotlib version's registry.
- `plot_point_size: float | None` intentionally widens upstream's `point_size: int = 30` type
  hint to `float`, for finer marker-size control (e.g. `12.5`); Python does not enforce the
  upstream hint at runtime and `ax.scatter(s=...)` accepts a float natively, so this is a
  strict superset of the upstream contract, not a mismatch.
- `create_umap_single_trait`'s `cmap` only affects the continuous-trait scatter; its
  `color_by`-set two-subplot branch colors by discrete RGBA tuples instead of a colormap. This
  is moot for this change either way: `_umap_plot_calls` never passes `color_by` to
  `create_umap_single_trait` today, so only the `cmap`-driven branch is ever reached.
  `create_pca_biplot` always passes `color_by=frame.genotype_col`, but only `plot_alpha`
  reaches it (not `plot_cmap`), and `alpha` (scatter-point transparency) behaves identically
  regardless of which coloring branch is active.

### Validation fires unconditionally; only *valid* values are ignored when `include_plots=False`

Two different things are both true and don't contradict each other:

1. An **out-of-range** value (`plot_alpha=1.5`, `plot_point_size=0` or negative) is a Pydantic
   `Field(gt=..., le=...)` constraint on `UMAPAnalysisParams`/`PCAAnalysisParams` themselves —
   it is rejected as `invalid_input` at model-construction time, **before** the tool body (and
   therefore before `include_plots` is ever examined). This happens regardless of
   `include_plots`'s value.
2. A **valid** style value (`plot_cmap="plasma"`, `plot_alpha=0.4`, etc.) set alongside
   `include_plots=False` is not rejected — it's simply never read, because the tool body never
   reaches `_umap_plot_calls`/`_pca_plot_calls` on that path. Mirrors the existing
   `plots`-with-`include_plots=False` ignore policy and #661's sibling font-field ignore
   policy: a caller with `plot_cmap` set in a reusable request template that also happens to
   set `plots=["create_umap_colored_by_top_traits"]` (which ignores `plot_cmap`) is not an
   error — consistent with these tools' general "extra style intent that doesn't apply to the
   current request is a no-op, not a rejection" policy, which only ever applies to values that
   passed field validation in the first place.

### Testing: the plotters are not module attributes of the tool files

`create_umap_single_trait`/`create_umap_colored_by_top_traits` and all four PCA plotters are
imported **inside** `_umap_plot_calls`/`_pca_plot_calls` (`from sleap_roots_analyze import
...`, function-local — see each file's existing docstring on why: avoiding a second redundant
import / preserving the Tier-0 import-clean guarantee). Because of that, `umap_analysis_tool`/
`pca_analysis_tool` never hold `create_umap_single_trait` etc. as a module attribute — a test
that does `monkeypatch.setattr(umap_analysis_tool, "create_umap_single_trait", fake)` patches
an attribute that doesn't exist there and is silently never consulted (the local `from ...
import` re-resolves the name from the `sleap_roots_analyze` package on every call). The
correct interception point is the actual defining module: `sleap_roots_analyze` itself
re-exports every catalog plotter at its top level (confirmed against
`sleap_roots_analyze/__init__.py`), so tests must patch
`monkeypatch.setattr(sleap_roots_analyze, "create_umap_single_trait", spy)` (import
`sleap_roots_analyze` in the test module), with `spy` wrapping-and-delegating to the real
function and recording `kwargs` — the same shape as this test suite's existing
`perform_umap_analysis`/`perform_pca_analysis` spies.

### `_umap_plot_calls`/`_pca_plot_calls` gain new keyword-only parameters — two existing tests
### must be updated in lockstep

Both `test_umap_analysis_tool.py::test_figure_cleanup_get_fignums_empty_on_partial_plotter_failure`
and the identically-named test in `test_pca_analysis_tool.py` replace
`_umap_plot_calls`/`_pca_plot_calls` wholesale with a fixed-arity `_patched(result_dict, frame,
trait_cols)` / `_patched(result_dict, pca, frame, threshold)` wrapper that calls through to the
real function with exactly those positional args. Once the tool's call site passes the new
`plot_cmap`/`plot_point_size`/`plot_alpha` keyword args, those two `_patched` wrappers must
accept and forward `**kwargs` too, or they raise `TypeError: unexpected keyword argument` the
moment the patched call site passes them — an existing, currently-green test would start
failing for a reason unrelated to what it's actually testing (figure cleanup on partial
failure). Tracked as its own task in `tasks.md` rather than left implicit.

## Risks / Trade-offs

- **Asymmetric API surface** (`UMAPAnalysisParams` gets 3 fields, `PCAAnalysisParams` gets 1)
  could look inconsistent at a glance. Mitigated by documenting the per-plotter support table
  in `proposal.md` and in each field's description, and by treating this as accurately
  reflecting today's real upstream capability rather than a bloommcp design inconsistency.
- **Future upstream drift**: if a `sleap-roots-analyze` release changes which plotters accept
  these kwargs, this wiring goes stale silently (no test failure) until someone notices. Same
  risk already accepted for #661's font wiring and the existing hardcoded plot calls generally
  — not new to this change.

## Migration Plan

Additive only — no rollback complexity. Both tools' new fields default to `None`; existing
callers are unaffected.
