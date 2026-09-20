## Context

`clustering` (#601, PR #668) reused `bloom_mcp.tools._plots` verbatim for its
`include_plots`/`plots` support — `validate_plot_keys`, `generate_figures`, `close_figures`.
`generate_figures` had already grown `font_family`/`font_size` kwargs in #661, and
`apply_font_style` was already tool-agnostic by construction. So the styling seam was live in
`clustering` from the day its plots landed; the only thing missing was two fields on
`ClusteringParams` and two kwargs at the call site.

This change is therefore not a design problem. What *does* need recording is the audit (#680's
second deliverable) — which other plot tools have the same gap, which are blocked upstream,
which are tracked elsewhere — plus three things the audit turned up that the issue never
anticipated: the error-precedence consequences of inserting a new early guard, what a
provenance record does and does not tell a reader about a styled figure, and two pre-existing
defects in the plots this tool persists.

## Goals / Non-Goals

- **Goal:** `clustering` accepts `plot_font_family`/`plot_font_size` with behavior
  indistinguishable from `pca_analysis`/`umap_analysis` — same field names, same descriptions,
  same ceiling, same schema metadata, same ignore-when-`include_plots=False` policy.
- **Goal:** record the audit with its verdicts, reasons, and the defects it exposed.
- **Non-Goal:** any *behavior* change to `bloom_mcp/tools/_plots.py`. If this change needs one,
  the premise ("the mechanism is already there") is wrong and the design should be revisited.
- **Non-Goal:** `plot_cmap`/`plot_point_size`/`plot_alpha` for clustering — blocked upstream.
- **Non-Goal:** `heritability_analysis`, `qc_inspect`, `remove_outliers`, and the three #466
  plot tools.
- **Non-Goal:** fixing either pre-existing defect recorded under "Defects the audit exposed".

## Decisions

**Decision: add the two fields to `ClusteringParams` by mirroring `PCAAnalysisParams`, not by
inventing a shared mixin.**
`pca_analysis` and `umap_analysis` already declare these fields independently, with
tool-specific description text. A third independent copy keeps all three symmetric and keeps
the diff reviewable against its siblings.
*Alternative considered:* a `PlotStyleParams` mixin the three models inherit. Rejected for now —
it would rewrite two working tools to add a third, and a mixin is a better fit once a fourth
caller exists. Worth revisiting when `heritability_analysis` is wired up (its D9/10.2), which
would make three callers into four.

**Decision: validate `plot_font_size` in the tool body via `check_plot_style_ceiling`, before
any I/O — but second in the body, not first.**
The *mechanism* is copied from `pca_analysis`: a Pydantic `Field(gt=0, le=...)` violation is
flattened by `BloomMCPError.from_input_validation` into a message naming only the field and the
error type — never the value or the ceiling — and the field is derived purely from the request,
so there is no reason to pay for a full experiment read before rejecting it. The
`json_schema_extra={"exclusiveMinimum": 0, "maximum": MAX_PLOT_FONT_SIZE}` shim keeps the bound
discoverable despite not being a `Field` constraint.

The *placement* deliberately diverges. `pca_analysis.py:304` puts its check first in the body;
`clustering.py:439` already opens with `_reject_wrong_method_controls(params)`, and the ceiling
check goes after it. Two error-precedence consequences follow, both observable, both specified
and tested rather than left to fall out:

1. **Method-control conflict beats the ceiling.** A request wrong on both axes reports the
   cross-method control — the more structural error, and the one existing tests pin.
2. **The ceiling beats plot-key validation.** `validate_plot_keys` runs at `clustering.py:640`,
   *after* `reader.load_experiment` at `:448`. Inserting the ceiling check at `:440` therefore
   flips which error a caller sees for `plots=["bogus"] + plot_font_size=101` (today: the bad
   key; after: the ceiling). This is the right way round — the ceiling check needs no data read
   and the key check does — but it is a contract change, not a no-op, so it gets a requirement
   and a test.

Note that review gate 4.1 ("the two fields match their PCA counterparts") covers the *fields*,
not this call-site placement, which is the one place the change is deliberately asymmetric.

**Decision: `plot_cmap`/`plot_point_size`/`plot_alpha` stay out, because the upstream plotters
cannot accept them.**
Verified directly against the installed `sleap_roots_analyze.cluster_visualization` rather than
inferred from the #662 pattern: `create_cluster_scatter_pca(cluster_result, pca_result,
highlight_indices, figsize, title)` and `create_cluster_size_barplot(cluster_labels, n_clusters,
figsize)` expose no cmap, marker-size, or alpha parameter, and hardcode `plt.cm.tab10`,
`alpha=0.6, s=50` (scatter) and `alpha=0.7` (bars) internally. That signature gap is the whole
reason, and it has to be closed upstream.

**It is *not* because a categorical palette is a settled question.** An earlier draft of this
design argued that `tab10` is "the correct qualitative default" and that only UMAP's
*continuous* trait colouring makes `plot_cmap` meaningful. The second half is right — a
sequential or diverging colormap over categorical cluster identity would actively mislead, and
`umap_analysis`'s allowlist (`umap_analysis.py:113-127`) is correctly scoped to that continuous
case. The first half is false: `tab10` contains `#d62728` and `#2ca02c`, the canonical
deuteranope-confusable red/green pair, and is not colourblind-safe. Colourblind-safe qualitative
palettes (Okabe–Ito, Paul Tol, ColorBrewer Dark2/Set2) are a standard journal requirement, and
for a change whose stated motivation is papers and posters, "you may not change the categorical
palette" is exactly the wrong thing to enshrine. A future `plot_qualitative_palette` with a
CVD-safe allowlist is legitimate and should be scoped against upstream — it is out of scope
here for the signature reason, not because the need isn't real.
*Alternative considered:* post-process the returned `Figure` the way `apply_font_style` does —
walk `ax.collections` and reassign facecolors/sizes. Rejected: font styling is a uniform,
semantically-neutral transform over text objects, while recolouring marks means reconstructing
the plotter's own category→colour mapping from the outside, and would silently break the moment
upstream changes how it draws.

**Decision: provenance records the *requested* style, not the *rendered* one — and the spec says
so.**
`@as_mcp_tool` stamps `Provenance.stamp(params=data.model_dump())`, so both fields land in the
run manifest automatically, for every run. Good — two runs with identical provenance cannot
produce differently-styled PNGs. But two spec clauses mandate silent no-ops: fields are ignored
when `include_plots=False`, and an unresolvable `plot_font_family` is not rejected (matplotlib
reports `findfont: … not found` through `logging`, not `warnings`, and falls back at render).
In both cases the requested value is still recorded. A manifest can therefore say
`plot_font_family: "Helvetica Neue"` for a PNG drawn entirely in DejaVu Sans.

This is worth naming because `clustering.py:250-260` already argues the opposite principle for
method controls: *"it would still land in `provenance.params`, so the recorded run would claim a
control that had no effect."* The principle does not extend here, for a reason specific to
fonts: family resolution happens at render time against the host's installed font set, so **no
tool-body check can be authoritative** — a family valid on the developer's machine may be
missing in the container. Rejecting would also break the three-tool symmetry this whole change
rests on. So the fields stay permissive, and the spec states outright that provenance records
the requested family, not the rendered one, so a reader reproducing a figure knows which it is
holding.

**Decision: `heritability_analysis` is reported, not fixed — and it needs no new issue.**
It has exactly `clustering`'s gap (`heritability_analysis.py:609` calls `generate_figures` with
no font kwargs; no `plot_font_*` fields). It is **already tracked**, as deferral **D9** and
follow-up **10.2** of `add-bloommcp-heritability-analysis-tool`, whose D9 records the same
reasoning this change applies to itself: "neither the issue nor #661 asks for it here … adding a
third would widen an already-breaking change for no requested benefit." #680 predates the tool,
so the audit records the gap; the earlier claim that it was an untracked new finding was wrong.
Not bundled here because #680's "Scope (this issue)" names `ClusteringParams` only.

**Decision: `qc_inspect`/`remove_outliers`/the three #466 plot tools stay out, on a restated
reason.**
#680 said these "don't route through `_plots.py` at all." No longer accurate — #721/#726
converged all five onto `call_with_figure_cleanup`, which lives in `_plots.py`. The conclusion
survives on a narrower fact: they use `_plots.py` only for figure-registry cleanup and still
`savefig()` inline, so `generate_figures` — and therefore `apply_font_style` — never sees their
figures. Giving them font styling means first converging them onto `generate_figures`, which
changes how each stages and persists its outputs: a refactor, not two fields.

Note this retires a commitment #661 made. #661 deferred the three plot tools explicitly "until
#466 converges them onto the same pattern, to avoid duplicate work." #466 has landed — but it
converged them onto `@as_mcp_tool`/Pydantic, a different axis, leaving the `generate_figures`
seam untouched. So #661's precondition has fired while its goal has not, and nothing tracks the
remainder. Recorded in `bloommcp/docs/roadmap.md` by this change, since `proposal.md` and this
file both archive.

## Risks / Trade-offs

- **Three independent copies of the same two fields can drift.** → The shared ceiling constant
  (`MAX_PLOT_FONT_SIZE`) and the shared checker (`check_plot_style_ceiling`) are single-sourced
  in `_plots.py`, so the part that *matters* cannot desync; only description prose can, and that
  is per-tool by design. The JSON-schema test pins the constant at the tool boundary.
- **A large-but-accepted `plot_font_size` degrades the persisted artifact, silently.** The
  ceiling of 100 is a resource-exhaustion guard (`_plots.py:25-31` is explicit about that), not
  a legibility bound, and measurement confirms the two are far apart. On a real 3-cluster,
  150-point scatter at the upstream default `figsize=(10, 8)`: at 22pt the legend covers 3.2% of
  the axes and nothing is occluded; at 60pt it covers 17.6%, occludes 4 points, and the y-axis
  label falls outside the `bbox_inches="tight"` region; at 100pt it covers 44.8% and hides 72 of
  150 points. The barplot's per-bar value labels — which *are* data — collide with the title at
  100pt. The underlying data is untouched (axes position, `xlim`/`ylim`, tick *locations* and
  `collections.get_offsets()` are bit-identical before and after `apply_font_style`, and
  `bbox_inches="tight"` expands rather than crops), but the *rendered artifact* loses content.
  → **Not mitigated here, deliberately.** `pca_analysis`/`umap_analysis` have accepted exactly
  this since #661 with the same ceiling; adding a clustering-only advisory (via the
  `ClusteringResult.warnings` field, which does exist and is used for GMM collapse and
  hierarchical seed) would make clustering behave differently from its siblings — the one thing
  this change is built to avoid — and lowering `MAX_PLOT_FONT_SIZE` would desync the three
  tools. The right fix is one advisory applied to all three at once. Measured numbers recorded
  here so that follow-up has a starting point rather than re-measuring.
- **The "no `_plots.py` change" claim is load-bearing and easy to violate quietly.** → Task 4.3
  makes it an explicit review step.
- **Tick-label styling depends on upstream keeping its tick locators fixed.** `apply_font_style`
  sets fontsize on the `Text` objects `get_xticklabels()` returns, which matplotlib can discard
  if an axis regenerates its `Tick` artists at draw. Verified to hold after `savefig` at 22pt and
  100pt for both catalog plots — the barplot pins a `FixedLocator` via `ax.set_xticks(...)`
  (`cluster_visualization.py:239`) and the scatter's limits are frozen before styling, so
  `AutoLocator` returns the same tick count. Both conditions are upstream's to change. → If an
  upstream bump ever breaks the spec's "tick labels" scenario, this is why.
- **`apply_font_style` walks `fig.texts` and per-`Axes` legends, but never `fig.legends`.** A
  figure-level `fig.legend()` would be missed. Neither cluster plotter creates one (verified: all
  23 visible `Text` artists on the scatter and all 19 on the barplot are reached, pre- and
  post-`savefig`), so the spec's claim holds for clustering. → Noted so the next caller of
  `generate_figures` doesn't read `_plots.py`'s "every text element" as unconditional.

## Defects the audit exposed (pre-existing, not fixed here)

Both are #601-era, both are in the figures this tool persists, and neither is caused by this
change. Recorded because #680 asked for an audit and an audit that finds these and says nothing
is worth less than one that does. Neither has a tracking issue as of 2026-09-21.

- **Upstream: clusters above 10 collapse onto duplicate colours.** `cluster_visualization.py:89`
  and `:215` both do `plt.cm.tab10(np.linspace(0, 1, n_clusters))`, which yields 10 distinct
  colours regardless of `n_clusters` — measured 10 for 12 and 10 for 15. `ClusteringParams`'s
  `n_clusters`/`max_clusters` are `ge=2` with no upper bound. So a run can commit a scatter and
  barplot drawing two different clusters in the same colour, with nothing in `outputs`,
  `warnings`, or the manifest saying so. Belongs upstream in `sleap-roots-analyze`.
- **`_compute_scatter_pca` can persist a figure containing no data.** `clustering.py:369` calls
  `perform_pca_analysis(frame.df[trait_cols], standardize=...)` with no `n_components`; upstream
  defaults to `explained_variance_threshold=0.95`, so when PC1 alone clears 95% the projection is
  `(n, 1)` and `create_cluster_scatter_pca` takes its early-return branch
  (`cluster_visualization.py:75-85`), producing a figure whose only content is the string "Need
  at least 2 PCA components for visualization". Measured: a well-separated 3-cluster fit
  (silhouette 0.73) with PC1 at 0.954 produces exactly this. The placeholder PNG is committed
  under the `create_cluster_scatter_pca` output key and hashed into `output_sha256`,
  indistinguishable in the manifest from a real scatter — and the better-separated the clusters
  are along one direction, the likelier it fires, which is the normal case for correlated root
  traits. Fix is plausibly one kwarg (`n_components=2`) plus a `warnings` entry when the
  certified selection genuinely cannot support two components. A bug, not an enhancement.

## Open Questions

- Should `heritability_analysis` get the same two fields (its D9/10.2), and if so should all four
  tools move to a shared `PlotStyleParams` mixin at that point?
- Should the large-font legibility advisory be added to all three tools at once, as the Risks
  section argues?
