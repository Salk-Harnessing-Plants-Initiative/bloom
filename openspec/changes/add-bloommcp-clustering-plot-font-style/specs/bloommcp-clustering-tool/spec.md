## ADDED Requirements

### Requirement: Clustering Accepts an Optional Font-Style Override for Generated Plots

The `clustering` tool input SHALL accept `plot_font_family: Optional[str] = None` and
`plot_font_size: Optional[float] = None` in `ClusteringParams`, matching the fields
`PCAAnalysisParams` and `UMAPAnalysisParams` already declare. When `include_plots` is `True`,
either value SHALL be applied uniformly to every generated figure's title, axis labels, tick
labels, standalone annotation text, figure-level text, and legend text and title. The override
SHALL apply identically for all three `method` values (`"kmeans"`, `"gmm"`, `"hierarchical"`)
and to both catalog plot keys. When both fields are `None` (the default), every generated plot
SHALL keep its upstream plotter's styling unmodified. Both fields SHALL be silently ignored (no
error) when `include_plots=False`, per the existing ignore policy in *Clustering Accepts
Optional Plot Requests*.

A `plot_font_family` matplotlib cannot resolve SHALL NOT be rejected — matplotlib falls back to
its default font at render time, so a typo does not surface as `invalid_input`. Because font
resolution is render-time and host-dependent, the value recorded in the run's provenance is the
**requested** family, not the rendered one; the same holds for a font style requested alongside
`include_plots=False`, which is recorded but never applied.

#### Scenario: A default call applies no styling to the generated figures

- **WHEN** `clustering` is called with `include_plots=True` and neither `plot_font_family` nor
  `plot_font_size` set
- **THEN** `generate_figures` is invoked with `font_family=None` and `font_size=None`
- **AND** each generated figure's text keeps whatever font family and size the upstream plotter
  drew it with — no attribute of the figure is modified

#### Scenario: Font family and size overrides reach every generated figure

- **WHEN** `clustering` is called with `include_plots=True`, `plots=None` (both catalog plots),
  `plot_font_family="serif"`, and `plot_font_size=22`
- **THEN** on **each** of the two generated figures, the title, axis labels, tick labels,
  standalone annotation text, and legend text and title (where present) report font family
  `"serif"` and font size `22`

#### Scenario: The override applies identically across all three methods

- **WHEN** `clustering` is called with `include_plots=True` and `plot_font_family="serif"` for
  `method="kmeans"`, `method="gmm"`, and `method="hierarchical"` in turn
- **THEN** each call succeeds and each produced figure's title reports font family `"serif"` —
  the font-style path is not method-dependent

#### Scenario: Font-style fields are ignored when include_plots is False

- **WHEN** `clustering` is called with `include_plots=False` and either `plot_font_family` or
  `plot_font_size` set to a valid value
- **THEN** the tool returns successfully with no `BloomMCPError` and no PNG outputs, and the
  font-style fields have no effect

#### Scenario: An unresolvable font family is not rejected

- **WHEN** `clustering` is called with `include_plots=True` and a `plot_font_family` matplotlib
  cannot resolve — an unrecognized name or the empty string
- **THEN** the tool succeeds and the PNG is persisted with matplotlib's fallback font; no
  `BloomMCPError` is raised

#### Scenario: Provenance records the requested font style, applied or not

- **WHEN** `clustering` is called with `plot_font_family` and `plot_font_size` set — whether or
  not `include_plots` is `True`, and whether or not the family resolves
- **THEN** the committed run's provenance records the submitted values
- **AND** a call omitting both records them as `null`

### Requirement: Clustering Plot Font Size Has a Sanity Ceiling

`ClusteringParams.plot_font_size` SHALL be rejected as `invalid_input` when not in `(0, 100]`
(`bloom_mcp.tools._plots.MAX_PLOT_FONT_SIZE`), checked in the `clustering` tool body via the
shared `bloom_mcp.tools._plots.check_plot_style_ceiling` helper — the same helper and ceiling
`pca_analysis` and `umap_analysis` use, so the three tools cannot silently desync — and NOT as a
Pydantic `Field(gt=0, le=100)` constraint. A `Field` constraint's violation is mapped by the
contract layer's `BloomMCPError.from_input_validation` into a message naming only the field and
error type; the rejection message here SHALL name both the submitted value and the ceiling. The
check SHALL run before any I/O and regardless of `include_plots`'s value, and SHALL reject
`float("nan")` and `float("inf")` along with out-of-range finite values. No run SHALL be
committed when the check rejects. The field's declared JSON schema SHALL still expose the bound
as `maximum`/`exclusiveMinimum` metadata (via `json_schema_extra`, not `Field(le=...)`) so a
schema-reading caller can discover it without triggering a rejection first.

The ceiling check SHALL run **after** `_reject_wrong_method_controls` — so a request wrong on
both axes reports the cross-method control conflict, the more structural error — and **before**
`validate_plot_keys`, which requires a loaded experiment where the ceiling check does not.

#### Scenario: An out-of-range plot_font_size is rejected regardless of include_plots

- **WHEN** `clustering` is called with `plot_font_size` of `0`, a negative value, a value
  greater than `100`, `float("inf")`, or `float("nan")` — with `include_plots` set to either
  `True` or `False`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input` whose message names both
  `plot_font_size` (with the submitted value) and the ceiling `100`, in both cases
- **AND** no run is committed to the `ResultStore` and no figure is left open

#### Scenario: The check runs before the experiment is read

- **WHEN** `clustering` is called with an out-of-range `plot_font_size`
- **THEN** the rejection happens before `reader.load_experiment` is called — a bad value costs
  no data read

#### Scenario: A method-control conflict outranks the font-size ceiling

- **WHEN** `clustering` is called with `method="kmeans"`, a GMM-only control such as
  `n_components`, and `plot_font_size=101`
- **THEN** the returned `BloomMCPError` names the cross-method control conflict, not the ceiling

#### Scenario: The font-size ceiling outranks plot-key validation

- **WHEN** `clustering` is called with `include_plots=True`, `plots=["not_a_real_plot"]`, and
  `plot_font_size=101`
- **THEN** the returned `BloomMCPError` names `plot_font_size` and the ceiling, and
  `reader.load_experiment` is never called

#### Scenario: The inclusive ceiling value is accepted

- **WHEN** `clustering` is called with `include_plots=True` and `plot_font_size=100`
- **THEN** the tool succeeds, the generated figure's title reports font size `100`, and the run
  is committed with its PNG output

#### Scenario: A font size just above zero is accepted

- **WHEN** `clustering` is called with `plot_font_size=0.01`
- **THEN** the tool succeeds — the lower bound is exclusive of `0` only

#### Scenario: The declared ceiling is discoverable in the tool's JSON schema

- **WHEN** `clustering`'s input schema is inspected (e.g. via MCP tool discovery)
- **THEN** `plot_font_size`'s schema entry declares `maximum: 100` and `exclusiveMinimum: 0`
- **AND** constructing `ClusteringParams` with an out-of-range `plot_font_size` does not raise —
  the bound is schema metadata, enforced only in the tool body
