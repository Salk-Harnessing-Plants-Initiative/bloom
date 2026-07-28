## ADDED Requirements

### Requirement: Cross-Experiment Correlations Tool Registration and Discovery

The system SHALL expose a `cross_experiment_correlations` MCP tool registered on the
`sleap_roots` section's FastMCP server (namespaced `sleap_roots_cross_experiment_correlations`
on the combined `/mcp` surface) so it is discoverable via the MCP `tools/list` operation.
The tool name SHALL be stable and its registration SHALL NOT remove or rename any existing
tool in the `sleap_roots` section.

#### Scenario: Tool appears in tools/list

- **WHEN** a FastMCP `Client` connects to the server and calls `tools/list`
- **THEN** a tool named `sleap_roots_cross_experiment_correlations` is present with a
  description and an input schema derived from its Pydantic input model

#### Scenario: Existing sleap_roots tools are preserved

- **WHEN** the server registers `cross_experiment_correlations`
- **THEN** `pca_analysis`, `qc_clean`, `qc_inspect`, `remove_outliers`, `clustering`, and
  `umap_analysis` all remain registered

### Requirement: Cross-Experiment Correlations Delegates All Computation to Tested Upstream Entry Points

The `cross_experiment_correlations` tool SHALL delegate genotype-mean aggregation to
`sleap_roots_analyze.calculate_genotype_means`, the correlation matrix to
`sleap_roots_analyze.calculate_cross_experiment_correlations`, significance filtering to
`sleap_roots_analyze.identify_significant_correlations`, and the summary to
`sleap_roots_analyze.summarize_correlation_results`. It SHALL contain no correlation math
of its own — no Pearson/Spearman computation, no FDR correction, no genotype aggregation
via a bespoke `groupby`/`mean` call — and SHALL NOT call
`sleap_roots_analyze.load_and_align_experiments` (which reads CSVs directly from file
paths, bypassing the `ExperimentReader` port).

#### Scenario: Correlation math is delegated, not re-implemented

- **WHEN** `cross_experiment_correlations` runs on two cleaned experiment frames
- **THEN** `calculate_genotype_means` is invoked once per experiment, and
  `calculate_cross_experiment_correlations`, `identify_significant_correlations`, and
  `summarize_correlation_results` are each invoked exactly once
- **AND** the tool never calls `pandas.DataFrame.groupby` directly on either frame to
  compute genotype means, and never calls `load_and_align_experiments`

#### Scenario: Tunable parameters are forwarded to the delegates

- **WHEN** a caller sets `min_samples`, `p_threshold`, `r_threshold`, or `use_fdr`
- **THEN** `min_samples` is forwarded to `calculate_cross_experiment_correlations` and
  `p_threshold`/`r_threshold`/`use_fdr` are forwarded to `identify_significant_correlations`

### Requirement: Cross-Experiment Correlations Requires Cleaned Input on Both Sides

The `cross_experiment_correlations` tool SHALL load both `experiment_1` and `experiment_2`
through the injected `ExperimentReader` port with `require_clean=True`. It SHALL NOT
compute correlations from a raw input and SHALL NOT perform its own cleaning. When no
committed cleaned version exists for either experiment, the tool SHALL surface a
structured `BloomMCPError` naming which experiment is missing a cleaned version, with a
remedy directing the caller to run `qc_clean` on that experiment first.

#### Scenario: Two cleaned experiments are consumed

- **WHEN** `cross_experiment_correlations` is invoked on two experiments that each have a
  committed cleaned version (a `qc_clean` run)
- **THEN** the reader resolves each cleaned version (source `v<N>_cleaned`, not `raw`),
  and correlations are computed on the cleaned data

#### Scenario: Either experiment missing a cleaned version is rejected with a remedy

- **WHEN** `experiment_1` has no committed cleaned version (while `experiment_2` does)
- **THEN** the tool returns a `BloomMCPError` naming `experiment_1` and whose remedy is to
  run `qc_clean` on it first, and no run is produced
- **AND** the same holds symmetrically when `experiment_2` is the one missing a cleaned
  version

### Requirement: Cross-Experiment Correlations Requires a Resolvable Genotype Column on Both Sides

Because correlations are computed at the genotype-mean level, the
`cross_experiment_correlations` tool SHALL require a resolvable `genotype_col` on both
consumed frames. When either frame's `genotype_col` is `None`, the tool SHALL surface a
`BloomMCPError` with code `assumption_violated` naming which experiment lacks a
resolvable genotype column, with a remedy pointing at `qc_clean`'s `genotype_column`
override, and SHALL NOT attempt genotype-mean aggregation.

#### Scenario: A missing genotype column on either experiment is rejected

- **WHEN** `experiment_1`'s cleaned frame has `genotype_col == None`
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` naming
  `experiment_1`, and no genotype-mean aggregation or correlation is attempted
- **AND** the same holds symmetrically when `experiment_2`'s frame is missing a
  resolvable genotype column instead

### Requirement: Trait Selection Is Validated Independently Per Experiment

The `cross_experiment_correlations` tool SHALL accept `trait_columns_1` and
`trait_columns_2` independently, each defaulting to its own experiment's full certified
`trait_cols` when omitted, and each validated against that experiment's certified-clean
trait set. A trait column outside its experiment's certified set, a non-numeric column,
an empty list, or a set of duplicate names on either side SHALL be rejected with a
`BloomMCPError` (`invalid_input`) naming the offending experiment and column(s), rather
than silently correlating an uncertified or degenerate selection.

#### Scenario: Omitted trait selections default to each experiment's certified set

- **WHEN** both `trait_columns_1` and `trait_columns_2` are omitted
- **THEN** the tool correlates every certified-clean trait of `experiment_1` against
  every certified-clean trait of `experiment_2`

#### Scenario: An uncertified or invalid trait column on either side is rejected

- **WHEN** `trait_columns_1` names a column absent from `experiment_1`'s certified-clean
  trait set (or `trait_columns_2` does so for `experiment_2`), or either list is empty or
  contains a duplicate
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming which
  experiment and column(s) are at fault, and no correlation is computed

### Requirement: Degenerate Correlation Results Are Rejected; Empty Significance Is Not an Error

When `calculate_cross_experiment_correlations` returns zero rows (no trait pair reached
`min_samples` aligned genotypes between the two experiments), the
`cross_experiment_correlations` tool SHALL surface a `BloomMCPError` with code
`assumption_violated` and a remedy (lower `min_samples`, or check genotype overlap
between the experiments), and SHALL NOT persist a run. In contrast, when correlations
exist but none clear `r_threshold`/`p_threshold` (an empty **significant** subset), the
tool SHALL treat this as a normal, successful outcome — reporting `n_significant == 0`
and persisting an empty-but-schema-consistent `significant.csv`.

#### Scenario: Zero aligned correlations is treated as degenerate input

- **WHEN** the two experiments share no genotypes meeting `min_samples` for any trait
  pair
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` and a
  remedy, and no run is persisted

#### Scenario: Zero significant correlations is a normal result

- **WHEN** `calculate_cross_experiment_correlations` returns a non-empty result but
  `identify_significant_correlations` filters it to zero rows
- **THEN** the tool completes successfully with `n_significant == 0`, and the persisted
  `significant.csv` has the expected header columns with zero data rows

### Requirement: Cross-Experiment Correlations Is Deterministic and Records No Seed

The `cross_experiment_correlations` tool SHALL be deterministic: it SHALL declare no
`seed`/`random_state` parameter, and the stamped `Provenance` SHALL record `seed = None`.
Two runs with identical inputs SHALL produce identical results.

#### Scenario: Seed is recorded as None

- **WHEN** `cross_experiment_correlations` completes
- **THEN** the stamped `Provenance` records `seed = None`, together with the tool name and
  params (both experiment names, trait selections, `min_samples`, `p_threshold`,
  `r_threshold`, `use_fdr`)

#### Scenario: Repeated runs are identical

- **WHEN** `cross_experiment_correlations` is invoked twice on the same pair of cleaned
  experiments with the same parameters
- **THEN** the two results' `n_correlations`, `n_significant`, and
  `n_highly_significant` are equal

### Requirement: Cross-Experiment Correlations Persists a Versioned Run Encoding Both Experiments

The `cross_experiment_correlations` tool SHALL persist its outputs as a versioned run via
the `ResultStore` port under tool class `cross_experiment_correlation`, encoding both
consumed experiments into the existing single-experiment-shaped fields without any change
to `ResultStore`, `Provenance`, or the manifest schema: the run's `experiment` SHALL be a
composite key derived from both experiment filenames' stems, `based_on_version` SHALL
record both consumed cleaned-version labels, and `source_csv` SHALL content-address both
consumed frames' selected trait data in a single combined snapshot. It SHALL persist the
full correlation matrix (`correlations.csv`), the significance-filtered subset
(`significant.csv`), and the JSON-serializable summary (`summary.json`), and SHALL return
only summary counts and **links** to these artifacts — never the full trait-by-trait
correlation matrix inline.

#### Scenario: Run is committed with a composite experiment key and dual-source lineage

- **WHEN** `cross_experiment_correlations` completes successfully for `experiment_1`
  (resolved cleaned source `v3_cleaned`) and `experiment_2` (resolved cleaned source
  `v2_cleaned`)
- **THEN** a `StoredRun` is recorded whose `experiment` is derived from both filenames'
  stems, and whose `Provenance.based_on_version` encodes both `v3_cleaned` and
  `v2_cleaned` together with each source experiment's identity
- **AND** the committed outputs include `correlations.csv`, `significant.csv`, and
  `summary.json`

#### Scenario: Content-addressing covers both consumed inputs

- **WHEN** the tool builds its `source_csv` snapshot for content-addressing
- **THEN** the snapshot's content reflects both experiments' selected trait data, so the
  manifest's `input_sha256` changes if either experiment's consumed data changes

#### Scenario: Summary is JSON-serializable with no numpy leaks

- **WHEN** the tool persists `summary.json`
- **THEN** the file parses via `json.loads` with no `numpy` scalar types surviving (all
  numpy int/float leaves from `summarize_correlation_results` are converted to native
  Python types before serialization)

#### Scenario: Result returns summary counts and links, not the matrix

- **WHEN** the tool returns its result
- **THEN** `n_correlations`, `n_significant`, `n_highly_significant`, and the per-experiment
  trait counts are inline
- **AND** the full `exp1_trait × exp2_trait` correlation matrix is reachable only via the
  persisted `correlations.csv` link, never embedded in the result

### Requirement: Cross-Experiment Correlations Honors the Contract Envelope

The `cross_experiment_correlations` tool SHALL be wrapped by `@as_mcp_tool` so that
inputs and outputs are validated against declared Pydantic models, every declared or
undeclared failure is mapped to a structured `BloomMCPError` (never a raw traceback or
leaked backend internals), and a single `Provenance` is stamped per call.

#### Scenario: Input/output schema round-trip

- **WHEN** a valid request is serialized to the tool's input schema and the result is
  validated against the output schema
- **THEN** both validate without loss

#### Scenario: No error leaks backend internals

- **WHEN** any mapped `BloomMCPError` is raised (missing cleaned version, missing
  genotype column, invalid trait selection, or degenerate correlation result)
- **THEN** its message contains no raw upstream exception text, filesystem path, or
  storage backend detail
