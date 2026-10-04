## Context

`plot_trait_histograms` and `plot_trait_boxplots` were converged onto `@as_mcp_tool` by #466 but
kept their pre-conversion reporting surface: `n_traits_plotted`, `batched`, `n_pages`,
`resolved_trait_columns`, `page_traits`. None of those says anything about how much **data** is
behind the rendered figure. Both read raw, uncleaned frames, where per-trait missingness is
disjoint and a genotype group can collapse to a couple of rows for one trait while staying full
for its neighbour.

The sibling tool in the same folder, `plot_correlation_matrix`, has been through several rounds
of exactly this argument (#466 rounds 4-7, then #784/#785). The pattern it settled on is adopted
here — with the caveat that **#784/#785's code is not in this branch's tree**: it is on the
unmerged PR #833, so nothing here imports, references or asserts against its symbols
(`_MAX_STRONG_PAIRS_REPORTED`, the de-aliased `_MIN_CORR_OVERLAP`). The precedent is followed by
restating it, not by depending on it. The pattern: compute the disclosure in bloommcp from the same selected frame, report the flagged tail as
a capped worst-first list with uncapped scalars alongside, stamp everything into the persisted
run's `params`, and put a signal on the image itself.

### Verified delegate behavior

Probed against `sleap-roots-analyze` 0.1.0a5 — the version resolved in `uv.lock`. Note the
dependency declaration is a **floor** (`sleap-roots-analyze>=0.1.0a5`), not a pin, so these are
properties of the resolved version, pinned by tests (tasks §1.6), not guarantees:

| Input | `create_trait_histograms` | `create_trait_boxplots_by_genotype` |
| --- | --- | --- |
| Trait with some `NaN` | silently dropped (`dropna()`), `n` shown in the panel title | silently dropped (`dropna()`), no `n` anywhere |
| Trait entirely `NaN` | literal `"No data"` panel | **one** `"No data"` panel for the whole trait |
| One genotype all-`NaN` for a trait | n/a | **group vanishes** — no tick, no box, no gap (ticks came back `['B']` for an A/B frame) |
| Row whose genotype value is null | n/a | silently dropped from every box (`groupby(dropna=True)` agrees cell-for-cell) |
| Trait containing `±inf` | **raises** `ValueError: supplied range of [1.0, inf] is not finite` | renders a box with a **confidently drawn but shifted median** and a `NaN` upper quartile/whisker |
| Genotype column entirely null | n/a | renders successfully today (panels titled, `"No data"` text) |

The `±inf` boxplot row is worse than "renders broken": on `[1, 2, inf, 4, 5]` the delegate draws
`q1=2.0`, `median=4.0`, `q3=NaN`. The finite median is 3.0. A reader sees a box with an open top
and a median line in the wrong place — not something that reads as corrupt data.

Tick labels are the genotype **values** themselves in both orientations (vertical: x-ticks via
`df_plot.boxplot(by=...)`; horizontal above 8 genotypes: y-ticks via an explicit
`tick_labels=genotype_order`). That fact is what makes Decision 4 possible.

## Goals / Non-Goals

**Goals**

- A caller can tell how many observations are behind any rendered box or histogram bar — from
  the JSON, from the manifest, or **from the image alone**.
- Every silent exclusion (missing values, missing genotype, non-finite values, a group that
  disappears) is named somewhere the caller will actually look.
- The disclosure stays bounded at cylinder scale (846 traits) in response size, CPU and wall
  clock.

**Non-Goals**

- Changing what the delegates *draw* from the data (bar heights, box geometry, colors) or how
  they handle missing values. Labels and an added note are annotation, not plotting logic.
- Turning either tool into a QC gate. Both stay pre-clean EDA views.
- Any significance or sufficiency claim for a sample size (Decision 3).

## Decisions

### Decision 1: Compute the sample-size table in bloommcp, from the same selected frame

`groupby(genotype)[trait_cols].count()` gives the full (genotype × trait) table of non-null counts
in one vectorized pass, and it agrees cell-for-cell with what the delegate's own
`df[[trait, genotype_col]].dropna()` keeps (verified, including `groupby`'s default
`dropna=True` matching the delegate's dropping of null-genotype rows). Nothing iterates over the
grid in Python; only the flagged tail is materialized.

Measured at cylinder width (3,000 rows × 846 traits × 60 genotypes) by this change's
`bloommcp/scripts/trait_plot_sample_disclosure_bench.py`: the whole table — both grouped passes and the
long-form reshape — takes **14 ms for 50,760 cells**, and the committed CSV is **1.95 MB**. The
cost is in the rendering, not here.

**Why not read it off the rendered figure**: that couples the wrapper to subplot geometry and the
orientation switch. The frame is ground truth; the figure is derived from it. **Why not ask the
delegate**: vendored-package change, out of this package.

### Decision 2: Capped worst-first lists + uncapped scalars in the response, full table as a committed CSV

At cylinder scale a (trait × genotype) dict in the response is tens of thousands of entries.
`plot_correlation_matrix`'s own test suite encodes the family's "links, not blobs" intent as a
5,000-char ceiling on any single result field — though that assertion runs only on a small
fixture, and these two tools already break it at width today: `resolved_trait_columns` measures
**24,862 chars** for cylinder's real 846 trait names, and `page_traits` roughly the same again.
The ceiling is therefore applied here to the **fields this change adds**, and the pre-existing
overrun is recorded as a separate finding (tasks §7) rather than silently inherited as if the
contract already held. So the response carries the **flagged tail**
(capped at `MAX_FLAGGED_REPORTED = 20`, ordered worst-first) plus **uncapped scalars**, and the
complete table ships as a committed run output with its own `OutputLink` — the same shape
`qc_inspect` uses for `nan_samples.csv` and `descriptive_stats` for `stats.csv`.

**Ordering is fully specified, because ties are the normal case here, not an edge case.** In a
replicated design most groups share the same `n` (in the `turface_19` fixture fifteen of nineteen
genotypes tie at 8). Ascending-by-`n` alone would leave which 20 of N tied cells get reported to
column order, so two runs over near-identical data could report disjoint lists while both
satisfy the spec. The order is therefore **ascending count, then `(trait, genotype)`
lexicographic**, and `absent_genotype_groups` — where every entry ties at zero by construction —
is ordered by `(trait, genotype)` alone.

**A capped list must never be dominated by one dead trait.** A single all-null trait makes *every*
genotype absent for it: 19 entries on the `turface_19` fixture, enough to exhaust a 20-slot cap by
itself and evict every genuinely informative absence elsewhere in the run. Such a trait is
therefore collapsed to a single `no_data_traits` entry and emits **no** per-group rows — which
also matches what the delegate draws (one `"No data"` panel for the trait, not G empty slots).

### Decision 3: `MIN_PLOTTED_SAMPLES = 5`, owned by `_viz_shared`, a quartile-degeneracy floor only

A box plot's five-number summary needs enough observations for its quartiles to *be* observations
rather than interpolations. Below n=5, matplotlib's default linear-interpolation quartiles are
weighted blends of adjacent order statistics that correspond to no measured plant: at n=2 on
`[1, 2]` the box spans `[1.25, 1.75]`, a range containing neither datum. **n=5 is the smallest
sample size above 1 at which Q1, the median and Q3 all land exactly on order statistics**
(x₍₂₎, x₍₃₎, x₍₄₎) — the first n at which the drawn box is made of data rather than of the
interpolation rule. The property is **non-monotone**: it holds at n ≡ 1 (mod 4), so at 5, 9 and
13 but *not* at 6, 7 or 8, where most `turface_19` boxes sit. 5 is therefore defensible as the
smallest such n, not as a threshold above which the property holds — what is monotone is only
the cruder fact that more observations make the interpolation matter less. A future maintainer
should not reason from this criterion to "n=7 is worse than n=5".

**This floor says nothing about the whiskers or the flier dots, and clearing it does not make
them trustworthy.** Measured here (60k replicates per n, clean standard-normal data, matplotlib's
`whis=1.5` default): matplotlib cannot draw a flier at all below n=4; at **n=5 — on the clearing
side of this floor — a sample carries at least one spurious "outlier" dot 33% of the time, and
8.6% of its points are flagged**, against the ~0.7% asymptotic rate for normal data. The
*fraction* of points flagged falls with n (8.6% at 5, 4.0% at 10, 1.8% at 30) but the probability
that a box shows at least one spurious flier does **not**: it is 21% at n=4, then sits between
26% and 34% at every n measured from 5 to 30 — it does not decay with sample size the way the
flagged fraction does. (The committed benchmark asserts these figures rather than printing them;
that is how the 27% originally written here was caught as wrong at n=8.) A flier dot is an arithmetic artifact whether or not the box clears this
floor, and nothing in this change should be read as certifying otherwise.

**Owned by `_viz_shared`, not aliased to `_qc_shared._CANONICAL_MIN_SAMPLES_PER_TRAIT` (10)** —
the same decoupling #784 makes for `_MIN_CORR_OVERLAP` (on unmerged PR #833; in this tree that
constant is still the alias), applied here at birth instead of being walked back later. That constant answers
"enough samples to keep a *trait* during cleaning"; this one answers "enough points for a *box* to
be made of data". Aliasing would let a QC-side retune silently move which boxes this tool flags.
The two do not even agree in value here.

**Why not 10**: measured on `tests/fixtures/turface_19_final_data.csv` — 19 genotypes, 7-9
replicates each, 11 detected trait columns, zero nulls, zero infs — a floor of 10 flags **209 of
209 (trait × genotype) cells**; a floor of 5 flags none. On the cylinder fixture a floor of 10
flags 15,228 of 16,074 cells (94.7%) against 846 (5.3%) at a floor of 5. A warning that fires on
100% of a healthy, complete experiment is one callers learn to ignore.

**For histograms the same constant carries no distributional claim**: a histogram has no
quartiles. There it is a bare "too few points for a shape to exist" floor — and note the delegate
hardcodes `bins=30`, so a panel well *above* the floor can still be visually degenerate (n=6
draws six bars of height 1). That is disclosed in the module docstring rather than flagged,
because it is a property of every panel, not of thin ones.

### Decision 4: The image itself carries per-box `n`, plus an always-on note

#748's re-scoping asks directly: *"consider whether per-group sample counts should also land in
the rendered image itself — the same 'a caller who only opens the image gets nothing' risk applies
here."* Two mechanisms, both measured before being chosen:

**(a) Per-box `n` appended to each genotype tick label** — `GH_7440 (n=8)`. This needs no geometry
and no orientation branch: the delegate's tick labels *are* the genotype values, so each label is
matched by text against the count table and rewritten. It is self-checking — a label matching no
known genotype is left alone, and the result reports `box_labels_annotated` so a caller is never
told the image carries `n` when the match failed.

Legibility was **rendered and inspected**, not assumed:

| Variant | Result |
| --- | --- |
| Two-line `GH_7440\n(n=8)`, 19 genotypes | labels collide into each other — unreadable. Rejected. |
| Inline `GH_7440 (n=8)`, 19 genotypes (horizontal) | clean |
| Inline, 5 genotypes (vertical, 90°-rotated x-ticks) | longer labels run into the next subplot row's titles |
| …the same plus `fig.tight_layout()` | clean |

So `tight_layout()` is called **on unbatched renders only**. The rule is that simple because the
*batched* delegate already calls it itself — `create_trait_boxplots_by_genotype_batched` does
`fig.tight_layout(rect=[0, 0, 1, 0.96])` before returning each page
(`visualization.py:430`) — while the unbatched one deliberately does not, assigning it to the
caller in its own docstring. So this change adds it exactly where the delegate left it undone.

That split is also what keeps the cost off the hot path. Measured at 40 genotypes × 16 traits
(one cylinder page's shape): relabelling alone costs **+5%** (1.66s → 1.74s), while relabelling
plus a `tight_layout` call costs **+21%** (2.01s). Paying that 53 times is what would push the
cylinder smoke test past its 120s client timeout — and it is unnecessary, because those pages are
already laid out. On the unbatched path it is one render, and free where it matters: 0.25s vs
0.30s at 5 genotypes × 11 traits.

It also fixes a second, measured defect. Because the unbatched delegate skips `tight_layout`, its
figure carries a large unused bottom margin that `bbox_inches="tight"` crops away today — so a
note anchored below the figure re-expands the canvas by **+14.5%**, most of it dead white space
(1912×2757 → 1912×3157). A batched page, already tight-laid-out, grows only +3.8% and sits snug
under the axes. Calling `tight_layout` on the unbatched path removes that gap instead of shipping
it.

**(b) An always-on note drawn below the axes.** Per-box labels cannot show what is *not* drawn: an
absent group has no tick to label. The note covers that, and gives the distribution at a glance.
It is drawn **unconditionally** — a note that appears only when something trips a threshold leaves
every other image as uninformative as before, and makes its own absence carry a sufficiency claim
Decision 3 explicitly disclaims.

**The note is page-scoped and says so.** A 53-page cylinder render must not stamp page 1's thin
groups onto page 37's note — and fixing only the *numbers* while leaving the *label* unqualified
reintroduces the same misreading at the text layer. The template names its own scope and every
denominator:

```
n per box (this page): min=7, median=8, max=9 across 209 box(es) — 19 genotype group(s) x 11 of 11 trait(s).
⚠ 3 of 209 box(es) below n=5: Solidity x GH_7420 (n=2), ...; 1 group absent (no box drawn): ...; 1 trait carries non-finite values: Holes. See group_sample_sizes.csv for every group.
```

`sample_size_note` in the result carries the run-wide summary, and is the exact drawn text in
the common single-page case.

**The per-page strings are deliberately NOT stamped into `params`** — reversing an earlier
decision in this change, on measured grounds. At cylinder width that is 53 pages × ~2.5 KB of
prose appended to every version of a `manifest.json` that `create_run` re-validates in full on
every subsequent run for the same tool and experiment, so the cost compounds with run count.
Each page's note is a pure function of `page_traits[page]`, the committed CSV,
`MIN_PLOTTED_SAMPLES` and `MAX_NOTE_NAMES` — all of which the manifest or its outputs already
carry — so stamping the rendered strings duplicates recoverable data rather than preserving
anything. The earlier argument for stamping (a run-wide string matches no page) is real, but it
is answered by reconstructibility, not by storage.

**The warning clause will fire on every cylinder page, and that is not a false alarm.** Measured
on the cylinder fixture (129 rows × 846 traits × 19 genotypes = 16,074 cells): box `n` runs
min 2 / median 7 / max 10, with **846 cells (5.3%) below 5**. Every one of its 53 pages will
therefore carry a `⚠`. This is the opposite of Decision 3's "fires on healthy data" objection —
cylinder genuinely has boxes built from 2 points — but a marker that is always present stops
carrying information, which is why the clause reports the **fraction** (`846 of 16,074 box(es)`)
rather than just the fact. A reader can calibrate severity from the number; they cannot from a
symbol.

**`plot_trait_histograms` needs no per-panel labelling but does get a note** (revised in PR
review round 2). `create_trait_histograms` already titles every panel `f"{trait}\n(n={count})"`,
so the per-box half of this decision has no counterpart there — tasks §1.6 pins that titling
directly, since the decision depends on it and the existing `_titled_traits` helper splits the
suffix off before asserting.

But `(n=…)` answers only the sample-size question. It is what was *binned*, not what was
*dropped*: a panel reading `(n=12)` is identical whether twelve plants were measured or 108 of
120 rows were lost — and "no 'N rows excluded' count, no per-trait missingness disclosure" is
the sentence #748 opens with. The first version of this change left that gap open on the image
and argued the asymmetry was principled; it was not, and the review was right to say the
justification answered a different question than the one #748 asks. So the same unconditional
note is drawn here in this tool's own unit (panels, not boxes), flagging on missing *fraction*
as well as count, and carrying its own caveat: the delegate bins into a fixed 30 bins regardless
of `n`, so a panel well above the floor can still be a single bar.

### Decision 5: Counts include `±inf`, so classification uses the *finite* count

`pandas.count()` and `dropna()` treat `±inf` as present — `qc_inspect`'s module docstring
documents this exact trap for its own `per_trait_nan_fraction`. Inherited naively it would be a
silent defect here: a cell whose 12 values are all `+inf` reports `n=12`, clears the floor, and is
described as a healthy box while drawing `q3=NaN`.

So: `n_plotted` is the `count()` value (documented as including `±inf`), `n_finite` is reported
**beside** it as a column of the CSV rather than left to be derived, and **every flag is computed
on the finite count**. `n_non_finite` is a subset of `n_plotted`, not an addition to it. The
buckets are therefore exhaustive and mutually exclusive, in precedence order:

1. `no_data_traits` — the trait has no non-null value for any genotype (one `"No data"` panel).
2. `absent_genotype_groups` — `n_plotted == 0` for this (trait, genotype): no box is drawn at all.
3. `non_finite_groups` — `n_non_finite > 0`: a box is drawn, but at least one `±inf` has corrupted
   its quartiles (any single `inf` is enough — see the verified-behavior note above).
4. `small_sample_groups` — `0 < n_finite < MIN_PLOTTED_SAMPLES`.
5. unflagged.

### Decision 6: Non-finite values — histograms fail loudly and early, boxplots disclose

The two delegates react differently, so one policy cannot serve both.

- **Histograms already fail.** `matplotlib.hist` cannot bin a non-finite range, so today's caller
  gets the delegate's `ValueError` through the redacting error path: no trait named, no remedy.
  This change detects non-finite values **before creating the run** and raises
  `assumption_violated` naming the offending traits with a remedy. No run outcome changes from
  success to failure — only the quality of the failure.
- **Boxplots silently corrupt.** Failing would be a regression (the figure is still useful for
  every unaffected group), so the traits and groups are named, counted in the CSV, and warned
  about on the image.

**Neither tool strips or replaces `±inf` before rendering** — both are pre-clean EDA views, and
silently altering data the caller asked to see raw would diverge the image from what `qc_inspect`
reports for the same frame.

**The third tool in this family is already handled elsewhere.** `plot_correlation_matrix` files an
`inf`-bearing trait under `zero_variance_traits` (its `std` is `NaN`), and the in-flight
`add-bloommcp-corr-pair-disclosure` (PR #833) adds exactly that fourth case to the field's
description. This change does not touch it; "settled once" applies to the two plot tools this
change owns, and the spec names them rather than saying "each plotting tool".

### Decision 7: Scalar summaries describe drawn boxes, never absent cells

Including zero-count cells in the min/median/max produces a line that is false on healthy data.
Measured on a realistic sparse raw frame (10 genotypes × 10 traits, disjoint missingness) where
**every drawn box has exactly n=6**, the zeros-included version renders
`n per box: min=0, median=0, max=6` — "the typical box is empty" on a run whose every box is
healthy. At cylinder scale with disjoint missingness, zero cells routinely outnumber drawn boxes.

So `box_n_min`/`_median`/`_max` are computed over cells with **at least one finite observation**,
the count of those cells is reported as `n_boxes_summarized`, and absent/non-finite cells are
carried completely by their own buckets and counts. The identity is **three-termed**, because the absent count deliberately excludes the cells of a
wholly-dead trait (those collapse into `no_data_traits`):

    n_boxes_drawn + absent_genotype_group_count
        + no_data_trait_count × n_genotype_groups
        == n_traits_plotted × n_genotype_groups

It is asserted by test in exactly that form, so nothing is lost by the exclusion. An earlier
draft of this document and of the spec stated a two-term version, which is false whenever a
trait is dead everywhere — worth recording rather than quietly correcting, because a delta
becomes a living spec on archive and a future maintainer would "fix" correct code to satisfy
it.

### Decision 8: Degenerate populations return `None`, never `NaN`

An all-null genotype column is reachable today (the tool guards only `genotype_col is None`) and
**renders successfully**. After this change `groupby()` over it yields zero groups, and
`min()`/`np.median()` over the empty result raise or produce `NaN` respectively — converting a
succeeding run into a crash, or writing a bare `NaN` token into a manifest that
`storage_backend._json_bytes` serializes with `allow_nan=True` and strict JSON readers reject.

Every scalar summary is therefore `Optional`, `None` when the population is empty, with a note
text for that case. The same applies to a zero-row frame, which `resolve_trait_columns` does not
exclude.

### Decision 9: Every stamped value is coerced to a native Python type

This is the first tool in the family to stamp **numeric aggregates** into a run's `params`.
`plot_correlation_matrix` stamps only strings and lists of strings; `qc_inspect` routes its
numerics through `convert_to_json_serializable` first. Everything here comes out of
`groupby().count()`, `np.median` and `isinf().sum()` as `np.int64`/`np.float64`, and
`manifest.py`'s `stamped.model_dump(mode="json")` raises
`PydanticSerializationError: Unable to serialize unknown type: <class 'numpy.int64'>` on those —
verified, not anticipated. So every count, scalar and fraction is converted at the boundary
(`int(...)`, `float(...)`, `None`) before it reaches either the result model or the stamp, and a
test asserts the manifest round-trips through strict JSON with `parse_constant` rejecting
`NaN`/`Infinity` tokens.

### Decision 10: The boxplot snapshot baseline is regenerated, and the generator needs help first

The render changes on purpose, so `boxplots_turface_19_baseline.png` is regenerated via
`scripts/gen_plot_snapshots_golden.py`. Two measured facts change how:

- The note grows the canvas (1912×2757 → 1912×3054 at the chosen placement), and
  `matplotlib.testing.compare.compare_images` **raises `ImageComparisonFailure` on a size
  mismatch instead of returning an RMS**. `_report_regeneration` calls it with no `try`/`except`,
  before anything is copied — so the script would die having written nothing, for all three
  baselines. The existing boxplot baseline is therefore `git rm`'d first, so that function takes
  its "new baseline, no prior version to diff against" branch.
- **RMS is undefined across a resize**, so the PR quotes old/new canvas dimensions and the reason,
  not an RMS.

The histogram and correlation baselines are restored from git afterwards (the script rewrites all
three by design). `test_viz_snapshot.py`'s docstring records a per-plot headroom measurement that
instructs re-measurement after a layout change; tasks §3.5 does that rather than leaving the
docstring describing a render that no longer exists.

### Decision 11: Exclusive buckets keep a separate thinness count beside them

The buckets are mutually exclusive and `non_finite` takes precedence over `small`, which leaves
a gap: a cell with 4 finite values and 2 `inf`s is reported only as non-finite, so
`small_sample_group_count` reads 0 while `box_n_min` reads 4 against a floor of 5. A caller
gating on the former concludes "no thin boxes".

Merging the two buckets would break the exactly-one-bucket property; reversing the precedence
would file an all-`inf` cell (zero finite values) under "small" and lose the `inf` signal
entirely. So the taxonomy stands and `thin_box_count` is reported beside it — every drawn box
below the floor, whatever else is also wrong with it. The image says the same thing: the
non-finite clause carries each cell's finite `n`, so a reader sees `(1 inf, n=4)` rather than
being told only that the cell contains an infinity.

### Decision 12: Out of scope

- **#747** (heatmap not per-cell masked) and **#768** (snapshot can't catch a single-cell defect)
  are `plot_correlation_matrix` issues, untouched here.
- **Boxplot outlier handling** (#748's title also names it): verified that neither delegate path
  passes `showfliers`, `whis` or `sym`, so matplotlib's defaults apply and every point outside the
  whiskers is still *drawn* as a flier — nothing is hidden, so no field claims to disclose it.
  Same for histograms: `hist(data, bins=30)` passes no `range=`, so nothing is clipped. Both
  assessments go into the module docstrings (not only here, which gets archived) and both are
  pinned by tests, because Decision 3's honesty about fliers and Decision 4's reliance on the
  `(n=…)` titling both depend on delegate behavior that a version bump could move.
- **A residual gap remains and gets its own issue** (tasks §7): a zero-variance trait still renders
  a degenerate box with no flag, and `box_labels_annotated=False` leaves a render whose only
  sample-size signal is the note. Filing it follows the precedent that made #785 exist — a
  disclosed gap with no tracking issue is the one thing #466's review singled out.

## Risks / Trade-offs

- **Render cost.** +5% per page from relabelling; `tight_layout` only on the unbatched path,
  where the delegate left it undone. The cylinder boxplot smoke test is already at ~109-111s
  against a 120s client timeout, so tasks §5.5 measures it end-to-end rather than extrapolating,
  and raises the timeout if it lands above ~115s.
- **The disclosure is row-count, not row-identity.** It records how many rows backed each box, not
  which ones; reconstructing the rows six months later still requires the run's recorded source
  version resolving to the same bytes. Neither tool snapshots the source frame (`clustering` does;
  these do not), and this change does not add one.
- **The two tools' denominators differ.** A histogram bins every row; a box excludes rows whose
  genotype is null. For the same trait, histogram `n_plotted` ≥ the sum of that trait's box counts,
  differing by exactly `rows_missing_genotype`. Both field descriptions name that reconciliation,
  so a scientist comparing the two reports does not read the gap as data loss.
- **Test frames in the existing suite sit below the new floor.** `_wide_df` builds 12 rows over 3
  genotypes (n=4 per cell) and `test_viz_tool_classes_discovery`'s `_df()` sits exactly at 5.
  Every existing batched test's render becomes flagged the moment the note lands — expected, not a
  regression, but it means those frames cannot serve the "only one page has a thin group" test
  (tasks §1.2.4 builds its own).
- **`group_sample_sizes.csv` can be large** (traits × genotypes rows; 1.95 MB measured at
  cylinder width — six columns per cell, not one). It is a download, not a response field: the
  same trade-off `cross_experiment_correlations` already makes with its genotype-means CSVs.
- **Tick-label coupling.** Relabelling matches on tick text. If a future delegate stops labelling
  ticks with genotype values, the match fails, `box_labels_annotated` goes `False`, and the note
  still carries the disclosure — degraded, not wrong.
- **The floor is a judgement call.** See Open Questions.

## Open Questions

- **Is 5 the right floor?** The order-statistic argument is exact, but the agronomically
  meaningful minimum replicate count is a domain call. Moving the constant changes only which
  cells are *flagged* — never a reported `n`, never the CSV, never the scalars — so comparability
  of persisted runs is preserved.
- **Should the flagging also key on missing *fraction*, not just absolute `n`?** A trait with 200
  non-null rows out of 20,000 clears the floor and is never named in the response, though its
  `nan_fraction` is in the CSV. `max_nan_fraction` + the trait/group carrying it are reported as
  uncapped scalars so the response-level answer does not depend on the survivor count being
  small; a full `high_missingness` bucket is deferred rather than guessed at a threshold.
