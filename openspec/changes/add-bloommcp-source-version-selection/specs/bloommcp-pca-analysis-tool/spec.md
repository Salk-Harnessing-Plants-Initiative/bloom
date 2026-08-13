## MODIFIED Requirements

### Requirement: PCA Analysis Requires a Cleaned Input and Selects Only Certified-Clean Traits

The `pca_analysis` tool SHALL load its experiment frame through the injected `ExperimentReader`
port with `require_clean=True`, as the **consumer** of cleaned data, and SHALL restrict the PCA to
columns within the resolved frame's certified-clean trait set (`frame.trait_cols`). It SHALL NOT
run PCA on a raw input and SHALL NOT perform its own cleaning. When no committed cleaned version
exists for the experiment, the tool SHALL surface a structured `BloomMCPError` whose remedy directs
the caller to run `qc_clean` first. A requested trait column outside the certified-clean set (or a
NaN that survives into the selected subset) SHALL be rejected rather than silently row-dropped by
the delegate. The tool SHALL accept an optional cleaned-version selector, threaded to
`load_experiment(experiment, require_clean=True, version=...)`; omitting it SHALL reproduce
today's behavior exactly (no `version` kwarg passed, so the Protocol's `"latest"` default
applies).

#### Scenario: A cleaned experiment is consumed

- **WHEN** `pca_analysis` is invoked on an experiment that has a committed cleaned version (a
  `qc_clean` run)
- **THEN** the reader resolves the cleaned version (source `v<N>_cleaned`, not `raw`), and the tool
  runs PCA on it

#### Scenario: An experiment with no cleaned version is rejected with a remedy

- **WHEN** `pca_analysis` is invoked on an experiment that has only a raw input and no committed
  cleaned version
- **THEN** the tool returns a `BloomMCPError` whose remedy is to run `qc_clean` first, and no PCA
  run is produced

#### Scenario: A trait column outside the certified-clean set is rejected, not silently dropped

- **WHEN** `trait_columns` names a numeric column that is present in the frame but not in the
  certified-clean trait set (`frame.trait_cols`), including one that still carries NaN values
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the column, and does
  not fit PCA on it — so the delegate never silently `dropna()`s the affected samples
- **AND** on a valid selection the result's `n_samples` equals the certified cleaned row count (no
  samples are silently lost)

#### Scenario: Omitting the version selector preserves today's default

- **WHEN** `pca_analysis` is invoked with no version selector given
- **THEN** the tool calls `load_experiment(params.experiment, require_clean=True)` with no
  `version` kwarg, exactly as before this change

#### Scenario: An explicit version selector is honored

- **WHEN** `pca_analysis` is invoked with an explicit version selector (e.g. `"v2"`)
- **THEN** the tool calls `load_experiment(params.experiment, require_clean=True, version="v2")`,
  and the persisted run's `based_on_version` (per the existing "Persists a Versioned Run With
  Lineage" requirement) records that explicitly pinned version, not whatever "latest" would have
  resolved to
