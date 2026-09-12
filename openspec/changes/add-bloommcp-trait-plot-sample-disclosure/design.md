## Context

`plot_trait_histograms` and `plot_trait_boxplots` were converged onto `@as_mcp_tool` by #466 but
kept their pre-conversion reporting surface: `n_traits_plotted`, `batched`, `n_pages`,
`resolved_trait_columns`, `page_traits`. None of those says anything about how much **data** is
behind the rendered figure. Both tools read raw, uncleaned frames, where per-trait missingness is
disjoint and a genotype group can collapse to a couple of rows for one trait while staying full
for its neighbour.

The sibling tool in the same folder, `plot_correlation_matrix`, has been through three rounds of
exactly this argument (#466 rounds 4-7, then #784/#785). The pattern it settled on is the one
adopted here, deliberately rather than by imitation:

1. compute the disclosure in bloommcp from the same selected frame, never by changing the
   vendored delegate;
2. report the flagged tail as a capped, worst-first list, with uncapped scalars alongside so the
   cap cannot misrepresent the population;
3. stamp everything into the persisted run's `params`;
4. put a signal on the image itself, because a caller who only opens the PNG gets nothing from
   the JSON.

### Verified delegate behavior

Every claim below was probed against the pinned `sleap-roots-analyze` (0.1.0a5), not assumed:

| Input | `create_trait_histograms` | `create_trait_boxplots_by_genotype` |
| --- | --- | --- |
| Trait with some `NaN` | silently dropped (`dropna()`), `n` shown in the panel title | silently dropped (`dropna()`), no `n` anywhere |
| Trait entirely `NaN` | literal `"No data"` panel | literal `"No data"` panel |
| One genotype all-`NaN` for a trait | n/a | **group vanishes** — no tick, no box, no gap (ticks came back `['B']` for an A/B frame) |
| Row whose genotype value is null | n/a | silently dropped from every box |
| Trait containing `±inf` | **raises** `ValueError: supplied range of [1.0, inf] is not finite` | quartiles become `NaN`; the box patch's upper edge is literally `nan` — renders broken, no error |

## Goals / Non-Goals

**Goals**

- A caller can tell how many points are behind any rendered box or histogram bar, from the JSON,
  from the manifest, or from the image alone.
- Every silent exclusion (missing values, missing genotype, non-finite values, a group that
  disappears) is named somewhere the caller will actually look.
- The disclosure stays bounded at cylinder scale (846 traits) in both response size and CPU.

**Non-Goals**

- Changing what the delegates draw (bar heights, box geometry, colors) or how they handle
  missing data. This file owns no vendored plotting logic, and neither tool's data handling is
  altered.
- Turning either tool into a QC gate. Both stay pre-clean EDA views; nothing new is *excluded*
  because of a disclosure, with the single exception in Decision 5.
- A significance or sufficiency claim for any sample size (Decision 3).

## Decisions

### Decision 1: Compute the sample-size table in bloommcp, from the same selected frame

`groupby(genotype)[trait_cols].count()` gives the full (genotype × trait) table of non-null
counts in one vectorized pass, and it is exactly what the delegate's own
`df[[trait, genotype_col]].dropna()` will keep, per trait. A Python loop over
(trait × genotype) is prohibitive at cylinder width, so nothing here iterates over the grid —
only over the *flagged* cells, which are the tail.

**Why not read it back off the rendered figure** (counting artists per axis): it would couple
this wrapper to the delegate's subplot geometry, orientation switch (`vertical` under 8
genotypes, `horizontal` above), and axis-flip conventions — the same coupling
`plot_correlation_matrix` refused when it chose a figure-level footnote over per-cell hatching.
The frame is the ground truth; the figure is derived from it.

**Why not ask the delegate to report it**: that is a vendored-package change, out of this
package, and the tool's stated principle is that it owns no plotting logic.

### Decision 2: Capped worst-first lists + uncapped scalars in the response, full table as a committed CSV

At cylinder scale (846 traits) even a single scalar per trait is a ~25 KB response field, and a
(trait × genotype) dict is far worse. `plot_correlation_matrix`'s
`test_provenance_stamped_seed_none_and_links_returned` encodes the family's "links, not blobs"
contract as a hard 5,000-char ceiling on any single result field; this change respects it rather
than relaxing it.

So the response carries:

- the **flagged tail** (`small_sample_groups`, `absent_genotype_groups`, `low_sample_traits`),
  ordered ascending by `n` and capped at 20, with an uncapped count alongside each — the
  ascending order is what makes the cap safe, exactly as in #784: it truncates the
  best-supported end, so entry `[0]` is always the worst-supported cell in the whole run;
- **uncapped scalars** (`group_n_min`/`_median`/`_max`, `plotted_n_min`/`_median`/`_max`)
  computed over *every* cell, because a capped list is a biased sample: 20 thin boxes drawn from
  16,000 read as if the whole run were thin;

and the **complete, uncapped table** ships as a committed run output — `group_sample_sizes.csv`
(`trait,genotype,n_plotted,n_non_finite`) and `trait_sample_sizes.csv`
(`trait,n_plotted,n_missing,nan_fraction`) — with its own `OutputLink`, the same way
`qc_inspect` commits `nan_samples.csv` and `descriptive_stats` commits `stats.csv`. That is what
makes "every box's `n` is recoverable" literally true at any scale, without putting a 2 MB table
in an agent's context.

**Cap value 20, not 50**: matches `_MAX_STRONG_PAIRS_REPORTED`/
`_MAX_LOCALLY_CONSTANT_PAIRS_REPORTED` from #784/#785, and stays clear of the 5,000-char ceiling
above.

### Decision 3: `_MIN_PLOTTED_SAMPLES = 5`, owned by `_viz_shared`, a degeneracy floor only

A box plot's five-number summary needs enough observations for its quartiles to describe a
distribution rather than individual points. Below 5, Q1/Q3 interpolate between at most four
values, the IQR is a function of one or two of them, and every "outlier" dot is an artifact of
that arithmetic — at n=2 the box is a line segment. That is the same *degeneracy* argument
`_MIN_CORR_OVERLAP` rests on, and it is all this constant claims.

It is explicitly **not** a sufficiency threshold. A box at n=6 clears it and is still a very thin
description of a genotype; the uncapped `group_n_min`/`_median`/`_max` scalars and the CSV are
reported unconditionally precisely so the floor is not the only thing a caller sees.

**Owned by `_viz_shared`, not aliased to `_qc_shared._CANONICAL_MIN_SAMPLES_PER_TRAIT` (10)** —
the decoupling #784 made for `_MIN_CORR_OVERLAP`, applied prospectively rather than being walked
back later. That constant answers "enough samples to keep a *trait* during cleaning"; this one
answers "enough points for a *box* to be a box". Aliasing would mean a QC-side retune silently
moves which boxes this tool flags and what its footer says. They do not even agree in value here,
which makes the distinction concrete rather than theoretical.

**Why 5 and not 10**: 10 is a per-column completeness convention with no bearing on quartile
degeneracy, and picking it would flag two-thirds of a typical replicated-genotype experiment
(the `turface_19` fixture runs 7-9 replicates per genotype) as suspect — a warning that fires on
healthy data is one callers learn to ignore. Recorded in Open Questions, since the right number
is ultimately an agronomic judgement, not a derivation.

### Decision 4: The boxplot footer is always drawn and page-scoped; the histogram image is untouched

The core of #748 is that a box with n=2 and a box with n=200 are *pixel-identical*. A footer that
appears only when something trips a threshold leaves every other image exactly as uninformative
as before, and makes the absence of a warning carry meaning it cannot support (see Decision 3).
So `plot_trait_boxplots` always draws a one-line footer — `n per box: min=…, median=…, max=…
across N genotype group(s)` — and escalates it to a `⚠`-prefixed dark-red line naming the
flagged groups (capped at 10, `+N more`) when any is small, absent, or non-finite. The mechanism
is `Figure.text(...)` on the already-rendered figure before `savefig`, identical to
`heatmap_caveat`'s footnote — not a per-box annotation, which would require reverse-engineering
the delegate's subplot geometry and orientation switch and would mislabel the wrong box when it
got them wrong.

**Page-scoped on a batched render.** A 53-page cylinder render must not stamp page 1's thin
groups onto page 37's footer: the statistics and the named groups are restricted to that page's
`page_traits`. `plot_correlation_matrix` never faced this — it is single-figure. The exact text
drawn on a page is reconstructible from `page_traits` + `group_sample_sizes.csv`, so the response
does not carry 53 near-duplicate strings; `sample_size_note` carries the run-wide summary (and is
the exact drawn text in the common unbatched case, where there is one page).

**`plot_trait_histograms` renders unchanged**: `create_trait_histograms` already titles every
panel `f"{trait}\n(n={count})"`. The PNG-only gap does not exist there, and adding a redundant
footer would churn a snapshot baseline for no signal. This asymmetry is the reason #748 was
re-scoped to lead with boxplots, and it survives into the fix.

### Decision 5: Non-finite values — histograms fail loudly and early, boxplots disclose

The two delegates react differently to `±inf` (see the table above), so one policy cannot serve
both.

- **Histograms already fail.** `matplotlib.hist` cannot bin a non-finite range, so today's caller
  gets the delegate's `ValueError` through the redacting error path: no trait named, no remedy.
  This change detects non-finite values in the resolved selection **before creating the run** and
  raises `BloomMCPError(code="assumption_violated")` naming the offending traits (capped) and
  pointing at `qc_clean`/`remove_outliers`. No run outcome changes from success to failure —
  only the quality of the failure — and detecting it pre-`create_run` means no staging directory
  is written and immediately torn down.
- **Boxplots silently corrupt.** The affected group's quartiles are `NaN`, so the box renders
  broken while every other box on the panel looks normal. Failing the run here would be a
  regression (the figure is still useful for the unaffected groups), so the traits are named in
  `non_finite_traits`, counted per (trait, genotype) in the CSV, and folded into the footer's
  warning clause.

**Why not strip `±inf` and render anyway**: that is a change to what the figure shows, made by
the wrapper, on data the caller asked to see raw. It would also diverge the image from
`qc_inspect`'s reading of the same frame. Naming the problem and letting the caller clean it is
the pre-clean EDA posture both tools already commit to.

### Decision 6: `absent_genotype_groups` is its own bucket, not the `n == 0` tail of the small list

They are qualitatively different failures. A small group draws a **misleading box**; an absent
group draws **nothing at all** — no tick, no gap, no trace that the genotype exists for that
trait. A reader scanning a panel can at least see a suspicious box; they cannot see an absence.
Separating the buckets lets the footer say the right sentence about each ("3 group(s) below n=5"
vs "1 group absent (all-null)") and mirrors the multi-bucket taxonomy #785 established next door,
where each blank cell gets exactly one named reason. Every (trait, genotype) cell therefore lands
in exactly one of: absent (`n == 0`), small (`0 < n < floor`), or unflagged.

### Decision 7: The boxplot snapshot baseline is regenerated, deliberately

`tests/tools/test_viz_snapshot.py` pixel-compares each tool's committed PNG against a baseline.
The footer changes the boxplot render by design, so `boxplots_turface_19_baseline.png` is
regenerated with `scripts/gen_plot_snapshots_golden.py` and the printed old-vs-new RMS is quoted
in the PR, per that script's own review convention. The histogram and correlation_matrix
baselines are **not** touched: neither render changes, and the script's all-or-nothing rewrite is
reverted for those two files so the diff shows exactly the one image that was meant to change.

Note the fixture's smallest genotype group is 7 rows (`turface_19_final_data.csv`, 19 genotypes,
no missing values), so the regenerated baseline carries the *unflagged* footer — the warning path
is covered by unit tests with purpose-built frames, not by the snapshot.

### Decision 8: Out of scope

- **#747** (the rendered heatmap is not per-cell masked) and **#768** (the snapshot check cannot
  catch a single-cell defect) are `plot_correlation_matrix` issues and untouched here.
- **Boxplot outlier handling** (#748's title also names "outlier handling"): matplotlib's whisker
  convention (1.5×IQR) is a documented, deterministic property of the chart type, not a silent
  data exclusion — every point outside the whiskers is still *drawn* as a flier. There is nothing
  hidden to disclose, so no field claims to. What genuinely hides data — missing values, missing
  genotypes, non-finite values — is what this change reports. Called out explicitly so a reader
  of #748 can see the sub-claim was assessed rather than dropped.
- **Per-box `n` in tick labels** (`GH_7420 (n=2)`): rejected in Decision 4. Revisit only if the
  delegate gains a first-class way to label groups.

## Risks / Trade-offs

- **The footer consumes vertical space on every boxplot page.** Mitigated by keeping it to one or
  two lines with `wrap=True` at `fontsize=8`, drawn below the axes area via `transFigure`, and by
  `bbox_inches="tight"` expanding the canvas rather than overlapping the plot.
- **A second full-width pass over the frame.** `groupby().count()` plus an `isinf` mask are two
  vectorized passes at (rows × traits); at cylinder width this is measured in the benchmark
  task rather than assumed (`tasks.md` §5.5).
- **`group_sample_sizes.csv` can be large** (traits × genotypes rows). It is a download, not a
  response field, and is the price of the uncapped-disclosure claim; the same trade-off
  `cross_experiment_correlations` already makes with its genotype-means CSVs.
- **The floor is a judgement call.** See Open Questions.

## Open Questions

- **Is 5 the right floor?** It is defensible as a quartile-degeneracy bound, but the agronomically
  meaningful minimum replicate count for a genotype is a domain call. If the lab's convention
  turns out to be higher, moving the constant changes only which cells are *flagged* — never a
  reported `n`, never the CSV, never the scalars — so the change is cheap and comparability of
  persisted runs is preserved.
- **Should `plot_trait_histograms` eventually gain the same always-on footer** for consistency, if
  a future delegate version stops printing `(n=…)` in panel titles? Today that would be pure
  duplication — and this change adds a test that pins the delegate's `(n=…)` titling directly
  (the existing `_titled_traits` helper splits it off before asserting, so nothing currently
  fails if the suffix disappears), which turns that future regression into a test failure
  instead of a silent loss of the only sample-size signal the histogram image carries.
