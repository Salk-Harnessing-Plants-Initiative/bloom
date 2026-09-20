## ADDED Requirements

### Requirement: Boxplot Sample Sizes Are Disclosed Per Genotype Group

`plot_trait_boxplots` SHALL report how many observations back each rendered box. A box plot
carries no inherent sample-size signal — a box drawn from 2 points is pixel-identical to one drawn
from 200 — and the rendering delegate titles each subplot with the trait name alone.

**Counting semantics SHALL be stated, not assumed.** `n_plotted` is the count of non-null values,
which **includes** `±inf` (matching `pandas`' `isna` convention and the caveat `qc_inspect`
documents for its own missingness fields). `n_non_finite` is a **subset** of `n_plotted`, not an
addition to it, and `n_finite = n_plotted − n_non_finite`. Every flag and threshold comparison
SHALL be computed on the **finite** count, so a cell whose values are all `±inf` is never reported
as a healthy box.

The result SHALL report:

- **Uncapped scalar summaries over an explicitly named population** — `box_n_min`,
  `box_n_median`, `box_n_max`: the minimum, median and maximum finite observation count across
  every (trait, genotype) cell carrying **at least one finite observation**. Cells with none are
  excluded, because no describable box exists for them; they are carried completely by the
  buckets below. The result SHALL also report `n_boxes_summarized` (the size of that population)
  and `n_boxes_drawn` (cells the delegate draws a tick for, i.e. `n_plotted > 0`), so the
  population is unambiguous and distinct from the genotype count.
- **Four mutually exclusive buckets**, in precedence order, each capped at a documented maximum
  with its **uncapped** count reported alongside:
  - `no_data_traits` — a trait with no non-null value for any genotype. Reported at **trait**
    granularity, not as one entry per genotype: the delegate draws a single "No data" panel for
    such a trait, and expanding it per genotype would let one dead trait exhaust a cap by itself
    and evict every other flagged cell in the run.
  - `absent_genotype_groups` — `n_plotted == 0` for this (trait, genotype): no box, no tick, no
    gap. Reported as its own bucket rather than as the zero tail of the list below, because a
    reader can at least *see* a suspicious box; they cannot see an absence.
  - `non_finite_groups` — the cell carries at least one `±inf`, which corrupts the drawn box's
    quartiles.
  - `small_sample_groups` — `0 < n_finite <` the documented minimum.
- **The denominators** — rows read, distinct genotype groups, and rows excluded from every box
  because the row's own genotype value is null.
- **A missingness scalar** — the largest missing fraction across cells **that have finite data**,
  and the cell carrying it, so a box that is 99% missing but still clears the count floor is
  named in the response rather than only in the committed table. Absent cells SHALL be excluded
  from it: they sit at a missing fraction of 1.0 by construction, so including them would hand
  the field to a cell already fully reported as absent and mask the case it exists for.
- **A bucket-independent count of thin boxes.** Because the buckets are mutually exclusive, a
  box that is both thin and `inf`-bearing is reported only as non-finite — so the small-sample
  count is not the count of thin boxes, and a caller gating on it would read "no thin boxes"
  while the reported minimum sits below the floor. The result SHALL therefore also report every
  drawn box below the floor, whatever else is wrong with it.

Every (trait, genotype) pair SHALL fall into exactly one of those buckets or be unflagged. The
totality identity is three-termed, because the absent count deliberately excludes the cells of a
wholly-dead trait (those collapse into `no_data_traits`): `n_boxes_drawn` + the absent count +
(the no-data trait count x the genotype-group count) SHALL equal the number of resolved traits
times the number of genotype groups.

**Ordering SHALL be fully determined, and worst-first in each bucket's own terms.**
`small_sample_groups` SHALL be ordered by ascending finite count, then `(trait, genotype)`;
`non_finite_groups` SHALL be ordered by **descending** non-finite count, then `(trait, genotype)`
— severity there is how many values are non-finite, so ascending order would let a cell with one
`inf` survive the cap while one with hundreds is truncated away; `absent_genotype_groups`, where
every entry ties at zero by construction, SHALL be ordered by `(trait, genotype)` alone. Ties are the normal case in a replicated
design, so an unspecified tie-break would make which entries survive the cap — and therefore the
persisted manifest — irreproducible between runs over the same data. The ascending order is also
what makes the cap safe: it truncates the best-supported end.

The **complete, uncapped** per-(trait, genotype) table SHALL be persisted as a committed run
output with its own download link, carrying each cell's row total, `n_plotted`, `n_finite`,
`n_non_finite`, missing count and missing fraction — the row total is required so that "this
genotype has 3 plants" is distinguishable from "this genotype has 50 plants and 47 are missing
for this trait", which support opposite conclusions.

All new result fields, plus the auto-detected `genotype_column`, SHALL be stamped into the
persisted run's `params`. `genotype_column` is data-dependent and currently recorded nowhere in
the manifest, which would leave every genotype label in the disclosure unreproducible from a
stored run.

#### Scenario: A thinly-supported box is named with its sample size

- **WHEN** a genotype group has fewer finite observations for a trait than the documented minimum,
  but more than zero
- **THEN** `small_sample_groups` names that trait, that genotype and its exact count, and the
  uncapped `small_sample_group_count` reports how many such boxes exist

#### Scenario: A genotype that renders no box at all is still reported

- **WHEN** a genotype group has zero non-null observations for a selected trait, and that trait has
  data for at least one other genotype
- **THEN** `absent_genotype_groups` names that (trait, genotype) pair, it does **not** appear in
  `small_sample_groups`, and it is excluded from the `box_n_*` summaries

#### Scenario: A wholly empty trait is reported once, not once per genotype

- **WHEN** a selected trait has no non-null value for any genotype
- **THEN** it appears once in `no_data_traits`, contributes no entries to
  `absent_genotype_groups`, and does not consume that bucket's cap

#### Scenario: A cell whose values are all non-finite is not reported as a healthy box

- **WHEN** a (trait, genotype) cell's non-null values are all `±inf`
- **THEN** it is reported in `non_finite_groups`, it is **not** counted as unflagged, and it does
  not contribute a healthy-looking count to `box_n_min`/`_median`/`_max`

#### Scenario: The worst-supported box survives the cap, deterministically

- **WHEN** more boxes fall below the minimum than the reported list's cap, including boxes tied at
  the same count
- **THEN** the list is ordered by ascending count then `(trait, genotype)`, its length equals the
  cap, its first entry is the smallest finite-backed box in the whole run, two runs over the same
  data produce identical lists, and the uncapped count still reports the true total

#### Scenario: The full table is downloadable, not inlined

- **WHEN** a run completes successfully
- **THEN** the committed outputs include a per-(trait, genotype) sample-size table with its own
  `OutputLink`, covering every resolved trait and every genotype group with its row total,
  plotted, finite, non-finite and missing counts, and no single result field carries the full
  table inline

#### Scenario: Rows with no genotype are accounted for

- **WHEN** the frame contains rows whose genotype value is null
- **THEN** the result reports how many rows were excluded from every box for that reason, and
  those rows are counted in no genotype group

#### Scenario: The disclosure is recoverable from the manifest

- **WHEN** a run is persisted
- **THEN** the persisted run's `params` carries the same summaries, buckets and counts as the live
  result, plus the auto-detected `genotype_column`, and the manifest parses as strict JSON with no
  `NaN` or `Infinity` token

### Requirement: Boxplot Sample Sizes Are Visible On The Rendered Image

`plot_trait_boxplots` SHALL put the sample-size disclosure on the figure itself, because a caller
who only opens the saved PNG is precisely the caller this disclosure exists for.

**Each genotype's own count SHALL be appended to its axis tick label** (e.g. `GH_7440 (n=8)`).
The delegate labels those ticks with the genotype values themselves in both orientations, so the
labels SHALL be rewritten by matching tick text against the computed counts — never by assuming
subplot geometry, axis identity or the orientation switch, which would risk labelling the wrong
box. A tick whose text matches no known genotype SHALL be left unchanged, and the result SHALL
report whether the annotation was applied, so a caller is never told the image carries `n` when
the match failed.

**A sample-size note SHALL additionally be drawn below the axes, unconditionally** — not only
when something is flagged. Per-box labels cannot show what is not drawn (an absent group has no
tick to label), and a note that appears only on flagged runs would leave every other image as
uninformative as it is today while making its own absence carry a sufficiency claim the
underlying threshold does not support. The note SHALL state the minimum, median and maximum
observations per box and name **every denominator it is computed over** — the number of boxes,
the number of genotype groups and the number of traits — so that no number in it can be read
against the wrong population.

When any box on the figure is below the minimum, absent, or affected by non-finite values, the
note SHALL escalate to a visibly marked warning that names the affected groups (capped, with a
"+N more" summary), reports the flagged count **as a fraction of the total** so a reader can
calibrate severity rather than only presence, and points at the committed sample-size table.

On a paginated render the note SHALL be **page-scoped** and SHALL say so in its own text: its
statistics and named groups SHALL cover only the traits rendered on that page. The result SHALL report the
run-wide note as a field and that value SHALL be stamped into the persisted run's `params`.

The per-page strings SHALL **not** be stamped. Each is a pure function of that page's entry in
`page_traits`, the committed sample-size table, and the documented floor and caps — all of which
the manifest or its outputs already carry — so stamping them duplicates recoverable data, and at
cylinder width it appends tens of pages' worth of prose to a `manifest.json` that is re-validated
in full on every subsequent run for that tool and experiment.

`plot_trait_histograms` SHALL NOT gain an equivalent note: its delegate already titles every
panel with that trait's own `(n=…)`.

#### Scenario: Every box carries its own sample size

- **WHEN** a run completes successfully and the delegate's tick labels are the genotype values
- **THEN** each genotype's tick label on each trait's panel carries that (trait, genotype) cell's
  own observation count, and the result reports that the annotation was applied

#### Scenario: An unrecognized tick label is left alone and reported

- **WHEN** a tick label matches no genotype in the computed counts
- **THEN** that label is left unchanged and the result reports that the per-box annotation was not
  applied, so the note remains the authoritative on-image signal

#### Scenario: An ordinary render still discloses its sample sizes

- **WHEN** every box in a run clears the documented minimum
- **THEN** the rendered figure still carries a note stating the minimum, median and maximum
  observations per box together with the box, genotype-group and trait counts it covers, and the
  note carries no warning marker

#### Scenario: A flagged render names the affected groups and their share

- **WHEN** any box is below the minimum, absent, or affected by non-finite values
- **THEN** the note drawn on the figure is marked as a warning, names the affected groups up to
  its cap, reports the flagged count as a fraction of the total, and directs the reader to the
  committed sample-size table

#### Scenario: A paginated render does not cross-contaminate pages

- **WHEN** the selection is wide enough to paginate and a thinly-supported group exists on one
  page only
- **THEN** that group is named on its own page's note and on no other page's note, each page's
  statistics cover only that page's traits, the note's own text identifies itself as page-scoped,
  and the persisted run's `params` carries the run-wide note (not the per-page strings, which
  are reconstructible from `page_traits` and the committed table)

#### Scenario: The histogram render is unchanged

- **WHEN** `plot_trait_histograms` renders a figure
- **THEN** no sample-size note is drawn onto it, and each panel's title still carries that trait's
  own observation count from the delegate

### Requirement: Histogram Trait Sample Sizes And Missingness Are Disclosed

`plot_trait_histograms` SHALL report, for each resolved trait, how many observations were actually
binned and how much of the raw column was missing — the delegate drops null values silently, and
an all-null trait renders a literal "No data" panel that appears nowhere in the structured result.

The result SHALL report uncapped minimum/median/maximum binned counts over **every resolved
trait**, including traits with none — unlike the boxplot summaries, which exclude absent cells,
because here a panel *is* drawn for every selected trait (an empty one carries a literal "No data"
label), so a zero is describing something the reader can see. It SHALL report a capped list of traits falling below the documented minimum (each with its
binned count and missing fraction) ordered by ascending count then trait name, with an uncapped
count alongside, the largest missing fraction and the trait carrying it, the number of rows read,
and SHALL persist the complete per-trait table as a committed run output with its own download
link. A trait with zero binned observations SHALL be included in the capped list. All new fields
SHALL be stamped into the persisted run's `params`.

The reported counts SHALL be reconcilable with `plot_trait_boxplots`' counts for the same
experiment: a histogram bins every row while a box excludes rows whose genotype is null, and the
field descriptions SHALL name that difference so the gap is not read as data loss.

#### Scenario: A trait's binned count and missingness are reported

- **WHEN** a selected trait has null values in the raw frame
- **THEN** the committed per-trait table reports that trait's binned count, missing count and
  missing fraction, and the uncapped summaries reflect it

#### Scenario: An all-null trait is named in the result, not only in the image

- **WHEN** a selected trait is entirely null
- **THEN** that trait appears in the reported low-sample list with a binned count of zero, rather
  than being discoverable only by opening the image and reading a "No data" panel

#### Scenario: A heavily-missing trait is named even when its count clears the floor

- **WHEN** a trait's binned count clears the documented minimum but most of its column is missing
- **THEN** the result's largest-missing-fraction scalar reports that fraction and names that trait

#### Scenario: The summaries stay uncapped when the list is truncated

- **WHEN** more traits fall below the minimum than the reported list's cap
- **THEN** the list is ordered by ascending binned count then trait name and truncated to the cap,
  the uncapped count is still reported, and the summaries cover every resolved trait

### Requirement: Non-Finite Trait Values Are Disclosed Rather Than Silently Distorting A Plot

`plot_trait_histograms` and `plot_trait_boxplots` SHALL each surface `+inf`/`-inf` values in the
resolved trait selection in the way that matches its own delegate's failure mode — neither
delegate handles them safely, and the two fail differently. Both tools read raw, uncleaned data,
so a non-finite value is reachable in normal use: no QC step has removed it yet. (The third tool in
this capability, `plot_correlation_matrix`, reports such a trait through its own
`zero_variance_traits` field and is not changed by this requirement.)

`plot_trait_histograms` SHALL detect non-finite values **before creating a run** and raise a
structured `assumption_violated` error naming the affected traits and pointing at the cleaning
tools, rather than letting the delegate raise a rendering error that names no trait and offers no
remedy. No successful run becomes a failure: the render already cannot succeed on such data.

`plot_trait_boxplots` SHALL NOT fail on non-finite values — the figure remains useful for every
unaffected group — and SHALL instead name the affected traits and (trait, genotype) cells in its
result, count them in its committed table, and include them in the warning drawn onto the figure.
A single `±inf` is enough to corrupt a box: the drawn median shifts to a value no observation
supports while the upper quartile and whisker become `NaN`, so the box does not read as broken
data.

Neither tool SHALL strip or replace non-finite values before rendering: both are pre-clean EDA
views, and silently altering the data the caller asked to see raw would diverge the image from
what `qc_inspect` reports for the same frame.

#### Scenario: A histogram over a non-finite trait fails with a named trait and a remedy

- **WHEN** any resolved trait for `plot_trait_histograms` contains `+inf` or `-inf`
- **THEN** the tool raises `BloomMCPError(code="assumption_violated")` naming the affected
  trait(s) and a remedy, the rendering delegate is never called, and no run is created and no
  staging directory is left behind

#### Scenario: A boxplot over a non-finite trait renders, discloses, and warns

- **WHEN** any resolved trait for `plot_trait_boxplots` contains `+inf` or `-inf`
- **THEN** the run completes, the result names that trait and the affected (trait, genotype)
  cells, the committed table reports their non-finite counts, and the note drawn on the figure is
  marked as a warning naming them

#### Scenario: The raw values reach the delegate unaltered

- **WHEN** either tool renders a selection containing non-finite or missing values and the render
  proceeds
- **THEN** the frame passed to the rendering delegate still contains those values unchanged

### Requirement: The Sample-Size Floor Is Owned By The Trait-Plot Tools

The minimum observation count SHALL be a constant owned by the shared visualization module
itself — the floor `plot_trait_histograms` and `plot_trait_boxplots` flag against — NOT an alias for the QC per-trait completeness convention
(`_qc_shared._CANONICAL_MIN_SAMPLES_PER_TRAIT`). The two answer different questions — "enough
samples to keep a trait during cleaning" versus "enough points for a box to be made of data" — and
aliasing would let a QC-side retune silently move which boxes these tools flag and what their
rendered note says.

The floor SHALL be documented as a **degeneracy** bound, not a sufficiency or significance
threshold. Clearing it means only that the drawn box's quartiles are actual observations rather
than interpolations between adjacent ones; it makes no claim about the whiskers or flier dots,
which remain artifact-prone well above it. A reported count SHALL never be altered by the floor —
the floor determines only which cells are *flagged*, so changing it later cannot break
comparability with persisted runs.

Both tools SHALL flag at the same boundary.

#### Scenario: The floor is owned, not inherited

- **WHEN** the visualization module's minimum-sample constant is inspected
- **THEN** it is defined in that module with its own documented rationale, and the module does not
  bind the QC completeness constant

#### Scenario: Both tools flag at the same boundary

- **WHEN** a trait or cell carries exactly one fewer observation than the documented minimum, and
  when it carries exactly the minimum
- **THEN** both tools flag the first and neither flags the second

### Requirement: Sample-Size Summaries Degrade Safely On Empty Populations

Every reported sample-size summary SHALL be optional and SHALL be reported as null when its
population is empty, never as `NaN` and never by failing the run. A frame whose detected genotype
column is entirely null, or which carries no rows, renders successfully today; computing a minimum
or a median over the resulting empty population would raise or produce `NaN`, turning a
succeeding run into a failure or writing a token that strict JSON readers reject from the
manifest.

The note drawn on the figure SHALL state that no box was drawn, rather than printing null
statistics.

#### Scenario: A frame with no drawable box still completes

- **WHEN** `plot_trait_boxplots` reads a frame whose detected genotype column is entirely null, or
  which has no rows
- **THEN** the run still completes, every sample-size summary is null rather than `NaN`, the
  reported genotype-group count is zero, the note drawn on the figure says no box was drawn, and
  both the result and the persisted manifest parse as strict JSON

#### Scenario: A zero-row histogram reports zeros, not nulls

- **WHEN** `plot_trait_histograms` reads a frame with no rows
- **THEN** the run still completes and its summaries report zero — a panel is still drawn per
  trait, so the population is not empty — and the result parses as strict JSON

## MODIFIED Requirements

### Requirement: Paginated Figure Persistence

`plot_trait_histograms`/`plot_trait_boxplots` SHALL, once the selected trait count exceeds
`_viz_shared.TRAIT_BATCH_THRESHOLD`, render via the delegate's batched variant and persist one
committed **figure** output (and one `OutputLink`) per page, rather than a single figure.

A run MAY additionally commit non-figure outputs — specifically the sample-size tables required
above — which are persisted **once per run**, not once per page. `page_traits` SHALL map only the
committed figure outputs to their traits, so a non-figure output never appears there as if it were
a page, and the reported page count SHALL count only rendered figures. The count of committed
outputs is therefore no longer equal to the page count.

#### Scenario: A wide selection persists one output per page

- **WHEN** the resolved trait selection exceeds `TRAIT_BATCH_THRESHOLD` traits
- **THEN** the committed run's `outputs` contains one figure entry per rendered page, each with
  its own `OutputLink`, and the result reports the page count

#### Scenario: Each page's traits are named, not just its count

- **WHEN** any of `plot_trait_histograms`/`plot_trait_boxplots` completes successfully
- **THEN** the result's `page_traits` maps every committed **figure** output filename to the exact
  trait columns rendered on that page (a single entry, covering every `resolved_trait_columns`,
  when not batched)

#### Scenario: A committed sample-size table is not mistaken for a page

- **WHEN** a run commits a sample-size table alongside one or more figure pages
- **THEN** that table appears in `outputs` and `output_links` with its own link but not in
  `page_traits`, the reported page count equals the number of rendered figures, and the number of
  committed outputs is that page count plus one
