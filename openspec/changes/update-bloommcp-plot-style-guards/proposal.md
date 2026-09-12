## Why

GitHub issue #721: the UMAP/PCA plot-styling fields added by #661/#662 accept values that
cause real cost or silently corrupt the figure's meaning. `plot_font_size` has no upper
bound — a `1000` typo (meant to be `10`) costs ~1s (UMAP) to ~12s/2.6GB (PCA), `4000` costs
15s/4GB, and `float('inf')` is accepted outright, all while holding the GIL and blocking
every other caller on the shared server. `plot_point_size` has the same unbounded shape
(lower risk — cost stays flat until ~1e8). `plot_cmap` is never validated, so a misspelling
(`virdis`) burns a full UMAP computation before failing with an opaque `internal_error`
(matplotlib's own message — which names the typo and lists valid options — only reaches the
server log, at ~124KB per occurrence since it enumerates every registered colormap), while a
validly-spelled but semantically wrong colormap (`hsv`, `tab10`) silently produces a
misleading continuous-trait plot. The same failure also leaks one matplotlib figure per bad
call, because the figure is allocated before the plotter raises and never reaches the
tool's own cleanup.

## What Changes

Code-level changes to existing fields; see Impact for which spec delta operation
(ADDED/MODIFIED) each maps to and why they differ from the code-level verb below:

- `UMAPAnalysisParams.plot_font_size` and `PCAAnalysisParams.plot_font_size` gain `le=100`
  (already `gt=0`).
- `UMAPAnalysisParams.plot_point_size` gains `le=10000` (already `gt=0`; PCA has no
  `plot_point_size` field — out of scope, unchanged).
- `UMAPAnalysisParams.plot_cmap` is validated in the `umap_analysis` tool body, before
  `perform_umap_analysis` runs, against a hand-authored allowlist of matplotlib's documented
  sequential + diverging colormaps (plus each name's `_r` reversed variant). An invalid value
  raises `BloomMCPError(invalid_input)` naming the value — no wasted computation, no opaque
  `internal_error`. (PCA has no `plot_cmap` field — out of scope, unchanged.)
- **MODIFIED** (also a `MODIFIED` spec delta — this one has a canonical baseline):
  `bloom_mcp.tools._plots.generate_figures` closes any figure a plotter callable allocates
  before raising, not only figures that already completed and were recorded —
  defense-in-depth so a mid-call exception (from a bad `cmap`, or any future plotter bug)
  never leaks a figure for the life of the long-running server process.
- Added boundary-acceptance tests for every new/tightened bound (the
  `test_plot_alpha_boundary_values_accepted` pattern the issue asks to replicate), and a new
  "allocate-then-raise" figure-cleanup test that — unlike the existing `_boom`-style
  stand-ins — actually opens a figure before raising.
- **BREAKING (narrow)**: a caller currently passing `plot_font_size > 100`,
  `plot_point_size > 10000`, or a `plot_cmap` outside the new allowlist (e.g. `hsv`, `tab10`,
  any qualitative/cyclic colormap) will now get `invalid_input` instead of a slow or
  misleading success. No persisted data or historical run is affected — this only changes
  acceptance of new calls.

## Impact

- Affected specs: `bloommcp-umap-analysis-tool`, `bloommcp-pca-analysis-tool`
- Affected code:
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/umap_analysis.py` —
    `plot_font_size`/`plot_point_size` `le=`, a new `plot_cmap` allowlist constant and a
    tool-body check for it
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/pca_analysis.py` —
    `plot_font_size` `le=100`
  - `bloommcp/src/bloom_mcp/tools/_plots.py` — `generate_figures` allocate-then-raise
    cleanup fix
  - `bloommcp/tests/tools/test_umap_analysis_tool.py`, `test_pca_analysis_tool.py`,
    `test_plots_helpers.py` — new/extended tests
- This proposal stacks on top of two already-implemented-but-not-yet-archived sibling
  changes touching these same fields: `add-bloommcp-plot-style-kwargs` (#662, adds
  `plot_cmap`/`plot_point_size`/`plot_alpha`) and `add-bloommcp-plot-font-style` (#661, adds
  `plot_font_size`/`plot_font_family`). Their `design.md`s explicitly accepted the
  trade-offs this issue now overturns — `add-bloommcp-plot-style-kwargs/design.md` argued no
  `point_size` ceiling was needed and that re-validating the colormap registry would
  "duplicate matplotlib's own check"; `add-bloommcp-plot-font-style/design.md` flagged
  `gt=0` admitting `float('inf')` as an accepted risk. Both are superseded by this change for
  these two specific points. The figure-cleanup requirements this change touches already
  exist in the two capabilities' canonical `openspec/specs/` baseline, so those deltas are
  authored as `MODIFIED`; the numeric ceilings and the `plot_cmap` allowlist have no
  canonical baseline yet (they live only in the two open sibling changes' deltas, not yet
  archived), so those are authored as `ADDED` — same precedent already used by
  `add-bloommcp-plot-style-kwargs` for stacking on other still-open sibling changes.
- **Archive after or together with `add-bloommcp-plot-style-kwargs` and
  `add-bloommcp-plot-font-style`.** This change's `ADDED` requirements reference "in addition
  to the existing `gt=0` lower-bound rejection" — a rule that itself only exists in those two
  still-open changes' deltas, not yet in the canonical `openspec/specs/` baseline. Archiving
  this change first would fold in a requirement that cites a baseline rule the canonical spec
  doesn't have yet; archive it after (or in the same batch as) the two sibling changes.
- No new dependency; no database/schema change; no breaking change to persisted data.
