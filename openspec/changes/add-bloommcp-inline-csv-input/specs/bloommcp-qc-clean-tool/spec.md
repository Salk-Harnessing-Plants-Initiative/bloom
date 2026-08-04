## MODIFIED Requirements

### Requirement: QC Clean Operates on Raw Input and Reads via the Port

The `qc_clean` tool SHALL accept exactly one of two mutually exclusive inputs: a registered
`experiment` name, or inline `csv_content` text for a one-off, unregistered analysis. When
`experiment` is given, the tool SHALL load its experiment frame through the injected
`ExperimentReader` port as the **producer** of cleaned data — it SHALL NOT request
`require_clean=True` — and SHALL surface an unresolvable experiment as a structured error
rather than a raw backend message. When `csv_content` is given, the tool SHALL parse it into an
`ExperimentFrame` via the shared `bloom_mcp.tools._inline_input.parse_inline_csv_frame` helper
instead of calling the `ExperimentReader` port at all — the reader is never invoked on this
path.

#### Scenario: Raw experiment is loaded for cleaning

- **WHEN** `qc_clean` is invoked with `experiment` set for an experiment that has a raw input
- **THEN** the tool loads the raw frame via the `ExperimentReader` port and proceeds with
  cleanup

#### Scenario: Missing experiment is rejected with a structured error

- **WHEN** the requested experiment cannot be resolved by the reader
- **THEN** the tool returns a `BloomMCPError` with a code and remedy, and no persisted run is
  produced

#### Scenario: Inline content bypasses the reader port entirely

- **WHEN** `qc_clean` is invoked with `csv_content` set
- **THEN** the tool parses the frame via `parse_inline_csv_frame` and makes no call to the
  injected `ExperimentReader` port — verified by a spy/mock asserting zero calls to
  `load_experiment`

### Requirement: QC Clean Honors the Contract Envelope

The `qc_clean` tool SHALL be wrapped by `@as_mcp_tool` so that inputs and outputs are
validated against declared Pydantic models, every declared/undeclared failure is mapped to a
structured `BloomMCPError` (never a raw traceback or leaked backend internals), and a single
`Provenance` is stamped per call.

#### Scenario: Input/output schema round-trip

- **WHEN** a valid request is serialized to the tool's input schema and the result is
  validated against the output schema
- **THEN** both validate without loss, and an invalid input (e.g. neither `experiment` nor
  `csv_content` given, or a cleanup threshold out of `[0,1]`) yields a `BloomMCPError` with an
  `input`/validation code

#### Scenario: A caller-supplied trait column that is unknown or non-numeric is rejected

- **WHEN** `trait_columns` names a column absent from the experiment, or a non-numeric
  (metadata/identifier) column
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` whose message names
  the offending column(s), rather than an opaque internal error or silent mis-filtering

#### Scenario: Errors surface as a structured envelope

- **WHEN** the delegated computation or backend read/write raises
- **THEN** the caller receives a `BloomMCPError` with a code and remedy, and no raw traceback
  or backend path is leaked

#### Scenario: Provenance is stamped and seed recorded as None

- **WHEN** `qc_clean` completes
- **THEN** the stamped `Provenance` records the tool name, the cleanup-threshold and
  trait-selection params, and `seed = None` (QC applies no `random_state`)

### Requirement: QC Clean Enforces Mutually Exclusive Input Selection

`QCCleanParams` SHALL enforce, via a model validator evaluated before the tool body runs, that
exactly one of `experiment` or `csv_content` is provided — neither supplying both nor supplying
neither is a valid call. This validation SHALL run for every construction of `QCCleanParams`
(not only calls routed through `@as_mcp_tool`'s `model_validate`), since Pydantic model
validators execute at instantiation regardless of caller.

#### Scenario: Supplying both experiment and csv_content is rejected before the tool runs

- **WHEN** `QCCleanParams` is constructed with both `experiment` and `csv_content` set
- **THEN** validation fails with a `BloomMCPError` (code `invalid_input`) before `qc_clean`'s
  body executes, and no reader or parsing call occurs

#### Scenario: Supplying neither experiment nor csv_content is rejected

- **WHEN** `QCCleanParams` is constructed with both `experiment` and `csv_content` absent
- **THEN** validation fails with a `BloomMCPError` (code `invalid_input`) before `qc_clean`'s
  body executes

### Requirement: QC Clean Persists a Versioned Cleaned Run and Returns Links

When invoked with a registered `experiment`, the `qc_clean` tool SHALL persist its outputs as a
versioned run via the `ResultStore` port under tool class `qc`, carrying the contract-stamped
`Provenance` into the manifest, writing the cleaned trait CSV and the cleanup log, and SHALL
return the small in/out summary inline together with **links** to the persisted artifacts (the
`run_ref`, the `manifest_path`, and the per-output object keys) — never the cleaned table
inline. The persisted run SHALL be resolvable by the `ExperimentReader` as a **cleaned version**
so a later `pca_analysis` (`require_clean=True`) consumes it. When invoked with `csv_content`
instead, the tool SHALL NOT call `ResultStore.create_run`/`.commit` — no run is persisted, no
manifest entry is written, and the result's `run_ref`, `version_dir`, and `manifest_path` SHALL
be `None` with `outputs` empty; the caller receives the same small in/out summary with
`experiment=None`, `source="inline"`, and `input_sha256` populated from the exact
`csv_content` bytes.

#### Scenario: Run is committed with provenance

- **WHEN** `qc_clean` completes successfully with a registered `experiment`
- **THEN** a `StoredRun` is recorded for `(experiment, "qc")` with a `run_ref`, a manifest
  path, and the same `Provenance` (including `seed = None`) the contract stamped
- **AND** the committed outputs include the cleaned CSV and the cleanup log

#### Scenario: Result returns links and a summary, not the table

- **WHEN** the tool returns its result for a registered `experiment`
- **THEN** `n_samples_in` / `n_samples_out` / `n_traits_in` / `n_traits_out` and the separate
  `sample_retention` / `trait_retention` ratios are inline
- **AND** the cleaned CSV and cleanup log are referenced via links (object keys + manifest
  path) to the persisted run rather than embedded inline

#### Scenario: Cleaned run composes into the PCA tool

- **WHEN** a downstream tool loads the experiment with `require_clean=True` after `qc_clean`
  has committed a run
- **THEN** the reader resolves the `qc_clean` cleaned version rather than the raw input

#### Scenario: Inline call never persists a run

- **WHEN** `qc_clean` completes successfully with `csv_content`
- **THEN** no `ResultStore.create_run`/`.commit` call occurred (verified by a spy/mock, not
  merely the absence of a run in a fake store's records), and the result's `run_ref`,
  `version_dir`, and `manifest_path` are `None` with `outputs == {}`

#### Scenario: Inline result reports the summary and the input hash, not an experiment identity

- **WHEN** `qc_clean` completes successfully with `csv_content`
- **THEN** the result's `experiment` is `None`, `source == "inline"`, and `input_sha256` equals
  the SHA-256 hex digest of the exact UTF-8-encoded `csv_content` string supplied

#### Scenario: Inline result never nudges toward qc_inspect

- **WHEN** `qc_clean` completes successfully with `csv_content` and the cleanup dropped one or
  more samples (the condition that populates `next_step` with a `qc_inspect` nudge on the
  `experiment` path)
- **THEN** the result's `next_step` is `None` — `qc_inspect` has no `csv_content` parameter and
  cannot act on ephemeral input, so the tool SHALL NOT recommend it, and SHALL NOT interpolate
  the caller's (absent) experiment identity into any advisory message

#### Scenario: Inline cleaning matches the file-based oracle for identical content

- **WHEN** `qc_clean` is invoked with `csv_content` equal to the text of the `turface_19` raw
  fixture, using the same thresholds as the existing file-based oracle
  (`turface_19_qc_golden.json`)
- **THEN** the resulting `n_samples_out`, `n_traits_out`, `removed_traits`, and resolved role
  columns are identical to the file-based oracle's result — the ephemeral and persisted paths
  clean the same input identically
