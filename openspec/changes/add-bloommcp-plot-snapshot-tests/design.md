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

**Known limitation — localized regressions.** A uniform dim changes every pixel by the
same amount; a real single-element bug (one bar recolored, one heatmap cell wrong) only
touches part of the image, and RMS is a whole-image average that dilutes a small affected
region. Measured directly against the `heritability_bar` baseline (a single fully-recolored
rectangle vs. the original, at increasing area fractions): 1.5% of the image area scores
RMS≈14.4 (would **not** clear `_TOL=15`); 2% scores RMS≈16.7 (would). A single bar in an
~18-trait bar chart occupies comfortably more than 2% of the image, but a single cell in a
large correlation-matrix heatmap could plausibly fall under that floor.
`test_viz_snapshot.py::test_tolerance_catches_a_localized_regression` pins the ~2% case as
caught; anything smaller is an accepted gap of this whole-image, "lightweight" approach —
closing it fully would need a per-region/structural comparison, a heavier-weight technique
than what #713 asked for.

## Decision 3: baseline generation environment — accepted risk

The baselines this change ships were generated on **macOS** (this development
environment): Docker Desktop was unavailable for producing a Linux-rendered baseline
matching the `python-audit` job's `ubuntu-latest` runner during this change's
authoring (the daemon could not be kept up long enough to complete a `uv sync` inside a
container). `plot_baselines/MANIFEST.json` records the actual generation platform
honestly (`macOS-14.8.2-arm64`) rather than claiming a Linux provenance it doesn't have.

This is a real, accepted gap, not a hidden one: the first genuine test of "does `_TOL=15`
survive real cross-platform FreeType differences" is this PR's own `python-audit` CI run
comparing the macOS-authored baselines against a `ubuntu-latest` render. Two outcomes:

- **CI passes**: the empirical tolerance holds cross-platform; no further action.
- **CI fails on `compare_images` RMS alone** (not a genuine content difference — check the
  `*-failed-diff.png` the failure names): regenerate the baselines from a Linux
  environment via `scripts/gen_plot_snapshots_golden.py` (its own docstring documents
  this) and re-push, rather than loosening `_TOL` blindly — a wider tolerance should be a
  deliberate choice backed by the observed RMS, not a reflex fix.

## Decision 4: scope — 5 dedicated plotting tools only

See proposal.md's Non-goals. `pca_analysis`/`umap_analysis`/`clustering`'s optional plot
keys are real plotting surfaces too, but covering all 7 of those keys across 3 more tools
would roughly triple this change for a feature the issue itself twice calls
"lightweight". Tracked as **#723**, filed alongside this proposal rather than left
implicit.

## Decision 5: shared `viz_env` fixture via `tests/tools/conftest.py`

`test_viz_snapshot.py` needs the identical real-TRAITS_DIR-read / real-PLOTS_DIR-write /
manifest-miss setup `test_viz_tools.py`'s `viz_env` fixture already provides. Duplicating
that fixture's body in a second file risks the two silently desyncing (e.g. one gets a
manifest-miss fix the other doesn't) — the same rationale `_viz_shared.py` already gives
for single-sourcing `save_plot`/`save_plot_or_plots` across the 5 tool files. Moved to a
new `tests/tools/conftest.py`; `test_viz_tools.py`'s own tests are unaffected (pytest
fixture resolution is unchanged from its perspective).
