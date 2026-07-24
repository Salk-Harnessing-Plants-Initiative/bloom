## MODIFIED Requirements

### Requirement: Storage Backend Interface

The system SHALL define a backend-agnostic object-storage interface covering the exact five
helpers bloommcp's write and output-read paths depend on — `upload_file`, `download_file`,
`write_json`, `read_json`, and `list_prefix` — with at least two implementations: a Supabase
Storage backend (the deployed default) and a local-filesystem backend. The public
`bloom_mcp.supabase_client` helper functions SHALL remain the call surface and delegate to
the process's active backend, so existing consumers (`manifest.manifest`,
`result_store.supabase_store`, `experiment_utils`) and the in-memory test fake keep working
without modification. This interface SHALL cover object storage only. PostgREST/table access
via `get_postgrest_client` — including `read_input_csv`, which rides that client — and
raw-experiment-input reads from the local `BLOOM_TRAITS_DIR` are outside the seam and SHALL
remain unchanged regardless of the selected storage backend.

#### Scenario: Callers and the test fake are unchanged

- **WHEN** the storage backend abstraction is introduced
- **THEN** `manifest.manifest`, `result_store.supabase_store`, and `experiment_utils` still
  import the same `bloom_mcp.supabase_client` helper names, and the existing
  `fake_supabase_storage` fixture still substitutes the boundary by monkeypatching those
  five module-level names in `supabase_client` and `manifest`

#### Scenario: Table reads and input reads are unaffected by the storage backend

- **WHEN** any storage backend is selected
- **THEN** `get_postgrest_client` and its table reads, `read_input_csv`, and raw-input reads
  from `BLOOM_TRAITS_DIR` continue to behave exactly as before, because the backend selection
  governs only the five object-storage helpers
