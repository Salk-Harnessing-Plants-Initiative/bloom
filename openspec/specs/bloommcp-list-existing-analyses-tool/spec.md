# bloommcp-list-existing-analyses-tool Specification

## Purpose
Governs what `list_existing_analyses` exposes about previously persisted analysis runs. Two
properties are load-bearing: every live tool class must be discoverable through it, so a run cannot
become unreachable by having been written under a class the listing does not know about; and
per-tool-class error entries must be redacted and named by their **public** tool name, so an
internal class name or raw error text is never surfaced to a caller.
## Requirements
### Requirement: Per-Tool-Class Error Entries Are Redacted And Named By Public Tool

`list_existing_analyses`'s per-`tool_class` error-aggregation loop SHALL apply
`experiment_utils.safe_error_text` to the raised exception before appending it to the
response's `errors` list — the `errors.append(...)` call made when
`store.list_runs(experiment, tool_class)` raises for one of `TOOL_CLASSES` — mirroring the
redaction its own `trim_staleness` sibling branch and `get_download_links`'s equivalent
handling both already apply. Each such entry SHALL be labeled with the failing tool's public
MCP tool name (e.g. `descriptive_stats`, `cross_experiment_correlations`) rather than its
internal `tool_class` string (e.g. `stats`, `correlation`), via a `tool_class` →
public-tool-name lookup covering the tool classes `TOOL_CLASSES` iterates that map to a
current tool. A `tool_class` with no known public-tool mapping SHALL fall back to the raw
`tool_class` string rather than raising or omitting the error entry.

#### Scenario: A tool_class error entry is redacted

- **WHEN** `store.list_runs(experiment, tool_class)` raises an exception whose text contains
  a credential/token-shaped fragment or exceeds `safe_error_text`'s length bound
- **THEN** the corresponding entry in the response's `errors` list contains neither the raw
  fragment nor the untruncated text — matching what `safe_error_text` would produce for that
  exception

#### Scenario: The error entry names the public tool, not the internal tool_class

- **WHEN** `store.list_runs(experiment, "stats")` raises, causing an aggregated error entry
  for the `descriptive_stats` tool's tool_class
- **THEN** the entry starts with `"descriptive_stats: "`, not `"stats: "`

#### Scenario: An unmapped tool_class falls back to itself

- **WHEN** `store.list_runs(experiment, tool_class)` raises for a `tool_class` with no known
  public-tool mapping (e.g. a legacy/retired entry such as `"dimred"`)
- **THEN** the aggregated error entry starts with that raw `tool_class` string (e.g.
  `"dimred: "`) rather than raising or being silently omitted from `errors`

#### Scenario: An unrelated successful result and a redacted, publicly-named error still survive together

- **WHEN** `trim_staleness` resolves successfully for the queried experiment in the same call
  where a different tool class's `list_runs` lookup raises
- **THEN** the response includes both the successful `trim_is_stale` result and the redacted,
  publicly-named error entry — neither is dropped because the other succeeded or failed,
  preserving the existing co-occurrence guarantee `bloommcp-outliers-staleness-audit`
  documents for this same loop

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

