## ADDED Requirements

### Requirement: Strong Correlation Pairs Report Their Overlap And Confidence Interval

`plot_correlation_matrix`'s result SHALL report `strong_correlation_pairs`: the trait pairs
counted by `strong_positive_correlations`/`strong_negative_correlations`, each carrying the
pair's Pearson coefficient, its pairwise non-null overlap `n`, and a Fisher-z 95% confidence
interval. The minimum-overlap floor is a *degeneracy* guard, not a significance test — clearing
it means only that a coefficient is not the arithmetic artifact an n=2/n=3 overlap guarantees.
A bare count therefore cannot tell a caller whether it rests on n=10 (where r=0.7 has a 95% CI
of roughly [0.13, 0.92]) or on n=1000, and the per-pair overlap needed to tell them apart is
already computed internally.

The cutoff that selects these pairs SHALL be a single constant shared by the pair list and by
`strong_positive_correlations`/`strong_negative_correlations`, and the list SHALL be derived as
the union of the two per-sign count masks, so that no edit can make the counts and the list
that explains them disagree.

The list SHALL be ordered by **ascending** overlap `n` — weakest evidence first — and SHALL be
capped at a documented maximum, because at cylinder scale (~846 traits) an uncapped list can
exceed 100,000 entries. The ascending order is what makes the cap safe: it truncates the
best-supported end, so the least-supported pair in the count is always reported.

Because a capped list is a biased sample of the population it is drawn from, the result SHALL
additionally report **uncapped scalar summaries** over *every* strong pair — the minimum,
median, and maximum overlap `n` — so a caller can tell whether the reported low-`n` pairs are
representative or exceptional. The two existing counts remain the authoritative totals and
SHALL NOT change value.

The confidence interval's description SHALL disclose the assumptions that make it weaker than
its "95%" label implies, because that label is the most calibrated-sounding value this tool
emits. Beyond not being a significance test and not being corrected for multiplicity, it SHALL
name two limits specific to how it is used here:

* **Selection.** The interval is not valid for a pair selected *because* `|r|` cleared the
  tool's own cutoff, since the selection uses the same data as the interval. Every pair in
  `strong_correlation_pairs` is selected that way by construction.
* **Independence.** Rows are treated as independent observations, and `overlap_n` counts rows,
  not independent units. Root-trait data is typically clustered (replicates within a genotype,
  scans within a plant), which makes the true interval wider than the reported one.

The confidence interval SHALL be well-defined in JSON for every reported pair. Where the Fisher
transform is undefined — a coefficient of exactly ±1.0, or an overlap too small for its standard
error — the interval SHALL be reported as null rather than as a fabricated zero-width interval
or a non-finite `NaN`/`Infinity` token, which is both false precision and invalid JSON.

#### Scenario: A strong pair discloses the overlap behind it

- **WHEN** a call reports a non-zero `strong_positive_correlations` or
  `strong_negative_correlations`, and the number of strong pairs does not exceed the reported
  list's cap
- **THEN** `strong_correlation_pairs` names every pair behind those counts, each with its
  coefficient, its pairwise overlap `n`, and a 95% confidence interval

#### Scenario: The weakest-supported pair survives the cap, and the summaries stay uncapped

- **WHEN** more strong pairs exist than the reported list's cap
- **THEN** `strong_correlation_pairs` is ordered by ascending overlap `n`, its length equals the
  cap, its first entry is the strong pair with the smallest overlap in the whole selection, the
  reported minimum/median/maximum overlap summaries are computed over **all** strong pairs (not
  only the capped sample), and `strong_positive_correlations` + `strong_negative_correlations`
  still report the true total

#### Scenario: An undefined interval is null, never a non-finite token

- **WHEN** a strong pair's coefficient is exactly ±1.0 over an overlap above the minimum, or its
  overlap is too small for the Fisher standard error
- **THEN** its reported confidence-interval bounds are null — not `±Infinity`, not `NaN`, and
  not a zero-width interval implying perfect precision — and both the result and the persisted
  manifest remain valid JSON

#### Scenario: The disclosure is recoverable from the manifest

- **WHEN** a run is persisted
- **THEN** the persisted run's `params["strong_correlation_pairs"]` equals the result's
  `strong_correlation_pairs`, and the uncapped overlap summaries are stamped alongside it

### Requirement: Locally-Constant Overlap Pairs Complete The Blank-Cell Taxonomy

`plot_correlation_matrix`'s result SHALL report `locally_constant_trait_pairs`: pairs whose
correlation is `NaN` despite both traits being globally non-constant and the pair clearing the
minimum-overlap floor — one trait takes the same value on exactly the rows where both are
non-null, so there is no variance within the shared overlap. Every off-diagonal `NaN` in the
guarded correlation matrix SHALL therefore fall into exactly one of `zero_variance_traits`,
`low_overlap_trait_pairs`, or `locally_constant_trait_pairs`, so the tool can always say *why*
a given cell is blank.

This bucket SHALL be derived from the guarded correlation matrix by elimination rather than by
recomputing a within-overlap variance, so exhaustiveness holds by construction and does not
depend on a numerical tolerance agreeing with pandas'. The elimination SHALL subtract the
zero-variance traits and the raw sub-threshold-overlap **mask**, not the published
`low_overlap_trait_pairs` list — that list already excludes pairs involving a zero-variance
trait, so subtracting it instead of the mask would leak those pairs into this bucket.

Because this bucket's population is independent of the other two — a trait that is globally
non-constant but takes one value on all but a few rows is locally constant against *every*
partner while `low_overlap_trait_pairs` stays empty — the list SHALL be capped at a documented
maximum and the result SHALL additionally report an **uncapped count** of such pairs, so a
caller can tell a two-pair frame from an eight-hundred-pair one.

This bucket SHALL NOT populate `heatmap_caveat` — see that field's own requirement.

#### Scenario: A locally-constant pair is named, not left unexplained

- **WHEN** two selected traits are each globally non-constant and overlap in at least the
  minimum number of non-null rows, but one of them is constant across exactly that overlap
- **THEN** the pair appears in `locally_constant_trait_pairs`, appears in neither
  `zero_variance_traits` nor `low_overlap_trait_pairs`, and counts toward neither
  `strong_positive_correlations` nor `strong_negative_correlations`

#### Scenario: Every blank cell has exactly one reason

- **WHEN** a selection is correlated whose data exercises all three blank-cell causes at once
- **THEN** for every off-diagonal `NaN` cell `(i, j)` of the guarded correlation matrix, exactly
  one of the following holds: trait `i` or trait `j` appears in `zero_variance_traits`; the pair
  `(i, j)` appears in `low_overlap_trait_pairs`; the pair `(i, j)` appears in
  `locally_constant_trait_pairs`

#### Scenario: A pair that is both low-overlap and zero-variance is claimed by exactly one bucket

- **WHEN** a pair's overlap falls below the minimum **and** one of its traits is in
  `zero_variance_traits`
- **THEN** the pair is accounted for by `zero_variance_traits` alone — it appears in neither
  `low_overlap_trait_pairs` (which already excludes it) nor `locally_constant_trait_pairs`

#### Scenario: The bucket is capped but its true size is still reported

- **WHEN** more locally-constant pairs exist than the reported list's cap
- **THEN** `locally_constant_trait_pairs` is truncated to the cap and the reported
  locally-constant pair count still equals the true, uncapped total

#### Scenario: The disclosure is recoverable from the manifest

- **WHEN** a run is persisted
- **THEN** the persisted run's `params["locally_constant_trait_pairs"]` equals the result's
  `locally_constant_trait_pairs`, and the uncapped count is stamped alongside it

## MODIFIED Requirements

### Requirement: Zero-Variance Traits Disclosed In Correlation Counts

`plot_correlation_matrix`'s result SHALL report `zero_variance_traits`: the selected traits
whose own standard deviation is not a usable positive finite number, and for which the tool
therefore reports no correlation at all. This field discloses which traits are silently
excluded rather than leaving the counts to look complete.

The guard SHALL be `not (0 < std(skipna=True) < inf)`, and the field's description SHALL
enumerate every case it files, because "zero variance" names only the commonest one:

* constant (std `0`), entirely NaN (std `NaN`), and exactly one non-null value (std `NaN`
  because `ddof=1` needs two observations);
* a trait carrying a **non-finite value** (`+inf`/`-inf`), whose std is `NaN` for that reason
  rather than for lack of variation;
* a trait of finite but enormous values whose sum of squares **overflows**, making std `+inf`.
  The upper bound is required: `inf > 0` is true, so a `std > 0` test admits such a trait as
  healthy, after which pandas returns `NaN` for its coefficients and the pair is filed as
  locally constant — asserting the opposite of the actual defect.

The field's description SHALL also name the case this guard **cannot** distinguish: a
genuinely varying trait whose variance underflows to exactly `0.0` is reported as a constant.

Traits reported here SHALL be excluded from `strong_positive_correlations`,
`strong_negative_correlations` and `strong_correlation_pairs` by an explicit mask. Exclusion
SHALL NOT be left to rest on "pandas returns `NaN` and `NaN > cutoff` is false": that
reasoning does not hold for a trait carrying an infinity, because pandas masks each pair with
`isfinite` and returns an ordinary coefficient over the remaining rows.

#### Scenario: A constant trait is named, not silently excluded

- **WHEN** a selected trait has zero variance (or is entirely NaN) in the raw data
- **THEN** it appears in the result's `zero_variance_traits`, and neither
  `strong_positive_correlations` nor `strong_negative_correlations` includes any pair involving
  it

#### Scenario: A non-finite trait is named here and counted nowhere

- **WHEN** a selected trait is otherwise varying but contains `+inf` or `-inf`
- **THEN** it appears in the result's `zero_variance_traits`, whose description names the
  non-finite case explicitly; it appears in no other disclosure list; and it appears in
  neither `strong_correlation_pairs` nor either strong-correlation count

#### Scenario: A trait whose variance overflows is not mislabelled as locally constant

- **WHEN** two selected traits are perfectly correlated but carry finite values large enough
  that their variance overflows to `+inf`
- **THEN** both appear in `zero_variance_traits`, and neither appears in
  `locally_constant_trait_pairs`

### Requirement: Low-Overlap Trait Pairs Excluded And Disclosed

`plot_correlation_matrix` SHALL compute Pearson correlation with a minimum pairwise-overlap
requirement (`min_periods`) and SHALL report `low_overlap_trait_pairs`: pairs whose overlapping
non-null observations fell below that minimum. Raw data can have disjoint per-trait missingness,
and a near-empty overlap (as few as 2 points) is otherwise always exactly ±1.0-correlated — a
spurious "strong correlation" from an unreliable sample. A pair already explained by
`zero_variance_traits` SHALL NOT also appear in `low_overlap_trait_pairs`.

That minimum SHALL be a constant owned by `plot_correlation_matrix` itself, not an alias for
`qc_clean`/`qc_inspect`'s canonical minimum-samples-per-trait threshold. The two express
different concepts — a per-column completeness convention for deciding what to drop, versus a
degeneracy floor on a bivariate statistic — and coincide in value only by accident. Aliasing
them means a future QC-side retune silently moves which pairs this tool counts and flags. The
value is unchanged by the decoupling; only the coupling is removed, and the two thresholds are
thereafter free to diverge without either one being wrong.

#### Scenario: A near-empty overlap is excluded, not miscounted

- **WHEN** two selected traits overlap in fewer non-null rows than the minimum-overlap threshold
- **THEN** that pair's coefficient counts toward neither `strong_positive_correlations` nor
  `strong_negative_correlations`, and the pair appears in `low_overlap_trait_pairs`

#### Scenario: The overlap threshold is owned, not inherited

- **WHEN** `plot_correlation_matrix`'s minimum-overlap constant is inspected
- **THEN** it is defined by `plot_correlation_matrix` itself, the QC module's
  minimum-samples-per-trait name is not bound in the module's namespace, and the threshold's
  value is unchanged from before the decoupling — so no pair changes bucket

### Requirement: Rendered Heatmap Masking Mismatch Is Disclosed

`plot_correlation_matrix`'s persisted PNG SHALL be understood to NOT be masked the way the
summary counts/disclosure lists are — it is rendered by a separate, independent delegate call
running its own unguarded correlation. The result SHALL carry a `heatmap_caveat` field,
populated whenever `zero_variance_traits` or `low_overlap_trait_pairs` is non-empty, directing
the caller to cross-check those fields before trusting a highlighted cell in the image; `None`
when neither is populated. `heatmap_caveat` SHALL name the actual flagged trait(s)/pair(s)
directly (capped at 10, with a "+N more" summary beyond that), not merely a count — so that a
caller viewing only the saved PNG can cross-reference a name against the image's own axis
labels. The same non-`None` value SHALL be drawn as a visible annotation directly onto the
rendered figure before it is saved, and SHALL also be stamped into the persisted run's
`params` — a caller who only opens the saved PNG, or only reads the manifest later, must still
receive the warning, not only a caller reading the live JSON response.

`locally_constant_trait_pairs` SHALL NOT be added to that trigger. The caveat's text is
specifically about cells the image **colors confidently** despite thin support; a
locally-constant pair is `NaN` in the delegate's own unguarded computation too, so it renders
blank and that text would be false of it. Widening the trigger without rewriting the text would
make the warning wrong for the new bucket; rewriting the text would change the disclosure
callers already receive for the existing two. The new bucket is disclosed through its own
result field and the manifest instead. This trigger set as a whole SHALL be revisited if the
underlying masking mismatch is ever fixed upstream, since the caveat's reason to exist
disappears with it.

#### Scenario: A flagged pair still renders unmasked, and the caveat says so

- **WHEN** `zero_variance_traits` or `low_overlap_trait_pairs` is non-empty for a call
- **THEN** the delegate that renders the persisted PNG is still called with the full,
  unmasked/unexcluded trait selection (the same `resolved_trait_columns`), and the result's
  `heatmap_caveat` is populated (not `None`) and names the specific flagged trait(s)/pair(s)

#### Scenario: Nothing flagged means no caveat

- **WHEN** neither `zero_variance_traits` nor `low_overlap_trait_pairs` is populated for a call
- **THEN** the result's `heatmap_caveat` is `None`, no annotation is drawn onto the figure, and
  the persisted run's `params["heatmap_caveat"]` is `None`

#### Scenario: A locally-constant pair alone does not trigger the caveat

- **WHEN** `locally_constant_trait_pairs` is non-empty but `zero_variance_traits` and
  `low_overlap_trait_pairs` are both empty
- **THEN** the result's `heatmap_caveat` is `None` and no annotation is drawn onto the figure —
  the cell is blank in the rendered image, so there is no confidently-colored cell to warn about

#### Scenario: The caveat is visible on the saved image itself

- **WHEN** `heatmap_caveat` is populated for a call
- **THEN** the rendered `Figure` gains a text annotation containing the same `heatmap_caveat`
  value before it is saved to PNG

#### Scenario: The caveat is recoverable from the manifest, not only the live response

- **WHEN** `heatmap_caveat` is populated for a call
- **THEN** the persisted run's `params["heatmap_caveat"]` equals the result's `heatmap_caveat`

### Requirement: Disclosure Caps Are Honest About What They Drop

Where `plot_correlation_matrix` caps a disclosure list in its response, the cap SHALL be
accompanied by enough uncapped information for the caller to know what was dropped, and the
field's description SHALL state whether the surviving entries were selected on merit or
arbitrarily.

`strong_correlation_pairs` is ordered by ascending overlap, so its cap truncates the
best-supported end and the surviving entries are the least-supported ones; its description
SHALL say so. `locally_constant_trait_pairs` has no evidence gradient to order by — every pair
in it is equally `NaN` — so its cap is a deterministic but **arbitrary** slice in
`resolved_trait_columns` order, and its description SHALL say *that*, rather than leaving a
reader who has just read the sibling's rationale to assume the same safety argument applies.

#### Scenario: An arbitrary cap is documented as arbitrary

- **WHEN** more locally-constant pairs are found than the cap reports
- **THEN** the field's description identifies the slice as arbitrary, and
  `locally_constant_pair_count` carries the uncapped total

### Requirement: Persisted Runs Record The Parameters Their Numbers Depend On

`plot_correlation_matrix`'s persisted run SHALL stamp the parameter values its reported numbers
were produced under — the minimum pairwise overlap, the strong-correlation magnitude cutoff,
the confidence level, and both disclosure caps. Without them, two manifests produced under
different thresholds are indistinguishable after the fact, which defeats the comparability the
tool's own owned-constant rationale rests on.

A capped list SHALL be recoverable from the persisted run wherever the payload cost permits:
name-only lists (`zero_variance_traits`, `low_overlap_trait_pairs`,
`locally_constant_trait_pairs`) SHALL be stamped **uncapped**. Where a list is stamped capped
because its entries are structured records rather than names, the run SHALL stamp that list's
**uncapped magnitudes** alongside it, so a manifest-only reader can still determine that the
list is truncated and by how much.

#### Scenario: A manifest reader can tell a stamped list was truncated

- **WHEN** more strong pairs are found than `strong_correlation_pairs` reports
- **THEN** the persisted run stamps the capped list together with the uncapped
  `strong_pair_count` and the two strong-correlation counts, from which the truncation is
  evident without the live response

#### Scenario: A later reader can tell which thresholds produced a stored result

- **WHEN** a run is persisted
- **THEN** its params carry the minimum overlap, the magnitude cutoff, the confidence level
  and both caps in force at the time
