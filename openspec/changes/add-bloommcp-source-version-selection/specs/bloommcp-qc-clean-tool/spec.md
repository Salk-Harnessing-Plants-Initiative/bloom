## ADDED Requirements

### Requirement: QC Clean Accepts an Explicit Source Pin

The `qc_clean` tool SHALL accept optional `source_id`/`run_id` params, threaded into its existing
raw-tier `load_experiment` call. Omitting both SHALL be behavior-identical to today (latest source
resolution, no response-text change). Passing both SHALL surface `AmbiguousSourceSelectionError`
as a structured `BloomMCPError` through the tool's existing `errors=(ExperimentReadError,)`
contract mapping. When the target experiment has more than one source and neither `source_id` nor
`run_id` was given, the result SHALL include an advisory note naming the source actually used and
pointing at `core_list_experiment_sources` to choose a different one. The note SHALL be absent on
the `csv_content` (inline) path, for single- or zero-source experiments, and whenever a pin was
given.

#### Scenario: Omitting both source params preserves today's behavior

- **WHEN** `qc_clean` is invoked on a registered experiment with no `source_id`/`run_id` given
- **THEN** the tool loads the raw frame exactly as before this change, and the result's advisory
  note field is `None` unless the experiment has more than one source

#### Scenario: An explicit source pin is honored

- **WHEN** `qc_clean` is invoked with `source_id` set to one of the experiment's known sources
- **THEN** the tool cleans the raw frame backed by that specific source, and the result's advisory
  note is `None` (a pin was given, so there is nothing to advise)

#### Scenario: Both source_id and run_id given is rejected

- **WHEN** `qc_clean` is invoked with both `source_id` and `run_id` set
- **THEN** the tool returns a `BloomMCPError` derived from `AmbiguousSourceSelectionError` — not an
  unhandled exception or a generic `internal_error`

#### Scenario: A multi-source experiment with no pin gets an explicit advisory note

- **WHEN** `qc_clean` is invoked on a registered experiment that has more than one known source,
  and neither `source_id` nor `run_id` is given
- **THEN** the result's advisory note names the resolved source (e.g. its `source_id`) and directs
  the caller to `core_list_experiment_sources` to pick a different one

#### Scenario: A single- or zero-source experiment gets no advisory note

- **WHEN** `qc_clean` is invoked on a registered experiment with zero or exactly one known source,
  and no pin is given
- **THEN** the result's advisory note is `None` — there is no meaningful choice to surface

#### Scenario: The csv_content (inline) path never surfaces a source note

- **WHEN** `qc_clean` is invoked with `csv_content` instead of a registered `experiment`
- **THEN** the result's advisory note is `None` regardless of source count, since inline content
  has no experiment identity to enumerate sources for

#### Scenario: A pinned source is traceable from the committed run's provenance

- **WHEN** `qc_clean` is invoked with an explicit `source_id` pin and commits a run
- **THEN** the committed `StoredRun`'s recorded source metadata (via the existing
  `store.create_run(source=frame.resolved_source)` path) identifies the pinned source, so a later
  reader of the manifest can trace the cleaned run back to the exact raw source used — not only
  the fact that cleaning occurred
