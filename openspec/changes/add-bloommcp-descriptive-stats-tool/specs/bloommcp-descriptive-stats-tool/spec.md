## ADDED Requirements

### Requirement: Descriptive Stats Tool Registration and Discovery

The system SHALL expose a `descriptive_stats` MCP tool registered on the FastMCP server (via the
`sleap_roots` section) so it is discoverable via the MCP `tools/list` operation. The tool name
SHALL be stable (`descriptive_stats`, never versioned in the name).

#### Scenario: Tool appears in tools/list

- **WHEN** a FastMCP `Client` connects to the server and calls `tools/list`
- **THEN** a tool named `descriptive_stats` is present with a description and an input schema
  derived from its Pydantic input model

### Requirement: Descriptive Stats Delegates All Computation to the Tested Upstream Entry Point

The `descriptive_stats` tool SHALL delegate its computation to
`sleap_roots_analyze.calculate_trait_statistics(df, trait_cols)` in exactly one call and SHALL
contain no statistics math of its own — no mean/std/quantile/skewness/kurtosis computation, and no
ANOVA, heritability, or variance-decomposition logic (each out of this tool's scope).

#### Scenario: Statistics are delegated, not re-implemented

- **WHEN** `descriptive_stats` runs on a cleaned experiment frame with a set of selected trait
  columns
- **THEN** `sleap_roots_analyze.calculate_trait_statistics` is invoked exactly once with those
  trait columns, and every reported per-trait statistic — `mean`, `std`, `median`, `q25`, `q75`,
  `min`, `max`, `cv`, `skewness`, `kurtosis`, and the delegate's `count` (surfaced as the result
  field `n`, the one deliberate rename) — is taken directly from its result with no recomputation

### Requirement: Descriptive Stats Requires a Cleaned Input and Selects Only Certified-Clean Traits

The `descriptive_stats` tool SHALL load its experiment frame through the injected
`ExperimentReader` port with `require_clean=True`, as a **consumer** of cleaned data, and SHALL
restrict the analysis to columns within the resolved frame's certified-clean trait set
(`frame.trait_cols`). It SHALL NOT compute statistics over a raw input. When no committed cleaned
version exists for the experiment, the tool SHALL surface a structured `BloomMCPError` whose
remedy directs the caller to run `qc_clean` first, and no run SHALL be persisted. A requested
`trait_columns` entry outside the certified-clean set, non-numeric, empty, or containing
duplicates SHALL be rejected as `invalid_input` rather than silently accepted or silently narrowed.

#### Scenario: A cleaned experiment is consumed

- **WHEN** `descriptive_stats` is invoked on an experiment that has a committed cleaned version (a
  `qc_clean` run)
- **THEN** the reader resolves the cleaned version (source `v<N>_cleaned`, not `raw`), and the tool
  computes statistics over it

#### Scenario: An experiment with no cleaned version is rejected with a remedy

- **WHEN** `descriptive_stats` is invoked on an experiment that has only a raw input and no
  committed cleaned version
- **THEN** the tool returns a `BloomMCPError` with a remedy to run `qc_clean` first, and no run is
  produced

#### Scenario: A trait column outside the certified-clean set is rejected

- **WHEN** `trait_columns` names a numeric column present in the frame but not in the
  certified-clean trait set (`frame.trait_cols`)
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the column, and does
  not compute statistics on it

#### Scenario: An explicitly empty or duplicate trait selection is rejected

- **WHEN** `trait_columns` is supplied as an empty list, or names the same column more than once
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, rather than treating an
  empty list as "all traits" or silently de-duplicating

### Requirement: Descriptive Stats Reproduces an Independently Computed Golden Through the Tool

The `descriptive_stats` tool SHALL, when invoked through the MCP boundary on the canonical-default
`qc_clean` output of the turface_19 fixture, reproduce the independently computed
`turface_19_stats_golden.json` values within tolerance for every recorded trait. Because
`calculate_trait_statistics` computes standard descriptive statistics (mean, standard deviation,
quantiles, skewness, kurtosis) via parameter-free textbook formulas, this golden — reproduced by
independent by-hand computation from the raw CSV — protects this tool's own code path (column
selection, ordering, serialization, cap/truncation, non-finite handling) more strongly than a mere
characterization/drift snapshot, though it is not an external cross-check of the upstream
`calculate_trait_statistics` implementation itself.

#### Scenario: Golden per-trait statistics match

- **WHEN** `descriptive_stats` runs on the canonical-default cleaned turface_19 experiment (158
  samples, 19 certified traits) with no `trait_columns` override
- **THEN** the result's `stats_per_trait` entry for `Shoot_Biomass_mg` reports `n == 158`, `mean ==
  158.2860759493671`, `std == 44.96525972299035`, `median == 158.75`, `q25 == 132.65`, `q75 ==
  188.54999999999998`, `min == 13.8`, `max == 253.5`, `cv == 0.28407590151754053`, `skewness ==
  -0.44548624755816857`, and `kurtosis == 0.2833162444945141`, each within `abs = 1e-9`
- **AND** the `Root_Shoot_Ratio` entry — a deliberately non-normal trait — reports `skewness ==
  6.782113998908719` and `kurtosis == 65.62876947585153` within `abs = 1e-6`, unmodified
  (not clipped, transformed, or flagged as anomalous)

#### Scenario: No sample is silently lost

- **WHEN** the same call completes
- **THEN** every reported trait's `n` equals 158 (the certified cleaned row count) — no certified
  trait loses samples to the delegate's per-trait `dropna()`, since `qc_clean` already guarantees
  zero NaN cells in kept trait columns

### Requirement: Descriptive Stats Is Deterministic and Records No Seed

The `descriptive_stats` tool SHALL be deterministic: it SHALL declare no `random_state` parameter,
and the stamped `Provenance` SHALL record `seed = None`. Two runs with identical inputs SHALL
produce identical results.

#### Scenario: Seed is recorded as None

- **WHEN** `descriptive_stats` completes
- **THEN** the stamped `Provenance` records `seed = None`, together with the tool name and the
  selected trait columns

#### Scenario: Repeated runs are identical

- **WHEN** `descriptive_stats` is invoked twice on the same cleaned experiment with the same
  `trait_columns`
- **THEN** the two results' `stats_per_trait` values are equal within `abs = 1e-9`

### Requirement: Descriptive Stats Bounds Its Inline Summary and Never Emits a Non-Finite JSON Token

The `descriptive_stats` tool SHALL cap its inline `stats_per_trait` summary to the first 50 traits
(in the order `trait_columns` was resolved) and SHALL set `truncated_in_summary = true` and list
the cut trait names in `omitted_traits` when more than 50 traits were computed; the persisted
`stats.csv` SHALL always contain every computed (non-failed) trait, uncapped. Any non-finite value
(`inf`, `-inf`, `nan`) produced by the delegate for a given statistic (e.g. `cv` for a zero-mean
trait, or `skewness`/`kurtosis` for a zero-variance trait) SHALL be represented as `null`/`None` in
both the inline result and the persisted CSV — never as a bare `Infinity`/`NaN` JSON token — and
SHALL be named in a `nonfinite_stat_traits` list rather than left as an unexplained blank cell.

#### Scenario: A wide experiment's inline summary is truncated and names what was cut

- **WHEN** `descriptive_stats` runs on an experiment with more than 50 certified traits (e.g. the
  cylinder fixture, ~649–880 traits)
- **THEN** the inline `stats_per_trait` contains exactly 50 entries, `truncated_in_summary == true`,
  `omitted_traits` lists exactly the trait names excluded from the inline summary (traits 51+, in
  the same resolved order), and the persisted `stats.csv` contains a row for every computed trait

#### Scenario: A non-finite statistic is coerced to null, not leaked as raw Infinity/NaN, and named

- **WHEN** a certified trait has a mean of exactly 0 (so `cv` is `inf`) or — via a hand-crafted
  cleaned frame bypassing `qc_clean`'s own zero-variance filter — zero variance (so
  `skewness`/`kurtosis` are `nan`)
- **THEN** the corresponding field in both the inline result and the persisted `stats.csv` is
  `None`/empty, the tool does not raise or leak a bare `Infinity`/`NaN` token in the JSON-RPC
  response, and the affected trait's name appears in `nonfinite_stat_traits`

### Requirement: Descriptive Stats Re-Verifies Finiteness Before Delegating

The `descriptive_stats` tool SHALL verify that every value in the selected certified trait columns
is finite (no `NaN`, `+inf`, or `-inf`) before delegating to `calculate_trait_statistics`, mirroring
`pca_analysis`'s defense-in-depth guard against a reader/`qc_clean`-invariant violation. A selected
certified trait carrying a non-finite value SHALL cause the tool to return a `BloomMCPError` with
code `assumption_violated` and no run persisted, rather than silently letting the delegate's own
per-trait `dropna()` under-report `n` with no signal.

#### Scenario: A non-finite value surviving into a certified trait is rejected, not silently under-counted

- **WHEN** a selected certified-clean trait carries a residual non-finite value (a violation of the
  invariant `qc_clean` is supposed to guarantee, reachable only via a reader/test double that
  bypasses that guarantee)
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` and no run is
  persisted, rather than reporting a trait whose `n` is silently smaller than the frame's true
  sample count

### Requirement: Descriptive Stats Honors the Contract Envelope

The `descriptive_stats` tool SHALL be wrapped by `@as_mcp_tool` so that inputs and outputs are
validated against declared Pydantic models, every declared/undeclared failure is mapped to a
structured `BloomMCPError` (never a raw traceback or leaked backend internals), and a single
`Provenance` is stamped per call.

#### Scenario: Input/output schema round-trip

- **WHEN** a valid request is serialized to the tool's input schema and the result is validated
  against the output schema
- **THEN** both validate without loss

#### Scenario: A caller-supplied trait column that is unknown or non-numeric is rejected

- **WHEN** `trait_columns` names a column absent from the experiment, or a non-numeric
  (metadata/identifier) column
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the offending
  column(s), rather than an opaque internal error

#### Scenario: A delegate-reported failed trait is surfaced, not raised

- **WHEN** the delegate returns `{"error": "No valid data"}` for a requested trait (a defense-in-depth
  branch — unreachable through a genuinely certified-clean selection since `qc_clean` guarantees no
  NaN cells in kept trait columns, but handled rather than assumed impossible)
- **THEN** the tool excludes that trait from `stats_per_trait`/`stats.csv`, increments `n_failed`,
  lists it in `failed_traits`, and does not raise a tool error

### Requirement: Descriptive Stats Persists a Versioned Run With Lineage and Returns Links

The `descriptive_stats` tool SHALL persist its output as a versioned run via the `ResultStore` port
under tool class `stats`, carrying the contract-stamped `Provenance` into the manifest, recording
the cleaned-source version it consumed as `based_on_version`, writing the full per-trait statistics
table as `stats.csv` (columns: `trait, n, mean, std, median, q25, q75, min, max, cv, skewness,
kurtosis`), and SHALL return the bounded inline summary together with **links** to the persisted
artifact — never the full table inline when it exceeds the 50-trait cap.

#### Scenario: Run is committed with provenance and cleaned-source lineage

- **WHEN** `descriptive_stats` completes successfully
- **THEN** a `StoredRun` is recorded for `(experiment, "stats")` with a `run_ref`, a manifest path,
  the same `Provenance` (including `seed = None`) the contract stamped, and `based_on_version` equal
  to the consumed cleaned source version
- **AND** the committed outputs include `stats.csv` with one row per computed (non-failed) trait

#### Scenario: Result returns a summary and links, not necessarily the full table

- **WHEN** the tool returns its result
- **THEN** `n_samples`, `n_traits_requested`, `n_traits_reported`, `n_failed`, `failed_traits`,
  `omitted_traits`, `nonfinite_stat_traits`, and the (possibly capped) `stats_per_trait` are inline,
  with no single field serializing to more than a few kilobytes
- **AND** the full per-trait table is always available via the persisted `stats.csv`, referenced
  through the inherited `RunLinks` fields

### Requirement: Descriptive Stats Is Exercised End-to-End Against the Real Dev Stack

The `descriptive_stats` tool SHALL be validated against a running dev stack through the **real**
`SupabaseReader` and `SupabaseResultStore` adapters (not the in-memory fakes), consuming a cleaned
version committed by a prior `qc_clean` run — both via the `make bloommcp-smoke` /
`live_persistence_smoke.py` driver's provenance/lineage checks, and via a dedicated
`tests/smoke/test_descriptive_stats_smoke.py` marked `live_smoke` and collected by CI's
`dev-stack-smoke` job (`pytest tests/smoke/ -m "live_smoke and not live_smoke_slow"`), matching the
per-tool smoke coverage every other granular tool (`pca_analysis`, `clustering`, `remove_outliers`,
etc.) already has.

#### Scenario: descriptive_stats consumes a committed cleaned run through the real ports

- **WHEN** the live persistence smoke, after a `qc_clean` run has committed a cleaned version, calls
  `descriptive_stats(experiment=...)` through the real `SupabaseReader`/`SupabaseResultStore`
- **THEN** the reader resolves the cleaned version (`require_clean=True` succeeds, source
  `v<N>_cleaned`, not `raw`)
- **AND** the committed run's manifest reports `manifest_schema_version == 3` and records
  `based_on_version` equal to the consumed cleaned version

#### Scenario: A dedicated per-tool smoke test runs the tool through the real MCP transport

- **WHEN** CI's `dev-stack-smoke` job collects `tests/smoke/test_descriptive_stats_smoke.py`
  against a seeded experiment (both `turface_19` and `cylinder`, via the shared `seeded_experiment`
  fixture)
- **THEN** calling `sleap_roots_qc_clean` then `sleap_roots_descriptive_stats` through a real
  `fastmcp.Client` succeeds, reports `n_traits_reported > 0`, and returns a resolvable `run_ref` and
  `manifest_path` — and on the wide `cylinder` fixture, exercises `truncated_in_summary == true` for
  real (not only in the synthetic unit test)
