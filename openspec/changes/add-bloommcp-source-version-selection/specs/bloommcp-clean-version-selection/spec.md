## ADDED Requirements

### Requirement: Explicit Cleaned-Version Selection on require_clean Tools

Each of 5 `require_clean=True` tools without their own archived capability spec (`clustering`,
`descriptive_stats`, `umap_analysis`, `remove_outliers`, `cross_experiment_correlations`) SHALL
accept an optional cleaned-version selector, threaded to
`load_experiment(..., require_clean=True, version=...)`. (The sixth `require_clean=True` tool,
`pca_analysis`, already has an archived capability spec — `bloommcp-pca-analysis-tool` — and gets
the identical selector via a MODIFIED delta there instead of here, so its behavior is not
duplicated across two capabilities.) Omitting the selector SHALL reproduce that tool's **current**
default exactly — most tools default to the Protocol's `"latest"` by omitting the `version` kwarg
entirely, but `remove_outliers` already hardcodes `"latest_qc"` today and MUST continue to resolve
`"latest_qc"` when its selector is omitted, not silently switch to `"latest"`.
`cross_experiment_correlations` reads two independent experiments and SHALL expose two independent
selectors (`version_1`/`version_2`), each threaded only to its own experiment's read.

#### Scenario: Omitting the selector preserves today's default on the three uniform tools

- **WHEN** `clustering`, `descriptive_stats`, or `umap_analysis` is invoked with no version
  selector given
- **THEN** the tool calls `load_experiment(params.experiment, require_clean=True)` with no
  `version` kwarg, exactly as before this change (Protocol default `"latest"` applies)

#### Scenario: An explicit version is honored on the three uniform tools

- **WHEN** `clustering`, `descriptive_stats`, or `umap_analysis` is invoked with an explicit
  version selector (e.g. `"v2"`)
- **THEN** the tool calls `load_experiment(params.experiment, require_clean=True, version="v2")`

#### Scenario: remove_outliers omitting the selector still defaults to latest_qc

- **WHEN** `remove_outliers` is invoked with no version selector given
- **THEN** the tool calls `load_experiment(params.experiment, require_clean=True, version="latest_qc")`,
  exactly as before this change — not the Protocol's generic `"latest"` default

#### Scenario: remove_outliers honors an explicit version override

- **WHEN** `remove_outliers` is invoked with an explicit version selector (other than `"latest"`)
- **THEN** the tool calls `load_experiment(params.experiment, require_clean=True, version=<given>)`,
  overriding its own `"latest_qc"` default

#### Scenario: remove_outliers treats an explicit "latest" the same as omitting the selector

- **WHEN** `remove_outliers` is invoked with `version="latest"` given explicitly
- **THEN** the tool still calls `load_experiment(params.experiment, require_clean=True,
  version="latest_qc")` — the bare Protocol default is not a deliberate override of this tool's
  own default, and passing it through unchanged would silently trim from this tool's own prior
  output (the generic outliers-preferring `"latest"`) instead of the plain clean

#### Scenario: cross_experiment_correlations selects each experiment's version independently

- **WHEN** `cross_experiment_correlations` is invoked with `version_1` set and `version_2` omitted
  (or vice versa)
- **THEN** the experiment with a given selector loads that explicit version, and the other
  experiment loads its own default (`"latest"`) independently — neither selector affects the
  other experiment's read

#### Scenario: cross_experiment_correlations omitting both selectors preserves today's behavior

- **WHEN** `cross_experiment_correlations` is invoked with neither `version_1` nor `version_2`
  given
- **THEN** both experiments load exactly as before this change, with no `version` kwarg passed to
  either `load_experiment` call
