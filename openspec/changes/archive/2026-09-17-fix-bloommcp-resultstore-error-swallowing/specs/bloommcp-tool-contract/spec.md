## MODIFIED Requirements

### Requirement: Structured Agent-Safe Errors

bloom-mcp SHALL define `BloomMCPError` in `bloom_mcp.contract.errors` carrying a `code`, a
`message`, and a `remedy`, with a serializable structured form. The `@as_mcp_tool`
decorator SHALL map a *declared* (author opted-in via `errors=`) exception to a
`BloomMCPError` whose message is passed through. A raw traceback SHALL NEVER be returned to
the agent, and an *internal* failure (an undeclared exception or an output-contract breach)
SHALL NOT leak internal detail (paths, hosts, connection strings, SQL, bucket keys): it
SHALL return a fixed message plus a short correlation id, with the detail logged
server-side. Input-validation errors SHALL surface only the offending field locations and
error types, never the offending values.

A tool whose body persists an artifact via `ResultStore.create_run`/`commit` SHALL declare
`CommitFailedError` and `ManifestReadError` (both `bloom_mcp.result_store`) in its `errors=`
tuple, alongside any read-side declarations — so a write-port failure (e.g. an upload/
signing/manifest-write failure inside `commit()`, or a manifest-read failure inside
`create_run()`) surfaces as a `tool_error` carrying the store's own already-redacted
message, rather than being downgraded to a generic `internal_error` ref that discards it.
This declaration SHALL NOT extend to `ResultStoreError` subtypes that are not reachable
from `create_run`/`commit` (`RunNotFoundError`, `CorruptRunLinksError`,
`OutputFileMissingError` — raised only by `get_run`/`list_runs`/`get_download_links`) or
that are never a caller-actionable condition (`RunStateError` — a handle-misuse/wiring
bug) — those SHALL continue to map to `internal_error`.

#### Scenario: Declared exception becomes a structured error

- **WHEN** a decorated stub tool raises a declared exception
- **THEN** the caller receives a `BloomMCPError` with `code`, `message`, and `remedy`
  populated, and no raw traceback or stack frames are exposed

#### Scenario: Internal failure does not leak detail to the agent

- **WHEN** a decorated stub tool raises an undeclared exception whose text carries a
  connection string / host / bucket key
- **THEN** the `BloomMCPError` is `internal_error`, its message omits that detail and
  carries a correlation id (`ref:`), and the detail is logged server-side only

#### Scenario: Input validation surfaces locations, not values

- **WHEN** input validation fails on a field whose value is sensitive
- **THEN** the `BloomMCPError` (`invalid_input`) names the field location and error type
  but not the offending value

#### Scenario: A ResultStore commit failure surfaces as a structured tool error, not a bare internal ref

- **WHEN** a write-and-link analysis tool (e.g. `qc_inspect`, `qc_clean`, `clustering`,
  `pca_analysis`, `remove_outliers`, `descriptive_stats`, `cross_experiment_correlations`,
  `umap_analysis`) calls `store.commit(...)` and the store raises `CommitFailedError`
  (e.g. because the active storage backend's signing/upload step failed)
- **THEN** the caller receives a `BloomMCPError` with `code="tool_error"` whose `message`
  is the `CommitFailedError`'s own (already-redacted) text — not a generic
  `internal_error` with only a correlation ref

#### Scenario: A ResultStore manifest-read failure during create_run surfaces the same way

- **WHEN** one of the same 8 tools calls `store.create_run(...)` and the store raises
  `ManifestReadError`
- **THEN** the caller receives a `BloomMCPError` with `code="tool_error"` whose `message`
  is the raised exception's own text, not a generic `internal_error` ref

#### Scenario: A schema-incompatible manifest is caught via its ManifestReadError subclass

- **WHEN** `store.create_run(...)` raises `ManifestIncompatibleError` (a `ManifestReadError`
  subclass, raised when the stored manifest's schema version is missing or newer than the
  server understands)
- **THEN** the declared-exception `isinstance` match against the tool's `errors=` tuple
  (which names `ManifestReadError`, not `ManifestIncompatibleError` itself) still succeeds,
  and the caller receives a `BloomMCPError` with `code="tool_error"` whose `message` is the
  raised exception's own text — not `internal_error`

#### Scenario: A handle-misuse ResultStore bug still maps to internal_error

- **WHEN** one of the same 8 tools' `commit()` call raises `RunStateError` (a handle
  reused or never opened by `create_run` — a wiring bug, never triggerable via tool
  input)
- **THEN** the caller still receives a generic `internal_error` with a correlation ref,
  not a `tool_error` implying their input or retry could fix it
