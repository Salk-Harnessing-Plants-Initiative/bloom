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

- **WHEN** `qc_clean` completes on the raw turface_19 experiment (187 samples × 20 traits)
  at `max_nans_per_trait = 0.1`
- **THEN** the persisted cleaned trait table has zero NaNs across its kept trait columns
- **AND** the inline summary reports `n_samples_out == 187` and `n_traits_out == 18`,
  matching the recorded golden cleaned shape (the two NaN-heavy traits `Root_Biomass_mg` and
  `Root_Shoot_Ratio` are dropped)

#### Scenario: Fewer samples dropped than a naive dropna

- **WHEN** the same raw turface_19 trait columns are reduced by a naive row-wise `dropna()`
- **THEN** the naive drop retains 158 samples, while `qc_clean` retains all 187
  (`n_samples_out > naive_dropna_samples`)
- **AND** the summary reports `n_samples_in == 187` and `n_samples_out == 187` so the
  retention is visible

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

### Requirement: QC Clean Guarantees a No-NaN, Non-Degenerate Cleaned Table Before Persisting

The `qc_clean` tool SHALL verify, before committing any run, that the cleaned table has no
NaNs in its kept trait columns and retains at least one trait column and at least one sample.
If the cleanup would leave residual NaNs, drop every trait, or drop every sample — **whether
the delegate raises (e.g. over-strict thresholds leaving too few samples or no non-constant
trait) or returns a degenerate frame** — the tool SHALL surface a `BloomMCPError` (code
`assumption_violated`) with a relax-the-thresholds remedy and SHALL NOT persist a run, rather
than letting the delegate's error fall through to the contract's opaque `internal_error`.
This is the guarantee a downstream `pca_analysis (require_clean=True)` relies on.

#### Scenario: Over-strict thresholds (delegate raises) surface as a self-correctable error

- **WHEN** thresholds are strict enough that the delegate `clean_traits_for_analysis` raises
  (e.g. `min_samples_per_trait` larger than the dataset)
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` and a
  relax-the-thresholds remedy (not `internal_error`), and no run is persisted

#### Scenario: Residual NaNs in kept columns are rejected

- **WHEN** the cleaned table would still contain a NaN in a kept trait column
- **THEN** the tool returns a `BloomMCPError` and no run is persisted

#### Scenario: A cleanup that drops every trait is rejected

- **WHEN** the cleanup would leave no kept trait columns
- **THEN** the tool returns a `BloomMCPError` with a relax-the-thresholds remedy and no run is
  persisted

#### Scenario: A cleanup that drops every sample is rejected

- **WHEN** the cleanup would leave zero samples (even if trait columns survive)
- **THEN** the tool returns a `BloomMCPError` and no run is persisted, so no empty cleaned
  version can be resolved downstream

### Requirement: QC Clean Persists a Versioned Cleaned Run and Returns Links

The `qc_clean` tool SHALL persist its outputs as a versioned run via the `ResultStore` port
under tool class `qc`, carrying the contract-stamped `Provenance` into the manifest, writing
the cleaned trait CSV and the cleanup log, and SHALL return the small in/out summary inline
together with **links** to the persisted artifacts (the `run_ref`, the `manifest_path`, and
the per-output object keys) — never the cleaned table inline. The persisted run SHALL be
resolvable by the `ExperimentReader` as a **cleaned version** so a later `pca_analysis`
(`require_clean=True`) consumes it.

#### Scenario: Run is committed with provenance

- **WHEN** `qc_clean` completes successfully
- **THEN** a `StoredRun` is recorded for `(experiment, "qc")` with a `run_ref`, a manifest
  path, and the same `Provenance` (including `seed = None`) the contract stamped
- **AND** the committed outputs include the cleaned CSV and the cleanup log

#### Scenario: Result returns links and a summary, not the table

- **WHEN** the tool returns its result
- **THEN** `n_samples_in` / `n_samples_out` / `n_traits_in` / `n_traits_out` and the separate
  `sample_retention` / `trait_retention` ratios are inline
- **AND** the cleaned CSV and cleanup log are referenced via links (object keys + manifest
  path) to the persisted run rather than embedded inline

#### Scenario: Cleaned run composes into the PCA tool

- **WHEN** a downstream tool loads the experiment with `require_clean=True` after `qc_clean`
  has committed a run
- **THEN** the reader resolves the `qc_clean` cleaned version rather than the raw input

### Requirement: QC Clean Is Exercised End-to-End by the Live Persistence Smoke

The `qc_clean` tool SHALL be validated against a running dev stack through the **real**
`SupabaseReader` and `SupabaseResultStore` adapters (not the in-memory fakes) by the
`make bloommcp-smoke` driver, so the cleaned run is proven to persist with a v3 manifest and
real stored bytes and to be resolvable by `require_clean=True` as a no-NaN cleaned version —
the live counterpart to the fakes-backed composition above. The smoke driver's pure decision
logic SHALL remain factored into importable helpers that are unit-testable with no live stack.

#### Scenario: qc_clean persists a cleaned run through the real Supabase ports

- **WHEN** the live persistence smoke uploads the raw `turface_19` input as `turface_raw.csv`
  and runs `qc_clean(experiment="turface_raw.csv", max_nans_per_trait=0.1)` through the real
  `SupabaseReader` / `SupabaseResultStore`
- **THEN** the committed run's outputs include `_cleaned.csv` and `cleanup_log.json`, its
  manifest reports `manifest_schema_version == 3`, and each recorded `output_sha256` equals
  the SHA-256 of the bytes actually stored for **both** artifacts

#### Scenario: require_clean resolves the cleaned artifact with zero NaNs

- **WHEN** the smoke then calls `SupabaseReader().load_experiment("turface_raw.csv",
  require_clean=True)` after the `qc_clean` run commits
- **THEN** the reader resolves the committed cleaned version (source `v<N>_cleaned`, not the
  `raw` input)
- **AND** the resolved frame's trait columns contain zero NaN cells
  (`df[trait_cols].isna().sum().sum() == 0`)
</content>
