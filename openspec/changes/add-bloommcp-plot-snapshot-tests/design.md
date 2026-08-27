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

**Known limitation — localized regressions, measured per plot type.** A uniform dim
changes every pixel by the same amount; a real single-element bug (one bar recolored, one
heatmap cell wrong) only touches part of the image, and RMS is a whole-image average that
dilutes a small affected region — by an amount that depends on the plot's own layout, not
one constant. Measured directly (single fully-recolored square vs. the original baseline,
at increasing area fractions) against **all 5** baselines, not just one assumed to
generalize (a PR review on #713 correctly flagged the original version of this section for
calibrating against only `heritability_bar`):

| baseline | ~2% area RMS | floor to clear `_TOL=15` |
|---|---|---|
| histograms | ≈22.0 | <2% |
| boxplots | ≈21.7 | <2% |
| variance_decomposition | ≈22.1 | <2% |
| heritability_bar | ≈17.0 | ~2% |
| correlation_matrix | ≈13.7 | ~2.5% (2% alone is **not** caught) |

`correlation_matrix` is the real outlier, and for a legible reason: its heatmap fills most
of the frame with already-saturated color, so a same-area recolor lands a smaller RGB
delta there than the same recolor landing on the sparser plots' white margins. It is also
the tool where a silent single-cell error is most scientifically consequential (a shifted
correlation value a researcher would act on) — the highest-stakes tool was, before this
fix, the least-validated one.
`test_viz_snapshot.py::test_tolerance_catches_a_localized_regression` is now parametrized
over both `heritability_bar` (~2%) and `correlation_matrix` (~3%, real margin above its
measured ~2.5% crossing point), not asserted only against the baseline with the most
headroom. Anything smaller than these per-baseline floors is an accepted gap of this
whole-image, "lightweight" approach — closing it fully would need a per-region/structural
comparison, a heavier-weight technique than what #713 asked for. This table is a
measurement of today's baselines, not a law — re-measure it after any change to a plot's
own color density or layout (e.g. a colormap change, added gridlines).

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
No baseline regeneration or tolerance adjustment was needed. If a *future* PR's CI run
fails on `compare_images` RMS alone (not a genuine content difference — check the
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

## Decision 5: regeneration safeguard — old-vs-new RMS on overwrite

A PR review on #713 flagged a real gap: `gen_plot_snapshots_golden.py` overwrote all 5
baseline PNGs unconditionally, with nothing distinguishing "regenerated because of an
intentional rendering change" from "regenerated because a real regression got baked into
the new golden and nobody noticed." A future baseline-touching PR is exactly the moment a
genuine bug could get laundered into the reference the tests compare against.

Fix: when `build()` overwrites an *existing* baseline, it now computes the old-vs-new RMS
via `compare_images(tol=0)` and prints it (`REGENERATED <path>: old-vs-new RMS=X.X`). An
RMS near 0 confirms nothing visually changed (e.g. a dependency patch bump with no
rendering effect); a large RMS means the regeneration changed what the golden considers
correct, and the script's own module docstring now states plainly that such a PR's
description MUST say what changed and why. This is intentionally a runtime print, not a
CI gate or required commit-message format — enforcing it mechanically (e.g. failing the
script above some RMS threshold without a `--force`/justification flag) is more machinery
than a "lightweight" testing change justifies; the goal is making the regeneration's
effect visible to both the author and reviewer, not blocking it.

## Decision 6: shared `viz_env` fixture via `tests/tools/conftest.py`

`test_viz_snapshot.py` needs the identical real-TRAITS_DIR-read / real-PLOTS_DIR-write /
manifest-miss setup `test_viz_tools.py`'s `viz_env` fixture already provides. Duplicating
that fixture's body in a second file risks the two silently desyncing (e.g. one gets a
manifest-miss fix the other doesn't) — the same rationale `_viz_shared.py` already gives
for single-sourcing `save_plot`/`save_plot_or_plots` across the 5 tool files. Moved to a
new `tests/tools/conftest.py`; `test_viz_tools.py`'s own tests are unaffected (pytest
fixture resolution is unchanged from its perspective).
