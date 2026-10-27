## ADDED Requirements

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
