## ADDED Requirements

### Requirement: clustering Accepts Inline Content With No Persistence

`clustering` SHALL accept `csv_content` as the mutually exclusive alternative to `experiment`
(exactly one required). On the inline path it SHALL skip the `ExperimentReader` port entirely,
dispatch on `method` to the same upstream delegate with the same parameters and the same seed,
and return the same cluster summary — while creating no run, committing nothing, and returning
`run_ref` / `version_dir` / `manifest_path` as `None` with `outputs` and `output_links` empty.

`input_sha256` SHALL be populated on the inline path. `version`, `user_label`, `include_plots`,
and `plots` SHALL be rejected when combined with `csv_content`. A non-finite value in a selected
trait column SHALL raise `invalid_input` naming the columns, with a remedy naming
`qc_clean(csv_content=..., return_cleaned_csv=true)` — not the registered path's
`assumption_violated`, which is unchanged.

Because `clustering` dispatches on `method` and its methods differ in cost and determinism,
equivalence SHALL be established **per method**, not once.

#### Scenario: Inline clustering matches the registered path, per method

- **WHEN** `clustering` is called with a cleaned fixture's text as `csv_content` and with the same
  fixture as a registered cleaned experiment, using the same parameters and the same explicit
  seed — repeated once for each supported `method`
- **THEN** for each method independently, the returned cluster labels and summary statistics are
  identical, differing only in `experiment`, `source`, `input_sha256`, and the persistence-linked
  fields

#### Scenario: Inline clustering persists nothing

- **WHEN** `clustering` is called with `csv_content`
- **THEN** a `ResultStore` spy records zero `create_run` and zero `commit` calls, and an
  `ExperimentReader` spy records zero `load_experiment` calls

#### Scenario: Plots and version pins are rejected on the inline path

- **WHEN** `clustering` is called with `csv_content` and any of `include_plots=true`, `plots`,
  `version`, or `user_label`
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming the offending parameter

#### Scenario: Hierarchical clustering is bounded on the inline path

- **WHEN** `clustering` is called with `csv_content`, `method="hierarchical"`, and a frame above
  the inline hierarchical sample cap
- **THEN** it raises `BloomMCPError(code="invalid_input")` before the delegate is called, while
  the same frame with `method="kmeans"` is accepted and the registered path is unaffected at any
  size
