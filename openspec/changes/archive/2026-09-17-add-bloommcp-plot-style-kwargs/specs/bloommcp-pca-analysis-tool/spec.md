## ADDED Requirements

### Requirement: PCA Biplot Alpha Override

The `pca_analysis` tool SHALL accept an optional `plot_alpha: float | None = None`
(`ge=0.0, le=1.0`) field in `PCAAnalysisParams`, forwarded to the upstream
`create_pca_biplot` plotter call only when set (omitted from the call entirely when `None`,
preserving the plotter's own default `alpha=0.6`). This field SHALL have no effect on
`create_pca_scree_plot`, `create_feature_contribution_plot`, or
`create_feature_contribution_heatmap`, none of whose upstream signatures accept `alpha`.
`PCAAnalysisParams` SHALL NOT expose `plot_cmap` or `plot_point_size` fields: no PCA catalog
plotter's current upstream signature accepts either kwarg. A **valid** `plot_alpha` value
SHALL be ignored (not rejected) when `include_plots=False` — it is simply never read on that
path. This is distinct from an **out-of-range** value: `ge=0.0, le=1.0` is a Pydantic field
constraint on `PCAAnalysisParams` itself, enforced at input-validation time before the tool
body (and therefore before `include_plots` is examined) — an out-of-range `plot_alpha` is
rejected as `invalid_input` regardless of `include_plots`'s value.

#### Scenario: plot_alpha is forwarded to create_pca_biplot

- **WHEN** `pca_analysis` is called with `include_plots=True`, `plots=["create_pca_biplot"]`,
  and `plot_alpha=0.3`
- **THEN** `create_pca_biplot` is invoked with `alpha=0.3`

#### Scenario: Unset plot_alpha reproduces today's default figure

- **WHEN** `pca_analysis` is called with `include_plots=True`, `plots=["create_pca_biplot"]`,
  and no `plot_alpha` value
- **THEN** `create_pca_biplot` is invoked with no `alpha` argument in its call kwargs — the
  plotter's own default (`alpha=0.6`) applies, unchanged from before this change

#### Scenario: plot_alpha has no effect on the other three plot keys

- **WHEN** `pca_analysis` is called with `include_plots=True`, all four plot keys requested,
  and `plot_alpha=0.3`
- **THEN** only `create_pca_biplot`'s call receives `alpha=0.3`; the calls to
  `create_pca_scree_plot`, `create_feature_contribution_plot`, and
  `create_feature_contribution_heatmap` are unchanged

#### Scenario: plot_alpha is ignored when include_plots is False

- **WHEN** `pca_analysis` is called with `include_plots=False` and `plot_alpha` set
- **THEN** the tool returns successfully with no figures generated and no error raised

#### Scenario: Out-of-range plot_alpha is rejected regardless of include_plots

- **WHEN** `pca_analysis` is called with `plot_alpha=1.5` (or a negative value) — with
  `include_plots` set to either `True` or `False`
- **THEN** the tool returns a `BloomMCPError` with code `invalid_input`, before any figure is
  generated, in both cases

#### Scenario: Boundary values 0.0 and 1.0 for plot_alpha are accepted

- **WHEN** `pca_analysis` is called with `include_plots=True`, `plots=["create_pca_biplot"]`,
  and `plot_alpha=0.0` (or `plot_alpha=1.0`)
- **THEN** the tool succeeds and `create_pca_biplot` is invoked with `alpha=0.0` (or
  `alpha=1.0`) — the inclusive bounds are valid values, not rejected
