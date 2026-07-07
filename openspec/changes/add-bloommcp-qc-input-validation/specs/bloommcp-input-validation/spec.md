## ADDED Requirements

### Requirement: Input Contract Validation at the QC Clean Entry Point

The `qc_clean` tool SHALL validate its input frame against the shared `sleap-roots-contracts`
schema by delegating to `sleap_roots_analyze.validation.validate_entry_input` in `warn` mode,
mapping bloommcp's resolved role columns to the delegate's canonical roles via its `columns=`
argument and forwarding the resolved metadata deny-list via `additional_exclude`. The tool SHALL
contain no contract-validation logic of its own and SHALL NOT call
`sleap_roots_contracts.validate_analysis_input` directly. Validation SHALL run **before** any run
is persisted. If `sleap-roots-contracts` is not installed, validation SHALL degrade to a logged
no-op and SHALL NOT raise `ImportError`. `warn`-mode advisory findings SHALL NOT abort the run.

#### Scenario: Contract-valid input cleans and persists a run

- **WHEN** `qc_clean` runs on a frame that resolves a genotype and a sample identifier and passes
  the contract in `warn` mode
- **THEN** validation is delegated to `validate_entry_input`, cleaning proceeds, and a versioned
  cleaned run is persisted

#### Scenario: warn-mode advisory findings are recorded but do not abort

- **WHEN** the contract reports a `warn`-level advisory (e.g. an optional metadata column contains
  NaN) rather than a structural error
- **THEN** the finding is recorded in the result and the manifest and the run **still commits**

#### Scenario: sleap-roots-contracts absent degrades gracefully

- **WHEN** `sleap-roots-contracts` is not installed
- **THEN** `validate_entry_input` degrades to a logged no-op, no `ImportError` is raised, and
  cleaning proceeds subject to bloommcp's own role guards

#### Scenario: Warn-mode structural failure surfaces as a structured error

- **WHEN** the `warn`-mode contract itself hard-fails on a universal structural error (e.g. a frame
  with a genotype column but zero numeric traits, or a role column of the wrong dtype)
- **THEN** `qc_clean` returns a `BloomMCPError` rather than an unscrubbed traceback, and persists
  no run

### Requirement: QC Clean Requires Traceable Roles

The `qc_clean` tool SHALL require a resolvable **genotype** column and a resolvable
**sample-identifier** column for every input. These guards SHALL be enforced at the bloommcp level
by checking the resolved role is not `None`, so traceability holds even when
`sleap-roots-contracts` is absent (the `warn`-mode contract alone would not fail a missing
sample identifier). When a required role cannot be resolved — no auto-detected match and no
override — `qc_clean` SHALL return a `BloomMCPError` with reason `assumption_violated` whose
message lists the available columns and whose remedy directs asking the user and re-calling with
the override, and SHALL persist **no** run. The `replicate` role SHALL remain optional
(auto-detect only). This requirement deliberately **supersedes** the in-flight
`bloommcp-qc-clean-tool` scenario *"An undetected role column falls back to the delegate default"*
for the **required** roles: an unresolved genotype or sample_id now hard-errors rather than
falling back to the delegate's default.

#### Scenario: Missing sample identifier with no override is a structured error

- **WHEN** `qc_clean` runs on a frame with no auto-detectable sample-identifier column and no
  `sample_id_column` override
- **THEN** it returns `BloomMCPError(assumption_violated)` whose message lists the available
  columns and whose remedy names the `sample_id_column` override
- **AND** no run is persisted

#### Scenario: Missing genotype with no override is a structured error

- **WHEN** `qc_clean` runs on a frame with no auto-detectable genotype column and no
  `genotype_column` override
- **THEN** it returns `BloomMCPError(assumption_violated)` listing the available columns
- **AND** no run is persisted

#### Scenario: Both required roles missing names both overrides

- **WHEN** neither a genotype nor a sample-identifier column resolves (no auto-detected match and
  no override for either)
- **THEN** the single structured error lists the available columns and names **both** the
  `genotype_column` and `sample_id_column` overrides
- **AND** no run is persisted

#### Scenario: Traceability holds without the contracts package

- **WHEN** `sleap-roots-contracts` is absent and the frame has no sample identifier
- **THEN** bloommcp's own guard still returns the structured error and persists no run, so a
  cleaned frame is never produced without a sample identifier

#### Scenario: Both required roles resolvable cleans successfully

- **WHEN** genotype and sample identifier both resolve (by auto-detection or override)
- **THEN** cleaning proceeds and a run is persisted, with `replicate` used only if it resolves

### Requirement: Column Resolution Delegates Trait Detection Upstream

The system SHALL provide a standalone `resolve_columns(df, *, sample_id_column=None,
genotype_column=None, exclude_columns=None) -> ResolvedColumns` unit in
`bloom_mcp/data_access/columns.py` returning the resolved `genotype`, `sample_id`, `replicate`,
`trait_cols`, and `excluded_cols`. Role-name matching (`SAMPLE_ID_PATTERNS`, `GENOTYPE_PATTERNS`,
`REPLICATE_PATTERNS`) SHALL live in bloommcp. Trait detection SHALL delegate to
`sleap_roots_analyze.get_trait_columns`, so numeric metadata columns (e.g. `Computation.Time.s`)
are excluded from the trait set. Both the reader (with no overrides) and `qc_clean` (with
overrides) SHALL obtain columns through `resolve_columns`, and `load_experiment`'s signature SHALL
NOT gain new parameters.

#### Scenario: Numeric metadata is excluded from the trait set

- **WHEN** `resolve_columns` runs on a frame containing a numeric processing column such as
  `Computation.Time.s`
- **THEN** that column is not returned in `trait_cols` (it is reported under `excluded_cols`),
  because trait detection is delegated to `get_trait_columns`

#### Scenario: The unit is standalone and shared by both call sites

- **WHEN** the reader adapters and `qc_clean` are inspected
- **THEN** both resolve columns through `resolve_columns`, which is independently unit-tested, and
  neither reimplements role matching or trait detection

#### Scenario: The read port signature is unchanged

- **WHEN** `ExperimentReader.load_experiment` is inspected
- **THEN** its parameters are unchanged (no override parameters added); overrides are supplied only
  at the `qc_clean` call site

### Requirement: QC Clean Column Override Parameters

The `qc_clean` tool SHALL accept `sample_id_column` and `genotype_column` parameters (default
auto-detect) and an `exclude_columns` parameter (a metadata deny-list). An override SHALL force the
named column to be used as that role during resolution. When both `exclude_columns` and the
existing `trait_columns` allow-list are supplied, the explicit `trait_columns` allow-list SHALL
win.

#### Scenario: A sample_id_column override is used

- **WHEN** `qc_clean` is called with `sample_id_column=<name>` for a frame whose sample identifier
  is not auto-detected
- **THEN** the named column is used as the sample identifier and cleaning succeeds

#### Scenario: An override naming a nonexistent column is rejected

- **WHEN** an override (e.g. `sample_id_column`, `genotype_column`, or `exclude_columns`) names a
  column that is not present in the frame
- **THEN** `qc_clean` returns a `BloomMCPError` with reason `invalid_input` naming the offending
  column, and persists no run

#### Scenario: exclude_columns removes a column from the trait set

- **WHEN** `qc_clean` is called with `exclude_columns=[<name>]`
- **THEN** the named column is removed from the resolved trait set and reported in
  `excluded_columns`

#### Scenario: Explicit trait_columns wins over exclusion

- **WHEN** the same column appears in both the `trait_columns` allow-list and `exclude_columns`
- **THEN** the column is kept as a trait (the explicit allow-list takes precedence)

### Requirement: Validation Findings Surfaced in Result and Manifest

The `qc_clean` result SHALL include the resolved `genotype_column`, `sample_id_column`, and
`replicate_column`, the `excluded_columns` list, and a `validation_warnings` list. The persisted
run manifest SHALL additionally carry an additive `input_validation` block recording the
validation `mode`, the `contract_version`, the `resolved_roles`, the `excluded_columns`, and the
`warnings`.

#### Scenario: Result carries the resolved roles and warnings

- **WHEN** a `qc_clean` call succeeds
- **THEN** the result reports the resolved genotype / sample_id / replicate columns, the
  `excluded_columns`, and any `validation_warnings`

#### Scenario: Manifest carries the input_validation block

- **WHEN** the persisted run manifest for a successful `qc_clean` is inspected
- **THEN** it contains an `input_validation` block with `mode`, `contract_version`,
  `resolved_roles`, `excluded_columns`, and `warnings`
