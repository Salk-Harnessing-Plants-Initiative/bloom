## Context

#768 records a structural limit found during review of PR #724: `test_viz_snapshot.py`'s
whole-image RMS pixel-diff cannot catch a single-cell defect in the correlation heatmap,
because RMS averages one cell's error across 2.6M pixels and lands in the same range as
legitimate cross-platform FreeType noise. The issue names two paths that could genuinely
close it — (1) per-region/structural comparison, (2) a numeric assertion on the correlation
values themselves — and defers both as heavier-weight than the "lightweight" pixel-diff
testing #713 asked for.

This document records which path was taken and why, and the measurements behind every number
quoted. Consistent with the suite it extends, every figure here was produced by running the
real delegate against the real fixture, not estimated.

## Goals / Non-Goals

- **Goal**: a wrong correlation value in any drawn cell fails CI, deterministically, at any
  magnitude — including one that shifts *within* `coolwarm`'s range, the case #768 flags as
  harder to detect than its own opaque-orange probe.
- **Goal**: the check is anchored to a **recomputed** oracle (`df[trait_cols].corr()` in the
  test), not to a committed baseline image, so it cannot be laundered by a baseline
  regeneration — the failure mode `add-bloommcp-plot-snapshot-tests`' Decisions 5/10 built the
  regen speed-bump for.
- **Non-goal**: replacing or retuning the RMS layer. See Decision 3 for the division of labor.
- **Non-goal**: breadth across the other plotting tools — that is #723 (Decision 7).

## Decision 1: path (1), structural per-cell comparison — not path (2), a numeric assertion

#768's path (2) ("a numeric assertion on the correlation matrix values themselves") was
evaluated first, because it looked cheaper and because a partial version already exists:
`test_plot_correlation_matrix_tool.py::test_pins_one_off_diagonal_cell_and_high_correlation_counts`
already pins one off-diagonal cell and the strong-correlation counts against an independently
computed `df[trait_cols].corr()`.

**Rejected as insufficient on its own, because it does not cover the gap #768 is about.** The
tool computes its *summary* from its own guarded `.corr(min_periods=...)`, while the *image* is
rendered by a separate, independent, unguarded `.corr()` inside
`sleap_roots_analyze.visualization.create_correlation_heatmap` (the same two-call split
`plot_correlation_matrix.py`'s docstring already discloses under #747). Widening the existing
numeric pin from one cell to all of them would therefore prove the **JSON summary** is right
and say nothing whatsoever about what the **PNG** draws — and #768's stated risk ("a researcher
acting on a shifted correlation") runs through the PNG, which is the artifact a researcher
actually looks at.

Path (1) was taken instead, and it subsumes path (2)'s value: asserting the rendered cells
against a recomputed matrix *is* a numeric assertion, applied at the point where it matters.

## Decision 2: the measurement — how bad the RMS gap actually is

Rendering the live delegate against `turface_19_final_data.csv` (11 detected traits → an 11×11
grid, 55 drawn cells), making **exactly one** real cell carry a *different correlation value*,
and scoring both ways. The cell chosen is the strongest real pair in the fixture —
`Surface Area (mm²)` × `Network Area (mm²)`, true r = 0.9942 — so the shifts below are all
"this plot claims a relationship that isn't there", the scientifically consequential direction.

Measured by **two independent methods**, because the first one alone would have been open to a
fair objection (see below):

| defect: cell drawn as r = | RMS, pixel-fill | RMS, re-render | caught at `_TOL=15`? | per-cell ΔC | caught per-cell? |
|---|---|---|---|---|---|
| 0.944 (−0.05) | 1.56 | 1.66 | no | 0.090 | **yes** |
| 0.894 (−0.10) | 2.29 | — | no | 0.184 | **yes** |
| 0.794 (−0.20) | 3.62 | — | no | 0.306 | **yes** |
| 0.694 (−0.30) | 4.85 | 4.92 | no | 0.408 | **yes** |
| 0.494 (−0.50) | 7.17 | — | no | 0.588 | **yes** |
| −0.006 (−1.00) | 11.17 | 11.08 | no | 0.851 | **yes** |
| −0.306 (−1.30, full scale) | 11.34 | 11.24 | no | 0.838 | **yes** |

*"pixel-fill"* recolors the cell's footprint in the saved PNG; *"re-render"* makes the delegate
actually draw the doctored matrix. The second method exists because the first holds `vmin`/
`vmax` fixed while a real defect would perturb them, so a skeptic could reasonably ask whether
the conclusion was an artifact of the method. It is not — the two agree to within ~0.1 RMS, and
in fact the perturbation is provably immaterial here: with `center=0`, seaborn's colormap
slicing makes a value's color depend only on `vrange = max(|vmin|, vmax)`, which `|r| ≤ 1`
already pins. The conclusion survives the method it was not measured with.

**The headline is the last row.** r=0.9942 → r=−0.306 is not a subtle defect: it is the widest
error the colormap can express on this fixture (`norm.vmin=−0.30799`, `norm.vmax=0.99421`), a
clean sign flip from "almost perfectly correlated" to "the most negatively correlated pair on
the plot". It scores **RMS ≈11.3 — under `_TOL=15`, not caught.** #768's own figure (opaque
orange, RMS≈5.2) understated this only in the sense that orange happens to sit closer in
luminance to the deep red it replaced; the honest conclusion is the stronger one: **no
single-cell correlation error in this heatmap is caught by the RMS layer, at any magnitude.**

By contrast the per-cell signal is cleanly separated from the noise:

- **Noise floor**: sampling each cell's pixels out of the saved PNG and comparing to the color
  its own value implies, the worst disagreement across all 55 cells is **0.00196** (per-channel,
  0-1 scale) — anti-aliasing and PNG quantization.
- **Smallest signal tested**: **0.090**, for a −0.05 shift (already smaller than a defect anyone
  would care about).
- `atol = 0.01` therefore sits **~5× above the noise and ~9× below the smallest tested signal**.

That separation is the whole argument. `_TOL=15` fails not because 15 is the wrong number but
because the RMS signal (≈1.6-11.3) and the RMS noise (≈5-12, per the existing suite's own
cross-platform measurements) *overlap*; there is no threshold between them. At the per-cell
level they are three orders of magnitude apart, so a threshold exists.

## Decision 3: division of labor — this does not replace the RMS layer

The two checks answer different questions and both are kept:

- **whole-image RMS** (`test_viz_snapshot.py`): "does this figure still rasterize the way the
  committed baseline does?" Catches global, layout, and dependency-bump regressions — a font
  change, a colormap swap, a shifted axes box, a canvas resize. Cheap and broad.
- **per-cell oracle** (`test_viz_cell_oracle.py`): "does each drawn cell carry the value it
  should?" Catches exactly the localized, value-level defect RMS structurally dilutes.

`test_single_cell_defect_rms_misses_but_cell_oracle_catches` asserts *both halves of that
sentence in one test*, so the split is enforced rather than merely documented, and it asserts
the oracle's three artist-state layers **separately** so that no one of them can carry the test
while another quietly stops detecting.

**Residual, stated plainly:** a defect confined to matplotlib's rasterizer — one that painted a
single cell's *pixels* wrongly while leaving the figure's artist state correct — would be caught
by neither layer, since the oracle's array/annotation/facecolor assertions read artist state and
its pixel assertion samples a render produced by that same rasterizer. This is not the failure
mode #768 describes (a wrong *value* reaching a researcher), and no realistic bug in this
codebase produces it; it is recorded so the next reader knows the boundary rather than inferring
a stronger claim than is made.

## Decision 4: the oracle compares against the delegate's *unguarded* `.corr()`

`plot_correlation_matrix` computes its JSON summary from a guarded
`.corr(min_periods=_MIN_CORR_OVERLAP)`; `create_correlation_heatmap` independently runs a plain
`.corr()`. These disagree exactly on low-overlap pairs — the open #747 gap where the image
confidently colors a cell the summary excluded.

The oracle deliberately compares against the **plain, unguarded** `.corr()`, i.e. against what
the delegate was actually asked to draw. Comparing against the guarded matrix instead would make
this new suite fail on any fixture that triggers #747, conflating "the heatmap drew the wrong
value" (this change's concern) with "the heatmap shouldn't have drawn this value at all"
(#747's). Keeping them separate means #768's check stays green and meaningful while #747 remains
independently open, and a future fix to #747 changes the delegate's output — which this oracle
would then correctly flag, prompting a deliberate update rather than a silent pass.

`turface_19` triggers neither condition (measured: no zero-variance traits, no pairs below
`_MIN_CORR_OVERLAP=10`, so `heatmap_caveat is None` and no footnote is drawn), so the two
matrices are identical on this fixture today; the distinction is about which one the test
*names* as its oracle, and therefore how it behaves when that stops being true.

## Decision 5: the drawn-cell set is `triu | ~isfinite`, not "the 55 lower-triangle cells"

The first draft of this change specified the mask as exactly
`np.triu(np.ones_like(corr), dtype=bool)` — the mask `create_correlation_heatmap` passes to
`sns.heatmap` — and spoke throughout of "the 55 cells". **That is wrong for any fixture whose
correlation matrix contains a NaN,** and a review caught it before implementation. Seaborn's
`_HeatMapper` applies its own `np.ma.masked_invalid` *on top of* the caller's mask, so the real
invariant is:

```
drawn cells = ~( np.triu(ones_like(corr), dtype=bool) | ~np.isfinite(corr) )
```

Measured, by forcing one trait in `turface_19` to a constant: drawn cells drop 55 → **45** and
annotations drop 55 → **45**, and the observed mask matches `triu | ~isfinite` exactly.

This matters beyond pedantry, because a NaN correlation is a *first-class, realistic* case for
this tool specifically — it reads raw, uncleaned data, and `zero_variance_traits` /
`low_overlap_trait_pairs` are shipped result fields with their own tests. Specifying the flat
mask would have written a requirement into `openspec/specs/` that is false whenever the tool is
used on the data it is designed for. Stating the mask correctly also makes the check *stronger*
rather than merely safer: it distinguishes "this cell is legitimately blank" from "this cell
should have carried a value and was left blank", which a flat mask cannot.

The 11-trait / 55-cell figures quoted elsewhere in this document remain accurate as
measurements *of the committed fixture*; they are not the invariant, and the implementation
derives the grid from `len(trait_cols)` and the mask from the formula above rather than
hardcoding either.

## Decision 6: what the oracle reads, and why pixels are checked too

Measured facts about the live render (verified against `turface_19`, all exact):

| what | measured |
|---|---|
| `ax.collections[0]` | a `QuadMesh`, 11×11 masked array, 55 unmasked (all values finite here) |
| its array vs. recomputed `corr` at unmasked positions | equal |
| its mask vs. `triu \| ~isfinite` | identical (Decision 5) |
| `norm.vmin` / `norm.vmax` | −0.30798709623699355 / 0.9942108612743239 = min/max of the drawn values |
| all 55 facecolors vs. `cmap(norm(value))` | equal to 1e-12 |
| `ax.texts` | exactly 55, each `== f"{corr[r,c]:.2f}"` at position `(c+0.5, r+0.5)` |
| non-drawn cells' facecolor | RGBA `(0,0,0,0)`, exactly one unique value across all 66 |
| both axes' tick labels | equal `trait_cols`, in order |
| bare delegate render + `savefig(dpi=150, bbox_inches="tight")` | equals the committed baseline at `compare_images(tol=0)` |

Artist-state assertions alone would already catch every defect in Decision 2's table, so the
**saved-PNG per-cell pixel check needs its own justification, and "closing the loop to the
bytes" is only half of it.** The load-bearing reason is coverage continuity: it is the only
per-cell check that does not read the seaborn/matplotlib artist tree. An upgrade that
restructures that tree breaks every artist-state assertion at once, and without the pixel layer
the change's entire contribution would lapse in exactly that window — while the tests still
looked like they were failing for a merely mechanical reason. The secondary reason is the one
stated first in the draft: the artifact a researcher opens is the PNG, and asserting only on the
in-memory figure leaves "figure correct, file wrong" unexamined by any per-cell check.

Mapping a cell to saved-PNG pixels through `bbox_inches="tight"` is the one non-obvious
mechanic: `fig.get_tightbbox(renderer)` minus savefig's default `pad_inches=0.1` gives the crop
origin, and display coordinates are then rescaled by `dpi/fig.dpi` and flipped vertically.
Verified: the predicted canvas is 1690.68×1539.82px against an actual 1690×1539 saved image, and
the predicted cell boxes land on the right cells — which is what the 0.00196 worst-case
agreement in Decision 2 *is*; a geometry error would show up immediately as a wildly wrong
sampled color.

Cells are sampled by **median** over an inset rather than mean, so the centered annotation text
cannot bias the sample. Rather than asserting that property in prose, the suite demonstrates it:
each cell is sampled twice, over a wide inset that *includes* the glyphs and a narrow one that
excludes them, and the two are asserted to agree (measured: both 0.00196). That keeps the check
independent of the cross-platform font-rendering differences that constrain `_TOL`.

## Decision 7: scope — `correlation_matrix` only, and why #768 goes before #723

Only the correlation heatmap is covered. It is the tool #768 is about, the one where a silently
wrong value is most consequential, and the only one whose rendering is a regular grid of
independently-meaningful cells that a per-cell oracle even applies to cleanly.

#723 (extend snapshot coverage to `pca_analysis`/`umap_analysis`/`clustering`'s 7 optional plot
keys) is **deliberately sequenced after this change, not bundled with it.** #713's review
established the convention that each newly covered plot key must have its real element footprint
measured and its detectability disclosed honestly. Two of #723's keys —
`create_feature_contribution_heatmap` (a grid heatmap) and `create_cluster_scatter_pca` (a
scatter) — would reproduce Decision 2's negative result verbatim. Landing #768 first means #723
extends a pattern that has an answer for localized defects, instead of restating the same
"not caught" disclosure two or three more times. The oracle here is written against
`create_correlation_heatmap`'s specific geometry rather than generalized speculatively; #723 can
generalize it against the second real case, when there is one to generalize *from*.

## Risks / Trade-offs

- **Coupling to seaborn/matplotlib artist internals** (`ax.collections[0]`, `ax.texts`,
  `norm`/`cmap`) → a seaborn upgrade that restructures the artist tree breaks these tests.
  Accepted: it breaks *loudly*, at the exact assertion whose premise changed, and the alternative
  (pixels only) is what #768 already proved insufficient. This is also precisely why the
  pixel-level layer is retained rather than cut as redundant (Decision 6).
- **The facecolor assertion uses the figure's own `cmap`/`norm`** to map value → expected color, so
  a *global* colormap or normalization change shifts both sides equally and passes. Covered from
  the other side, on purpose: `vmin`/`vmax` are pinned against a recomputed min/max, and a
  colormap swap is a whole-image change, which is precisely what the RMS layer does catch. Stated
  because "it compares the render against itself" is a fair first reading of that one assertion
  and deserves an answer rather than silence.
- **"Independent oracle" is a claim about the rendering path, not the whole system.** Trait
  selection still flows through production `experiment_utils.detect_columns`, and the axis-label
  assertion compares against the tool's own `resolved_trait_columns`. Both are deliberate — the
  point is to verify what was *drawn*, given the selection — but the spec says so rather than
  letting "independently computed" be read as stronger than it is.
- **Fixture-shape dependence** → the oracle derives the grid size from `len(trait_cols)` and the
  drawn set from `triu | ~isfinite` at runtime rather than hardcoding 11 or 55, so a fixture
  change re-derives instead of going stale. It does still assume annotations are on, which holds
  at or below `annot_threshold`; the threshold is read from the delegate's own parameter default
  and the guard's failure path is exercised by a dedicated test against a synthetic count, since
  the committed fixture can never trigger it.
- **Runtime** → a handful of extra delegate renders plus `savefig`s in an unmarked per-PR job.
  Same order as the existing snapshot tests, which already render all three tools.

## Migration Plan

None — additive test-only change. No production code path is modified; the only non-test edits
are docstring paragraphs that currently describe #768 as an open gap.

## Open Questions

None blocking. One deliberate follow-up: whether `test_realistic_single_cell_defect_in_
correlation_matrix_is_not_caught` should eventually be deleted rather than kept as a pin on the
RMS layer's blind spot. Kept for now — it is the regression guard on Decision 3's division of
labor, and deleting it would make a future `_TOL` change silently reintroduce the confusion #768
was filed to end.

One thing found while reviewing this change and deliberately **not** fixed here: the sibling
`add-bloommcp-plot-snapshot-tests` spec still requires 5 baseline PNGs for 5 plotting tools,
while bloom#462 retired two of those tools and the repo now carries 3. Archiving that change
unamended writes a knowingly-false requirement into `openspec/specs/`. It belongs to that
change, not this one; recorded here because this change is the only other thing touching that
capability and the next person to notice should not have to re-derive it.
