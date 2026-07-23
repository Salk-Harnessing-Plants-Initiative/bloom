## ADDED Requirements

### Requirement: UMAP on a cleaned experiment

The system SHALL provide an `umap_analysis` tool that reads a cleaned experiment (via
`require_clean=True`), delegates all UMAP computation to
`sleap_roots_analyze.perform_umap_analysis`, and wraps the result into the upstream
`UMAPResult` type. The tool SHALL own no UMAP math of its own.

#### Scenario: Successful embedding on a cleaned experiment

- **WHEN** `umap_analysis` is called with a valid `experiment` that has a cleaned version
  and a certified-clean trait selection
- **THEN** it returns an embedding summary whose `n_samples` equals the certified-clean row
  count, whose `feature_names` matches the selected trait columns, and whose embedding
  values are all finite

#### Scenario: Missing cleaned version

- **WHEN** `umap_analysis` is called with an `experiment` that has no cleaned version
- **THEN** it raises a `tool_error` naming the missing cleaned version, with a remedy to run
  `qc_clean` first

#### Scenario: Delegation is pinned to the upstream entry point

- **WHEN** `umap_analysis` runs successfully
- **THEN** `sleap_roots_analyze.perform_umap_analysis` is called exactly once, with the
  certified-clean trait selection and the resolved seed, and the tool performs no UMAP
  computation of its own

#### Scenario: Degenerate or invalid delegate input

- **WHEN** the certified-clean trait selection is degenerate (too few samples, no
  non-constant trait, or otherwise rejected by `perform_umap_analysis`)
- **THEN** `umap_analysis` raises a structured `assumption_violated` error describing the
  degeneracy, without leaking the delegate's raw exception text

#### Scenario: Non-finite embedding is never persisted or leaked past the JSON boundary

- **WHEN** the delegate returns an embedding containing a non-finite value (NaN or ±inf) —
  whether from a degenerate fit or an unstable UMAP initialization
- **THEN** `umap_analysis` detects this before persistence begins and raises a structured
  `assumption_violated` error; no run is committed and no unstructured `ValueError` from
  `UMAPResult.to_json()`'s `allow_nan=False` boundary escapes as an unhandled error

### Requirement: Stochastic seed resolution and provenance

The system SHALL treat `umap_analysis` as a stochastic tool: it SHALL declare a
`random_state` parameter so the contract layer resolves the requested `seed` into an
effective integer, forwards it to `perform_umap_analysis`, and records the resolved integer
in the run's `Provenance.seed`.

#### Scenario: Resolved seed recorded in provenance

- **WHEN** `umap_analysis` is called with an explicit `seed`
- **THEN** the persisted run's provenance records that exact resolved integer as `seed`,
  never `None`

#### Scenario: Default seed

- **WHEN** `umap_analysis` is called without a `seed`
- **THEN** the resolved seed defaults to `42`

#### Scenario: Same seed reproduces the same embedding within a run

- **WHEN** `umap_analysis` is called twice with the same `seed` and the same inputs on the
  same platform
- **THEN** the two embeddings are identical

### Requirement: Parameter bounds validated before dispatch

The system SHALL reject an out-of-range `n_neighbors`, `min_dist`, or `n_components` value
as `invalid_input` before calling the delegate, rather than letting the delegate's own
parameter `ValueError`s be caught and mislabeled as a data-quality `assumption_violated`.

#### Scenario: n_neighbors below 2 is rejected

- **WHEN** `umap_analysis` is called with `n_neighbors < 2` (including `n_neighbors == 1`,
  which `umap-learn` itself hard-rejects for any data, independent of sample count)
- **THEN** it raises `invalid_input` and never calls `perform_umap_analysis`

#### Scenario: Negative min_dist is rejected

- **WHEN** `umap_analysis` is called with `min_dist < 0`
- **THEN** it raises `invalid_input` and never calls `perform_umap_analysis`

#### Scenario: n_components below 1 is rejected

- **WHEN** `umap_analysis` is called with `n_components < 1`
- **THEN** it raises `invalid_input` and never calls `perform_umap_analysis`

#### Scenario: n_components above the sanity ceiling is rejected

- **WHEN** `umap_analysis` is called with `n_components > 50`
- **THEN** it raises `invalid_input` and never calls `perform_umap_analysis` (UMAP has no
  natural upper clamp the way PCA does; this bound exists to protect a shared container
  from an unreasonable request on this LLM-driven input surface, not to express a
  scientific limit)

### Requirement: n_neighbors bounded by sample count

The system SHALL reject an `n_neighbors` value that is greater than or equal to the
certified-clean sample count with a structured `assumption_violated` error, rather than
silently forwarding it to a delegate that clamps the value internally.

#### Scenario: n_neighbors at or above the sample count is rejected

- **WHEN** `umap_analysis` is called with `n_neighbors >= n_samples` for the certified-clean
  selection
- **THEN** it raises `assumption_violated` naming both the requested `n_neighbors` and the
  maximum usable value (`n_samples - 1`), and no run is committed

#### Scenario: n_neighbors at the boundary succeeds

- **WHEN** `umap_analysis` is called with `n_neighbors == n_samples - 1`
- **THEN** the embedding is computed without clamping or error

### Requirement: Versioned persistence with traceable lineage

The system SHALL persist each `umap_analysis` call as a versioned run under
`tool_class="umap"`, recording `based_on_version` as the consumed cleaned version, and SHALL
return only a summary and object-key links — never the embedding matrix inline.

#### Scenario: Embedding coordinates persisted with sample identity

- **WHEN** a `umap_analysis` run is committed
- **THEN** the persisted `embedding_coords.csv` carries the experiment's identity columns
  (e.g. barcode/genotype/replicate) alongside the embedding coordinates, and the result's
  `outputs` links to it and to the serialized `UMAPResult` by object key only

#### Scenario: Lineage to the cleaned source is recoverable

- **WHEN** a `umap_analysis` run is committed
- **THEN** its provenance records `based_on_version` equal to the cleaned experiment version
  it consumed

#### Scenario: Second run increments the version

- **WHEN** `umap_analysis` is called twice against the same experiment
- **THEN** the second run is persisted under a new, incremented version rather than
  overwriting the first

### Requirement: Tool registration and discovery

The system SHALL register `umap_analysis` as a discoverable MCP tool in the `sleap_roots`
section, alongside its sibling `sleap-roots-analyze` consumers, namespaced
`sleap_roots_umap_analysis` on the combined server surface.

#### Scenario: Tool is discoverable with a valid schema

- **WHEN** the MCP server's tool list is queried
- **THEN** `sleap_roots_umap_analysis` appears with a non-null input schema

#### Scenario: Sibling analysis tools are unaffected

- **WHEN** the MCP server's tool list is queried after `umap_analysis` is added
- **THEN** every other `sleap_roots` analysis tool (`pca_analysis`, `qc_clean`,
  `qc_inspect`, `remove_outliers`, `clustering`, and the 5 plotting tools) is still present
  and unchanged

### Requirement: Optional plots — request semantics and validation

The system SHALL support `include_plots` / `plots` parameters on `umap_analysis`, reusing the
existing `bloom_mcp.tools._plots.validate_plot_keys` helper unmodified, validating any
requested `plots` subset before any run is committed.

#### Scenario: Default behavior is unchanged without plots

- **WHEN** `umap_analysis` is called without `include_plots` (default `False`)
- **THEN** no figures are generated and `outputs` contains only the data artifacts (this
  code path does not itself execute an `import matplotlib` statement, though matplotlib may
  already be resident in the process via this module's own upstream import — see design.md)

#### Scenario: A plots value with include_plots=False is silently ignored

- **WHEN** `umap_analysis` is called with `include_plots=False` and a non-empty `plots` value
- **THEN** no error is raised and no figures are generated

#### Scenario: Unknown plot key rejected before any run is committed

- **WHEN** `umap_analysis` is called with `include_plots=True` and a `plots` value naming an
  unknown key
- **THEN** it raises `invalid_input` naming the unknown key, and no run is committed

#### Scenario: Duplicate or empty plots value is rejected before any run is committed

- **WHEN** `umap_analysis` is called with `include_plots=True` and a `plots` value containing
  a duplicate key, or an empty list
- **THEN** it raises `invalid_input`, and no run is committed

### Requirement: Optional plots — persistence and figure cleanup

The system SHALL persist each requested plot as an additional `*.png` entry in the existing
`outputs` dict (no new result field) via the existing
`bloom_mcp.tools._plots.generate_figures` / `close_figures` helpers, and SHALL close every
generated figure regardless of success or failure.

#### Scenario: Requested plots are persisted as additional outputs

- **WHEN** `umap_analysis` is called with `include_plots=True` and a valid subset of `plots`
- **THEN** each requested plot is persisted as an additional `*.png` entry in `outputs`

#### Scenario: Figures are closed on success, on an invalid key, and on partial plotter failure

- **WHEN** a `umap_analysis` call with `include_plots=True` succeeds, is rejected for an
  invalid plot key, or fails partway through generating multiple plots
- **THEN** every figure already generated in that call is closed
  (`matplotlib.pyplot.get_fignums() == []` afterward) in every case

### Requirement: Top-traits plot consumes an internal, non-persisted PCA call

The system SHALL support the `create_umap_colored_by_top_traits` plot key by computing
trait-importance ranking via an internal, in-memory call to
`sleap_roots_analyze.perform_pca_analysis` over the same certified-clean trait selection
already validated for the UMAP embedding. This internal call SHALL NOT be persisted as its
own versioned run.

#### Scenario: Internal PCA call uses the same validated trait selection

- **WHEN** `umap_analysis` is called with `plots` including `create_umap_colored_by_top_traits`
- **THEN** the internal `perform_pca_analysis` call receives exactly the same certified-clean
  trait columns (same set, same order) already validated and used for the UMAP embedding

#### Scenario: No second run is committed for the internal PCA call

- **WHEN** `umap_analysis` is called with `plots` including `create_umap_colored_by_top_traits`
- **THEN** no `tool_class="pca"` run is created or committed as a side effect
