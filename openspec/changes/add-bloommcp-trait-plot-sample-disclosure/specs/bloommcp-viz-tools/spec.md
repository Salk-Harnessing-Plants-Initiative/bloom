## ADDED Requirements

### Requirement: Boxplot Sample Sizes Are Disclosed Per Genotype Group

`plot_trait_boxplots` SHALL report how many observations back each rendered box. A box plot
carries no inherent sample-size signal — a box drawn from 2 points is pixel-identical to one
drawn from 200 — and the rendering delegate titles each subplot with the trait name alone, so
today a caller has no way to tell them apart without independently re-querying the data.

The result SHALL report, for the resolved trait selection:

- **uncapped scalar summaries** — the minimum, median, and maximum non-null count across
  **every** (trait, genotype) box in the run, so a capped list can never misrepresent the
  population it is drawn from;
- **`small_sample_groups`** — the boxes whose non-null count is above zero but below the viz
  family's documented minimum, each naming its trait, its genotype, and its count, ordered by
  **ascending** count (weakest evidence first) and capped at a documented maximum, with an
  uncapped count reported alongside. The ascending order is what makes the cap safe: it truncates
  the best-supported end, so the least-supported box in the whole run is always reported;
- **`absent_genotype_groups`** — the (trait, genotype) pairs with **zero** non-null observations,
  reported as their own bucket rather than as the tail of the list above, because such a group is
  not drawn at all: the delegate drops it before taking its tick labels, so it leaves no tick, no
  box, and no gap in the panel. Capped the same way, with its own uncapped count;
- **the denominators** — the number of rows read, the number of distinct genotype groups, and the
  number of rows excluded from every box because the row's own genotype value is null.

Every (trait, genotype) pair SHALL fall into exactly one of: absent (count zero), small (count
above zero and below the minimum), or unflagged.

The **complete, uncapped** per-(trait, genotype) count table SHALL be persisted as a committed
run output with its own download link, so that "every box's sample size is recoverable" holds at
any scale without placing a table of traits × genotypes rows into the response. All new result
fields SHALL additionally be stamped into the persisted run's `params`.

#### Scenario: A thinly-supported box is named with its sample size

- **WHEN** a genotype group has fewer non-null observations for a trait than the documented
  minimum, but more than zero
- **THEN** `small_sample_groups` names that trait, that genotype, and its exact count, and
  `small_sample_group_count` reports the uncapped number of such boxes

#### Scenario: A genotype that renders no box at all is still reported

- **WHEN** a genotype group has zero non-null observations for a selected trait
- **THEN** `absent_genotype_groups` names that (trait, genotype) pair, it does **not** appear in
  `small_sample_groups`, and the rendered panel for that trait contains no tick for that genotype

#### Scenario: The worst-supported box survives the cap and the summaries stay uncapped

- **WHEN** more boxes fall below the minimum than the reported list's cap
- **THEN** `small_sample_groups` is ordered by ascending count, its length equals the cap, its
  first entry is the smallest non-empty box in the whole run, the uncapped count is still
  reported, and the minimum/median/maximum summaries are computed over **every** box rather than
  only the reported sample

#### Scenario: The full table is downloadable, not inlined

- **WHEN** a run completes successfully
- **THEN** the committed outputs include a per-(trait, genotype) sample-size table with its own
  `OutputLink`, covering every resolved trait and every genotype group, and no single result
  field carries the full table inline

#### Scenario: Rows with no genotype are accounted for

- **WHEN** the frame contains rows whose genotype value is null
- **THEN** the result reports how many rows were excluded from every box for that reason, and
  those rows are counted in no genotype group

#### Scenario: The disclosure is recoverable from the manifest

- **WHEN** a run is persisted
- **THEN** the persisted run's `params` carries the same sample-size summaries, flagged lists,
  and counts as the live result

### Requirement: Boxplot Sample Sizes Are Visible On The Rendered Image

`plot_trait_boxplots` SHALL draw a sample-size note directly onto every rendered figure before it
is saved — **unconditionally**, not only when something is flagged. A caller who only ever opens
the saved PNG is precisely the caller this disclosure exists for, and a note that appears only on
flagged runs would leave every other image exactly as uninformative as it is today while making
its own absence carry a sufficiency claim the underlying threshold does not support.

The note SHALL state the minimum, median, and maximum number of observations per box, and the
number of genotype groups those cover. When any box on the figure is below the minimum, absent,
or affected by non-finite values, the note SHALL escalate to a visibly marked warning naming the
affected groups (capped, with a "+N more" summary beyond the cap) and SHALL point at the
committed sample-size table for the complete set.

On a paginated (batched) render the note SHALL be **page-scoped**: its statistics and named
groups SHALL be restricted to the traits actually rendered on that page, so a page never carries
statistics for a trait its viewer cannot see. The text drawn on any page SHALL be reconstructible
from the result's `page_traits` and the committed sample-size table, and the result SHALL report
the run-wide note as a field, stamped into the persisted run's `params`.

`plot_trait_histograms` SHALL NOT gain an equivalent note: its delegate already titles every
panel with that trait's own `(n=…)`, so the image is not missing the signal.

#### Scenario: An ordinary render still discloses its sample sizes

- **WHEN** every box in a run clears the documented minimum
- **THEN** the rendered figure still carries a note stating the minimum, median, and maximum
  observations per box and the number of genotype groups, and the note carries no warning marker

#### Scenario: A flagged render names the affected groups on the image

- **WHEN** any box is below the minimum, absent, or affected by non-finite values
- **THEN** the note drawn on the figure is marked as a warning, names the affected
  trait/genotype groups up to its cap (summarizing the remainder as "+N more"), and directs the
  reader to the committed sample-size table

#### Scenario: A paginated render does not cross-contaminate pages

- **WHEN** the selection is wide enough to paginate and a thinly-supported group exists on one
  page only
- **THEN** that group is named on its own page's note and on no other page's note, and each
  page's statistics are computed only over the traits rendered on that page

#### Scenario: The histogram render is unchanged

- **WHEN** `plot_trait_histograms` renders a figure
- **THEN** no sample-size note is drawn onto it, and each panel's title still carries that
  trait's own observation count from the delegate

### Requirement: Histogram Trait Sample Sizes And Missingness Are Disclosed

`plot_trait_histograms` SHALL report, for each resolved trait, how many observations were
actually binned and how much of the raw column was missing — the delegate drops null values
silently, and an all-null trait renders a literal "No data" panel that appears nowhere in the
structured result.

The result SHALL report uncapped minimum/median/maximum binned counts across the selection, a
capped, ascending list of traits falling below the documented minimum (each with its binned count
and its missing fraction) with an uncapped count alongside, and SHALL persist the complete
per-trait table as a committed run output with its own download link. A trait with zero binned
observations — the "No data" panel — SHALL be included in that list. All new fields SHALL be
stamped into the persisted run's `params`.

#### Scenario: A trait's binned count and missingness are reported

- **WHEN** a selected trait has null values in the raw frame
- **THEN** the committed per-trait table reports that trait's binned count, its missing count,
  and its missing fraction, and the result's uncapped summaries reflect it

#### Scenario: An all-null trait is named in the result, not only in the image

- **WHEN** a selected trait is entirely null
- **THEN** that trait appears in the reported low-sample list with a binned count of zero, rather
  than being discoverable only by opening the image and reading a "No data" panel

#### Scenario: The summaries stay uncapped when the list is truncated

- **WHEN** more traits fall below the minimum than the reported list's cap
- **THEN** the list is ordered by ascending binned count and truncated to the cap, the uncapped
  count is still reported, and the minimum/median/maximum summaries cover every resolved trait

### Requirement: Non-Finite Trait Values Are Disclosed Rather Than Silently Distorting A Plot

Each plotting tool SHALL surface `+inf`/`-inf` values in the resolved trait selection in the way
that matches its own delegate's failure mode — neither delegate handles them safely, and the two
fail differently. Both tools read raw, uncleaned data, so a non-finite value is reachable in
normal use: no QC step has removed it yet.

`plot_trait_histograms` SHALL detect non-finite values in the resolved trait selection **before
creating a run** and raise a structured `assumption_violated` error naming the affected traits
and pointing at the cleaning tools, rather than letting the delegate raise a rendering error that
names no trait and offers no remedy. No successful run becomes a failure: the render already
cannot succeed on such data.

`plot_trait_boxplots` SHALL NOT fail on non-finite values — the figure remains useful for every
unaffected group — and SHALL instead name the affected traits in its result (capped, with an
uncapped count), count them per (trait, genotype) in its committed sample-size table, and include
them in the warning drawn onto the figure. Such a group's quartiles are `NaN`, so its box renders
incomplete while every neighbouring box looks normal.

Neither tool SHALL strip or replace non-finite values before rendering: both are pre-clean EDA
views, and silently altering the data the caller asked to see raw would diverge the image from
what `qc_inspect` reports for the same frame.

#### Scenario: A histogram over a non-finite trait fails with a named trait and a remedy

- **WHEN** any resolved trait for `plot_trait_histograms` contains `+inf` or `-inf`
- **THEN** the tool raises `BloomMCPError(code="assumption_violated")` naming the affected
  trait(s) and a remedy, and no run is created and no staging directory is left behind

#### Scenario: A boxplot over a non-finite trait renders, discloses, and warns

- **WHEN** any resolved trait for `plot_trait_boxplots` contains `+inf` or `-inf`
- **THEN** the run completes, the result names that trait as non-finite, the committed
  sample-size table reports the non-finite count for each affected (trait, genotype) pair, and
  the note drawn on the figure is marked as a warning that names it

## MODIFIED Requirements

### Requirement: Paginated Figure Persistence

`plot_trait_histograms`/`plot_trait_boxplots` SHALL, once the selected trait count exceeds
`_viz_shared.TRAIT_BATCH_THRESHOLD`, render via the delegate's batched variant and persist one
committed **figure** output (and one `OutputLink`) per page, rather than a single figure.

A run MAY additionally commit non-figure outputs — specifically the sample-size tables required
above — which are persisted **once per run**, not once per page. `page_traits` SHALL map only the
committed figure outputs to their traits, so a non-figure output never appears there as if it
were a page.

#### Scenario: A wide selection persists one output per page

- **WHEN** the resolved trait selection exceeds `TRAIT_BATCH_THRESHOLD` traits
- **THEN** the committed run's `outputs` contains one figure entry per rendered page, each with
  its own `OutputLink`, and the result reports the page count

#### Scenario: Each page's traits are named, not just its count

- **WHEN** any of `plot_trait_histograms`/`plot_trait_boxplots` completes successfully
- **THEN** the result's `page_traits` maps every committed **figure** output filename to the
  exact trait columns rendered on that page (a single entry, covering every
  `resolved_trait_columns`, when not batched)

#### Scenario: A committed sample-size table is not mistaken for a page

- **WHEN** a run commits a sample-size table alongside one or more figure pages
- **THEN** that table appears in `outputs` with its own `OutputLink` but not in `page_traits`,
  and the reported page count counts only the rendered figures
