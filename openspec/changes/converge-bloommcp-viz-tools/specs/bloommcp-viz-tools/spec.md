## ADDED Requirements

### Requirement: Contract-Wrapped Trait Visualization Tools

`plot_trait_histograms`, `plot_trait_boxplots`, and `plot_correlation_matrix` SHALL be wrapped
by `@as_mcp_tool` with a Pydantic input model and a Pydantic `RunLinks`-based output model, each
declaring `provenance` (a single `Provenance` stamped per call) but no `random_state` (these
tools are deterministic given their input selection). Each tool SHALL delegate all figure
*rendering* to `sleap_roots_analyze` and SHALL introduce no new rendering or statistics logic of
its own; `plot_correlation_matrix`'s existing, non-delegated strong-correlation summary count
(computed directly from the selected trait frame) carries over unchanged, not as new logic this
requirement introduces.

#### Scenario: Structured result replaces the plain string

- **WHEN** any of the 3 tools is called with a valid `experiment`
- **THEN** the result is a Pydantic object with `run_ref`, `version_dir`, `manifest_path`,
  `outputs`, and `output_links` fields (never a plain formatted string)

#### Scenario: Invalid input is a structured error

- **WHEN** a tool is called with a missing `experiment` or another schema-violating input
- **THEN** it raises a `BloomMCPError` with `code="invalid_input"`, not a raw validation
  exception or a plain error string

### Requirement: Raw (Pre-Clean) Experiment Read

Each of the 3 tools SHALL read the experiment frame through the `ExperimentReader` port with no
`require_clean` (equivalent to `version="raw"`), the same read-only pre-clean posture as
`qc_inspect`.

#### Scenario: A cleaned version existing does not change the read

- **WHEN** `experiment` has both a raw and a previously cleaned version on record
- **THEN** the tool reads the raw frame, not the cleaned one, and its result reflects the raw
  data's trait values

### Requirement: Trait Column Selection Validation

Each tool SHALL accept an optional `trait_columns: list[str]` parameter (replacing the legacy
comma-separated `traits: str`). When omitted (`None`), all detected trait columns are used. When
supplied, every name SHALL exist in the frame and be numeric, validated the same way `qc_clean`/
`qc_inspect` validate a raw-frame trait subset (`require_certified=False`). An explicit empty
list is rejected, not treated as "all traits". An experiment with zero detected numeric trait
columns is rejected, not silently plotted as an empty figure.

#### Scenario: Unknown trait name is rejected, not silently dropped

- **WHEN** `trait_columns` names a column that does not exist in the experiment, or a
  non-numeric column
- **THEN** the tool raises `BloomMCPError(code="invalid_input")` naming the offending column(s)
  and does not silently plot a reduced subset

#### Scenario: Explicit empty trait_columns is rejected

- **WHEN** `trait_columns` is supplied as `[]`
- **THEN** the tool raises `BloomMCPError(code="invalid_input")` rather than treating it as
  "all detected traits"

#### Scenario: No numeric trait columns detected

- **WHEN** `trait_columns` is omitted and the experiment has no detected numeric trait columns
- **THEN** the tool raises `BloomMCPError(code="invalid_input")` and no run is persisted

#### Scenario: Duplicate trait name is rejected, not silently double-counted

- **WHEN** `trait_columns` names the same trait more than once
- **THEN** the tool raises `BloomMCPError(code="invalid_input")` naming the duplicated column,
  rather than proceeding with a repeated selection (for `plot_correlation_matrix`, an unchecked
  duplicate would otherwise count a self-correlation as a "strong positive correlation")

### Requirement: Path-Safety Guard Before Any Read

Each tool SHALL reject a non-bare `experiment` identifier (containing a path separator, `..`, or
an absolute path) before any file read is attempted, raising `BloomMCPError(code=
"invalid_input")`.

#### Scenario: Traversal filename never reaches the delegate

- **WHEN** `experiment` is `"../secret.csv"`, `"..\\secret.csv"`, an absolute path, or a path
  outside the configured experiment root
- **THEN** the tool raises `BloomMCPError` before the `ExperimentReader` is invoked, and the
  `sleap_roots_analyze` delegate is never called

### Requirement: Versioned Run Persistence With Per-Tool Tool Classes

Each tool SHALL persist a versioned run via the `ResultStore` port under its own tool class
(`trait_histograms` for `plot_trait_histograms`, `trait_boxplots` for `plot_trait_boxplots`,
`correlation_matrix` for `plot_correlation_matrix`). Every rendered PNG SHALL be committed as a
run output (never returned as an inline blob); the result SHALL expose it via `outputs` +
`output_links`, never a bare file-system URL.

#### Scenario: A successful call persists a discoverable run

- **WHEN** any of the 3 tools completes successfully for a registered `experiment`
- **THEN** a versioned run is committed under that tool's tool class, and the run's PNG
  output(s) are each linked via `output_links` with a signed or served URL

#### Scenario: Different tools on the same experiment do not share version history

- **WHEN** `plot_trait_histograms` and `plot_trait_boxplots` are both called for the same
  `experiment`
- **THEN** each commits into its own tool class's version lineage; neither call's `version_dir`
  numbering is affected by the other's

#### Scenario: A committed run content-addresses its source

- **WHEN** any of the 3 tools completes successfully for a registered `experiment`
- **THEN** the committed run's provenance records `based_on_version`/`source` and the manifest
  content-addresses the exact frame read (`input_sha256`), the same reproducibility guarantee
  `qc_inspect`/`pca_analysis` provide

#### Scenario: A failure during render or commit leaves no partial run

- **WHEN** figure rendering or `ResultStore.commit` fails partway through (including partway
  through a multi-page batched persist)
- **THEN** no partial run is discoverable via `list_existing_analyses`, and any staging directory
  created for the attempt is cleaned up

#### Scenario: A ResultStore write-path failure is a structured, actionable error

- **WHEN** `ResultStore.create_run`/`commit` raises `CommitFailedError` or `ManifestReadError`
  (a transient storage/manifest failure, not a caller mistake)
- **THEN** the tool raises `BloomMCPError(code="tool_error")` carrying the store's own actionable
  message, not a generic `internal_error` correlation ref (each tool declares both exception
  types in its `@as_mcp_tool(errors=...)`, matching every sibling tool in the folder)

### Requirement: Zero-Variance Traits Disclosed In Correlation Counts

`plot_correlation_matrix`'s result SHALL report `zero_variance_traits`: the selected traits that
are constant or entirely NaN in the raw data. Such a trait's Pearson correlation against every
other trait is `NaN`, which counts toward neither `strong_positive_correlations` nor
`strong_negative_correlations` — this field discloses which traits are silently excluded rather
than leaving the counts to look complete.

#### Scenario: A constant trait is named, not silently excluded

- **WHEN** a selected trait has zero variance (or is entirely NaN) in the raw data
- **THEN** it appears in the result's `zero_variance_traits`, and neither
  `strong_positive_correlations` nor `strong_negative_correlations` includes any pair involving
  it

### Requirement: Correlation Requires At Least Two Traits

`plot_correlation_matrix` SHALL reject a resolved trait selection of fewer than 2 columns as
`BloomMCPError(code="invalid_input")`, before any run is persisted — a correlation view of a
single trait has no pair to correlate.

#### Scenario: A single resolved trait is rejected

- **WHEN** `trait_columns` resolves to exactly one column (via an explicit single-element list,
  or because the experiment has only one detected numeric trait)
- **THEN** `plot_correlation_matrix` raises `BloomMCPError(code="invalid_input")` and no run is
  persisted

#### Scenario: Fewer than two non-constant traits is also rejected

- **WHEN** the resolved trait selection has 2 or more columns, but fewer than 2 of them have
  non-zero variance (constant or all-NaN in the raw data)
- **THEN** `plot_correlation_matrix` raises `BloomMCPError(code="assumption_violated")` and no
  run is persisted — a plain column-count check alone is not sufficient, since every cell of the
  resulting correlation matrix would otherwise be `NaN`

### Requirement: Low-Overlap Trait Pairs Excluded And Disclosed

`plot_correlation_matrix` SHALL compute Pearson correlation with a minimum pairwise-overlap
requirement (`min_periods`, matching `qc_clean`/`qc_inspect`'s canonical minimum-samples
threshold) and SHALL report `low_overlap_trait_pairs`: pairs whose overlapping non-null
observations fell below that minimum. Raw data can have disjoint per-trait missingness, and a
near-empty overlap (as few as 2 points) is otherwise always exactly ±1.0-correlated — a spurious
"strong correlation" from an unreliable sample. A pair already explained by
`zero_variance_traits` SHALL NOT also appear in `low_overlap_trait_pairs`.

#### Scenario: A near-empty overlap is excluded, not miscounted

- **WHEN** two selected traits overlap in fewer non-null rows than the minimum-overlap threshold
- **THEN** that pair's coefficient counts toward neither `strong_positive_correlations` nor
  `strong_negative_correlations`, and the pair appears in `low_overlap_trait_pairs`

### Requirement: Rendered Heatmap Masking Mismatch Is Disclosed

`plot_correlation_matrix`'s persisted PNG SHALL be understood to NOT be masked the way the
summary counts/disclosure lists are — it is rendered by a separate, independent delegate call
running its own unguarded correlation. The result SHALL carry a `heatmap_caveat` field,
populated whenever `zero_variance_traits` or `low_overlap_trait_pairs` is non-empty, directing
the caller to cross-check those fields before trusting a highlighted cell in the image; `None`
when neither is populated.

#### Scenario: A flagged pair still renders unmasked, and the caveat says so

- **WHEN** `zero_variance_traits` or `low_overlap_trait_pairs` is non-empty for a call
- **THEN** the delegate that renders the persisted PNG is still called with the full,
  unmasked/unexcluded trait selection (the same `resolved_trait_columns`), and the result's
  `heatmap_caveat` is populated (not `None`)

#### Scenario: Nothing flagged means no caveat

- **WHEN** neither `zero_variance_traits` nor `low_overlap_trait_pairs` is populated for a call
- **THEN** the result's `heatmap_caveat` is `None`

### Requirement: Resolved Trait Selection Is Recorded, Not Just Counted

Each of the 3 tools SHALL record the exact, resolved trait columns used to render/persist a run
— in the result (`resolved_trait_columns`) and stamped into the persisted run's `params`
(`resolved_trait_columns`) — not merely their count. This holds even when `trait_columns` was
omitted and the actual list was determined by (data-dependent) auto-detection.

#### Scenario: Auto-detected trait selection is recoverable from the manifest

- **WHEN** any of the 3 tools completes successfully with `trait_columns` omitted
- **THEN** the result's `resolved_trait_columns` names the exact traits used, and the
  persisted run's recorded `params["resolved_trait_columns"]` matches it exactly

### Requirement: Paginated Figure Persistence

`plot_trait_histograms`/`plot_trait_boxplots` SHALL, once the selected trait count exceeds
`_viz_shared.TRAIT_BATCH_THRESHOLD`, render via the delegate's batched variant and persist one
committed output (and one `OutputLink`) per page, rather than a single figure.

#### Scenario: A wide selection persists one output per page

- **WHEN** the resolved trait selection exceeds `TRAIT_BATCH_THRESHOLD` traits
- **THEN** the committed run's `outputs` contains one entry per rendered page, each with its own
  `OutputLink`, and the result reports the page count

#### Scenario: Each page's traits are named, not just its count

- **WHEN** any of `plot_trait_histograms`/`plot_trait_boxplots` completes successfully
- **THEN** the result's `page_traits` maps every committed output filename to the exact trait
  columns rendered on that page (a single entry, covering every `resolved_trait_columns`, when
  not batched)

### Requirement: Genotype Column Required For Boxplots

`plot_trait_boxplots` SHALL require an auto-detected genotype column on the read frame (no
override parameter). When none is detected, it SHALL raise a structured error rather than
silently returning a message string.

#### Scenario: No detectable genotype column

- **WHEN** the experiment frame has no column the reader detects as a genotype column
- **THEN** `plot_trait_boxplots` raises `BloomMCPError(code="assumption_violated")` naming the
  experiment, and no run is persisted

### Requirement: Discoverability Via list_existing_analyses

The 3 new tool classes (`trait_histograms`, `trait_boxplots`, `correlation_matrix`) SHALL be
registered in both `manifest.CANONICAL_TOOL_CLASSES` and `list_existing_analyses.TOOL_CLASSES`,
so a committed run from any of the 3 tools appears in `list_existing_analyses`'s response for
that experiment.

#### Scenario: A committed plot run is listed

- **WHEN** `list_existing_analyses` is called for an experiment with at least one committed
  `trait_histograms`/`trait_boxplots`/`correlation_matrix` run
- **THEN** that run appears in the response, distinguishable by its tool class
