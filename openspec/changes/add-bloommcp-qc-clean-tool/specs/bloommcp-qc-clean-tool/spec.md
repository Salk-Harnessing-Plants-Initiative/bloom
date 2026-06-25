## ADDED Requirements

### Requirement: QC Clean Tool Registration and Discovery

The system SHALL expose a `qc_clean` MCP tool registered on the FastMCP server so it is
discoverable via the MCP `tools/list` operation. The tool name SHALL be stable (`qc_clean`,
never versioned in the name) and SHALL NOT remove or rename the existing `run_qc_workflow`
tool or the vendored `bloom_mcp.data_cleanup` module.

#### Scenario: Tool appears in tools/list

- **WHEN** a FastMCP `Client` connects to the server and calls `tools/list`
- **THEN** a tool named `qc_clean` is present with a description and an input schema derived
  from its Pydantic input model

#### Scenario: Existing QC workflow tool is preserved

- **WHEN** the server registers `qc_clean`
- **THEN** `run_qc_workflow` remains registered and `bloom_mcp.data_cleanup` remains
  importable, so server boot is unaffected

### Requirement: QC Clean Delegates All Cleanup to the Tested Upstream Entry Point

The `qc_clean` tool SHALL delegate cleanup-and-validate to
`sleap_roots_analyze.clean_traits_for_analysis` and SHALL contain no QC logic of its own. It
SHALL NOT run the full `QCPipeline`, SHALL NOT re-stitch `load → cleanup → validate` in the
MCP, and SHALL NOT call the vendored `bloom_mcp.data_cleanup` filters. It SHALL pass the
adapter-detected role columns (genotype / replicate / sample-id) from the `ExperimentFrame`
into the delegate rather than relying on the delegate's `geno`/`rep`/`Barcode` defaults.

#### Scenario: Cleanup is delegated, not re-implemented

- **WHEN** `qc_clean` runs on a raw experiment frame
- **THEN** the cleaned table, kept trait columns, and cleanup log are produced by
  `clean_traits_for_analysis`
- **AND** the tool performs no per-trait or per-sample filtering itself

#### Scenario: Detected role columns are forwarded to the delegate

- **WHEN** the experiment's role columns differ from the delegate defaults (e.g. `Genotype`
  / `Replicate` rather than `geno` / `rep`)
- **THEN** `qc_clean` passes the `ExperimentFrame`'s detected `genotype_col`,
  `replicate_col`, and `sample_id_col` into `clean_traits_for_analysis`

#### Scenario: An undetected role column falls back to the delegate default

- **WHEN** the `ExperimentFrame` reports a role column (e.g. `genotype_col`) as `None`
- **THEN** `qc_clean` lets `clean_traits_for_analysis` use its default for that role rather
  than forwarding `None` into the delegate

### Requirement: QC Clean Produces a No-NaN Table With Less Sample Loss Than Naive Dropna Through the Tool

The `qc_clean` tool SHALL, when invoked through the MCP boundary on the raw #120 turface_19
fixture, produce a trait table with **no NaNs** in its kept trait columns, retaining **more
samples than a naive `dropna()`** over the same raw trait columns.

#### Scenario: Cleaned table has no NaNs

- **WHEN** `qc_clean` completes on the raw turface_19 experiment
- **THEN** the persisted cleaned trait table has zero NaNs across its kept trait columns
- **AND** the inline summary reports `n_samples_out` and `n_traits_out` matching the
  recorded golden cleaned shape

#### Scenario: Fewer samples dropped than a naive dropna

- **WHEN** the same raw turface_19 trait columns are reduced by a naive row-wise `dropna()`
- **THEN** `qc_clean` retains strictly more samples than that naive drop
- **AND** the summary reports `n_samples_in` and `n_samples_out` so the retention is visible

### Requirement: QC Clean Operates on Raw Input and Reads via the Port

The `qc_clean` tool SHALL load its experiment frame through the injected `ExperimentReader`
port as the **producer** of cleaned data — it SHALL NOT request `require_clean=True` — and
SHALL surface an unresolvable experiment as a structured error rather than a raw backend
message.

#### Scenario: Raw experiment is loaded for cleaning

- **WHEN** `qc_clean` is invoked on an experiment that has a raw input
- **THEN** the tool loads the raw frame and proceeds with cleanup

#### Scenario: Missing experiment is rejected with a structured error

- **WHEN** the requested experiment cannot be resolved by the reader
- **THEN** the tool returns a `BloomMCPError` with a code and remedy, and no persisted run is
  produced

### Requirement: QC Clean Honors the Contract Envelope

The `qc_clean` tool SHALL be wrapped by `@as_mcp_tool` so that inputs and outputs are
validated against declared Pydantic models, every declared/undeclared failure is mapped to a
structured `BloomMCPError` (never a raw traceback or leaked backend internals), and a single
`Provenance` is stamped per call.

#### Scenario: Input/output schema round-trip

- **WHEN** a valid request is serialized to the tool's input schema and the result is
  validated against the output schema
- **THEN** both validate without loss, and an invalid input (e.g. missing experiment, a
  cleanup threshold out of `[0,1]`) yields a `BloomMCPError` with an `input`/validation code

#### Scenario: Errors surface as a structured envelope

- **WHEN** the delegated computation or backend read/write raises
- **THEN** the caller receives a `BloomMCPError` with a code and remedy, and no raw traceback
  or backend path is leaked

#### Scenario: Provenance is stamped and seed recorded as None

- **WHEN** `qc_clean` completes
- **THEN** the stamped `Provenance` records the tool name, the cleanup-threshold and
  trait-selection params, and `seed = None` (QC applies no `random_state`)

#### Scenario: A cleanup that would drop every trait is a structured error

- **WHEN** the delegate's cleanup would leave no kept trait columns (every trait fails the
  thresholds)
- **THEN** `qc_clean` returns a `BloomMCPError` with a remedy (e.g. relax the thresholds)
  rather than persisting an empty cleaned run

### Requirement: QC Clean Persists a Versioned Cleaned Run and Returns Links

The `qc_clean` tool SHALL persist its outputs as a versioned run via the `ResultStore` port
under tool class `qc`, carrying the contract-stamped `Provenance` into the manifest, writing
the cleaned trait CSV and the cleanup log, and SHALL return the small in/out summary inline
together with `resource_link`s to the persisted artifacts — never the cleaned table inline.
The persisted run SHALL be resolvable by the `ExperimentReader` as a **cleaned version** so a
later `pca_analysis` (`require_clean=True`) consumes it.

#### Scenario: Run is committed with provenance

- **WHEN** `qc_clean` completes successfully
- **THEN** a `StoredRun` is recorded for `(experiment, "qc")` with a `run_ref`, a manifest
  path, and the same `Provenance` (including `seed = None`) the contract stamped
- **AND** the committed outputs include the cleaned CSV and the cleanup log

#### Scenario: Result returns links and a summary, not the table

- **WHEN** the tool returns its result
- **THEN** `n_samples_in` / `n_samples_out` / `n_traits_in` / `n_traits_out` (and retention)
  are inline
- **AND** the cleaned CSV and cleanup log are referenced via `resource_link`s to the
  persisted run rather than embedded inline

#### Scenario: Cleaned run composes into the PCA tool

- **WHEN** a downstream tool loads the experiment with `require_clean=True` after `qc_clean`
  has committed a run
- **THEN** the reader resolves the `qc_clean` cleaned version rather than the raw input
</content>
