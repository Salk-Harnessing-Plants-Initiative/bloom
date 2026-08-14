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

### Requirement: Paginated Figure Persistence

`plot_trait_histograms`/`plot_trait_boxplots` SHALL, once the selected trait count exceeds
`_viz_shared.TRAIT_BATCH_THRESHOLD`, render via the delegate's batched variant and persist one
committed output (and one `OutputLink`) per page, rather than a single figure.

#### Scenario: A wide selection persists one output per page

- **WHEN** the resolved trait selection exceeds `TRAIT_BATCH_THRESHOLD` traits
- **THEN** the committed run's `outputs` contains one entry per rendered page, each with its own
  `OutputLink`, and the result reports the page count

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
