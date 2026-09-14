## Context

#723 asks for #713's pixel-diff harness to be extended over the optional plot keys
`pca_analysis`/`umap_analysis`/`clustering` emit under `include_plots=True`. The harness
itself needs no redesign — this document records only what could **not** be carried over
from #713 by assumption, and what measuring it actually showed.

#713's own design.md closes its tolerance section with: *"This is a measurement of today's
baselines, not a law — re-measure after any change to a plot's trait count, color density,
or layout."* Eight new plots of unfamiliar composition are exactly that case, so every
number below is measured, not inherited.

All measurements: macOS, matplotlib 3.10.8, Pillow 12.3.0, `sleap-roots-analyze` 0.1.0a5,
umap-learn 0.5.12, numba 0.65.0, Python 3.11.15, against `turface_19_final_data.csv`
seeded as a cleaned version, each tool called at its defaults with `include_plots=True`.

## Goals / Non-Goals

- **Goals**: real pixel coverage for all 8 catalog keys; a tolerance justified for *these*
  plots; every detection limit measured, written down, and pinned by a test.
- **Non-Goals**: changing `_TOL`/mechanism/regen gate; covering non-default parameterizations;
  closing the dilution limits this document measures (see Decision 2).

## Decision 0: these plots are deterministic enough to have a baseline at all

Prerequisite, checked before anything else — a baseline for a non-reproducible render is
worse than no baseline, because it produces flaky CI that trains reviewers to ignore it.

| probe | result |
|---|---|
| re-render in the same process | RMS = 0.0000, all 8 |
| re-render in a **separate process** | RMS = 0.0000, all 8 |
| render order (`clustering` alone vs. after `pca`+`umap` in one process) | RMS = 0.0000, dims identical |
| matplotlib `rcParams` after each tool | unchanged (no global-style contamination) |

The separate-process check matters because PYTHONHASHSEED randomization varies per process
and `pca_analysis`/`umap_analysis` iterate a `frozenset` to pick which figures to generate.
That ordering does vary — it changes `outputs` key insertion order — but it provably does
not reach pixel content. The snapshot tests compare files by name, never by `outputs` order.

UMAP is deterministic here only because `umap-learn` forces `n_jobs = 1` whenever
`random_state` is set (`umap_.py:1950-1954`), and both stochastic tools default `seed = 42`.
That is same-machine determinism; see Decision 3 for the cross-platform half.

## Decision 1: reuse `_TOL = 15` unchanged — re-derived, not inherited

#713's methodology reproduced verbatim for the 8 new baselines: uniformly dim each with
`PIL.ImageEnhance.Brightness`, score against the original with `compare_images(tol=0)`.

| baseline (`create_*`) | dims | KB | 2% dim | 5% dim | 10% dim | 15% dim |
|---|---|---|---|---|---|---|
| `pca_scree_plot` | 989x589 | 38.8 | 5.7 | 12.3 | 24.6 | 36.8 |
| `pca_biplot` | 989x790 | 99.3 | 5.9 | 12.8 | 25.5 | 38.2 |
| `feature_contribution_plot` | 1189x790 | 42.6 | 4.7 | 10.3 | 20.5 | 30.6 |
| `feature_contribution_heatmap` | 971x790 | 73.4 | 5.1 | 11.6 | 22.9 | 34.2 |
| `umap_single_trait` | 769x590 | 46.5 | 5.9 | 12.7 | 25.5 | 38.2 |
| `umap_colored_by_top_traits` | 1481x985 | 214.1 | 5.8 | 12.6 | 25.2 | 37.8 |
| `cluster_scatter_pca` | 989x790 | 48.0 | 5.9 | 12.9 | 25.7 | 38.5 |
| `cluster_size_barplot` | 989x590 | 17.9 | 4.9 | 10.8 | 21.3 | 31.9 |

`_TOL = 15` clears every 5%-dim value (max **12.9**) and sits below every 10%-dim value
(min **20.5**), so the selection rule #713 used holds for all 8 without modification. The
band is tighter than #713's 3 measured (5%: 12.0-12.5, 10%: 23.4-24.9) — `cluster_scatter_pca`
and `pca_biplot` reach 12.9/12.8 at 5% — but the ordering `_TOL` depends on is intact, and a
**shared** constant is worth more than a marginally better-fitted per-plot one: #713's
Decision 7 already worked through why per-plot tolerances trade a real property (one
auditable number) for a false sense of precision.

Total added weight: **580.6 KB** across 8 files, largest **214.1 KB** — under the 500 KB
`check-added-large-files` pre-commit limit, which a single file would otherwise trip.

## Decision 2: two measured blind spots — documented and pinned, not papered over

A tolerance that passes a dimming probe is not evidence the check catches a *real* bug.
Re-rendering each tool with a genuinely different parameter and scoring against the
baseline gives that evidence, and for two plots the answer is negative.

**What is caught** (comfortably):

| perturbation | result |
|---|---|
| `pca standardize=False` → `pca_scree_plot` | RMS 110.2 |
| `pca standardize=False` → `feature_contribution_plot` | RMS 100.6 |
| `pca standardize=False` → `pca_biplot`, `feature_contribution_heatmap` | canvas dimension mismatch (hard fail) |
| `umap seed=7` (was 42) → both UMAP keys | canvas dimension mismatch (hard fail) |
| `clustering n_clusters` 3/4/5/8 → `cluster_size_barplot` | RMS 64.7-69.5 |

**Blind spot A — `create_cluster_scatter_pca` cannot see a clustering change at all.**

| perturbation (vs. default, auto-selected k=2) | scatter RMS | caught at `_TOL=15`? |
|---|---|---|
| `n_clusters=3` | 6.8 | no |
| `n_clusters=4` | 10.7 | no |
| `n_clusters=5` | 8.8 | no |
| `n_clusters=8` | 11.6 | no |
| `method="gmm"` (k=1) | 4.5 | no |
| `method="hierarchical"` (k=2) | 3.8 | no |

Every cluster assignment in the figure can change — the plot's entire semantic content —
and the comparison passes. The cause is dilution: ~153 small markers on a mostly-white
canvas, so recoloring all of them moves few pixels. This is **more severe than #768's
single-cell case**, which at least requires the defect to be small.

No tolerance fixes it. The signal range (3.8-11.6) sits **entirely below that same plot's
own 5%-dim noise floor of 12.9** — the identical structural argument #713's Decision 7 made
for `correlation_matrix`, reproduced here with this plot's own numbers rather than assumed
by analogy. A `_TOL` low enough to catch a k-change would fire on ordinary cross-platform
hinting noise.

The baseline still earns its place: it catches what the tolerance *can* see, and the
"caught" table above shows those regressions surface as hard dimension mismatches. It is
coverage against rendering/layout/dependency regressions, not against clustering
correctness — which is what `test_clustering_tool.py`'s numeric oracles are for.

`method="hierarchical"` also scores **13.5** on `cluster_size_barplot` (same k as default,
different membership sizes) — under `_TOL`. Recorded for completeness; the barplot catches
every k-change, which is the regression shape that plot exists to show.

**Blind spot B — `create_feature_contribution_heatmap`, one cell: RMS 14.0.** Geometry
measured live via `ax.get_window_extent()` after `fig.canvas.draw()`, not assumed: the
heatmap is 13 features x 5 PCs, its axes measure 622.2x704.4px, so one cell is
124.4x54.2px = 6,742px² = **0.879%** of the 971x790 canvas. Recoloring exactly that
footprint to the most-detectable-possible color (opaque orange) scores **RMS 14.0** —
below `_TOL = 15`, though far closer to it than `correlation_matrix`'s 5.2. A real defect
shifting a cell *within* the colormap would score lower still. Same class as #768; this
plot is a second instance of it, not a new phenomenon.

Both blind spots get a negative-control test asserting `compare_images` returns `None`,
mirroring `test_realistic_single_cell_defect_in_correlation_matrix_is_not_caught`. Pinning
a limitation means any future change to it — a fix, or a regression into a worse one —
fails loudly rather than passing unnoticed.

## Decision 3: cross-platform risk is higher here than #713's, and accepted the same way

Baselines are generated on macOS; `python-audit` asserts on `ubuntu-latest`. Docker is
unavailable in this environment to produce a Linux render locally — the same constraint
#713 hit and recorded in its Decision 3, where the accepted risk **passed on the first CI
run**. Three reasons the risk is nonetheless larger for these 8:

1. **No explicit dpi.** All three tools call `fig.savefig(..., bbox_inches="tight")` with no
   `dpi=` (the covered 3 pin `dpi=150`), so the canvas is derived from rendered text extents
   at the ambient dpi of 100. A cross-platform font-metric difference therefore changes the
   *canvas size*, which fails as an `ImageComparisonFailure` — a hard error, not an RMS miss
   `_TOL` can absorb. The "caught" table above shows how readily this path triggers.
2. **`create_pca_biplot` runs `adjustText`** (`visualization.py:2510-2526`, when ≥5 feature
   arrows) — an iterative label-repulsion solver whose output is a function of rendered text
   extents. The most layout-fragile of the 8.
3. **UMAP is not bit-reproducible across platforms** (numba/LLVM/BLAS), as
   `test_umap_analysis_tool.py:3-4` already states. Decision 0's determinism is same-machine.

Accepted rather than mitigated, because the alternatives are worse: skipping UMAP leaves the
two plots at magic-byte coverage, and widening `_TOL` cannot help a dimension mismatch at
all. This PR's own `python-audit` run is the first real cross-platform test, exactly as
#713's was. **If it fails**, the fallback is per-key and evidence-led, in this order:
(a) read the uploaded `*-failed-diff.png` artifact and confirm whether content or canvas
size differs; (b) if canvas size, regenerate that key's baseline from Linux; (c) only if a
genuine RMS-noise floor above `_TOL` is demonstrated, revisit the constant — never as a
reflex, per #713's Decision 3.

## Decision 4: baseline filenames match the committed PNG name

`<catalog_key>_turface_19_baseline.png` (e.g. `create_pca_scree_plot_turface_19_baseline.png`),
so the baseline name is the produced name plus a suffix. #713's 3 need a
`_PRODUCED_NAME_OVERRIDES` dict because their baseline labels (`histograms`) and committed
filenames (`trait_histograms.png`) drifted apart across #462/#466. Starting these 8 aligned
means no mapping table to keep in sync — one fewer place for a rename to rot. The existing 3
are left alone; renaming them would churn committed binaries for cosmetics.

## Risks / Trade-offs

- **CI fails cross-platform on first run** → Decision 3's ordered fallback; the diff-image
  artifact #713 wired up makes diagnosis a download rather than a re-run.
- **A future `sleap-roots-analyze` bump changes several plots at once** → the regen script's
  per-file old-vs-new RMS print (#713 Decision 5) plus the `--yes` gate (Decision 10) already
  force a deliberate step; `tests/fixtures/README.md` already requires the PR to quote each
  RMS and justify it.
- **8 more baselines to regenerate on every intentional rendering change** → real, accepted
  cost; it is the same one-command regeneration, and 580 KB is a rounding error against the
  660 KB already committed.
- **The two pinned blind spots could read as "tested" to someone skimming** → mitigated by
  naming the tests for what they assert (`..._is_not_caught`) and by this section.

## Open Questions

None blocking. Two adjacent items are deliberately filed rather than fixed here: the
`outputs`-ordering inconsistency between the three tools (proposal.md Non-goals), and
`heritability_analysis`'s uncovered figures (already recorded in `tests/fixtures/README.md`).
