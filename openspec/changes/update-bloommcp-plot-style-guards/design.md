## Context

UMAP/PCA plot-styling fields (`plot_font_size`, `plot_point_size`, `plot_cmap`,
`plot_alpha`) were added across two prior changes (#661, #662), both merged and fully
implemented but not yet archived. GitHub issue #721 — filed while reviewing #720, a
cherry-pick of #666 onto `staging` — found five related gaps in these fields: two missing
numeric ceilings, one missing string validation, one semantic colormap-category gap, and one
figure-cleanup gap the first three combine to expose. This design covers the four
code-level decisions needed to close them.

## Goals / Non-Goals

- Goals: bound `plot_font_size`/`plot_point_size` to values that can't cause multi-second,
  multi-GB renders; reject an invalid `plot_cmap` before any UMAP computation runs, with a
  message that tells the caller what went wrong; restrict `plot_cmap` to colormaps that
  render continuous trait data faithfully; make figure cleanup survive a plotter that
  allocates a figure and then raises.
- Non-Goals: a general per-tool render timeout or memory limit (the issue explicitly frames
  these as follow-ups, "not urgent," not a request for backpressure infrastructure); adding
  `plot_cmap`/`plot_point_size` to PCA (no PCA catalog plotter accepts either kwarg —
  established by `add-bloommcp-plot-style-kwargs`); revisiting `plot_font_family`'s
  unvalidated free-text (out of scope for #721); a matplotlib-registry-derived colormap
  category (matplotlib exposes no such categorization at runtime — see Decision 3).
- **Also Non-Goals, added to issue #721 as a follow-up comment after this change's proposal
  was scaffolded** (findings 6-8, posted 2026-08-22, a day before this change's
  implementation commits): (6) `create_umap_colored_by_top_traits` hardcodes its own
  `cmap="viridis", s=30, alpha=0.7` and never receives any of `plot_cmap`/`plot_point_size`/
  `plot_alpha`, so requesting both UMAP catalog plots together can produce two figures styled
  inconsistently with each other — documented in `plot_cmap`/`plot_point_size`'s field
  descriptions (this change) but not fixed, since fixing it means either wiring new kwargs
  into `create_umap_colored_by_top_traits` upstream in `sleap_roots_analyze` (not a
  `bloommcp`-only change) or dropping the styling on the other plot instead; (7) `plot_alpha`
  near (but not at) `0.0` can render a lone point invisible against a dense cluster's haze,
  producing a plot that silently looks like "no outliers" — `plot_alpha` is untouched by this
  change (it already shipped in `add-bloommcp-plot-style-kwargs`, is not part of #721's
  original five findings, and a fix here would mean judging a _scientifically_ correct
  minimum alpha, not a resource-exhaustion ceiling like Decisions 1-2); (8) no test pins that
  style fields are persisted with the run record, so a future refactor could silently drop
  them from provenance with nothing failing. All three are real and worth a dedicated
  follow-up change — deliberately not folded into this one, which was already scoped,
  reviewed, and approved against the original five findings before comment 6-8 was posted.

## Decisions

**Decision 1 — `plot_font_size` ceiling: `le=100` (both tools).**
Issue data: `1000` already costs ~1s (UMAP) to ~12s/2.6GB (PCA); `4000` costs 15s/4GB. `100`
is 10x below the cheapest measured slow value and comfortably above any real plot-text size
(readable figure text is usually 6-72pt). Plain `Field(gt=0, le=100)` — no new validator, no
change to the existing `gt=0` check or its `invalid_input` mapping.

**Decision 2 — `plot_point_size` ceiling: `le=10000` (UMAP only — PCA has no such field).**
Issue data: cost is flat until ~1e8; trouble starts around 1e24. `10000` is 4 orders of
magnitude below the danger zone and far above any real marker size (typical scatter markers
are 1-500 in matplotlib's `s` units). Same `Field(gt=0, le=10000)` mechanism as Decision 1.

**Decision 3 — `plot_cmap` allowlist, checked in the tool body (not a Pydantic validator).**
`plot_cmap` needs a message that names the bad value — the whole point of the fix is that
today's matplotlib `ValueError` (which already does this) never reaches the caller. This
codebase already documents, with an empirically-verified reason, why that check cannot be a
`@model_validator`/`@field_validator`: `qc_clean.py:204-212` records that a validator's
raised `ValueError` is remapped by the contract layer's `from_input_validation` into a
generic `"(<root>: value_error)"` message — **the author's own message text is discarded**.
So `plot_cmap` is checked in `umap_analysis`'s tool body, in the same place as the existing
`n_neighbors >= n_samples` check (`umap_analysis.py:367-381`), raising
`BloomMCPError(code="invalid_input", ...)` directly, before `perform_umap_analysis` is
called. `plot_cmap=None` (the default) skips the check entirely — unchanged behavior when
the field isn't set.

The allowlist itself is a module-level frozenset, hand-authored from matplotlib's own
colormap-reference documentation (Perceptually Uniform Sequential, Sequential, Sequential
(2), and Diverging categories) — verified against the installed matplotlib 3.10.8,
`matplotlib.colormaps` exposes no such categorization at runtime; it is a flat name registry
with no sequential/diverging/qualitative metadata, so there is nothing to introspect
instead. Each base name's `_r` reversed variant is included too — reversing a
sequential/diverging colormap keeps it perceptually valid for continuous data; only the
direction changes. This hand-authored list can drift from a future matplotlib version's
registry (a new colormap matplotlib adds won't be recognized until this list is updated) —
accepted the same way `add-bloommcp-plot-style-kwargs/design.md` already accepted
registry-drift risk for the (now-superseded) "no validation" choice, just shifted from
"silent drift" to "a legitimate new colormap is rejected until this list catches up," which
is the safer direction to drift in.

**Decision 4 — the figure-cleanup fix lives in the shared `_plots.generate_figures`, not
per-tool.**
Today's `generate_figures` (`_plots.py:122-124`) does `figures[key] = fn()` — if `fn()`
raises after internally calling `plt.subplots()`/`plt.figure()` (exactly what the vendored
`create_umap_single_trait` does: allocates at `visualization.py:2767`, raises from
`ax.scatter(cmap=...)` at `visualization.py:2778`), the new figure is never assigned into
`figures`, so `close_figures` — which only iterates that dict — never sees it. This is a
real leak, reproduced empirically in this investigation: `plt.get_fignums()` still shows the
figure after the `ValueError`. The fix: snapshot `plt.get_fignums()` immediately before
calling each `fn()`; if `fn()` raises, diff `plt.get_fignums()` against the snapshot and
`plt.close()` every newly-created figure number before re-raising. Fixed once in the shared
helper (not duplicated in UMAP/PCA) so it also covers any future plotter that
allocates-then-raises for an unrelated reason — the existing tests' `_boom`-style stand-ins
(`test_umap_analysis_tool.py:1017`, `test_pca_analysis_tool.py:802`,
`test_plots_helpers.py:96`) never exercised this path because none of them allocate a figure
before raising; this change adds one that does.

**Decision 5 — `generate_figures` serializes across concurrent calls via a process-wide
lock (added after PR review).** Decision 4's fignum-diffing cleanup is only safe if no
_other_ call is concurrently mutating the same global registry while the diff is computed.
That assumption was wrong, not hypothetical: matplotlib's pyplot figure registry
(`matplotlib._pylab_helpers.Gcf.figs`) is a single class-level `OrderedDict`, shared by the
whole process regardless of thread — and FastMCP dispatches sync tool handlers via a thread
pool (`bloom_mcp/result_store/_locks.py`'s module docstring documents the same fact, for the
same reason: two `ResultStore.commit` calls for the same key can race today). Two concurrent
`umap_analysis`/`pca_analysis` calls therefore really can both reach `generate_figures` at
the same time, and one call's allocate-then-raise cleanup could close a figure a
_different_, unrelated concurrent call had just allocated — silently corrupting or blanking
that other call's plot with no error surfaced to it. Fixed with a single
`FIGURE_REGISTRY_LOCK` (`threading.Lock`) held for the entire `generate_figures` call, not
just the diff-and-close step — serializing all plot generation across the process. The
alternative fix (have the plotters construct `matplotlib.figure.Figure()` directly, bypassing
the shared registry entirely) isn't available from within `bloommcp`: the plotters that call
`plt.subplots()`/`plt.figure()` live in the vendored, third-party `sleap_roots_analyze`
package.

**Decision 5b — the same lock is not private to `generate_figures`; every other
matplotlib-figure-creating call site in `bloommcp` acquires it too (added after a second PR
review round).** `generate_figures`'s cleanup can be confused by _any_ figure that appears
in the shared registry during its locked window, not just one from another
`umap_analysis`/`pca_analysis` call. `qc_inspect.py`'s `_render_report`, `remove_outliers.py`'s
`_make_figures`, `clustering.py`, and each of the 5 legacy `plot_*` tools
(`plot_trait_boxplots.py`, `plot_correlation_matrix.py`, `plot_heritability_bar.py`,
`plot_variance_decomposition.py`, `plot_trait_histograms.py`) all create figures against
that same registry, independently of `generate_figures`, and none of them previously
synchronized with it at all — `qc_inspect.py` even carried its own pre-existing comment
naming a "single-writer assumption" for the shared registry, without ever enforcing it. A
`umap_analysis`/`pca_analysis` failure could therefore have silently corrupted or blanked a
concurrent, unrelated tool call's plot — the exact failure mode Decision 5 claims to close,
just via an uncovered caller. `FIGURE_REGISTRY_LOCK` is a plain `threading.Lock`
(non-reentrant) — documented on the lock itself, since nothing today transitively re-enters
it, but a future plotter that did would deadlock.

**Decision 5e — the shared cleanup logic was extracted into
`bloom_mcp.tools._plots.call_with_figure_cleanup` (added after a third PR review round),
replacing both `generate_figures`'s own inline lock-and-diff loop and every one of the 8
other call sites' ad hoc `with FIGURE_REGISTRY_LOCK: fig = <delegate>()`.** Two problems
surfaced with the ad hoc version this decision fixes:

1. Every one of those 8 sites wraps its delegate call in its _own_ `except Exception:
return "<message>"` (best-effort, pre-existing, predates this PR) — which, without
   `generate_figures`'s allocate-then-raise cleanup, silently swallowed the exception
   without closing whatever the delegate had already allocated mid-render. This was a
   real, currently-shipping leak at 7 of those 8 sites (everywhere except
   `remove_outliers.py`, which has no such swallow), not merely a documentation gap
   about the lock's own comment overstating "closes by direct object reference" safety
   — that claim is only true if the delegate never raises after partially rendering,
   and several of these delegates (batched multi-page plotters especially) can.
2. `generate_figures` held `FIGURE_REGISTRY_LOCK` across its _entire_ multi-plot loop,
   not per key — coarser than strictly necessary, since each key's own diff only cares
   about figures created during that one key's `fn()` call.

`call_with_figure_cleanup(fn)` is the single, tested implementation of "call `fn` under
`FIGURE_REGISTRY_LOCK`; on exception, close any figure(s) newly registered since before
the call, then re-raise" — `generate_figures` now calls it once per key inside its loop
(fixing the coarser-than-necessary lock scope as a side effect), and every one of the
other 8 call sites calls it directly around its own delegate call instead of the old
`with FIGURE_REGISTRY_LOCK:` block, which both plugs each site's leak for real and
reduces the amount of test surface needing direct coverage to one shared helper (tested
thoroughly once in `test_plots_helpers.py`) plus one targeted "allocate-then-raise, still
closed" regression test per call site (matching the precedent every existing shared
`_plots.py` helper — `validate_plot_keys`, `apply_font_style` — already followed: tested
once, centrally, not re-tested bespoke at each call site).

**Decision 5c — the internal PCA fit for `create_umap_colored_by_top_traits` moved outside
`generate_figures`'s lock window (added after the same review round).** That fit
(`perform_pca_analysis`) previously ran lazily inside the plot callable itself — i.e. while
`FIGURE_REGISTRY_LOCK` was held — needlessly serializing every other concurrent
figure-creating call for the duration of an unrelated PCA fit, not just the actual
matplotlib rendering call. `umap_analysis`'s tool body now computes it eagerly via a new
`_compute_top_traits_pca` helper, before `_umap_plot_calls`/`generate_figures` run, and only
when `create_umap_colored_by_top_traits` is actually in the requested `keys_to_generate` (so
no wasted work when it isn't requested). `_umap_plot_calls` takes the already-computed
result as `top_traits_pca_result_dict` instead of computing it itself; the callable it
returns for that key now only ever does matplotlib rendering.

**Decision 5d — the numeric ceiling is still discoverable via the tool's JSON schema
(added after the same review round), via `json_schema_extra` rather than `Field(le=...)`.**
Decision 6 removed the `Field` constraint entirely to get an informative rejection message,
but that also silently dropped `maximum`/`exclusiveMinimum` from the auto-generated JSON
schema — a schema-reading caller could no longer discover the ceiling without triggering a
rejection first, and `plot_alpha` (untouched, still a real `Field(ge=0.0, le=1.0)`
constraint) became inconsistent with the two fields this change touched. Restored via
`Field(json_schema_extra={"exclusiveMinimum": 0, "maximum": MAX_PLOT_FONT_SIZE})` (and the
`plot_point_size` analog) — `json_schema_extra` injects raw JSON Schema keywords into
`model_json_schema()`'s output without Pydantic enforcing them, so the schema documents the
bound while `check_plot_style_ceiling` remains the sole enforcement path.

**Decision 5f — `clustering.py`'s `create_cluster_scatter_pca` had the identical
lazy-PCA-fit-inside-the-plot-callable bug Decision 5c fixed for UMAP, and was missed in that
pass (added after a third PR review round).** `clustering.py`'s `_scatter_pca` closure ran
its own internal, non-persisted `perform_pca_analysis` call lazily, the same anti-pattern
`_compute_top_traits_pca` was extracted out of `umap_analysis.py` to avoid — and
`clustering.py` shares `generate_figures`, so this PCA fit was running inside
`FIGURE_REGISTRY_LOCK`'s window too, an audit gap (searched for existing lock-adjacent call
sites rather than checking every consumer of `generate_figures`) rather than a deliberate
scope cut. Fixed identically: a new `_compute_scatter_pca` helper, called eagerly by the
tool body before `_clustering_plot_calls`/`generate_figures`, only when
`create_cluster_scatter_pca` is actually requested.

**Decision 5g — `plot_cmap`'s length cap moved from a Pydantic `Field(max_length=...)`
constraint into the tool body (added after the same review round), for the same reason
Decision 6 moved the numeric ceilings.** Shipping `max_length=32` as a `Field` constraint
(as this change originally did, in response to the previous round's suggestion) produced
the one remaining opaque-message inconsistency: every other `plot_cmap`/ceiling rejection in
this change names the submitted value, but an over-length `plot_cmap` would have raised
Pydantic's generic `ValidationError` instead. Checked in the tool body now, before the
allowlist membership check (a cheap early-exit that also avoids hashing an arbitrarily long
string just to look it up in `_ALLOWED_CMAPS`), naming both the actual length and the limit.
The cap is still discoverable via `json_schema_extra={"maxLength": ...}`, matching Decision
5d's pattern for the numeric fields.

**Decision 6 — `plot_font_size`/`plot_point_size` ceilings checked in the tool body too
(added after PR review), not as `Field(gt=0, le=...)` constraints.** Decision 3's rationale
for `plot_cmap` — a `BloomMCPError.from_input_validation` mapping surfaces only the field
name + error type, never the submitted value or the ceiling — applies identically to a bare
numeric `Field` constraint. Shipping `le=100`/`le=10000` as Field constraints (as this
change originally did) produced the exact opaque message this PR set out to eliminate, just
for two different fields. Both ceilings moved into each tool's body via a new shared
`bloom_mcp.tools._plots.check_plot_style_ceiling` helper (NaN-safe: `not (0 < nan <= max)` is
`True`, matching Pydantic's own NaN rejection), checked immediately alongside the
`plot_cmap` allowlist check — before `reader.load_experiment` — since all three fields are
derived purely from the request, with no reason to pay for a full experiment read (or the
`np.isfinite` scan over it) before rejecting a bad one. The ceiling values themselves
(`MAX_PLOT_FONT_SIZE`, `MAX_PLOT_POINT_SIZE`) now live in `_plots.py`, imported by both
`umap_analysis.py` and `pca_analysis.py`, rather than duplicated as a local constant in each
— the same duplication-desync risk Decision 3's colormap allowlist already had to avoid.

## Risks / Trade-offs

- **Narrow breaking change**: an existing caller relying on `plot_font_size > 100`,
  `plot_point_size > 10000`, or an excluded `plot_cmap` (e.g. `hsv`, `tab10`) gets
  `invalid_input` where it previously succeeded. Accepted: the issue's own cost data shows
  nothing in that range was usably fast, and nothing outside the sequential/diverging
  allowlist renders continuous trait data correctly.
- **Hand-authored colormap list can go stale** → see Decision 3; mitigated by keeping the
  list a single, easily-editable module-level constant near `_MAX_N_COMPONENTS`'s existing
  pattern, plus a dedicated test (`test_allowed_cmaps_exist_at_the_declared_dependency_floor`)
  checking new entries against a separately hand-maintained "known good at the declared
  `pyproject.toml` floor" snapshot — added after PR review found the _installed_-matplotlib
  regression test alone couldn't have caught this PR's own `berlin`/`managua`/`vanimo`
  floor-mismatch bug, since the locked matplotlib (3.10.8) is newer than the floor
  (`>=3.7.0`) and already has those three names.
- **Concurrent plot generation is now fully serialized process-wide** (Decisions 5/5b/5e) —
  a correctness-over-throughput trade-off, covering not just `umap_analysis`/`pca_analysis`
  but every matplotlib-figure-creating call in `bloommcp`. Genuinely flagged as worth
  watching, not dismissed as purely theoretical: plotting is the feature researchers reach
  for specifically to look at their results, so "opt-in" (`include_plots=True`) doesn't
  obviously mean "rare" the way a true edge-case flag would. Narrowed twice since first
  introduced: Decision 5c/5f move the internal PCA fits (UMAP's
  `create_umap_colored_by_top_traits`, clustering's `create_cluster_scatter_pca`) outside
  the lock entirely; Decision 5e's `call_with_figure_cleanup` extraction narrows
  `generate_figures`'s hold from its whole multi-plot loop to one lock acquisition per key,
  and every other call site locks only its own figure-creating delegate call, not a full
  save/persist span. The lock itself remains unbounded and timeout-free — a caller
  rendering a genuinely large/slow figure still blocks every other concurrent
  figure-creating call server-wide for that render's real duration, not just a brief
  registry-bookkeeping instant. Accepted for now; worth a profiling follow-up against real
  plot-generation latencies (not just this design's own reasoning about the ceilings) if
  concurrent plotting ever becomes a reported pain point, rather than assuming today's
  "not expected to be a hot path" holds indefinitely.
- **Ceilings are not validated against dataset scale**: `plot_point_size=10000` against a
  very large experiment still draws that many oversized markers in one `savefig()` — no
  test exercises the ceiling at realistic data scale, only at the boundary value itself.
  Accepted for the same reason `_MAX_N_COMPONENTS`'s precedent never added a data-scale
  test either: the ceiling's purpose is bounding a single scalar input against measured
  per-call cost, not scaling with dataset size, and a genuinely slow at-scale test isn't a
  good fit for this fast unit-test suite. Worth a profiled follow-up if a real workload
  ever shows this ceiling is still too generous at scale.
- **Two related gaps identified in PR review, deliberately left unfixed here**:
  - `PCAAnalysisParams`/`UMAPAnalysisParams` don't set `extra="forbid"`, so passing a
    UMAP-only kwarg (e.g. `plot_point_size`) to `pca_analysis` is silently accepted and
    dropped (Pydantic v2's `extra="ignore"` default) rather than raising `invalid_input` —
    the same "loud, actionable rejection" this PR built for the misspelled-colormap case,
    just missing for the wrong-tool case. This is pre-existing, codebase-wide behavior (no
    params model anywhere in `bloommcp` sets `extra="forbid"`), not a regression this PR
    introduced, and deciding to add `extra="forbid"` is a broader schema-wide decision than
    this PR's narrow scope — left as a documented follow-up rather than a unilateral change
    here.
  - `UMAPAnalysisParams.n_neighbors` (`ge=2`, no `le`, only indirectly bounded by a runtime
    check against sample count) and `min_dist` (`ge=0.0`, no `le`, no runtime check at all)
    are the same "unbounded numeric field on this LLM-driven input surface" risk class this
    PR just fixed for `plot_font_size`/`plot_point_size` — but unlike those two, without any
    measured cost data behind a specific ceiling. Pre-existing, out of #721's stated scope;
    worth the same profile-then-bound treatment in a dedicated follow-up.
- **Follow-up issues worth filing separately** so the gaps above don't get lost once #721
  closes: `extra="forbid"` for the plot-style params models; profiled ceilings for
  `n_neighbors`/`min_dist`; `plot_alpha` near-zero point invisibility (issue #721's own
  follow-up comment, finding 7); colormap-list drift against a future matplotlib bump.
  Not filed as part of this change — deliberately left as a decision for whoever owns
  `bloommcp`'s backlog, not assumed here.
- **CI's "Build Docker Images & Scan for CVEs" job is failing on this PR, unrelated to this
  PR's diff**: `CVE-2026-56854` (`golang.org/x/crypto/ssh` auth bypass, fixed upstream in
  `0.55.0`, currently resolving to `0.50.0`) in the `caddy:ci` image's Trivy scan — confirmed
  via the job's own log. This PR's diff is Python-only (`bloommcp/` + `openspec/`) and never
  touches `caddy/` or its Dockerfile; a prior PR merged immediately before this branch's tip
  passed the identical job cleanly. Repo-wide CI gate issue (needs a `caddy` base-image bump
  or an accepted-risk `.trivyignore` entry), not something this change should try to fix —
  noted here so a reviewer doesn't wait on this specific check turning green on its own.

## Migration Plan

No data migration. Purely additive/tightening input validation and a cleanup fix; no
persisted schema, no stored data affected. Deploy is a normal `bloommcp` image
rebuild/restart.

## Open Questions

None — numeric ceilings and the colormap-validation strategy were confirmed with the
requester before scaffolding this proposal.
