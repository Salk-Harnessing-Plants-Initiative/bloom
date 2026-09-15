## MODIFIED Requirements

### Requirement: PCA Analysis Requires a Cleaned Input and Selects Only Certified-Clean Traits

**When invoked with a registered `experiment`,** the `pca_analysis` tool SHALL load its experiment
frame through the injected `ExperimentReader` port with `require_clean=True`, as the **consumer**
of cleaned data, and SHALL restrict the PCA to columns within the resolved frame's
certified-clean trait set (`frame.trait_cols`). It SHALL NOT run PCA on a raw input and SHALL NOT
perform its own cleaning. When no committed cleaned version exists for the experiment, the tool
SHALL surface a structured `BloomMCPError` whose remedy directs the caller to run `qc_clean`
first. A requested trait column outside the certified-clean set (or a NaN that survives into the
selected subset) SHALL be rejected rather than silently row-dropped by the delegate.

**When invoked with `csv_content`, this requirement does not apply**: there is no reader, no
manifest, and therefore no certification. See "PCA Analysis Accepts Inline Content With No
Persistence" for the substituted guarantees — the same trait-membership and finiteness checks,
re-established locally against the caller's assertion rather than against a committed cleaned
version.

#### Scenario: A cleaned experiment is consumed

- **WHEN** `pca_analysis` is invoked with an `experiment` that has a committed cleaned version (a
  `qc_clean` run)
- **THEN** the reader resolves the cleaned version (source `v<N>_cleaned`, not `raw`), and the tool
  runs PCA on it

#### Scenario: An experiment with no cleaned version is rejected with a remedy

- **WHEN** `pca_analysis` is invoked with an `experiment` that has only a raw input and no
  committed cleaned version
- **THEN** the tool returns a `BloomMCPError` whose remedy is to run `qc_clean` first, and no PCA
  run is produced

#### Scenario: A trait column outside the certified-clean set is rejected, not silently dropped

- **WHEN** `pca_analysis` is invoked with an `experiment` and `trait_columns` names a numeric
  column that is present in the frame but not in the certified-clean trait set
  (`frame.trait_cols`), including one that still carries NaN values
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the column, and does
  not fit PCA on it — so the delegate never silently `dropna()`s the affected samples
- **AND** on a valid selection the result's `n_samples` equals the certified cleaned row count (no
  samples are silently lost)

## ADDED Requirements

### Requirement: PCA Analysis Accepts Inline Content With No Persistence

`pca_analysis` SHALL accept `csv_content` as the mutually exclusive alternative to `experiment`
(exactly one required). On the inline path it SHALL skip the `ExperimentReader` port entirely,
fit PCA on the in-memory frame through the same `perform_pca_analysis` delegate with the same
parameters, and return the same variance summary — while creating no run, committing nothing,
and returning `run_ref` / `version_dir` / `manifest_path` as `None` with `outputs` and
`output_links` empty.

`input_sha256` SHALL be populated on the inline path and `None` on the registered path.
`version`, `user_label`, `include_plots=true`, and every plot-companion parameter SHALL be
rejected with `BloomMCPError` (`invalid_input`) when combined with `csv_content`.

Because inline content carries no certification, the `require_clean` guarantee SHALL be
re-established locally: a non-finite value in a selected trait column SHALL raise
`invalid_input` naming the offending columns with a remedy directing the caller to
`qc_clean(csv_content=..., return_cleaned_csv=true)`, rather than the `assumption_violated`
error the registered path raises — which attributes the fault to a mis-reporting reader that
the inline path does not use.

#### Scenario: Inline PCA reproduces the registered path's fit exactly

- **WHEN** `pca_analysis` is called with the text of the cleaned turface_19 fixture as
  `csv_content`, and separately with that fixture as a registered cleaned experiment, using
  identical parameters
- **THEN** the explained variance ratios, cumulative variance ratios, eigenvalues, component
  count, and feature names are identical, differing only in `experiment`, `source`,
  `input_sha256`, and the persistence-linked fields
- **AND** the inline result reproduces the recorded #120 turface_19 golden to the same tolerance
  the registered path is held to

#### Scenario: Inline PCA persists nothing

- **WHEN** `pca_analysis` is called with `csv_content`
- **THEN** a `ResultStore` spy records zero `create_run` and zero `commit` calls, and an
  `ExperimentReader` spy records zero `load_experiment` calls

#### Scenario: Non-finite inline traits are a caller error

- **WHEN** `pca_analysis` is called with `csv_content` whose selected trait columns contain a NaN
  or an infinity
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming the offending columns, with a
  remedy naming `qc_clean` with `return_cleaned_csv` — and not the registered path's
  `assumption_violated` error

#### Scenario: A trait subset outside the detected set is rejected without claiming certification

- **WHEN** `pca_analysis` is called with `csv_content` and `trait_columns` naming a column absent
  from the frame's resolved trait set, an empty list, or a duplicated column
- **THEN** each is rejected with `invalid_input`, exactly as on the registered path, but the
  message describes the columns relative to `csv_content` rather than as "certified-clean traits"
  of an experiment

#### Scenario: Plots are rejected on the inline path

- **WHEN** `pca_analysis` is called with `csv_content` and `include_plots=true`, or with any
  plot-companion parameter
- **THEN** it raises `BloomMCPError(code="invalid_input")`, and a spy on `Figure.savefig` records
  zero calls
