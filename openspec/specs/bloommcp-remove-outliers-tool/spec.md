# bloommcp-remove-outliers-tool Specification

## Purpose
TBD - created by archiving change fix-bloommcp-remove-outliers-fit-gate. Update Purpose after archive.
## Requirements
### Requirement: Remove Outliers Gates Persistence on Untrustworthy Mahalanobis Fit

The `remove_outliers` tool SHALL NOT persist a run when the mahalanobis detector's fit is
untrustworthy. Immediately after `remove_outlier_samples` returns — before the existing
pre-commit NaN/row-subset structural guard, before any `plots=` validation or figure generation,
and before any `ResultStore.create_run` call — the tool SHALL evaluate `fit_is_trustworthy`
(derived from `goodness_of_fit.fit_quality`) and, when it is `False`, SHALL raise a
`BloomMCPError` with code `assumption_violated` whose message embeds the computed
`n_input_samples`, `n_outliers`, `n_output_samples`, `goodness_of_fit.fit_quality`, and the
sorted `outlier_barcodes`, and whose remedy names `method="isolation_forest"` with an explicit
`contamination` starting point. No run and no detection plot SHALL be persisted on this path,
regardless of whether `include_plots`/`plots` were requested. This gate SHALL NOT fire when
`fit_is_trustworthy` is `None` (the `isolation_forest` method, which has no chi-squared
assumption) or `True` (an acceptable-or-better mahalanobis fit).

#### Scenario: An untrustworthy mahalanobis fit is gated, not persisted

- **WHEN** `remove_outliers` runs with `method="mahalanobis"` (default) and the delegate's
  `goodness_of_fit.fit_quality` is poor/very_poor/unknown
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` whose message
  includes the computed outlier counts, `fit_quality`, and the sorted `outlier_barcodes`, and
  whose remedy names `method="isolation_forest"`
- **AND** no run is committed via `ResultStore` and no detection figures are generated

#### Scenario: An acceptable-or-better mahalanobis fit is not gated

- **WHEN** `remove_outliers` runs with `method="mahalanobis"` and the delegate's
  `goodness_of_fit.fit_quality` is acceptable or better (`fit_is_trustworthy is True`)
- **THEN** the tool proceeds to persist the trimmed run exactly as it did before this change

#### Scenario: isolation_forest is never gated

- **WHEN** `remove_outliers` runs with `method="isolation_forest"` (`fit_is_trustworthy` is
  always `None` for this method)
- **THEN** the fit-trustworthiness gate never fires and the tool persists the trimmed run
  exactly as it did before this change

#### Scenario: The fit gate takes precedence over plot-key validation

- **WHEN** `remove_outliers` runs with `method="mahalanobis"`, an untrustworthy fit, `include_plots=True`,
  and a `plots` value naming a figure key the delegate does not produce
- **THEN** the tool returns the fit gate's `BloomMCPError(assumption_violated)`, not the
  plot-key `invalid_input` error, since the fit is evaluated before any figure handling

