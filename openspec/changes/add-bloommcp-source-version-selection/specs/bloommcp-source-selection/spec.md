## ADDED Requirements

### Requirement: Source Discovery Tool

The system SHALL expose a `core_list_experiment_sources(experiment)` MCP tool, registered
alongside the other `core` discovery tools, that reports each distinct source backing an
experiment's raw trait data (`source_id`, `source_name`, `pipeline_run_id`). It SHALL be
isinstance-gated on the active reader's `SourceSelectable` capability: on a reader that does not
implement it (`LocalReader`, `FakeReader`), the tool SHALL return a clear "not applicable for this
backend" message rather than raising an error.

#### Scenario: Multiple sources are listed

- **WHEN** `core_list_experiment_sources` is called for an experiment with more than one known
  source, against a `SourceSelectable` reader
- **THEN** the response lists each source's `source_id`, `source_name`, and `pipeline_run_id`

#### Scenario: A single- or zero-source experiment gets a clear message, not a list

- **WHEN** `core_list_experiment_sources` is called for an experiment with zero or exactly one
  known source
- **THEN** the response states there is no meaningful source choice for that experiment, rather
  than returning an empty or single-item list with no explanation

#### Scenario: A non-Supabase backend gets a clear "not applicable" message

- **WHEN** `core_list_experiment_sources` is called while the active reader is `LocalReader` or
  `FakeReader` (neither implements `SourceSelectable`)
- **THEN** the response states that source selection is not applicable for the active backend —
  not a raised exception or an `AttributeError`

### Requirement: qc_inspect Accepts an Explicit Source Pin

The `qc_inspect` tool SHALL accept optional `source_id`/`run_id` params, threaded into its
existing raw-tier `load_experiment` call, with the same default-preserving and
ambiguous-pin-rejection guarantees as `qc_clean`'s equivalent params.

#### Scenario: Omitting both source params preserves today's behavior

- **WHEN** `qc_inspect` is invoked on a registered experiment with no `source_id`/`run_id` given
- **THEN** the tool loads the raw frame exactly as before this change

#### Scenario: An explicit source pin is honored

- **WHEN** `qc_inspect` is invoked with `source_id` set to one of the experiment's known sources
- **THEN** the tool inspects the raw frame backed by that specific source

#### Scenario: Both source_id and run_id given is rejected

- **WHEN** `qc_inspect` is invoked with both `source_id` and `run_id` set
- **THEN** the tool returns a `BloomMCPError` derived from `AmbiguousSourceSelectionError`

### Requirement: load_experiment_data Accepts an Explicit Source Pin

The `load_experiment_data` tool SHALL accept optional `source_id`/`run_id` kwargs, threaded
through `_ports.load_frame` into `load_experiment`, with the same default-preserving and
ambiguous-pin-rejection guarantees as `qc_clean`'s equivalent params. Any resulting
`ExperimentReadError` (ambiguous pin, unsupported pin, not-found) SHALL surface through the tool's
existing string-error return contract, not an unhandled exception.

#### Scenario: Omitting both source params preserves today's behavior

- **WHEN** `load_experiment_data` is invoked with no `source_id`/`run_id` given
- **THEN** the tool summarizes the experiment exactly as before this change

#### Scenario: An explicit source pin is honored

- **WHEN** `load_experiment_data` is invoked with `source_id` set to one of the experiment's known
  sources
- **THEN** the summary reflects the frame backed by that specific source

#### Scenario: Both source_id and run_id given returns the existing error string, not a crash

- **WHEN** `load_experiment_data` is invoked with both `source_id` and `run_id` set
- **THEN** the tool returns the ambiguous-selection error message as its string result, matching
  its existing `(df, ..., error_message)` contract for unresolvable reads
