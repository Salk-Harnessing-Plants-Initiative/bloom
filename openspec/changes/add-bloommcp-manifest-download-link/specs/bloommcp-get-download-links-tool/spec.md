## MODIFIED Requirements

### Requirement: get_download_links Returns the Result Store's Re-Signed Links Unmodified

The `get_download_links` tool SHALL be a thin shim over
`ResultStore.get_download_links(experiment, tool_class, run_ref)` — it SHALL perform no
signing, sizing, or key-scope logic of its own, and SHALL return the resolved run's
`experiment`, `tool_class`, resolved `run_ref`, `version_dir`, `outputs`, `output_links`,
`params`, and `based_on_version` as JSON. An unknown `experiment` SHALL surface the same
`{"error": ..., "available_experiments": ...}` shape `list_existing_analyses` already returns.
Any exception the underlying `ResultStore.get_download_links` call raises SHALL surface as
`{"error": ...}` with no raw traceback — this SHALL NOT be narrowed to a fixed, enumerated
exception list: named `ResultStore`-level types (`RunNotFoundError`, `ManifestReadError`,
`ManifestIncompatibleError`, `CorruptRunLinksError`) are the expected common cases, but the
live `create_signed_url`/`get_object_size` calls `get_download_links` makes on the Supabase
backend can raise whatever the underlying storage client raises for a given failure (unlike
`LocalStorageBackend`, whose failures are the typed `StorageKeyNotFound`/`StorageBackendError`)
— so a closed catch-list would be structurally incomplete for that backend, mirroring
`list_existing_analyses.py`'s own broad `except Exception` for the same underlying reason.

**The tool SHALL NOT return a signed link to the run's own `manifest.json` (`manifest_url`, a
prior design of this same requirement).** That file is keyed only by `(experiment, tool_class)`
— never by `run_ref` — so a link to it cannot be scoped to the one run the caller resolved; it
would expose every run ever committed for that pair instead. `params`/`based_on_version` above
carry the same run's own provenance without that cross-run exposure.

#### Scenario: Happy path returns the resolved run's links and own params as JSON

- **WHEN** `get_download_links(experiment, tool_class, run_ref)` resolves an existing run with
  populated `output_keys`
- **THEN** the tool returns JSON carrying `experiment`, `tool_class`, the resolved `run_ref`,
  `version_dir`, `outputs`, `output_links` (one entry per output, each with `key`, `url`,
  `sha256`, `size_bytes`), `params` (that run's own recorded tool-call kwargs), and
  `based_on_version`

#### Scenario: A legacy run's response still carries its own params

- **WHEN** `get_download_links(experiment, tool_class, run_ref)` resolves an existing run whose
  `output_keys` is empty (e.g. a v2 manifest entry)
- **THEN** the tool returns JSON with `output_links == {}` and populated `params`/
  `based_on_version` for that run — these are never gated on the outputs' own key-presence
  check, and were part of the manifest schema since v2

#### Scenario: Unknown experiment is reported with available experiments

- **WHEN** `get_download_links` is called with an `experiment` not known to the active reader
- **THEN** it returns `{"error": ..., "available_experiments": ...}`, matching
  `list_existing_analyses`'s existing shape

#### Scenario: A structured failure never leaks a raw traceback

- **WHEN** the underlying `ResultStore.get_download_links` call raises any exception —
  including but not limited to `RunNotFoundError`, `ManifestReadError`,
  `ManifestIncompatibleError`, `CorruptRunLinksError`, or a live output-signing/sizing failure
  whose exact type depends on the active backend
- **THEN** the tool returns `{"error": ...}` with no raw traceback or stack frame exposed
