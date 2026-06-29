## ADDED Requirements

### Requirement: QC Inspect Tool Registration and Discovery

The system SHALL expose a `qc_inspect` MCP tool registered on the FastMCP server so it is
discoverable via the MCP `tools/list` operation. The tool name SHALL be stable (`qc_inspect`,
never versioned in the name) and SHALL NOT remove or rename the existing `qc_clean` or
`run_qc_workflow` tools or the vendored `bloom_mcp.data_cleanup` module.

#### Scenario: Tool appears in tools/list

- **WHEN** a FastMCP `Client` connects to the server and calls `tools/list`
- **THEN** a tool named `qc_inspect` is present with a description and an input schema derived
  from its Pydantic input model

#### Scenario: Sibling QC tools are preserved

- **WHEN** the server registers `qc_inspect`
- **THEN** `qc_clean` and `run_qc_workflow` remain registered and `bloom_mcp.data_cleanup`
  remains importable, so server boot is unaffected

### Requirement: QC Inspect Wraps the Tested Upstream EDA Functions

The `qc_inspect` tool SHALL produce its missingness report by delegating to
`sleap_roots_analyze`'s EDA functions — `apply_data_cleanup_filters` (to obtain the
`cleanup_log`), `create_trait_eda_plots` (per-trait NaN/zero/outlier bar charts with the
threshold lines and the "traits actually removed" panel), the `missing_data_pattern` figure
from `create_exploratory_summary_plots`, and `inspect_nan_samples` (the per-sample NaN report)
— and SHALL contain no EDA, plotting, or NaN-tabulation logic of its own. It SHALL NOT call
the vendored `bloom_mcp.data_cleanup`. It SHALL pass the adapter-detected role columns
(genotype / replicate / sample-id) from the `ExperimentFrame` into each delegate that accepts
them rather than relying on the delegate's `geno` / `rep` / `Barcode` defaults.

#### Scenario: EDA is delegated, not re-implemented

- **WHEN** `qc_inspect` runs on a raw experiment frame
- **THEN** the per-trait fraction charts, the missingness heatmap, the cleanup log, and the
  per-sample NaN table are produced by the upstream analyze functions
- **AND** the tool performs no per-trait or per-sample fraction computation, plotting, or
  filtering itself, and does not call `bloom_mcp.data_cleanup`

#### Scenario: Detected role columns are forwarded to the delegates

- **WHEN** the experiment's role columns differ from the delegate defaults (e.g. `Genotype` /
  `Replicate` rather than `geno` / `rep`)
- **THEN** `qc_inspect` passes the `ExperimentFrame`'s detected `genotype_col`,
  `replicate_col`, and `sample_id_col` into the delegates that accept them

#### Scenario: An undetected role column falls back to the delegate default

- **WHEN** the `ExperimentFrame` reports a role column (e.g. `genotype_col`) as `None`
- **THEN** `qc_inspect` lets the delegate use its default for that role rather than
  forwarding `None`

### Requirement: QC Inspect Operates on Raw Input and Reads via the Port

The `qc_inspect` tool SHALL load its experiment frame through the injected `ExperimentReader`
port as a **read-only inspector** of the raw missingness — it SHALL NOT request
`require_clean=True` — and SHALL surface an unresolvable experiment as a structured error
rather than a raw backend message.

#### Scenario: Raw experiment is loaded for inspection

- **WHEN** `qc_inspect` is invoked on an experiment that has a raw input
- **THEN** the tool loads the raw frame and inspects its missingness, including any NaN-bearing
  traits (an all-NaN or NaN-heavy trait is reportable, not an error)

#### Scenario: Missing experiment is rejected with a structured error

- **WHEN** the requested experiment cannot be resolved by the reader
- **THEN** the tool returns a `BloomMCPError` with a code and remedy, and no run is produced

### Requirement: QC Inspect Honors the Contract Envelope

The `qc_inspect` tool SHALL be wrapped by `@as_mcp_tool` so that inputs and outputs are
validated against declared Pydantic models, every declared/undeclared failure is mapped to a
structured `BloomMCPError` (never a raw traceback or leaked backend internals), and a single
`Provenance` is stamped per call. It SHALL accept the **same threshold parameters as
`qc_clean`** (`max_zeros_per_trait`, `max_nans_per_trait`, `max_nans_per_sample`,
`min_samples_per_trait`, optional `trait_columns`) so its threshold overlays and its
recommendation reflect what a subsequent `qc_clean` would apply (the NaN/zero overlay lines
reflect `max_nans_per_trait` / `max_zeros_per_trait`; `max_nans_per_sample` and
`min_samples_per_trait` shape the cleanup log and therefore `traits_would_be_removed`).

#### Scenario: Input/output schema round-trip

- **WHEN** a valid request is serialized to the tool's input schema and the result is
  validated against the output schema
- **THEN** both validate without loss, and an invalid input (e.g. missing experiment, or a
  threshold outside `[0,1]`) yields a `BloomMCPError` with an `input`/validation code

#### Scenario: A caller-supplied trait column that is unknown or non-numeric is rejected

- **WHEN** `trait_columns` names a column absent from the experiment, or a non-numeric
  (metadata/identifier) column
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` whose message names
  the offending column(s), rather than an opaque internal error

#### Scenario: Errors surface as a structured envelope

- **WHEN** a delegated computation or backend read/write raises
- **THEN** the caller receives a `BloomMCPError` with a code and remedy, and no raw traceback
  or backend path is leaked

#### Scenario: Provenance is stamped and seed recorded as None

- **WHEN** `qc_inspect` completes
- **THEN** the stamped `Provenance` records the tool name, the threshold and trait-selection
  params, and `seed = None` (QC inspection applies no `random_state`)

### Requirement: QC Inspect Produces a Threshold-Aware Missingness Recommendation

The `qc_inspect` tool SHALL return, inline, a structured recommendation that reports per-trait
NaN fractions, the traits that exceed the supplied thresholds, the traits a `qc_clean` at
those thresholds would remove (from the cleanup log), and a recommended `max_nans_per_trait`
together with its consequence — which traits it would drop, the samples it would lose, and the
samples a naive row-wise `dropna()` would lose instead — so the agent can pick a threshold
before committing a `qc_clean` run.

#### Scenario: Recommendation flags the NaN-heavy traits and minimizes sample loss (turface_19 oracle)

- **WHEN** `qc_inspect` runs on the raw `turface_19` experiment (187 samples × 20 traits) at
  the canonical `qc_clean` defaults (`max_nans_per_trait = 0.2`, `max_nans_per_sample = 0.0`)
- **THEN** the per-trait NaN fractions report `Root_Biomass_mg` and `Root_Shoot_Ratio` at
  ~0.155, both **under** the 0.2 default so neither is in `traits_would_be_removed` — and
  because `max_nans_per_sample = 0.0`, keeping them drops their 29 NaN-bearing samples
  (`samples_lost_at_current_params == 29`)
- **AND** the recommendation proposes a `recommended_max_nans_per_trait` strictly below 0.155
  whose `would_remove_traits` is exactly `{Root_Biomass_mg, Root_Shoot_Ratio}`, with
  `samples_lost_at_recommendation == 0` versus `naive_dropna_samples_lost == 29`

#### Scenario: Recommendation tracks the supplied thresholds

- **WHEN** `qc_inspect` is invoked with `max_nans_per_trait = 0.1`
- **THEN** `traits_would_be_removed` already contains `Root_Biomass_mg` and `Root_Shoot_Ratio`
  (they exceed 0.1), and the recommendation reports zero additional residual-NaN traits and
  zero sample loss at the supplied threshold

#### Scenario: No NaN-bearing trait yields a no-change recommendation

- **WHEN** `qc_inspect` runs on an experiment whose trait columns carry no NaNs that the
  supplied thresholds would leave behind
- **THEN** the recommendation reports "no change recommended" — `would_remove_traits` is empty
  and `samples_lost_at_recommendation == 0` — rather than proposing a spurious lower threshold

#### Scenario: An all-NaN trait is reported, not rejected

- **WHEN** a trait column is entirely NaN
- **THEN** the report lists that trait with a NaN fraction of 1.0 and includes it in
  `traits_would_be_removed`, and the tool returns a report (it does not raise), because
  `qc_inspect` inspects missingness rather than gating on it

### Requirement: QC Inspect Persists a Versioned Report Run and Returns Links

The `qc_inspect` tool SHALL persist its outputs as a versioned run via the `ResultStore` port
under tool class `qc_inspect`, carrying the contract-stamped `Provenance` into the manifest,
writing the EDA figures (PNG), the `inspect_nan_samples` CSV, and a `recommendation.json`, and
SHALL return the small inline summary and recommendation together with **links** to the
persisted artifacts (the `run_ref`, the `manifest_path`, and the per-output object keys) —
never the figures or tables inline as blobs / base64.

#### Scenario: Run is committed with provenance

- **WHEN** `qc_inspect` completes successfully
- **THEN** a `StoredRun` is recorded for `(experiment, "qc_inspect")` with a `run_ref`, a
  manifest path, and the same `Provenance` (including `seed = None`) the contract stamped
- **AND** the committed outputs include the EDA figure(s), the NaN-samples CSV, and the
  recommendation JSON

#### Scenario: Result returns links and a summary, not blobs

- **WHEN** the tool returns its result
- **THEN** the per-trait NaN summary and the structured recommendation are inline
- **AND** the figures and the NaN-samples table are referenced via links (object keys +
  manifest path) to the persisted run rather than embedded inline

#### Scenario: Persisted figures round-trip as real bytes

- **WHEN** the committed run's recorded artifacts are read back from the store
- **THEN** each persisted PNG is a non-empty image whose stored bytes match the recorded
  `output_sha256`, and the recommendation JSON deserializes to the same recommendation
  returned inline

### Requirement: QC Inspect Is Read-Only and Does Not Produce a Cleaned Version

The `qc_inspect` tool SHALL be read-only with respect to the experiment data: it SHALL NOT
write `CLEANED_CSV_NAME`, SHALL NOT persist under tool class `qc`, and SHALL NOT mutate the
input frame. Its report run SHALL NOT be resolvable by the `ExperimentReader` as a cleaned
version, so a later `load_experiment(require_clean=True)` never resolves a `qc_inspect` run.

#### Scenario: A qc_inspect run is not a cleaned version

- **WHEN** `qc_inspect` has committed a report run for an experiment that has no `qc_clean`
  run, and a downstream caller loads it with `require_clean=True`
- **THEN** the reader does **not** resolve the `qc_inspect` run as a cleaned version (it
  surfaces the no-cleaned-version outcome the experiment-read capability already defines),
  because `qc_inspect` wrote no `qc`-class `_cleaned.csv`

#### Scenario: Report artifacts are distinct from a cleaned run

- **WHEN** the `qc_inspect` run's committed outputs are listed
- **THEN** they are the report artifact set (EDA figures, NaN-samples CSV, recommendation
  JSON) under tool class `qc_inspect`, and contain no `CLEANED_CSV_NAME` artifact
</content>
