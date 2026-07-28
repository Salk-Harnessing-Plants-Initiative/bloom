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
- **THEN** every tool already registered on the `sleap_roots` section immediately
  before this change (as enumerated by the live `tools/list` response at that point,
  e.g. `pca_analysis`, `qc_clean`, `qc_inspect`, `remove_outliers`, `clustering`,
  `umap_analysis`, `descriptive_stats`, and the plotting tools) remains registered

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

#### Scenario: `min_samples` is enforced by a bloommcp-side pre-filter, not by the delegate

- **WHEN** a caller sets `min_samples` to a value greater than 3
- **THEN** the tool filters both genotype-means tables to `n_samples >= min_samples`
  **before** calling `calculate_cross_experiment_correlations` (a documented workaround
  for a confirmed upstream no-op — see design.md D8, filed upstream as a bug against
  `sleap-roots-analyze`), so a genotype present in both experiments but under-replicated
  in either does not participate in any resulting correlation
- **AND** `p_threshold`/`r_threshold`/`use_fdr` are forwarded unchanged to
  `identify_significant_correlations`

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
- **AND** the tool's result reports each experiment's resolved source label
  (e.g. `v3_cleaned` for `experiment_1`, `v2_cleaned` for `experiment_2`)

#### Scenario: Either experiment missing a cleaned version is rejected with a remedy

- **WHEN** `experiment_1` has no committed cleaned version (while `experiment_2` does)
- **THEN** the tool returns a `BloomMCPError` naming `experiment_1` and whose remedy is to
  run `qc_clean` on it first, and no run is produced
- **AND** the same holds symmetrically when `experiment_2` is the one missing a cleaned
  version

### Requirement: Experiment Names Are Validated as Safe Bare Filenames

The `cross_experiment_correlations` tool SHALL explicitly validate that `experiment_1`
and `experiment_2` are each a safe bare filename (no path separators, no `..`, not
empty or dot-only) before any I/O — an explicit defense-in-depth check (this tool
doubles the untrusted-filename surface relative to a single-experiment consumer), not
reliance on the incidental safety of the read path alone.

#### Scenario: A path-unsafe experiment name is rejected

- **WHEN** `experiment_1` or `experiment_2` contains a path separator, `..`, or is
  empty/dot-only
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` before any
  experiment is loaded, and no run is persisted

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

### Requirement: Selected Trait Data Must Be Finite Before Genotype-Mean Aggregation

Before calling `calculate_genotype_means`, the `cross_experiment_correlations` tool
SHALL verify that each experiment's selected trait columns contain no non-finite value
(`NaN`, `+inf`, or `-inf`) — the same defense-in-depth `pca_analysis` and `clustering`
apply before delegating, since `require_clean=True` alone is not trusted as a
finiteness guarantee. A non-finite value surviving into either experiment's selection
SHALL be rejected with `BloomMCPError(code="assumption_violated")` naming which
experiment, rather than silently understating that experiment's true `n_samples` (via
`.mean()`'s NaN-skipping) or propagating `±inf` into a genotype mean.

#### Scenario: A non-finite value in either experiment's selection is rejected

- **WHEN** a selected certified-clean trait column of `experiment_1` (or, symmetrically,
  `experiment_2`) carries a non-finite value
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` naming the
  offending experiment, and no genotype-mean aggregation is attempted

### Requirement: Trait Selection Is Validated Independently Per Experiment

The `cross_experiment_correlations` tool SHALL accept `trait_columns_1` and
`trait_columns_2` independently, each defaulting to its own experiment's full certified
`trait_cols` when omitted, and each validated against that experiment's certified-clean
trait set via `_validate_trait_subset(..., require_certified=True)`. A trait column
outside its experiment's certified set, a non-numeric column, an empty list, or a set of
duplicate names on either side SHALL be rejected with a `BloomMCPError` (`invalid_input`)
whose message names the specific experiment (`experiment_1` or `experiment_2`) at fault
in every one of these four cases, rather than silently correlating an uncertified or
degenerate selection.

#### Scenario: Omitted trait selections default to each experiment's certified set

- **WHEN** both `trait_columns_1` and `trait_columns_2` are omitted
- **THEN** the tool correlates every certified-clean trait of `experiment_1` against
  every certified-clean trait of `experiment_2`

#### Scenario: A trait column outside the certified-clean set is rejected

- **WHEN** `trait_columns_1` names a column absent from `experiment_1`'s certified-clean
  trait set (or `trait_columns_2` does so for `experiment_2`)
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the
  offending experiment and column(s), and no correlation is computed

#### Scenario: A non-numeric, empty, or duplicate trait selection is rejected

- **WHEN** `trait_columns_1` or `trait_columns_2` names a non-numeric column, is given
  as an empty list, or contains a duplicate column name
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` whose message
  names which of `experiment_1`/`experiment_2` triggered the failure in each of these
  three cases (not only the outside-certified-set case)

### Requirement: Degenerate Correlation Results Are Rejected; Empty Significance Is Not an Error

When `calculate_cross_experiment_correlations` returns zero rows (no trait pair reached
`min_samples` aligned genotypes between the two experiments, after the `min_samples`
pre-filter), the `cross_experiment_correlations` tool SHALL surface a `BloomMCPError`
with code `assumption_violated` and a remedy (lower `min_samples`, or check genotype
overlap between the experiments), and SHALL NOT persist a run. In contrast, when
correlations exist but none clear `r_threshold`/`p_threshold` (an empty **significant**
subset), the tool SHALL treat this as a normal, successful outcome — reporting
`n_significant == 0` and persisting an empty-but-schema-consistent `significant.csv`.

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
the `ResultStore` port under tool class `correlation` (the slot reserved since the
pre-#438 legacy correlation tools were retired — reused here, not newly registered; see
design.md D9), encoding both consumed experiments into the existing
single-experiment-shaped fields without any change to `ResultStore`, `Provenance`, or
the manifest schema:

- `experiment` SHALL equal exactly
  `f"{_storage_safe_stem(experiment_1)}__x__{_storage_safe_stem(experiment_2)}"`, where
  `_storage_safe_stem(name)` is `Path(name).stem` with every internal `.` replaced by
  `_`. This dot-sanitization (added after review found the naive un-sanitized
  composite silently truncated whenever either original stem contained a dot — see
  design.md D1) makes the composite string itself dot-free, so it is immune to
  `AnalysisDir`'s own re-applied `Path(...).stem`, regardless of what either original
  filename's stem contains.
- `based_on_version` SHALL equal exactly
  `f"{experiment_1}@{frame1.source}|{experiment_2}@{frame2.source}"`
- `source_csv` SHALL content-address both consumed frames' selected trait data in a
  single combined snapshot

Before building either composite string, the tool SHALL reject `experiment_1` or
`experiment_2` containing `@` or `|` with `BloomMCPError(code="invalid_input")` (these
characters are reserved for the composite-string encoding above and are not blocked by
the shared `_validate_experiment_name` guard), and SHALL reject `experiment_1 ==
experiment_2` (self-correlation) with the same error code. The tool SHALL persist the
full correlation matrix (`correlations.csv`), the significance-filtered subset
(`significant.csv`), both experiments' genotype-means tables with per-genotype
`n_samples` (`genotype_means_1.csv`, `genotype_means_2.csv` — for traceability, since
upstream itself discards per-pair genotype identity; see design.md D12), and the
JSON-serializable summary (`summary.json`). It SHALL return only summary counts and
**links** to these artifacts — never the full trait-by-trait correlation matrix inline.

#### Scenario: Run is committed with the exact composite experiment key and based_on_version

- **WHEN** `cross_experiment_correlations` completes successfully for `experiment_1`
  (resolved cleaned source `v3_cleaned`) and `experiment_2` (resolved cleaned source
  `v2_cleaned`)
- **THEN** the `StoredRun`'s `experiment` equals
  `f"{_storage_safe_stem(experiment_1)}__x__{_storage_safe_stem(experiment_2)}"`
- **AND** the committed `Provenance.based_on_version` equals
  `f"{experiment_1}@v3_cleaned|{experiment_2}@v2_cleaned"`
- **AND** the committed outputs include `correlations.csv`, `significant.csv`,
  `genotype_means_1.csv`, `genotype_means_2.csv`, and `summary.json`

#### Scenario: A dotted experiment filename does not truncate or collide the composite key

- **WHEN** either `experiment_1` or `experiment_2`'s stem (the filename without its
  final `.csv` extension) itself contains a dot (e.g. `experiment_1 =
  "my.experiment.v2.csv"`)
- **THEN** the composite `experiment` key still contains a recognizable, sanitized form
  of both original stems, and re-applying `Path(...).stem` to that composite key
  (exactly what `AnalysisDir` does internally) returns the composite key unchanged —
  it is not truncated, and two different such filenames do not collide on the same
  storage prefix

#### Scenario: A filename containing a reserved encoding character is rejected

- **WHEN** `experiment_1` or `experiment_2` contains `@` or `|`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` before either
  composite string is built, and no run is persisted

#### Scenario: Self-correlation is rejected

- **WHEN** `experiment_1` and `experiment_2` are the same filename
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` before any I/O,
  and no run is persisted

#### Scenario: Content-addressing covers both consumed inputs

- **WHEN** the tool builds its `source_csv` snapshot for content-addressing
- **THEN** the snapshot's content reflects both experiments' selected trait data, so the
  manifest's `input_sha256` changes if either experiment's consumed data changes

#### Scenario: Summary is JSON-serializable with no numpy leaks

- **WHEN** the tool persists `summary.json`
- **THEN** the file parses via `json.loads` with no `numpy` scalar types surviving (all
  numpy int/float leaves from `summarize_correlation_results` are converted to native
  Python types before serialization)

#### Scenario: Persisted runs are discoverable via list_existing_analyses

- **WHEN** a run has been committed under the reused `correlation` tool class
- **THEN** `list_existing_analyses` (called with the composite `experiment` key) surfaces
  it without any change to that tool's `TOOL_CLASSES` list

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
leaked backend internals), and a single `Provenance` is stamped per call. `min_samples`
SHALL be constrained to `>= 1` and `p_threshold`/`r_threshold` SHALL each be constrained
to `[0, 1]` at the input-model level, rejecting an out-of-range value before any
computation.

#### Scenario: Input/output schema round-trip

- **WHEN** a valid request is serialized to the tool's input schema and the result is
  validated against the output schema
- **THEN** both validate without loss

#### Scenario: Out-of-range parameters are rejected

- **WHEN** a request sets `min_samples < 1`, or `p_threshold`/`r_threshold` outside
  `[0, 1]`
- **THEN** the tool returns a `BloomMCPError` with an input-validation code, and no run
  is persisted

#### Scenario: No error leaks backend internals

- **WHEN** any mapped `BloomMCPError` is raised (missing cleaned version, missing
  genotype column, non-finite input, invalid trait selection, a reserved encoding
  character, or a degenerate correlation result)
- **THEN** its message contains no raw upstream exception text, filesystem path, or
  storage backend detail
