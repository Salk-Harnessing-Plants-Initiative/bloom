## ADDED Requirements

### Requirement: Remove Outliers Tool Registration and Discovery

The system SHALL expose a `remove_outliers` MCP tool registered on the FastMCP server so it is
discoverable via the MCP `tools/list` operation. The tool name SHALL be stable
(`remove_outliers`, never versioned in the name) and SHALL NOT remove or rename the existing
`run_outlier_workflow` tool or the vendored `bloom_mcp.outlier_detection` module.

#### Scenario: Tool appears in tools/list

- **WHEN** a FastMCP `Client` connects to the server and calls `tools/list`
- **THEN** a tool named `remove_outliers` is present with a description and an input schema
  derived from its Pydantic input model

#### Scenario: Existing outlier workflow tool is preserved

- **WHEN** the server registers `remove_outliers`
- **THEN** `run_outlier_workflow` remains registered and `bloom_mcp.outlier_detection` remains
  importable, so server boot is unaffected

### Requirement: Remove Outliers Delegates All Detection and Removal to the Tested Upstream Entry Point

The `remove_outliers` tool SHALL delegate outlier detection and removal to
`sleap_roots_analyze.remove_outlier_samples` and SHALL contain no outlier detection or removal
logic of its own. It SHALL NOT call the vendored `bloom_mcp.outlier_detection` filters. It SHALL
pass the adapter-detected role columns (genotype / replicate / sample-id) from the
`ExperimentFrame` into the delegate rather than relying on the delegate's `geno`/`rep`/`Barcode`
defaults, and SHALL omit any role column the frame reports as `None` rather than forwarding
`None`. It SHALL forward the resolved seed to the delegate by keyword (`random_state=`).

#### Scenario: Detection and removal are delegated, not re-implemented

- **WHEN** `remove_outliers` runs on a cleaned experiment frame
- **THEN** the trimmed table and the outlier report are produced by `remove_outlier_samples`,
  called exactly once
- **AND** the tool performs no detection or row-dropping itself and never calls the vendored
  `bloom_mcp.outlier_detection` filters

#### Scenario: Detected role columns are forwarded to the delegate

- **WHEN** the experiment's role columns differ from the delegate defaults (e.g. `Genotype` /
  `Replicate` rather than `geno` / `rep`)
- **THEN** `remove_outliers` passes the `ExperimentFrame`'s detected `sample_id_col` /
  `genotype_col` / `replicate_col` into `remove_outlier_samples` as `barcode_col` /
  `genotype_col` / `replicate_col`

#### Scenario: An undetected role column falls back to the delegate default

- **WHEN** the `ExperimentFrame` reports a role column (e.g. `genotype_col`) as `None`
- **THEN** `remove_outliers` omits that kwarg so `remove_outlier_samples` uses its default,
  rather than forwarding `None`

### Requirement: Remove Outliers Exposes a Small Method Surface With Validated Per-Method Thresholds

The `remove_outliers` tool SHALL expose exactly two detection methods — `mahalanobis` (default)
and `isolation_forest` — and SHALL forward the per-method threshold via the delegate's
`**detect_kwargs` (`chi2_percentile` for mahalanobis, `contamination` for isolation_forest). A
threshold field set for a method it does not belong to SHALL be rejected up front — validated in
the tool body — as a structured `invalid_input` error naming the offending field, rather than
surfacing the delegate's opaque cross-method `ValueError`. A caller-supplied `trait_columns`
subset SHALL likewise be validated (existence + numeric dtype) in the tool body, with an unknown
or non-numeric column rejected as `invalid_input` naming the offending column.

#### Scenario: Default method is mahalanobis

- **WHEN** `remove_outliers` is invoked with no `method`
- **THEN** it delegates with `method="mahalanobis"`

#### Scenario: A threshold set for the wrong method is rejected

- **WHEN** `method="mahalanobis"` and a `contamination` value is supplied (or
  `method="isolation_forest"` and a `chi2_percentile` value is supplied)
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` whose message names the
  offending field, and no run is persisted

#### Scenario: An unknown or non-numeric trait column is rejected

- **WHEN** `trait_columns` names a column absent from the experiment, or a non-numeric
  (metadata/identifier) column
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the offending
  column(s), rather than an opaque internal error or silent mis-filtering

### Requirement: Remove Outliers Operates on Cleaned Input and Enforces the QC Guardrail

The `remove_outliers` tool SHALL load its experiment frame through the injected
`ExperimentReader` port with `require_clean=True` — it is a *consumer* of cleaned, NaN-free
data. If no cleaned version can be resolved (the reader raises `CleanedVersionRequiredError`),
the tool SHALL catch it in its body and return a structured
`BloomMCPError(assumption_violated)` whose remedy directs the caller to run `qc_clean` first,
and SHALL NOT persist a run.

#### Scenario: Cleaned experiment is loaded for trimming

- **WHEN** `remove_outliers` is invoked on an experiment that has a cleaned version
- **THEN** the tool loads the cleaned frame (`require_clean=True`) and proceeds with detection

#### Scenario: Un-cleaned input is rejected with the QC guardrail

- **WHEN** the requested experiment has no cleaned version (`require_clean=True` cannot resolve
  one)
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` and a remedy of
  "run qc_clean first", and no run is persisted

### Requirement: Remove Outliers Honors the Contract Envelope and Records the Resolved Seed

The `remove_outliers` tool SHALL be wrapped by `@as_mcp_tool` so that inputs and outputs are
validated against declared Pydantic models, every failure is mapped to a structured
`BloomMCPError` (never a raw traceback or leaked backend internals), and a single `Provenance`
is stamped per call. Because detection is stochastic, the tool SHALL declare `random_state` so
the contract records the **resolved integer** seed (the given `seed`, or a fresh integer) in
`Provenance` — the seed that actually reached the delegate. Because several report fields are
method-dependent, the output model SHALL type `threshold_type` as `Optional[str]`,
`threshold_value` as `Optional[float]`, and `goodness_of_fit` as `Optional[dict]` (all `None`
for `isolation_forest`), so a valid `isolation_forest` result does not fail output validation.

#### Scenario: Input/output schema round-trip

- **WHEN** a valid request is serialized to the tool's input schema and the result is validated
  against the output schema
- **THEN** both validate without loss, and an invalid input (e.g. missing experiment, an
  unknown `method`, a `chi2_percentile` out of `(0,100)`) yields a `BloomMCPError` with an
  `invalid_input` code

#### Scenario: An isolation_forest result validates with null threshold and fit fields

- **WHEN** `remove_outliers` runs with `method="isolation_forest"`
- **THEN** the result validates with `threshold_type is None`, `threshold_value is None`, and
  `goodness_of_fit is None`, and a run is persisted normally

#### Scenario: An undeclared delegate failure surfaces scrubbed, without leaking internals

- **WHEN** the delegated computation or backend read/write raises an undeclared exception whose
  message embeds a backend path, host, or secret
- **THEN** the caller receives a `BloomMCPError` with code `internal_error` and a correlation
  reference, and neither the message nor the remedy leaks the path, host, or secret

#### Scenario: Provenance is stamped with the resolved integer seed

- **WHEN** `remove_outliers` completes with the default `seed = 42`
- **THEN** the stamped `Provenance` records the tool name, the method + threshold + trait
  selection params, and `seed = 42` (a resolved integer, not `None`), and the persisted run's
  recorded `seed` matches

### Requirement: Remove Outliers Guarantees a Non-Degenerate, No-NaN Trimmed Table Before Persisting

The `remove_outliers` tool SHALL, before committing any run, verify defensively that the
trimmed table has no NaNs in its trait columns, is a row-subset of the cleaned input with its
trait columns unchanged, and retains at least one sample — **whether the delegate raises on a
degenerate trim or returns a degenerate frame**. A delegate raise (`ValueError` /
`OutlierRemovalError`, its subclass, on a trim below the minimum surviving samples or with no
non-constant trait) SHALL be caught in the tool body and surfaced as a
`BloomMCPError(assumption_violated)` with a relax-the-threshold remedy, and any failed own-guard
assertion SHALL likewise surface a structured error — in neither case persisting a run, and
never letting the delegate's error fall through to the contract's opaque `internal_error`.

#### Scenario: An over-aggressive trim (delegate raises) surfaces as a self-correctable error

- **WHEN** the threshold is aggressive enough that `remove_outlier_samples` raises (e.g. a
  `chi2_percentile` low enough to trim below the minimum surviving samples, or leaving no
  non-constant trait)
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` and a
  relax-the-threshold remedy (raise `chi2_percentile` / lower `contamination`), not
  `internal_error`, and no run is persisted

#### Scenario: A non-unique index is rejected

- **WHEN** the cleaned frame has a non-unique index (the delegate's precondition fails)
- **THEN** the tool returns a `BloomMCPError` and no run is persisted, so no misaligned trim can
  be produced

#### Scenario: Own guard rejects a degenerate returned frame

- **WHEN** the delegate were to return (rather than raise on) a trimmed frame with a residual
  NaN in a trait column or zero surviving samples
- **THEN** the tool's pre-commit guard raises a `BloomMCPError` and persists nothing, so no
  corrupt cleaned version can be resolved downstream

### Requirement: Remove Outliers Reproduces the Golden Trim Through the Tool

The `remove_outliers` tool SHALL, when invoked through the MCP boundary on the cleaned
turface_19 fixture at `method=mahalanobis`, `seed=42`, reproduce a recorded golden
characterization snapshot: the number of flagged outliers, the number of retained samples, and
the sorted flagged sample barcodes. The cleaned input is the frame `qc_clean` produces at its
**canonical-default thresholds** (`max_nans_per_trait=0.2`, etc.), which retains **158 samples**
— recorded in `turface_19_outlier_golden.json` with its exact cleaning params. (Note: this
differs from `turface_19_qc_golden.json`, which snapshots the `max_nans_per_trait=0.1` clean at
187 samples; the 158 here is the canonical-default cleaned count, **not** the naive-`dropna`
number.) The golden is an explicit recorded value (mirroring the `qc_clean` golden), not
re-derived from the code under test, and is understood to be a method+seed characterization
(turface_19's mahalanobis chi-squared fit is poor), not a claim that the flagged samples are
ground-truth outliers.

#### Scenario: Flagged counts and barcodes match the recorded golden

- **WHEN** `remove_outliers` completes on the cleaned turface_19 experiment (158 samples) at
  `method=mahalanobis`, `seed=42`
- **THEN** the inline report satisfies `n_input_samples == 158`, `n_outliers == 8`, and
  `n_output_samples == 150`, matching the recorded golden
- **AND** the sorted `outlier_barcodes` equal the recorded golden barcode list
- **AND** the persisted trimmed table has `n_output_samples` rows and zero NaNs in its trait
  columns

#### Scenario: Goodness-of-fit is surfaced as a structured field, not hidden

- **WHEN** `remove_outliers` completes with `method=mahalanobis` and the chi-squared assumption
  fits poorly for the experiment
- **THEN** the inline report includes `goodness_of_fit` as the delegate's fit-report dict, whose
  `fit_quality` reads `"very_poor"`, so the caller can judge the threshold's trustworthiness

### Requirement: Remove Outliers Persists a Versioned Trimmed Cleaned Run and Returns Links

The `remove_outliers` tool SHALL persist its outputs as a versioned run via the `ResultStore`
port under tool class `qc`, carrying the contract-stamped `Provenance` into the manifest,
writing the trimmed trait CSV under the shared `CLEANED_CSV_NAME` (`_cleaned.csv`) and the
outlier report as `outlier_report.json`, and SHALL return the numeric report inline together
with **`resource_link`s** to the persisted artifacts (the `run_ref`, the `manifest_path`, and
the per-output object keys) — never the trimmed table inline. The persisted run SHALL be
resolvable by the `ExperimentReader` as the newest **cleaned version** so any later
`require_clean=True` consumer reads the trimmed table.

#### Scenario: Run is committed with provenance under class qc

- **WHEN** `remove_outliers` completes successfully
- **THEN** a `StoredRun` is recorded for `(experiment, "qc")` with a `run_ref`, a manifest path,
  and the same `Provenance` (including the resolved integer `seed`) the contract stamped
- **AND** the committed outputs include the trimmed `_cleaned.csv` and `outlier_report.json`,
  and reloading `outlier_report.json` yields valid JSON carrying the report (`n_outliers`)

#### Scenario: Result returns links and the report, not the table

- **WHEN** the tool returns its result
- **THEN** the numeric report (`method`, `n_input_samples`, `n_outliers`, `n_output_samples`,
  `removal_fraction`, `threshold_type`, `threshold_value`, `goodness_of_fit`,
  `outlier_barcodes`) is inline
- **AND** the trimmed CSV and outlier report are referenced via `resource_link`s (object keys +
  manifest path) to the persisted run rather than embedded inline (no dataframe/blob field)

#### Scenario: Counts and removal fraction are internally consistent

- **WHEN** `remove_outliers` returns its report
- **THEN** `0 < n_output_samples <= n_input_samples`, `n_outliers == n_input_samples -
  n_output_samples`, and `removal_fraction == n_outliers / n_input_samples`

#### Scenario: Trimmed run supersedes the prior clean as the newest cleaned version

- **WHEN** `remove_outliers` commits a run for an experiment that already had a `qc_clean`
  cleaned version, and later a second `remove_outliers` run commits
- **THEN** each run is a distinct version (`v<N>`, `v<N+1>`) and a `require_clean=True` load
  resolves the latest (most recently committed) trimmed version — the order-dependent
  "latest cleaned" behavior the design documents

### Requirement: Remove Outliers Optionally Persists Detection Plots as Linked Artifacts

The `remove_outliers` tool SHALL default to a report-only result (`include_plots=False`). When
`include_plots=True`, it SHALL delegate figure generation to
`sleap_roots_analyze.plot_outlier_analysis` (re-detecting with the same seed and parameters),
persist each returned Figure into the same versioned run via `ResultStore` (recorded in
`output_keys` / `output_sha256`), and return `resource_link`s to those figures — not inline
image blobs and not a URL-string result shape. The MCP SHALL contain no plotting logic: with
`plots=None` it SHALL persist every figure the delegate returns for the chosen method, and an
explicit `plots` list SHALL be validated in the tool body against the delegate's available
figure keys before delegating and forwarded as the delegate's `which=`.

#### Scenario: Default is report-only (no plots)

- **WHEN** `remove_outliers` is invoked without `include_plots`
- **THEN** the result contains the numeric report and the trimmed-run links, and no figure
  artifacts are persisted

#### Scenario: Plots are persisted and returned as links

- **WHEN** `remove_outliers` is invoked with `include_plots=True` and `method=mahalanobis`
- **THEN** the persisted run carries the figure artifacts the delegate returned for the method
  (the `mahalanobis_*` figures, plus the per-genotype figure when the genotype column is
  present) in its `output_keys`, and the result references them via resource links (object
  keys), not inline image blobs

#### Scenario: A specific requested plot is honored

- **WHEN** `include_plots=True` and `plots` names an available figure key for the method
- **THEN** exactly that figure is persisted and linked

#### Scenario: An unknown requested plot key is rejected

- **WHEN** `include_plots=True` and `plots` names a figure key the delegate does not produce for
  the chosen method
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the unknown key,
  and no run is persisted

### Requirement: Remove Outliers Is Exercised End-to-End by the Live Persistence Smoke

The `remove_outliers` tool SHALL be validated against a running dev stack through the **real**
`SupabaseReader` and `SupabaseResultStore` adapters (not the in-memory fakes) by the
`make bloommcp-smoke` driver, so the trimmed run is proven to persist with a v3 manifest and
real stored bytes and to be resolvable by `require_clean=True` as a no-NaN trimmed cleaned
version. The smoke driver's pure decision logic SHALL remain factored into importable helpers
that are unit-testable with no live stack.

#### Scenario: remove_outliers persists a trimmed run through the real Supabase ports

- **WHEN** the live persistence smoke first runs `qc_clean` on the seeded raw `turface_19`
  input, then runs `remove_outliers(experiment=…, method="mahalanobis", seed=42)` through the
  real `SupabaseReader` / `SupabaseResultStore`
- **THEN** the committed run's outputs include `_cleaned.csv` and `outlier_report.json`, its
  manifest reports `manifest_schema_version == 3`, and each recorded `output_sha256` equals the
  SHA-256 of the bytes actually stored

#### Scenario: require_clean resolves the trimmed artifact with zero NaNs

- **WHEN** the smoke then calls `SupabaseReader().load_experiment(…, require_clean=True)` after
  the `remove_outliers` run commits
- **THEN** the reader resolves the committed trimmed version (source `v<N>_cleaned`) with fewer
  samples than the pre-trim clean
- **AND** the resolved frame's trait columns contain zero NaN cells
