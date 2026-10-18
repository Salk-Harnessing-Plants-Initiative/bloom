## MODIFIED Requirements

### Requirement: Remove Outliers Gates Persistence on Untrustworthy Mahalanobis Fit

The `remove_outliers` tool SHALL NOT persist a run — and, on the inline path where nothing is
persisted, SHALL NOT report a trim — when the mahalanobis detector's fit is untrustworthy.
Immediately after `remove_outlier_samples` returns — before the existing pre-commit NaN/row-subset
structural guard, before any `plots=` validation or figure generation, and before any
`ResultStore.create_run` call — the tool SHALL evaluate `fit_is_trustworthy` (derived from
`goodness_of_fit.fit_quality`) and, when it is `False`, SHALL raise a `BloomMCPError` with code
`assumption_violated` whose message embeds the computed `n_input_samples`, `n_outliers`,
`n_output_samples`, `goodness_of_fit.fit_quality`, and the sorted `outlier_barcodes`, and whose
remedy names `method="isolation_forest"` with an explicit `contamination` starting point. No run
and no detection plot SHALL be persisted on this path, regardless of whether
`include_plots`/`plots` were requested. This gate SHALL NOT fire when `fit_is_trustworthy` is
`None` (the `isolation_forest` method, which has no chi-squared assumption) or `True` (an
acceptable-or-better mahalanobis fit).

**The gate applies identically on the inline path**, where its purpose shifts from "do not commit
a run built on an untrustworthy fit" to "do not report a trim built on an untrustworthy fit" — a
caller must not receive flagged samples they would act on when the fit does not support them.
The one difference is the identifier: this message interpolates the experiment identifier, which
is absent on the inline path, so it SHALL render `'csv_content'` rather than `'None'` (see
"One Resolver, One Message Vocabulary"). The error code, the embedded counts and barcodes, and
the remedy SHALL be identical on both paths.

#### Scenario: An untrustworthy mahalanobis fit is gated, not persisted

- **WHEN** `remove_outliers` runs with `method="mahalanobis"` (default) on a registered
  `experiment` and the delegate's `goodness_of_fit.fit_quality` is poor/very_poor/unknown
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` whose message
  includes the computed outlier counts, `fit_quality`, and the sorted `outlier_barcodes`, and
  whose remedy names `method="isolation_forest"`
- **AND** no run is committed via `ResultStore` and no detection figures are generated

#### Scenario: An untrustworthy mahalanobis fit is gated on the inline path too

- **WHEN** `remove_outliers` runs with `method="mahalanobis"` on `csv_content` whose fit quality
  is poor/very_poor/unknown
- **THEN** the same `assumption_violated` error is raised with the same embedded counts,
  `fit_quality`, barcodes, and remedy, no flagged samples are returned, and the identifier in the
  message reads `'csv_content'` — never `'None'`

#### Scenario: An acceptable-or-better mahalanobis fit is not gated

- **WHEN** `remove_outliers` runs with `method="mahalanobis"` and the delegate's
  `goodness_of_fit.fit_quality` is acceptable or better (`fit_is_trustworthy is True`)
- **THEN** the tool proceeds to persist the trimmed run exactly as it did before this change (on
  the registered path), or to return the trim summary (on the inline path)

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

## ADDED Requirements

### Requirement: Remove Outliers Accepts Inline Content With No Persistence

`remove_outliers` SHALL accept `csv_content` as the mutually exclusive alternative to
`experiment` (exactly one required). On the inline path it SHALL skip the `ExperimentReader` port
entirely — including its `version="latest_qc"` resolution, which has no meaning without a
manifest — fit the same outlier detection on the in-memory frame, and return the same
flagged-sample summary, while creating no run and committing nothing.

`version` (including the innocuous-looking `"latest"`, which the registered path coerces to
`"latest_qc"`), `user_label`, `include_plots=true`, and `plots` SHALL be rejected when combined
with `csv_content`. `input_sha256` SHALL be populated on the inline path.

**A composition guarantee is structurally absent on this path and SHALL be documented as such.**
On the registered path, forcing `version="latest_qc"` is what stops a trim from being taken from
a prior trim. The inline path has no manifest and no version resolution, so a caller can feed an
already-trimmed table straight back in and double-trim it, with no lineage and no warning. This
is inherent to the ephemeral mode, not a defect to be fixed here, and it SHALL be stated in the
tool's docstring and in `csv_content`'s field description.

`remove_outliers` has no finiteness pre-check today, because `require_clean` made one
unreachable. It SHALL gain one **scoped to the inline path**: a non-finite value in a selected
trait column SHALL raise `invalid_input` naming the columns, with a remedy naming
`qc_clean(csv_content=..., return_cleaned_csv=true)`. The registered path SHALL gain no such
check and SHALL be unchanged.

#### Scenario: Inline outlier removal flags the same samples as the registered path

- **WHEN** `remove_outliers` is called with the text of a cleaned fixture as `csv_content` and
  with that fixture as a registered cleaned experiment, using identical parameters and a method
  whose fit is trustworthy for that fixture
- **THEN** the flagged sample identifiers, the fit quality, and the retention counts are
  identical, differing only in `experiment`, `source`, `input_sha256`, and the
  persistence-linked fields
- **AND** the comparison is made against the same table on both sides, accounting for the fact
  that serializing an in-memory cleaned frame to CSV resets a non-contiguous index — the flagged
  identifiers, not row positions, are what must match

#### Scenario: Inline outlier removal persists nothing

- **WHEN** `remove_outliers` is called with `csv_content`
- **THEN** a `ResultStore` spy records zero `create_run` and zero `commit` calls, and an
  `ExperimentReader` spy records zero `load_experiment` calls

#### Scenario: A version pin is rejected, including "latest"

- **WHEN** `remove_outliers` is called with `csv_content` and `version` set to any value,
  including `"latest"`
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming `version` — the registered
  path's coercion of `"latest"` to `"latest_qc"` makes it a real pin request, not a no-op

#### Scenario: Non-finite inline traits are rejected, and the registered path is untouched

- **WHEN** `remove_outliers` is called with `csv_content` whose selected trait columns contain a
  non-finite value
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming the columns
- **AND** the registered path has no finiteness check before or after this change

### Requirement: Remove Outliers Optionally Returns the Trimmed Table on the Inline Path

`remove_outliers` SHALL accept `return_trimmed_csv: bool` (default `false`), valid **only**
together with `csv_content` and rejected with `experiment`. When true, the response SHALL carry
`trimmed_csv` — the trimmed table serialized as CSV text, no index column, explicit `\\n` line
terminator — and `trimmed_csv_sha256`, its digest. Both SHALL be `None` otherwise.

`remove_outliers` is the other producer in the `qc` tool class, and the trimmed table is its
substantive output; without this the inline path can report which samples were flagged but never
hand back the result. It carries the same semantics as `qc_clean`'s `return_cleaned_csv`: not
persistence, no server-side lineage, capped at `MAX_INLINE_CSV_BYTES`, opt-in so an agent that
does not ask never receives a large string.

#### Scenario: The trimmed table is returned only when asked for

- **WHEN** `remove_outliers` is called with `csv_content` and `return_trimmed_csv=true`
- **THEN** `trimmed_csv` parses back to the trimmed table, `trimmed_csv_sha256` equals an
  independently computed digest of its UTF-8 bytes, and a `ResultStore` spy still records zero
  `create_run` and zero `commit` calls
- **AND** with the parameter omitted, both fields are `None`

#### Scenario: return_trimmed_csv is rejected with a registered experiment

- **WHEN** `remove_outliers` is called with `experiment` and `return_trimmed_csv=true`
- **THEN** it raises `BloomMCPError(code="invalid_input")` — the registered path already persists
  the trimmed table as a linkable artifact

#### Scenario: An oversized trimmed table is rejected rather than returned

- **WHEN** `remove_outliers` is called with `return_trimmed_csv=true` and the serialized trimmed
  table exceeds `MAX_INLINE_CSV_BYTES`
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming the serialized size and the
  limit, and no partial or truncated table is returned
