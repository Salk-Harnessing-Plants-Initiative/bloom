## ADDED Requirements

### Requirement: Every Live Tool Class Is Discoverable Via `list_existing_analyses`

`list_existing_analyses.TOOL_CLASSES` SHALL include the `tool_class` string every
currently-shipping analysis tool persists its runs under, so the aggregation loop calls
`store.list_runs(experiment, tool_class)` for each one — surfacing that tool's run history
in the response's `analyses` field when runs exist, and surfacing a `list_runs` failure for
that tool class in `errors` rather than silently never asking. `manifest.CANONICAL_TOOL_CLASSES`
SHALL remain a superset of `list_existing_analyses.TOOL_CLASSES`, per its own convention.

#### Scenario: A pca_analysis run is discoverable

- **WHEN** at least one `pca_analysis` run has been committed for an experiment (persisted
  under `tool_class="pca"`) and `list_existing_analyses(experiment)` is called
- **THEN** the response's `analyses` field includes a `"pca"` entry listing that run

#### Scenario: A umap_analysis run is discoverable

- **WHEN** at least one `umap_analysis` run has been committed for an experiment (persisted
  under `tool_class="umap"`) and `list_existing_analyses(experiment)` is called
- **THEN** the response's `analyses` field includes a `"umap"` entry listing that run

#### Scenario: A qc_inspect run is discoverable

- **WHEN** at least one `qc_inspect` run has been committed for an experiment (persisted
  under `tool_class="qc_inspect"`) and `list_existing_analyses(experiment)` is called
- **THEN** the response's `analyses` field includes a `"qc_inspect"` entry listing that run

#### Scenario: `pca`, `umap`, and `qc_inspect` are registered in both registries

- **WHEN** `list_existing_analyses.TOOL_CLASSES` and `manifest.CANONICAL_TOOL_CLASSES` are
  inspected
- **THEN** both tuples contain `"pca"`, `"umap"`, and `"qc_inspect"`

#### Scenario: A `list_runs` failure for one of the 3 newly-registered classes is still reported, not dropped

- **WHEN** `store.list_runs(experiment, tool_class)` raises for `tool_class` equal to
  `"pca"`, `"umap"`, or `"qc_inspect"`
- **THEN** the response's `errors` list contains an entry for that failure, exactly as it
  already does for any other member of `TOOL_CLASSES` — this tool class is not a special
  case that gets silently skipped
