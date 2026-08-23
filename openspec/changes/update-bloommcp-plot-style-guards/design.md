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

## Risks / Trade-offs

- **Narrow breaking change**: an existing caller relying on `plot_font_size > 100`,
  `plot_point_size > 10000`, or an excluded `plot_cmap` (e.g. `hsv`, `tab10`) gets
  `invalid_input` where it previously succeeded. Accepted: the issue's own cost data shows
  nothing in that range was usably fast, and nothing outside the sequential/diverging
  allowlist renders continuous trait data correctly.
- **Hand-authored colormap list can go stale** → see Decision 3; mitigated by keeping the
  list a single, easily-editable module-level constant near `_MAX_N_COMPONENTS`'s existing
  pattern.
- **`plt.get_fignums()` diffing assumes single-threaded figure creation** within one
  `generate_figures` call — true today (each `resolved_calls` entry runs synchronously, one
  at a time, within a single tool invocation); flagged here in case a future
  concurrent-plotting change is considered.

## Migration Plan

No data migration. Purely additive/tightening input validation and a cleanup fix; no
persisted schema, no stored data affected. Deploy is a normal `bloommcp` image
rebuild/restart.

## Open Questions

None — numeric ceilings and the colormap-validation strategy were confirmed with the
requester before scaffolding this proposal.
