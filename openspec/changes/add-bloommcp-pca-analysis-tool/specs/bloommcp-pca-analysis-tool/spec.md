## ADDED Requirements

### Requirement: PCA Analysis Tool Registration and Discovery

The system SHALL expose a `pca_analysis` MCP tool registered on the FastMCP server so it is
discoverable via the MCP `tools/list` operation. The tool name SHALL be stable (`pca_analysis`,
never versioned in the name) and its registration SHALL NOT remove or rename the existing
`run_dimensionality_reduction_workflow` tool or the vendored `bloom_mcp.pca` module.

#### Scenario: Tool appears in tools/list

- **WHEN** a FastMCP `Client` connects to the server and calls `tools/list`
- **THEN** a tool named `pca_analysis` is present with a description and an input schema derived
  from its Pydantic input model

#### Scenario: Existing dimensionality-reduction workflow is preserved

- **WHEN** the server registers `pca_analysis`
- **THEN** `run_dimensionality_reduction_workflow` remains registered and `bloom_mcp.pca` remains
  importable, so server boot is unaffected

### Requirement: PCA Analysis Delegates All Computation to the Tested Upstream Entry Point

The `pca_analysis` tool SHALL delegate the PCA computation to
`sleap_roots_analyze.perform_pca_analysis` and SHALL wrap its result into the upstream typed
`PCAResult` via `PCAResult.from_pca_dict`. It SHALL contain no PCA math of its own — no
standardization, eigendecomposition, component selection, or loadings/scores computation — and
SHALL NOT call the vendored `bloom_mcp.pca`.

#### Scenario: PCA is delegated, not re-implemented

- **WHEN** `pca_analysis` runs on a cleaned experiment frame
- **THEN** `sleap_roots_analyze.perform_pca_analysis` is invoked exactly once and the component
  count, explained-variance ratios, loadings, and scores are taken from its result via
  `PCAResult.from_pca_dict`
- **AND** the vendored `bloom_mcp.pca` is never called and the tool performs no standardization or
  decomposition itself

#### Scenario: Tunable parameters are forwarded to the delegate

- **WHEN** a caller sets `standardize`, `explained_variance_threshold`, or `n_components`
- **THEN** the tool forwards them to `perform_pca_analysis`, and an explicit `n_components`
  overrides threshold-based component selection

#### Scenario: A component count above the feature count is clamped, not rejected

- **WHEN** `n_components` exceeds the number of selected trait features
- **THEN** the delegate clamps the component count to the feature count without raising, and the
  result's reported `n_components` reflects the clamped value

### Requirement: PCA Analysis Requires a Cleaned Input and Selects Only Certified-Clean Traits

The `pca_analysis` tool SHALL load its experiment frame through the injected `ExperimentReader`
port with `require_clean=True`, as the **consumer** of cleaned data, and SHALL restrict the PCA to
columns within the resolved frame's certified-clean trait set (`frame.trait_cols`). It SHALL NOT
run PCA on a raw input and SHALL NOT perform its own cleaning. When no committed cleaned version
exists for the experiment, the tool SHALL surface a structured `BloomMCPError` whose remedy directs
the caller to run `qc_clean` first. A requested trait column outside the certified-clean set (or a
NaN that survives into the selected subset) SHALL be rejected rather than silently row-dropped by
the delegate.

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

### Requirement: PCA Analysis Reproduces the #120 turface_19 Golden Through the Tool

The `pca_analysis` tool SHALL, when invoked through the MCP boundary on the cleaned #120 turface_19
fixture restricted to the recorded golden trait columns, reproduce the independently recorded PCA
oracle within tolerance: three selected components and the recorded **cumulative** explained
variance. It SHALL additionally reproduce the recorded per-PC explained-variance split (a
characterization drift gate, not an independent oracle) within tolerance.

#### Scenario: Golden component count and cumulative explained variance match (independent oracle)

- **WHEN** `pca_analysis` runs on the cleaned turface_19 experiment with `trait_columns` set to the
  8 recorded `turface_19_pca_golden.json` trait columns, `standardize = true`, and
  `explained_variance_threshold = 0.95`
- **THEN** the result reports `n_components == 3`
- **AND** the `cumulative_variance_ratio` at the third component equals `0.9599095965599803` within
  `abs = 1e-6` — the independently recorded #120 / PR #146 value

#### Scenario: Per-PC explained-variance split matches the recorded characterization snapshot

- **WHEN** the same call completes
- **THEN** the per-PC `explained_variance_ratio` equals the recorded
  `pca_explained_variance_ratio` `[0.8612933510667774, 0.05820169635401897, 0.040414549139183936]`
  within `abs = 1e-6` — a drift gate re-derived from `perform_pca_analysis==0.1.0a3`, whose sum
  equals the independent cumulative oracle above

### Requirement: PCA Analysis Is Deterministic and Records No Seed

The `pca_analysis` tool SHALL be deterministic: it SHALL declare no `random_state` parameter, and
the stamped `Provenance` SHALL record `seed = None` (matching the codebase convention for
non-stochastic tools such as `qc_clean`). Two runs with identical inputs SHALL produce identical
results.

#### Scenario: Seed is recorded as None

- **WHEN** `pca_analysis` completes
- **THEN** the stamped `Provenance` records `seed = None`, together with the tool name and the PCA
  params (standardize, threshold, `n_components`, trait selection)

#### Scenario: Repeated runs are identical

- **WHEN** `pca_analysis` is invoked twice on the same cleaned experiment with the same parameters
- **THEN** the two results' `explained_variance_ratio` and `cumulative_variance_ratio` are equal
  within `abs = 1e-6`

### Requirement: PCA Analysis Honors the Contract Envelope

The `pca_analysis` tool SHALL be wrapped by `@as_mcp_tool` so that inputs and outputs are validated
against declared Pydantic models, every declared/undeclared failure is mapped to a structured
`BloomMCPError` (never a raw traceback or leaked backend internals), and a single `Provenance` is
stamped per call.

#### Scenario: Input/output schema round-trip

- **WHEN** a valid request is serialized to the tool's input schema and the result is validated
  against the output schema
- **THEN** both validate without loss

#### Scenario: Out-of-range parameters are rejected

- **WHEN** a request sets `explained_variance_threshold` outside `[0,1]` or `n_components < 1`
- **THEN** the tool returns a `BloomMCPError` with an input/validation code, and no run is persisted

#### Scenario: A caller-supplied trait column that is unknown or non-numeric is rejected

- **WHEN** `trait_columns` names a column absent from the experiment, or a non-numeric
  (metadata/identifier) column
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` whose message names the
  offending column(s), rather than an opaque internal error or a silently mis-selected fit

#### Scenario: A degenerate fit surfaces as a self-correctable error

- **WHEN** the delegate `perform_pca_analysis` raises `ValueError` (e.g. a `trait_columns` subset
  leaving fewer than two samples or no non-zero-variance column)
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` and a remedy (not
  `internal_error`), and no run is persisted

### Requirement: PCA Analysis Persists a Versioned Run With Lineage and Returns Links

The `pca_analysis` tool SHALL persist its outputs as a versioned run via the `ResultStore` port
under tool class `pca`, carrying the contract-stamped `Provenance` into the manifest, recording the
cleaned-source version it consumed as `based_on_version`, writing the component loadings and scores
and the serialized `PCAResult`, and SHALL return the small variance summary inline together with
**links** to the persisted artifacts — never the loadings or score matrices inline.

#### Scenario: Run is committed with provenance and cleaned-source lineage

- **WHEN** `pca_analysis` completes successfully
- **THEN** a `StoredRun` is recorded for `(experiment, "pca")` with a `run_ref`, a manifest path,
  the same `Provenance` (including `seed = None`) the contract stamped, and `based_on_version` equal
  to the consumed cleaned source version (e.g. `v3_cleaned`)
- **AND** the committed outputs include the loadings CSV, the component scores CSV, and the
  serialized `PCAResult` (`pca_result.json`)

#### Scenario: Result returns a summary and links, not the matrices

- **WHEN** the tool returns its result
- **THEN** `n_components`, the per-PC `explained_variance_ratio`, the `cumulative_variance_ratio`,
  the `eigenvalues`, `feature_names`, and the certified `n_samples` / `n_features` are inline
- **AND** the loadings and component-score matrices are referenced via links (object keys +
  manifest path) to the persisted run rather than embedded inline

### Requirement: PCA Analysis Is Exercised End-to-End by the Live Persistence Smoke

The `pca_analysis` tool SHALL be validated against a running dev stack through the **real**
`SupabaseReader` and `SupabaseResultStore` adapters (not the in-memory fakes) by the
`make bloommcp-smoke` driver, consuming a cleaned version committed by a prior `qc_clean` run —
proving the `qc_clean` → `pca_analysis` composition resolves and persists end-to-end. The smoke
driver's pure decision logic SHALL remain factored into importable helpers that are unit-testable
with no live stack.

#### Scenario: pca_analysis consumes a committed cleaned run through the real ports

- **WHEN** the live persistence smoke, after a `qc_clean` run has committed a cleaned version, calls
  `pca_analysis(experiment=…, trait_columns=…)` through the real `SupabaseReader` /
  `SupabaseResultStore`
- **THEN** the reader resolves the cleaned version (`require_clean=True` succeeds, source
  `v<N>_cleaned`, not `raw`)
- **AND** the committed PCA run's manifest reports `manifest_schema_version == 3`, records
  `based_on_version` equal to the consumed cleaned version, and each recorded `output_sha256` equals
  the SHA-256 of the bytes actually stored for its artifact
