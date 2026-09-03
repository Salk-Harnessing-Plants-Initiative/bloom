## ADDED Requirements

### Requirement: Heritability Analysis Tool Registration and Discovery

The system SHALL expose a `heritability_analysis` MCP tool registered on the FastMCP server via
the `sleap_roots` section, discoverable through the MCP `tools/list` operation and namespaced
`sleap_roots_heritability_analysis` on the combined surface. The tool name SHALL be stable
(`heritability_analysis`, never versioned in the name). Its description SHALL name the two tools
it replaces and the `plots` key that reproduces each one's figure, so a caller whose invocation of
a retired name just failed can recover the migration from `tools/list` alone.

#### Scenario: Tool appears in tools/list

- **WHEN** a FastMCP `Client` connects to the server and calls `tools/list`
- **THEN** a tool named `sleap_roots_heritability_analysis` is present with a description and a
  non-null input schema derived from its Pydantic input model

#### Scenario: The description carries the migration path

- **WHEN** the `heritability_analysis` entry returned by `tools/list` is read
- **THEN** its description names `plot_heritability_bar` and `plot_variance_decomposition` as
  retired, and names the `plots` key that reproduces each one's figure

#### Scenario: Sibling analysis tools are unaffected

- **WHEN** the MCP server's tool list is queried after `heritability_analysis` is added
- **THEN** `pca_analysis`, `qc_clean`, `qc_inspect`, `remove_outliers`, `clustering`,
  `umap_analysis`, `descriptive_stats`, `cross_experiment_correlations`, and the 3 surviving
  plotting tools (`plot_trait_histograms`, `plot_trait_boxplots`, `plot_correlation_matrix`) are
  all still present and unchanged

### Requirement: The Retired Heritability Plot Tools Are Removed

The system SHALL NOT register `plot_heritability_bar` or `plot_variance_decomposition` as
standalone MCP tools. Their modules SHALL be deleted rather than left unregistered, so no
unreachable second implementation of the heritability calculation remains in the tree. This is a
**BREAKING** change for any caller invoking either tool name directly; the replacement is
`heritability_analysis` with `include_plots=true` and the corresponding `plots` key.

#### Scenario: The retired tool names are absent from tools/list

- **WHEN** a FastMCP `Client` calls `tools/list`
- **THEN** neither `sleap_roots_plot_heritability_bar` nor
  `sleap_roots_plot_variance_decomposition` is present

#### Scenario: The retired modules no longer exist

- **WHEN** the package is inspected after this change
- **THEN** `sections/sleap_roots/analysis/plot_heritability_bar.py` and
  `sections/sleap_roots/analysis/plot_variance_decomposition.py` do not exist, neither is
  importable, and no module in the package other than `heritability_analysis` references
  `calculate_heritability_estimates`

#### Scenario: Re-registering a retired tool fails the suite

- **WHEN** a future change re-adds either retired tool name to the registered surface
- **THEN** a test fails, because each retired name is asserted **absent** rather than merely
  omitted from the expected-present set

### Requirement: Heritability Analysis Delegates All Computation to the Tested Upstream Entry Point

The `heritability_analysis` tool SHALL delegate its heritability computation to
`sleap_roots_analyze.statistics.calculate_heritability_estimates` in exactly one call per
invocation, and SHALL wrap that call's return into the upstream typed
`sleap_roots_analyze.HeritabilityResult` via `HeritabilityResult.from_heritability_dict`. The tool
SHALL contain no variance-component, mixed-model, ANOVA, or H² arithmetic of its own. The
comparison table required by the variance-decomposition figure SHALL be obtained from
`sleap_roots_analyze.statistics.compare_trait_heritabilities`, never re-derived.

#### Scenario: Heritability is delegated, not re-implemented

- **WHEN** `heritability_analysis` runs on a cleaned experiment frame with a set of selected trait
  columns
- **THEN** `calculate_heritability_estimates` is invoked exactly once with those trait columns and
  the frame's resolved genotype/replicate column roles, and every reported per-trait value —
  `h2`, `var_genetic`, `var_residual`, `n_genotypes`, `n_observations`, `model_type` — is taken
  from its result with no recomputation

#### Scenario: The comparison table is delegated

- **WHEN** the variance-decomposition figure is requested
- **THEN** the frame passed to `create_variance_decomposition_plot` is the return of
  `compare_trait_heritabilities`, called with the same experiment frame, the same trait list, and
  the same heritability dict the returned numbers were built from

### Requirement: Heritability Analysis Requires a Cleaned Input

The `heritability_analysis` tool SHALL load its experiment frame through the injected
`ExperimentReader` port with `require_clean=True`, as a **consumer** of cleaned data. It SHALL NOT
estimate heritability over a raw input. When no committed cleaned version exists for the
experiment, the tool SHALL surface a structured `BloomMCPError` with code `tool_error` whose
remedy directs the caller to run `qc_clean` first, and no run SHALL be persisted. An optional
`version` parameter SHALL pin the analysis to a specific committed cleaned version; omitting it
SHALL resolve the latest.

This closes a gap in the retired `plot_heritability_bar` / `plot_variance_decomposition` tools,
which passed no `require_clean` and so estimated H² on raw data, letting the delegate's internal
per-trait `dropna()` change the analyzed sample count with no signal to the caller.

#### Scenario: A cleaned experiment is consumed

- **WHEN** `heritability_analysis` is invoked on an experiment that has a committed cleaned version
- **THEN** the reader resolves the cleaned version (source `v<N>_cleaned`, not `raw`) and the tool
  estimates heritability over it

#### Scenario: An experiment with no cleaned version is rejected with a remedy

- **WHEN** `heritability_analysis` is invoked on an experiment that has only a raw input
- **THEN** the tool returns a `BloomMCPError` with code `tool_error` and a remedy naming
  `qc_clean`, and no run is persisted

#### Scenario: An explicit version pins the consumed input

- **WHEN** `heritability_analysis` is invoked with an explicit `version`
- **THEN** that cleaned version is consumed and recorded as `based_on_version`; when `version` is
  omitted the call is identical to resolving the latest cleaned version

### Requirement: Heritability Analysis Selects Only Certified-Clean Traits

The `heritability_analysis` tool SHALL restrict the analysis to columns within the resolved
frame's certified-clean trait set (`frame.trait_cols`). A requested `trait_columns` entry outside
that set, empty, or containing duplicates SHALL be rejected as `invalid_input`. The `threshold`
parameter SHALL be constrained to the closed interval `[0.0, 1.0]`; a value outside it SHALL be
rejected as `invalid_input`.

#### Scenario: A trait column outside the certified-clean set is rejected

- **WHEN** `trait_columns` names a column present in the frame but not in `frame.trait_cols`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the column, and does
  not estimate heritability for it

#### Scenario: An explicitly empty or duplicate trait selection is rejected

- **WHEN** `trait_columns` is supplied as an empty list, or names the same column more than once
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, rather than treating an
  empty list as "all traits" or silently de-duplicating

#### Scenario: An out-of-range threshold is rejected

- **WHEN** `threshold` is supplied outside `[0.0, 1.0]`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, and no run is persisted

### Requirement: Heritability Analysis Requires a Genotype Column but Not a Replicate Column

The `heritability_analysis` tool SHALL require the resolved frame to carry a detected genotype
column, and SHALL reject a frame without one as `assumption_violated`, naming the column roles the
reader resolved. The tool SHALL NOT require a replicate column: it SHALL pass the frame's
`replicate_col` through to the delegate unchanged, including when it is `None`, and the estimated
H² SHALL be identical either way.

This is a behavior change relative to the retired plot tools, which rejected any experiment
lacking **either** column (see design.md D3 for the rationale and for why it is load-bearing
rather than an edge case).

#### Scenario: An experiment with no replicate column is analyzed

- **WHEN** `heritability_analysis` runs on a cleaned frame whose `replicate_col` is `None` but
  whose `genotype_col` is present
- **THEN** the run succeeds, the delegate is called with `replicate_col=None`, and per-trait H²
  values are returned

#### Scenario: The replicate column does not change the estimate

- **WHEN** the same cleaned frame is analyzed once with its replicate column resolved and once
  with `replicate_col=None`
- **THEN** the per-trait H² values are identical

#### Scenario: An experiment with no genotype column is rejected

- **WHEN** `heritability_analysis` runs on a cleaned frame whose `genotype_col` is `None` or empty
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` naming the resolved
  column roles, and no run is persisted

### Requirement: Heritability Analysis Returns Per-Trait Values as Data

The `heritability_analysis` tool SHALL return per-trait heritability values inline as structured
data — not merely a figure link and an aggregate count. Each reported trait SHALL carry `trait`,
`h2`, `passed_threshold`, `var_genetic`, `var_residual`, `n_genotypes`, `n_observations`, and
`model_type`. The result SHALL additionally carry `experiment`, `source`, `n_samples`,
`genotype_col`, `replicate_col`, `method`, `threshold`, `mean_h2`, `n_above_threshold`,
`n_traits_requested`, `n_traits_reported`, `n_failed`, `failed_traits`, `nonfinite_traits`,
and `zero_variance_traits`, plus the inherited `RunLinks` fields. The invariant
`n_traits_requested == n_traits_reported + n_failed` SHALL hold for every successful call.

The inline `per_trait` list SHALL be capped at the first 50 traits, with `truncated_in_summary`
set and `omitted_traits` naming exactly the traits beyond the cap. The persisted per-trait table
SHALL always contain every scored trait, uncapped.

`mean_h2` SHALL be `null` — not `0.0` — when no trait scored, so a caller cannot read "no data" as
"heritability is zero".

#### Scenario: Per-trait H² values are queryable from the tool result

- **WHEN** `heritability_analysis` completes on an experiment with fewer than 50 certified traits
- **THEN** `per_trait` contains one entry per scored trait with its `h2` value,
  `truncated_in_summary` is `false`, `omitted_traits` is empty, and
  `n_traits_requested == n_traits_reported + n_failed`

#### Scenario: A wide experiment truncates the inline list but not the persisted table

- **WHEN** `heritability_analysis` completes on an experiment with more than 50 certified traits
- **THEN** `per_trait` contains exactly 50 entries, `truncated_in_summary` is `true`,
  `omitted_traits` names exactly the remaining traits in the resolved order, and the persisted
  per-trait CSV contains a row for every scored trait

#### Scenario: A run with no scored trait reports no mean

- **WHEN** every requested trait fails estimation
- **THEN** the call succeeds, a run is persisted, `n_traits_reported` is `0`, `per_trait` is
  empty, and `mean_h2` is `null` rather than `0.0`

#### Scenario: The inline payload stays bounded

- **WHEN** the result of any successful `heritability_analysis` call is serialized
- **THEN** no single field's serialized form is an unbounded matrix or table — the full per-trait
  table is reachable only through the run's persisted outputs

### Requirement: A Trait With No Variance to Partition Is Named

The `heritability_analysis` tool SHALL list, in `zero_variance_traits`, every scored trait
whose `var_genetic` and `var_residual` are both exactly zero. Such a trait has no variance to
partition, so its reported H² is not a measurement — and which number the delegate attached
depends on the branch that produced it: its `no_variance` branch reports `0.0`, while a
mixed-model fit returning exact zeros divides `0/0`, producing `NaN`, which its own clamp
turns into `1.0`. Reporting a *perfect* and a *zero* heritability for the same non-finding is
why the caller needs the names.

Such traits SHALL still be reported in `per_trait` and in the persisted table, and SHALL
still contribute to `mean_h2` and `n_above_threshold` — the tool labels the delegate's
verdict rather than overriding it, so the returned aggregates cannot drift from the ones a
reader of the persisted result JSON would compute. The tool's description SHALL direct a
caller to check this field before quoting either aggregate.

The test SHALL be exact equality with zero, not a tolerance: a near-constant trait fits with
tiny-but-nonzero variance components and yields an ordinary quotient, which is a real
estimate and SHALL NOT be flagged.

#### Scenario: A constant trait is reported and named

- **WHEN** `heritability_analysis` runs on a cleaned frame containing a trait whose values
  are all identical
- **THEN** that trait appears in `zero_variance_traits`, still appears in `per_trait` with
  the delegate's own `model_type`, and does **not** appear in `failed_traits`

#### Scenario: A degenerate perfect heritability is named

- **WHEN** the delegate returns a trait with `var_genetic` and `var_residual` both zero and a
  heritability of `1.0`
- **THEN** that trait appears in `zero_variance_traits`, so a caller can tell the reported
  `1.0` apart from a genuine one

#### Scenario: An ordinary trait is not flagged

- **WHEN** a trait's variance components are non-zero, including a near-constant trait whose
  components are very small but non-zero
- **THEN** it does not appear in `zero_variance_traits`

### Requirement: Heritability Analysis Persists a Versioned Run With Provenance

The `heritability_analysis` tool SHALL persist a versioned run through the `ResultStore` port under
tool class `heritability`, writing a per-trait CSV and the serialized `HeritabilityResult` as JSON,
and SHALL return `resource_link`s to them. Provenance SHALL record the tool name, the selected
trait columns, `seed = None` (the delegate has no RNG), and `based_on_version` set to the consumed
cleaned version. The consumed frame SHALL be content-addressed via `source_csv` so the
`qc_clean` → `heritability_analysis` lineage is recoverable. The persisted result JSON SHALL carry
the **uncapped** per-trait set and the caller-supplied `threshold`, so it is not merely a copy of
the truncated inline summary.

The tool class `heritability` SHALL be discoverable through `list_existing_analyses`, and a
`list_runs` failure for that class SHALL name the public tool name `heritability_analysis`.

#### Scenario: A successful run is persisted and linked

- **WHEN** `heritability_analysis` completes successfully
- **THEN** a run exists for `(experiment, "heritability")` whose outputs include the per-trait CSV
  and the result JSON, whose provenance records `seed = None` and `based_on_version` equal to the
  consumed cleaned source, and whose object keys and manifest path are returned in the result

#### Scenario: The persisted result JSON is uncapped and self-describing

- **WHEN** the committed result JSON of a run over more than 50 traits is read back
- **THEN** it parses as strict JSON, carries one entry per scored trait (not 50), and records the
  `threshold` the caller supplied

#### Scenario: A second run increments the version

- **WHEN** `heritability_analysis` runs twice on the same experiment
- **THEN** the two runs land at consecutive versions and both remain independently readable

#### Scenario: Runs are discoverable

- **WHEN** `list_existing_analyses` is called for an experiment with a committed
  `heritability_analysis` run
- **THEN** the `heritability` tool class is among the classes it queries, and the run is reported

### Requirement: Optional Plots Are Folded Into the Same Call

The `heritability_analysis` tool SHALL support `include_plots: bool = False` and
`plots: list[str] | None = None`, with the catalog keys `create_heritability_plot` and
`create_variance_decomposition_plot`. With `include_plots=false` (the default) no figure SHALL be
generated and no plotting library import SHALL be executed on that path. A `plots` value supplied
with `include_plots=false` SHALL be silently ignored. An empty `plots=[]`, an unknown key, or a
duplicated key SHALL be rejected as `invalid_input` **before** any run is committed, reusing the
existing `bloom_mcp.tools._plots.validate_plot_keys` helper. Generated figures SHALL be persisted
as additional entries in the existing `outputs` field, and every figure generated SHALL be closed
whether the call succeeds, fails during generation, or fails during persistence.

#### Scenario: The default path generates no figures

- **WHEN** `heritability_analysis` is called without `include_plots`
- **THEN** no figure is generated, `outputs` contains only the per-trait CSV and the result JSON,
  and the plotting library is not imported by this code path

#### Scenario: A plots value with include_plots=false is ignored

- **WHEN** `heritability_analysis` is called with `include_plots=false` and a non-empty `plots`
- **THEN** no error is raised and no figure is generated

#### Scenario: An invalid plot key is rejected before any run is committed

- **WHEN** `heritability_analysis` is called with `include_plots=true` and `plots` containing an
  unknown key, a duplicate key, or an empty list
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` naming the offending
  value, and no run is persisted

#### Scenario: Requested figures are persisted into the same run

- **WHEN** `heritability_analysis` is called with `include_plots=true`
- **THEN** each requested figure is persisted as a `.png` entry in the same run's `outputs`,
  alongside the per-trait CSV and the result JSON, and each persisted entry is a valid PNG

#### Scenario: Every figure is closed on every exit path

- **WHEN** a plot-generating call succeeds, or a plotter raises partway through generation, or the
  commit that follows generation raises
- **THEN** no figure allocated by that call remains open afterward

#### Scenario: The comparison table is computed only when its figure is requested

- **WHEN** `heritability_analysis` is called with `include_plots=true` and
  `plots=["create_heritability_plot"]`
- **THEN** `compare_trait_heritabilities` is not called

#### Scenario: An empty comparison frame skips the figure and names the reason

- **WHEN** the variance-decomposition figure is requested and no trait scored, so the comparison
  frame is empty
- **THEN** the run succeeds with no `create_variance_decomposition_plot` entry in `outputs`, and
  the result names the unscored traits rather than persisting an empty figure or failing silently

### Requirement: A Paginating Plotter's Pages Are Each Persisted and Closed

`bloom_mcp.tools._plots.generate_figures` SHALL accept a plotter returning either a single
`Figure` or a `list[Figure]`. A list return SHALL be expanded into one `<key>_page<N>` entry per
figure (1-indexed) in the caller's `figures` dict, so `apply_font_style` and `close_figures`
continue to operate on a flat `dict[str, Figure]`. Every page SHALL be recorded into that dict
**before** any page is styled, so a styling failure cannot strand an already-generated page
outside the caller's cleanup. A single-`Figure` return SHALL keep the key naming it has today,
unchanged. `create_heritability_plot` paginates above its `traits_per_page` default (50 traits),
so a wide experiment SHALL persist one `.png` per page rather than a single figure or a leaked
list.

#### Scenario: A paginated plotter persists one output per page

- **WHEN** `heritability_analysis` is called with `include_plots=true`,
  `plots=["create_heritability_plot"]`, on an experiment with more than 50 certified traits
- **THEN** the run's `outputs` contain `create_heritability_plot_page1.png` …
  `create_heritability_plot_pageN.png`, one per returned page, and no `outputs` entry holds a
  non-`Figure` value

#### Scenario: The pagination boundary is exactly the plotter's page size

- **WHEN** `heritability_analysis` renders the bar plot for exactly 50 scored traits, and
  again for 51
- **THEN** 50 traits produce a single `create_heritability_plot.png`, while 51 produce
  `_page1`/`_page2` outputs whose first page carries the 50 highest-H² traits and whose
  second carries the remaining one — the page split and each page's membership, not merely
  the page count

#### Scenario: Existing single-figure callers are unaffected

- **WHEN** `pca_analysis`, `umap_analysis`, or `clustering` generates a figure after this change
- **THEN** its output key is byte-identical to the key it produced before the change, with no
  `_page` suffix

#### Scenario: Every page is closed even when styling fails

- **WHEN** a plotter returns several pages and applying style to one of them raises
- **THEN** every page returned by that plotter is closed, none is left open in the plotting
  library's registry

### Requirement: The Rendered Figures and the Returned Numbers Cannot Diverge

The `heritability_analysis` tool SHALL derive the returned per-trait values, the persisted
per-trait table, and every rendered figure from a single `calculate_heritability_estimates` call
per invocation. The caller-supplied `threshold` SHALL be forwarded explicitly to
`HeritabilityResult.from_heritability_dict`, to `create_heritability_plot`, and to
`create_variance_decomposition_plot` — the latter's own default differs — so the reported
`passed_threshold` classification and every plotted reference line reflect the same value.

Row **ordering** is the one deliberate exception: `create_heritability_plot` sorts by H²
descending before paginating, while the returned and persisted tables preserve the resolved trait
order. The tool SHALL document this in its description, so a caller comparing the inline top-50
against the first plotted page does not read a different ordering as a different calculation.

#### Scenario: One delegate call feeds numbers and both figures

- **WHEN** `heritability_analysis` is called with `include_plots=true` and both catalog keys
- **THEN** `calculate_heritability_estimates` is invoked exactly once, and the heritability values
  plotted in each figure are the same values returned inline and written to the persisted table

#### Scenario: The threshold reaches every consumer

- **WHEN** `heritability_analysis` is called with an explicit non-default `threshold` and
  `include_plots=true`
- **THEN** that value is used for the `passed_threshold` classification and is passed to both
  plotters, rather than either plotter falling back to its own default

#### Scenario: The ordering difference is documented, not silent

- **WHEN** a wide experiment is analyzed with the bar plot requested
- **THEN** the inline `per_trait` order and the plotted page order may differ, and that difference
  is stated in the tool's own description rather than left for a caller to discover

### Requirement: Heritability Analysis Never Emits a Non-Finite or Zero-Filled Number

The `heritability_analysis` tool SHALL ensure no non-finite value (`NaN`, `Infinity`) and no
silently defaulted value reaches the JSON-RPC envelope, the persisted result JSON, or a rendered
figure. A per-trait entry whose `heritability`, `var_genetic`, or `var_residual` is non-finite
**or absent** SHALL be routed to `failed_traits` before the typed result is constructed. A
non-finite value SHALL additionally be named in `nonfinite_traits`.

The absence half is not redundant with the non-finite half:
`HeritabilityResult.from_heritability_dict` defaults a missing `var_genetic` / `var_residual` to
`0.0` and a missing `n_genotypes` to `0`, so a renamed upstream key would otherwise be emitted as
a plausible-looking zero variance component on the default path, where no figure is rendered and
the variance-component plot guard never runs. This is the obligation `bloommcp-packaging`'s
"a renamed or dropped key SHALL fail rather than silently defaulting to zero" places on this tool.

The delegate's own returned dict SHALL NOT be mutated.

#### Scenario: A non-finite per-trait result is routed, not emitted

- **WHEN** the delegate returns a per-trait entry with a non-finite `heritability`,
  `var_genetic`, or `var_residual`
- **THEN** that trait appears in `failed_traits` and in `nonfinite_traits`, does not appear in
  `per_trait` or the persisted per-trait table, the call succeeds, the run is persisted, and the
  persisted result JSON is written successfully

#### Scenario: A missing variance key is routed, not zero-filled

- **WHEN** the delegate returns a per-trait entry carrying a heritability value but missing
  `var_genetic`, `var_residual`, or `n_genotypes`, with no figures requested
- **THEN** that trait appears in `failed_traits` and is absent from `per_trait`, the persisted
  per-trait table, and the persisted result JSON — never emitted with a `0.0` variance component

#### Scenario: The delegate's own return is not mutated

- **WHEN** the tool scrubs a per-trait entry before building its typed result
- **THEN** the dict object returned by the delegate is unchanged

### Requirement: Delegate Failures Are Surfaced Without Raising

A trait the delegate reports as failed, or omits from its result entirely, SHALL be counted in
`n_failed` and named in `failed_traits`, and SHALL NOT raise. Because
`HeritabilityResult.from_heritability_dict` only iterates the keys the delegate actually returned,
an omitted trait is invisible to it; the tool SHALL therefore reconcile `failed_traits` against
the requested trait list rather than adopting the typed result's list unexamined. A **run-level**
short-circuit — the delegate returning a lone `{"error": ...}` with no per-trait results — SHALL
instead surface as a structured `BloomMCPError` with no run persisted, and SHALL NOT echo the
delegate's raw message verbatim into the user-facing envelope.

#### Scenario: A per-trait delegate failure does not fail the run

- **WHEN** the delegate returns `{"error": ...}` for one trait, or omits a requested trait from its
  result entirely
- **THEN** that trait is counted in `n_failed` and named in `failed_traits`, the remaining traits
  are reported normally, and the call does not raise

#### Scenario: A run-level delegate error is surfaced as a structured error

- **WHEN** the delegate short-circuits with a run-level `{"error": ...}` and no per-trait results
- **THEN** the tool returns a `BloomMCPError` carrying that condition, no run is persisted, and
  the delegate's raw message is not reproduced verbatim in the envelope

#### Scenario: A raised delegate or plotter leaks nothing

- **WHEN** the delegate, the comparison helper, or either plotter raises an exception whose text
  carries backend internals
- **THEN** the returned `BloomMCPError`'s message and remedy contain none of that text, and no run
  is persisted

### Requirement: A Scored Trait With a Missing Variance Component Refuses to Plot

The `heritability_analysis` tool SHALL refuse to render a zero-filled variance decomposition:
when the variance-decomposition figure is requested and a trait carrying a heritability value has
a missing `var_genetic` or `var_residual` in the comparison table — meaning the delegated return
contract changed shape — the tool SHALL return `assumption_violated` naming the affected traits
and SHALL persist no run. A trait with no heritability value at all SHALL simply be dropped from
the comparison frame, not treated as this error.

#### Scenario: A scored trait missing a variance component refuses to plot

- **WHEN** the variance-decomposition figure is requested and a trait with a heritability value has
  a missing `var_genetic` or `var_residual` in the comparison table
- **THEN** the tool returns a `BloomMCPError` with code `assumption_violated` naming the affected
  traits, no run is persisted, and no zero-filled decomposition is rendered

#### Scenario: An unscored trait is dropped, not escalated

- **WHEN** the comparison table contains a trait with no heritability value
- **THEN** that row is dropped from the frame handed to the plotter and the figure renders
  normally

### Requirement: Heritability Analysis Reproduces a Recorded Golden Through the MCP Boundary

The `heritability_analysis` tool SHALL, when invoked through the MCP boundary on the
canonical-default `qc_clean` output of the turface_19 fixture, reproduce the recorded
`turface_19_heritability_golden.json` per-trait H² values within tolerance, along with the
recorded estimation method and the discrete count of traits at or above the recorded threshold.
The tolerance SHALL be no tighter than the repo's existing heritability oracle tolerance and SHALL
carry an absolute floor, because the fixture contains a trait whose H² sits near the
variance-estimation boundary and no relative tolerance is meaningful there. The golden SHALL be
labeled a **characterization snapshot** of the pinned `sleap-roots-analyze` version — turface_19
carries no externally validated per-trait H² — and SHALL record the version that produced it.

#### Scenario: The golden reproduces through the tool

- **WHEN** `heritability_analysis` is invoked on the canonical-default cleaned turface_19 frame
- **THEN** each recorded trait's `h2` matches the golden within the stated tolerance, the reported
  `method` matches the golden's recorded method, and the count of traits at or above the golden's
  threshold matches the recorded count

#### Scenario: Repeated calls are deterministic

- **WHEN** `heritability_analysis` is invoked twice with identical inputs in the same process
- **THEN** the two results' per-trait `h2` values are identical
