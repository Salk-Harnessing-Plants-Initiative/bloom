## ADDED Requirements

### Requirement: RunLinks Output Signed URLs

`RunLinks` SHALL carry an additive `output_links: dict[str, OutputLink]` field alongside its
existing `outputs: dict[str, str]` field — `outputs` SHALL remain unchanged in meaning and
value (logical output name → object key). Each `OutputLink` SHALL carry the output's storage
`key`, a signed/served `url`, its `sha256`, and its non-negative `size_bytes`. Every tool result model that
persists artifacts through `ResultStore` — whether it inherits `RunLinks` directly or
independently declares the same four run-link fields — SHALL populate `output_links` from the
`StoredRun` its own `commit()` call returned, so that every consumer-tool result includes a
working download link and integrity hash for every output it reports, with no exceptions.

#### Scenario: A consumer tool's result carries a link per output

- **WHEN** any tool that persists via `ResultStore` (e.g. `pca_analysis`, `remove_outliers`,
  `qc_clean`) completes successfully
- **THEN** its result's `output_links` has one entry per `outputs` entry, each with a non-empty
  `url`, a `sha256` matching the committed run's `output_sha256`, and a non-negative `size_bytes`

#### Scenario: The existing outputs field is unchanged

- **WHEN** a tool result is constructed after this change
- **THEN** its `outputs: dict[str, str]` field has the exact same keys and values
  (`{name: object_key}`) it would have had before `output_links` was added

#### Scenario: A tool result model that duplicates RunLinks's fields still gets the field

- **WHEN** a tool result model (e.g. `ClusteringResult`, `QCCleanResult`, `QCInspectResult`)
  declares `run_ref`/`version_dir`/`manifest_path`/`outputs` inline instead of inheriting
  `RunLinks`
- **THEN** it still carries `output_links`, populated the same way as a `RunLinks` subclass
