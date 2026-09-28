## ADDED Requirements

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
