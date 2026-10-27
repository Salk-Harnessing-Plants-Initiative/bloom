## ADDED Requirements

### Requirement: get_download_links Tool Registration and Discovery

The system SHALL expose a `get_download_links` MCP tool, registered in the `core` section
alongside `list_available_experiments`, `load_experiment_data`, and `list_existing_analyses`,
so it is discoverable via the MCP `tools/list` operation. The tool name SHALL be stable
(`get_download_links`, never versioned in the name) and SHALL NOT remove, rename, or alter the
behavior of any existing `core` tool. Unlike its three section-mates, it SHALL NOT be added to
`ALWAYS_INCLUDE_MCP_TOOLS` — it is a targeted, on-demand retrieval tool a caller uses once it
already has a specific `(experiment, tool_class, run_ref)` in hand, not a session-bootstrap
discovery tool.

#### Scenario: Tool appears in tools/list

- **WHEN** a FastMCP `Client` connects to the server and calls `tools/list`
- **THEN** a tool named `get_download_links` (namespaced `core_get_download_links` on the
  combined surface) is present with a description and an input schema reflecting
  `experiment`, `tool_class`, and an optional `run_ref` defaulting to `"latest"`

#### Scenario: Sibling core tools are preserved

- **WHEN** the server registers `get_download_links`
- **THEN** `list_available_experiments`, `load_experiment_data`, and `list_existing_analyses`
  remain registered and behave exactly as before

#### Scenario: The tool is not foundational

- **WHEN** `ALWAYS_INCLUDE_MCP_TOOLS` (`langchain/helpers/foundational_tools.py`) is inspected
- **THEN** it does not include `get_download_links`, so it is filtered by the same
  tool_set/mcp_tool_names routing as the analysis tools rather than always being present

### Requirement: get_download_links Returns the Result Store's Re-Signed Links Unmodified

The `get_download_links` tool SHALL be a thin shim over
`ResultStore.get_download_links(experiment, tool_class, run_ref)` — it SHALL perform no
signing, sizing, or key-scope logic of its own, and SHALL return the resolved run's
`experiment`, `tool_class`, resolved `run_ref`, `version_dir`, `outputs`, and `output_links`
as JSON. An unknown `experiment` SHALL surface the same `{"error": ..., "available_experiments":
...}` shape `list_existing_analyses` already returns. Any exception the underlying
`ResultStore.get_download_links` call raises SHALL surface as `{"error": ...}` with no raw
traceback — this SHALL NOT be narrowed to a fixed, enumerated exception list: named
`ResultStore`-level types (`RunNotFoundError`, `ManifestReadError`,
`ManifestIncompatibleError`, `CorruptRunLinksError`) are the expected common cases, but the
live `create_signed_url`/`get_object_size` calls `get_download_links` makes on the Supabase
backend can raise whatever the underlying storage client raises for a given failure (unlike
`LocalStorageBackend`, whose failures are the typed `StorageKeyNotFound`/`StorageBackendError`)
— so a closed catch-list would be structurally incomplete for that backend, mirroring
`list_existing_analyses.py`'s own broad `except Exception` for the same underlying reason.

#### Scenario: Happy path returns the resolved run's links as JSON

- **WHEN** `get_download_links(experiment, tool_class, run_ref)` resolves an existing run with
  populated `output_keys`
- **THEN** the tool returns JSON carrying `experiment`, `tool_class`, the resolved `run_ref`,
  `version_dir`, `outputs`, and `output_links` (one entry per output, each with `key`, `url`,
  `sha256`, `size_bytes`)

#### Scenario: Unknown experiment is reported with available experiments

- **WHEN** `get_download_links` is called with an `experiment` not known to the active reader
- **THEN** it returns `{"error": ..., "available_experiments": ...}`, matching
  `list_existing_analyses`'s existing shape

#### Scenario: A structured failure never leaks a raw traceback

- **WHEN** the underlying `ResultStore.get_download_links` call raises any exception —
  including but not limited to `RunNotFoundError`, `ManifestReadError`,
  `ManifestIncompatibleError`, `CorruptRunLinksError`, or a live storage-lookup failure whose
  exact type depends on the active backend
- **THEN** the tool returns `{"error": ...}` with no raw traceback or stack frame exposed
