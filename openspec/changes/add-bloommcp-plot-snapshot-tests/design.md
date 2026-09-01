## Context

#713 asks for "lightweight image-diff/snapshot testing (e.g. perceptual hash or
pixel-diff with a tolerance threshold)" over the plotting tools under
`sections/sleap_roots/analysis/`, loose enough to survive font-hinting/anti-aliasing
noise across platforms. This document records the technical decisions behind how that
was implemented, since none of them are obvious from the code alone.

## Decision 1: comparison mechanism — `matplotlib.testing.compare.compare_images`

Two realistic options: (a) `matplotlib.testing.compare.compare_images` — an RMS
pixel-diff with a tolerance threshold, already built into matplotlib for its own test
suite; (b) a perceptual hash (`imagehash`, PIL-based) — coarser, more tolerant of
anti-aliasing noise, but less able to localize *what* changed and requires a new
dependency.

Chosen: (a). It needs zero new dependencies (Pillow, which it uses to load PNGs, is
already a hard transitive floor of both `matplotlib` and `sleap-roots-analyze` — bloommcp
main dependencies, not incidental extras) and matches the issue's own "pixel-diff with a
tolerance threshold" phrasing exactly. It's also more diagnostic on failure: it writes a
`*-failed-diff.png` and reports an RMS number, versus a phash's opaque "different enough"
verdict. Its main weakness — RMS is a whole-image, not localized, statistic, so it can't
distinguish "font hinting nudged some label pixels" from "the whole plot changed slightly"
— is exactly what the tolerance calibration in Decision 2 has to account for.

## Decision 2: tolerance value (`_TOL = 15`)

Derived from a real, reproducible measurement rather than guessed. Using
`PIL.ImageEnhance.Brightness` to uniformly dim one committed baseline PNG against itself
(a synthetic stand-in for "a rendering regression large enough to matter" — not a literal
simulation of a specific known bug) and scoring with `compare_images(tol=0)`:

| perturbation | RMS |
|---|---|
| 2% dim | ≈5.6 |
| 5% dim | ≈12.2 |
| 10% dim | ≈24.4 |
| 15% dim | ≈36.6 |

`_TOL = 15` sits just above the 5%-dim case and comfortably below the 10%-dim case. Two
supporting data points from a separate experiment (rendering an equivalent 3×3 histogram
grid with plain matplotlib+pandas, no bloommcp involved):

- Re-rendering under matplotlib 3.7.5 vs. 3.10.8 on **this same machine** — a stand-in for
  "the dependency bump #713 is worried about" — produced **RMS = 0**. A version bump
  alone, same OS, isn't the noise source; both wheels bundle their own FreeType, and
  neither changed this plot's rasterization at all here.
- A deliberate content regression (bars recolored + rebinned) produced **RMS ≈106** —
  nowhere near `_TOL`.

`test_viz_snapshot.py::test_tolerance_catches_a_real_regression` reproduces the 10%-dim
case live (not hardcoded) so `_TOL` staying meaningful is itself asserted, not just
documented here.

**Known limitation — localized regressions, measured against real element sizes.** A
uniform dim changes every pixel by the same amount; a real single-element bug (one bar
recolored, one heatmap cell wrong) only touches part of the image, and RMS is a
whole-image average that dilutes a small affected region — by an amount that depends on
both the affected region's *actual* size and the plot's own layout, not one constant
percentage assumed to generalize.

*(Revision history on this section: a first pass measured only `heritability_bar` and
extrapolated an assumed area fraction for the others — a PR review correctly flagged that.
A second pass measured all 5 baselines at a uniform 2%-of-image-area synthetic recolor, and
called `correlation_matrix`'s ~3%-area test case a "resolved" fix for that tool's gap — an
independent review then measured `correlation_matrix`'s ACTUAL cell size directly against
the live delegate and found the 3% test was ~6-7x larger than one real cell, so "resolved"
was false. What follows is that measurement, done properly, and the honest conclusion.)*

Measuring each plot's real element geometry directly against the live delegate call (via
`ax.get_window_extent()` after `fig.canvas.draw()`, not eyeballed or assumed):

- **`correlation_matrix`** (turface_19's 11 real trait columns → 55 lower-triangle cells,
  `mask = np.triu(...)` in `create_correlation_heatmap`): the heatmap axes measures
  1196.7×1196.7px at the actual save dpi=150, so one cell is 1196.7/11 ≈ 108.8px per side
  ≈ **11,836px², ≈0.455%** of the 1690×1539 baseline's 2,600,910px² total. Recoloring
  *exactly* that footprint to the most-detectable-possible color (opaque orange against
  `coolwarm`'s blue/white/red range — a real miscolored cell would typically shift color
  **within** the colormap's range instead, an even smaller RGB delta) scores **RMS≈5.2** —
  nowhere near `_TOL=15`. **A genuine single-cell defect in `correlation_matrix` is not
  reliably caught, full stop.** There is no `_TOL` that both survives legitimate
  cross-platform noise (~5-12 RMS, this section's own measurements above) and clears a ~5
  RMS single-cell signal — the two are the same order of magnitude. This is a real,
  structural limit of whole-image RMS applied to a 55-cell grid, not a gap this change
  closes — tracked as **#768** rather than left as an unstated assumption. (`3%`, the
  size `test_tolerance_catches_a_localized_regression`'s `correlation_matrix` case actually
  uses, represents ~6-7 real cells — a plausible "whole row/column wrong" bug shape, not a
  single-cell claim.)
- **`heritability_bar`** (same 11 traits → 11 bars): bar *area* varies with each trait's H2
  value — measured directly via each bar patch's `get_window_extent()`, real single-bar
  areas range ~0%-2.89% of the image, median ~2.41%. The 2% test case is therefore within
  the real, measured range for a plausible single-bar defect (not an assumed number that
  happened to also be defensible), and clears `_TOL=15` at RMS≈17.0.
- `histograms`/`boxplots`/`variance_decomposition`: not re-measured at a real single-element
  size — all three clear a uniform 2% synthetic probe with large headroom (RMS≈21.7-22.1),
  so a smaller real element is very likely still caught, but this is prose, not an enforced
  test (see `test_viz_snapshot.py`'s comment on `_SNAPSHOT_TOOLS`).

This is a measurement of today's baselines, not a law — re-measure after any change to a
plot's trait count, color density, or layout (e.g. a colormap change, added gridlines). See
Decision 7 for why #768 is tracked separately rather than attempted here.

## Decision 3: baseline generation environment — accepted risk

The baselines this change ships were generated on **macOS** (this development
environment): Docker Desktop was unavailable for producing a Linux-rendered baseline
matching the `python-audit` job's `ubuntu-latest` runner during this change's
authoring (the daemon could not be kept up long enough to complete a `uv sync` inside a
container). `plot_baselines/MANIFEST.json` records the actual generation platform
honestly (`macOS-14.8.2-arm64`) rather than claiming a Linux provenance it doesn't have.

This was a real, accepted gap, not a hidden one, going into review: the first genuine
test of "does `_TOL=15` survive real cross-platform FreeType differences" was this
PR's own `python-audit` CI run comparing the macOS-authored baselines against a
`ubuntu-latest` render. **Outcome: it passed.** All 5 baseline comparisons (plus both
negative-control tests) succeeded on `ubuntu-latest` against the macOS-generated
baselines with no RMS-over-tolerance or dimension-mismatch failure — confirmed by reading
the job log directly (`gh api .../actions/jobs/<id>/logs`), not just the green checkmark.
No baseline regeneration or tolerance adjustment was needed. The follow-up fix commit
addressing the first review round (which added the `correlation_matrix`-parametrized
localized-regression case) re-ran on `ubuntu-latest` and passed again — the new
`test_tolerance_catches_a_localized_regression[correlation_matrix]` case, not just the
original 5 baseline comparisons, has now itself been cross-platform-verified, not merely
asserted to work from a macOS-only local run. If a *future* PR's CI run fails on
`compare_images` RMS alone (not a genuine content difference — check the
`*-failed-diff.png` the failure names), the fallback is unchanged: regenerate the
baselines from a Linux environment via `scripts/gen_plot_snapshots_golden.py` (its own
docstring documents this) rather than loosening `_TOL` blindly — a wider tolerance should
be a deliberate choice backed by the observed RMS, not a reflex fix.

## Decision 4: scope — 5 dedicated plotting tools only

See proposal.md's Non-goals. `pca_analysis`/`umap_analysis`/`clustering`'s optional plot
keys are real plotting surfaces too, but covering all 7 of those keys across 3 more tools
would roughly triple this change for a feature the issue itself twice calls
"lightweight". Tracked as **#723**, filed alongside this proposal rather than left
implicit.

## Decision 5: regeneration visibility aid — old-vs-new RMS on overwrite (not a gate)

A PR review on #713 flagged a real gap: `gen_plot_snapshots_golden.py` overwrote all 5
baseline PNGs unconditionally, with nothing distinguishing "regenerated because of an
intentional rendering change" from "regenerated because a real regression got baked into
the new golden and nobody noticed." A future baseline-touching PR is exactly the moment a
genuine bug could get laundered into the reference the tests compare against.

Change: when `build()` overwrites an *existing* baseline, it now computes the old-vs-new
RMS via `compare_images(tol=0)` and prints it (`REGENERATED <path>: old-vs-new RMS=X.X`),
via the pure, unit-tested `_report_regeneration` helper (`tests/scripts/
test_gen_plot_snapshots_golden.py`). **Precisely what this is and isn't**, since an earlier
version of this section called it a "safeguard" — a word a second review correctly pushed
back on as overselling: `shutil.copy` runs unconditionally regardless of the reported RMS,
nothing in `.github/` reads this script's output, and there is no `--force`/justification
flag. It is a **visibility aid**, not an enforced gate — a large RMS means the regeneration
changed what the golden considers correct, and a PR touching these files *should* quote
that RMS and say why, but nothing mechanically requires it. Building real enforcement (e.g.
failing above some RMS threshold without an explicit override flag) is more machinery than
a "lightweight" testing change justifies; the goal is making the regeneration's effect
visible to a human, not blocking it in CI.

## Decision 7: why #768 (correlation_matrix single-cell detection) is tracked, not fixed here

Given the ~5 RMS single-cell signal sits in the same range as legitimate cross-platform
noise (Decision 2's own measurements), a single global `_TOL` cannot both survive that
noise and catch that signal. Two paths that could genuinely close this gap — a per-region/
structural comparison (extract and compare each heatmap cell individually), or a
complementary *numeric* assertion on the correlation values themselves (independent of
rendering) — are both meaningfully heavier-weight than the "lightweight" pixel-diff testing
#713 asked for. Making `_TOL` a **per-plot** dict (raised by a second review as an
alternative) would not actually help here either: it would need to drop to roughly
3-4 for `correlation_matrix` specifically to have any chance of catching a ~5 RMS
signal, which is already within the noise floor a uniform 2%-dim scores (~5.5 RMS) — a
per-plot tolerance that low would trade single-cell detection for a high false-positive
rate on ordinary cross-platform noise, not a net improvement. Filed as **#768** so the
limitation is tracked and re-discoverable rather than re-litigated from scratch by the next
person who notices it; `test_realistic_single_cell_defect_in_correlation_matrix_is_not_caught`
pins the current (negative) behavior so any future change to this outcome — in either
direction — fails loudly instead of silently.

## Decision 6: shared `viz_env` fixture via `tests/tools/conftest.py`

`test_viz_snapshot.py` needs the identical real-TRAITS_DIR-read / real-PLOTS_DIR-write /
manifest-miss setup `test_viz_tools.py`'s `viz_env` fixture already provides. Duplicating
that fixture's body in a second file risks the two silently desyncing (e.g. one gets a
manifest-miss fix the other doesn't) — the same rationale `_viz_shared.py` already gives
for single-sourcing `save_plot`/`save_plot_or_plots` across the 5 tool files. Moved to a
new `tests/tools/conftest.py`; `test_viz_tools.py`'s own tests are unaffected (pytest
fixture resolution is unchanged from its perspective).
