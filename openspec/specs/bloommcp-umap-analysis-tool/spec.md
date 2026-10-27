# bloommcp-umap-analysis-tool Specification

## Purpose
TBD - created by archiving change add-bloommcp-umap-analysis-tool. Update Purpose after archive.
## Requirements
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
generated figure regardless of success or failure — including a figure a plotter callable
allocates internally (e.g. via `plt.subplots()`) before raising partway through the same
call, before the callable ever returns.

#### Scenario: Requested plots are persisted as additional outputs

- **WHEN** `umap_analysis` is called with `include_plots=True` and a valid subset of `plots`
- **THEN** each requested plot is persisted as an additional `*.png` entry in `outputs`

#### Scenario: Figures are closed on success, on an invalid key, and on partial plotter failure

- **WHEN** a `umap_analysis` call with `include_plots=True` succeeds, is rejected for an
  invalid plot key, or fails partway through generating multiple plots
- **THEN** every figure already generated in that call is closed
  (`matplotlib.pyplot.get_fignums() == []` afterward) in every case

#### Scenario: A figure allocated then abandoned mid-call is still closed

- **WHEN** a requested plot's callable internally allocates a matplotlib figure (e.g. via
  `plt.subplots()`) and then raises before returning it — as happens today when an invalid
  `plot_cmap` reaches `create_umap_single_trait`'s `ax.scatter(cmap=...)` call after the
  figure was already created
- **THEN** that figure is closed too (`matplotlib.pyplot.get_fignums() == []` afterward),
  even though it was never recorded into the tool's own `figures` dict

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

#### Scenario: Internal PCA call failure is a structured, non-leaking assumption_violated

- **WHEN** the internal `perform_pca_analysis` call raises because the certified-clean
  selection is degenerate for PCA (even though it already fit the UMAP embedding
  successfully — PCA's standardization/eigendecomposition is stricter about near-constant
  columns than UMAP is)
- **THEN** `umap_analysis` raises a structured `assumption_violated` error naming the
  degeneracy, without leaking the delegate's raw exception text, and commits no run

### Requirement: UMAP Analysis Accepts the Same Font-Style Override, Reusing the Shared Helper Unmodified

The `umap_analysis` tool input SHALL accept `plot_font_family: Optional[str] = None` and
`plot_font_size: Optional[float] = None` in `UMAPAnalysisParams`, with identical semantics and
validation to `pca_analysis` (`plot_font_size` rejected as `invalid_input` when not strictly
positive; both fields silently ignored when `include_plots=False`), forwarding both values
into the same `bloom_mcp.tools._plots.generate_figures` call already used for its
`include_plots`/`plots` params — no UMAP-specific font-styling code.

#### Scenario: Default call keeps default matplotlib styling

- **WHEN** `umap_analysis` is called with `include_plots=True` and neither
  `plot_font_family` nor `plot_font_size` set
- **THEN** every generated figure's text elements keep the styling the upstream plotter drew
  them with, unchanged from behavior before this change

#### Scenario: A font family override is applied to every generated figure

- **WHEN** `umap_analysis` is called with `include_plots=True` and `plot_font_family="serif"`
- **THEN** every generated figure's title, axis labels, tick labels, standalone annotation
  text, figure-level text, and legend text and title (when present) have their font family
  set to `"serif"` — concretely, `create_umap_colored_by_top_traits`'s overall
  `fig.suptitle(...)` heading (figure-level text, not reachable via any `Axes`) is included.
  Neither UMAP catalog plot (`create_umap_single_trait`, `create_umap_colored_by_top_traits`)
  currently renders a legend (both use colorbars instead), so legend/legend-title coverage is
  exercised via the shared `_plots.py` behavior rather than a UMAP-specific legend fixture;
  see the `bloommcp-pca-analysis-tool` delta for the concrete legend/legend-title coverage
  (`create_pca_biplot` does render one)

#### Scenario: A font size override is applied to every generated figure

- **WHEN** `umap_analysis` is called with `include_plots=True` and `plot_font_size=22`
- **THEN** every generated figure's title, axis labels, tick labels, standalone annotation
  text, figure-level text (including `create_umap_colored_by_top_traits`'s `fig.suptitle`),
  and legend text and title (when present) have their font size set to `22`

#### Scenario: Both overrides apply together

- **WHEN** `umap_analysis` is called with `include_plots=True`, `plot_font_family="serif"`,
  and `plot_font_size=22`
- **THEN** every generated figure's text elements reflect both the family and the size

#### Scenario: A non-positive font size is rejected as invalid_input

- **WHEN** `umap_analysis` is called with `plot_font_size` less than or equal to `0`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, and no run is
  committed

#### Scenario: Font-style fields are ignored when include_plots is False

- **WHEN** `umap_analysis` is called with `include_plots=False` and either
  `plot_font_family` or `plot_font_size` set
- **THEN** the tool returns successfully with no `BloomMCPError`, and no figures are
  generated — the font-style fields have no effect

### Requirement: UMAP Single-Trait Plot Style Overrides

The `umap_analysis` tool SHALL accept optional `plot_cmap: str | None = None`,
`plot_point_size: float | None = None` (`gt=0`), and `plot_alpha: float | None = None`
(`ge=0.0, le=1.0`) fields in `UMAPAnalysisParams`. Each SHALL be forwarded to the upstream
`create_umap_single_trait` plotter call only when set (omitted from the call entirely when
`None`, preserving the plotter's own default). These fields SHALL have no effect on the
`create_umap_colored_by_top_traits` plot key, whose upstream signature does not accept
`cmap`, `point_size`, or `alpha`. A **valid** value for any of the three fields SHALL be
ignored (not rejected) when `include_plots=False` — the fields are simply never read on that
path. This is distinct from **out-of-range** values: `gt=0`/`ge=0.0,le=1.0` are Pydantic
field constraints on `UMAPAnalysisParams` itself, enforced at input-validation time before
the tool body (and therefore before `include_plots` is examined) — an out-of-range
`plot_point_size` or `plot_alpha` is rejected as `invalid_input` regardless of
`include_plots`'s value.

#### Scenario: A style override is forwarded to create_umap_single_trait

- **WHEN** `umap_analysis` is called with `include_plots=True`,
  `plots=["create_umap_single_trait"]`, and `plot_cmap="plasma"`, `plot_point_size=50`,
  `plot_alpha=0.4`
- **THEN** `create_umap_single_trait` is invoked with `cmap="plasma"`, `point_size=50`,
  `alpha=0.4`

#### Scenario: Unset style fields reproduce today's default figure

- **WHEN** `umap_analysis` is called with `include_plots=True`,
  `plots=["create_umap_single_trait"]`, and no `plot_cmap`/`plot_point_size`/`plot_alpha`
  values
- **THEN** `create_umap_single_trait` is invoked with none of `cmap`, `point_size`, `alpha`
  in its call kwargs — the plotter's own hardcoded defaults apply, unchanged from before this
  change

#### Scenario: Style fields have no effect on create_umap_colored_by_top_traits

- **WHEN** `umap_analysis` is called with `include_plots=True`,
  `plots=["create_umap_single_trait", "create_umap_colored_by_top_traits"]`, and
  `plot_cmap="plasma"`
- **THEN** `create_umap_single_trait` receives `cmap="plasma"` and
  `create_umap_colored_by_top_traits`'s call is unchanged — no `cmap` argument is passed to it

#### Scenario: Style fields are ignored when include_plots is False

- **WHEN** `umap_analysis` is called with `include_plots=False` and
  `plot_cmap`/`plot_point_size`/`plot_alpha` set
- **THEN** the tool returns successfully with no figures generated and no error raised

#### Scenario: Out-of-range plot_point_size or plot_alpha is rejected regardless of include_plots

- **WHEN** `umap_analysis` is called with `plot_point_size=0` (or negative), or
  `plot_alpha=1.5` (or negative) — with `include_plots` set to either `True` or `False`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, before any figure is
  generated, in both cases

#### Scenario: Boundary values 0.0 and 1.0 for plot_alpha are accepted

- **WHEN** `umap_analysis` is called with `include_plots=True`,
  `plots=["create_umap_single_trait"]`, and `plot_alpha=0.0` (or `plot_alpha=1.0`)
- **THEN** the tool succeeds and `create_umap_single_trait` is invoked with `alpha=0.0` (or
  `alpha=1.0`) — the inclusive bounds are valid values, not rejected

### Requirement: UMAP Plot Style Fields Have Sanity Ceilings

`UMAPAnalysisParams.plot_font_size` SHALL be rejected as `invalid_input` when not in
`(0, 100]`, and `UMAPAnalysisParams.plot_point_size` SHALL be rejected as `invalid_input`
when not in `(0, 10000]` — both checked in the `umap_analysis` tool body (via the shared
`bloom_mcp.tools._plots.check_plot_style_ceiling` helper), before `reader.load_experiment`
is called, rather than as Pydantic `Field(gt=0, le=...)` constraints. A `Field` constraint's
violation is mapped by the contract layer's `BloomMCPError.from_input_validation` into a
message naming only the field and error type — never the submitted value or the ceiling —
which is exactly the opaque failure mode this same change eliminates for `plot_cmap` by
using the identical tool-body-check approach. Both checks run regardless of
`include_plots`'s value and before any I/O, the same rule already established for
`plot_alpha`'s bounds and the `plot_cmap` allowlist. Each field's declared JSON schema SHALL
still expose its ceiling as `maximum`/`exclusiveMinimum` metadata (via Pydantic's
`json_schema_extra`, not `Field(le=...)`) so a schema-reading caller can discover the bound
without needing to trigger a rejection first — the ceiling is undiscoverable-until-tried
only via a naive reading of the Python type annotation, not via the tool's actual declared
schema.

#### Scenario: An excessive plot_font_size is rejected regardless of include_plots

- **WHEN** `umap_analysis` is called with `plot_font_size` greater than `100` (including
  `float("inf")` or `float("nan")`) — with `include_plots` set to either `True` or `False`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, naming the
  submitted value and the ceiling, before any figure is generated, in both cases

#### Scenario: An excessive plot_point_size is rejected regardless of include_plots

- **WHEN** `umap_analysis` is called with `plot_point_size` greater than `10000` (including
  `float("inf")` or `float("nan")`) — with `include_plots` set to either `True` or `False`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, naming the
  submitted value and the ceiling, before any figure is generated, in both cases

#### Scenario: Boundary values 100 and 10000 are accepted

- **WHEN** `umap_analysis` is called with `include_plots=True`,
  `plots=["create_umap_single_trait"]`, and `plot_font_size=100` (or `plot_point_size=10000`)
- **THEN** the tool succeeds and `create_umap_single_trait` is invoked accordingly — the
  inclusive ceilings are valid values, not rejected

#### Scenario: The declared ceiling is discoverable in the tool's JSON schema

- **WHEN** `umap_analysis`'s input schema is inspected (e.g. via MCP tool discovery)
- **THEN** `plot_font_size`'s schema entry declares `maximum: 100` and
  `plot_point_size`'s declares `maximum: 10000`, even though neither is enforced via a
  Pydantic `Field` constraint

### Requirement: UMAP plot_cmap Is Restricted to Known Sequential and Diverging Colormaps

`UMAPAnalysisParams.plot_cmap`, when set, SHALL be validated in the `umap_analysis` tool body
— before `perform_umap_analysis` is called — against a fixed allowlist of matplotlib's
documented sequential and diverging colormap names (including each name's `_r` reversed
variant). A name outside the allowlist — whether unregistered (e.g. a misspelling) or a
registered-but-excluded qualitative/cyclic colormap (e.g. `hsv`, `tab10`) — SHALL be rejected
as `invalid_input`, naming the invalid value, before any UMAP computation runs. This
validation SHALL run regardless of `include_plots`'s value, the same rule already
established for the other `plot_*` fields' out-of-range checks. `plot_cmap=None` (the
default) SHALL skip the check entirely.

#### Scenario: An unregistered colormap name is rejected before any computation runs

- **WHEN** `umap_analysis` is called with `plot_cmap="virdis"` (a misspelling of `viridis`)
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming `"virdis"`,
  and `perform_umap_analysis` is never called

#### Scenario: A registered but excluded colormap is rejected before any computation runs

- **WHEN** `umap_analysis` is called with `plot_cmap="hsv"` or `plot_cmap="tab10"` — both
  valid matplotlib colormap names, neither sequential nor diverging
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the value, and
  `perform_umap_analysis` is never called

#### Scenario: An allowed sequential or diverging colormap is accepted

- **WHEN** `umap_analysis` is called with `include_plots=True`,
  `plots=["create_umap_single_trait"]`, and `plot_cmap="viridis"` (or `plot_cmap="RdBu"`)
- **THEN** the tool succeeds and `create_umap_single_trait` is invoked with `cmap="viridis"`
  (or `cmap="RdBu"`)

#### Scenario: An invalid plot_cmap is rejected regardless of include_plots

- **WHEN** `umap_analysis` is called with an invalid `plot_cmap` value — with `include_plots`
  set to either `True` or `False`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, before any figure is
  generated, in both cases

#### Scenario: An unset plot_cmap skips the check entirely

- **WHEN** `umap_analysis` is called with `plot_cmap` unset (the default, `None`)
- **THEN** no allowlist check is performed and the call behaves exactly as it did before this
  change

