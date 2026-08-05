## ADDED Requirements

### Requirement: Object Byte Size Lookup

The system SHALL add an eighth object-storage operation, `get_object_size(key: str) -> int`, to
the `StorageBackend` Protocol (alongside the existing `upload_file`, `download_file`,
`write_json`, `read_json`, `list_prefix`, `delete_files`, `create_signed_url`), with a matching
`bloom_mcp.supabase_client.get_object_size` re-export delegating to the active backend, mirroring
the existing seven helpers' delegate-to-active-backend pattern. `SupabaseStorageBackend`'s
implementation SHALL query the deployed object-storage client's per-object info endpoint and
return that object's real byte size; the exact key/nesting the size is read from (e.g. a flat
field vs. one nested under a `metadata`-shaped key) is an implementation detail confirmed
empirically at implementation time, not assumed. A response missing or malformed for the
resolved size field, or the underlying client call itself raising (e.g. because the object was
deleted from storage), SHALL raise rather than return a fabricated value. `LocalStorageBackend`'s
implementation SHALL
return the real size of the file on disk at the key's resolved path (via the existing
`_resolve(key)` containment guard), and SHALL raise the same redacted not-found condition
`download_file`/`read_json` already raise for a key with no backing file. This operation performs
NO ownership or scope check of its own, identically to `create_signed_url` — it is a generic
object-storage primitive that will report the size of whatever syntactically valid key it is
given; a caller is responsible for restricting `key` to its own authorized scope before calling
it.

#### Scenario: Supabase backend returns the real uploaded byte size

- **WHEN** `SupabaseStorageBackend.get_object_size(key)` is called for a key that was
  previously uploaded via `upload_file`
- **THEN** it returns that object's actual byte size, read from the storage client's
  per-object info response

#### Scenario: Local backend returns the real on-disk byte size

- **WHEN** `LocalStorageBackend.get_object_size(key)` is called for a key with a backing file
  under the configured root
- **THEN** it returns `Path.stat().st_size` for that file

#### Scenario: A missing key raises the existing not-found condition

- **WHEN** `get_object_size(key)` is called for a key with no backing object, on either backend
- **THEN** it raises the same not-found error `download_file`/`read_json` already raise for a
  missing key, rather than returning `0` or `None`

#### Scenario: A malformed size response is not silently treated as zero

- **WHEN** the Supabase backend's per-object info response is missing its resolved size field
  or carries a non-numeric value
- **THEN** `get_object_size` raises rather than returning a fabricated `0`

#### Scenario: The underlying client call raising propagates, not swallowed

- **WHEN** the storage client's per-object info call itself raises (for example, the object
  was deleted from storage after being uploaded)
- **THEN** `get_object_size` propagates a clear error rather than swallowing it and returning
  a fabricated size

#### Scenario: The primitive performs no ownership check

- **WHEN** `get_object_size` is called with a syntactically valid key belonging to a different
  experiment/tool_class than the caller's own context
- **THEN** it returns that object's real size with no authorization error — restricting `key` to
  an authorized scope is the caller's responsibility, identically to `create_signed_url`
