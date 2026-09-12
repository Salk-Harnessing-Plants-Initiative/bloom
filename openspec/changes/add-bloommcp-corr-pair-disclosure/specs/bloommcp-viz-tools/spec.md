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

The list SHALL be ordered by **ascending** overlap `n` — weakest evidence first — and MAY be
capped in length, because at cylinder scale (~846 traits) an uncapped list can exceed 100,000
entries. The ascending order is what makes the cap safe: it truncates the best-supported end,
so the least-supported pair in the count is always reported. The two existing counts remain the
authoritative totals and SHALL NOT change value.

The confidence interval SHALL be well-defined in JSON for every reported pair: a coefficient of
exactly ±1.0 SHALL yield a degenerate interval rather than a non-finite bound, and an overlap
too small for the Fisher standard error SHALL yield a null interval rather than `NaN`.

#### Scenario: A strong pair discloses the overlap behind it

- **WHEN** a call reports a non-zero `strong_positive_correlations` or
  `strong_negative_correlations`
- **THEN** `strong_correlation_pairs` names the pairs behind those counts, each with its
  coefficient, its pairwise overlap `n`, and a 95% confidence interval

#### Scenario: The weakest-supported pair survives the cap

- **WHEN** more strong pairs exist than the reported list's cap
- **THEN** `strong_correlation_pairs` is ordered by ascending overlap `n`, its first entry is
  the strong pair with the smallest overlap in the whole selection, and
  `strong_positive_correlations` + `strong_negative_correlations` still report the true total

#### Scenario: A perfectly collinear pair yields a finite interval

- **WHEN** a strong pair's coefficient is exactly ±1.0 over an overlap above the minimum
- **THEN** its reported confidence interval is finite (degenerate at the coefficient), not
  `±inf` or `NaN`

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
depend on a numerical tolerance agreeing with pandas'. A pair whose shared overlap contains a
non-finite value also produces `NaN` under the same two conditions and SHALL land in this
bucket; the result field SHALL disclose that rather than implying the cause is always local
constancy.

This bucket SHALL NOT populate `heatmap_caveat`. That field discloses a *masking mismatch*
between this tool's guarded summary and the unguarded delegate that renders the persisted PNG;
a locally-constant pair is `NaN` in the delegate's independent computation too, so the image
and the summary already agree and there is no mismatch to warn about.

#### Scenario: A locally-constant pair is named, not left unexplained

- **WHEN** two selected traits are each globally non-constant and overlap in at least the
  minimum number of non-null rows, but one of them is constant across exactly that overlap
- **THEN** the pair appears in `locally_constant_trait_pairs`, appears in neither
  `zero_variance_traits` nor `low_overlap_trait_pairs`, and counts toward neither
  `strong_positive_correlations` nor `strong_negative_correlations`

#### Scenario: Every blank cell has exactly one reason

- **WHEN** any selection is correlated
- **THEN** each off-diagonal `NaN` in the guarded correlation matrix is accounted for by
  exactly one of `zero_variance_traits`, `low_overlap_trait_pairs`, or
  `locally_constant_trait_pairs`

#### Scenario: A locally-constant pair does not trigger the heatmap caveat

- **WHEN** `locally_constant_trait_pairs` is non-empty but `zero_variance_traits` and
  `low_overlap_trait_pairs` are both empty
- **THEN** `heatmap_caveat` is `None` and no warning footnote is drawn onto the rendered figure

## MODIFIED Requirements

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
two values remain equal; only the coupling is removed.

#### Scenario: A near-empty overlap is excluded, not miscounted

- **WHEN** two selected traits overlap in fewer non-null rows than the minimum-overlap threshold
- **THEN** that pair's coefficient counts toward neither `strong_positive_correlations` nor
  `strong_negative_correlations`, and the pair appears in `low_overlap_trait_pairs`

#### Scenario: The overlap threshold is independent of the QC threshold

- **WHEN** `plot_correlation_matrix`'s minimum-overlap constant is inspected
- **THEN** it is defined by `plot_correlation_matrix` itself, is not imported from the shared QC
  module, and currently equals the canonical minimum-samples-per-trait value — so the decoupling
  changes no behavior
