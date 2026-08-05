## MODIFIED Requirements

### Requirement: Storage Backend Interface

The system SHALL define a backend-agnostic object-storage interface covering the exact seven
helpers bloommcp's write and output-read paths depend on — `upload_file`, `download_file`,
`write_json`, `read_json`, `list_prefix`, `delete_files`, and `create_signed_url` — with at
least two implementations: a Supabase Storage backend (the deployed default) and a
local-filesystem backend. The public `bloom_mcp.supabase_client` helper functions SHALL remain
the call surface and delegate to the process's active backend, so existing consumers
(`storage.writer`, `storage.manifest`, `result_store.supabase_store`, `experiment_utils`) and
the in-memory test fake keep working without modification. This interface SHALL cover object
storage only. PostgREST/table access via `get_postgrest_client` — including `read_input_csv`,
which rides that client — and raw-experiment-input reads from the local `BLOOM_TRAITS_DIR` are
outside the seam and SHALL remain unchanged regardless of the selected storage backend.

#### Scenario: Callers and the test fake are unchanged

- **WHEN** the storage backend abstraction is introduced
- **THEN** `storage.writer`, `storage.manifest`, `result_store.supabase_store`, and
  `experiment_utils` still import the same `bloom_mcp.supabase_client` helper names, and
  the existing `fake_supabase_storage` fixture still substitutes the boundary by
  monkeypatching those helper names in `supabase_client` and `manifest`

#### Scenario: Table reads and input reads are unaffected by the storage backend

- **WHEN** any storage backend is selected
- **THEN** `get_postgrest_client` and its table reads, `read_input_csv`, and raw-input reads
  from `BLOOM_TRAITS_DIR` continue to behave exactly as before, because the backend selection
  governs only the seven object-storage helpers

### Requirement: Documentation of Output Destinations

Documentation SHALL describe where bloommcp analysis outputs actually go by default (Supabase
Storage, backed by MinIO in local dev) and how to reach them (MinIO console, Supabase Studio,
the MCP read tools), and SHALL clarify that `BLOOM_OUTPUT_DIR` and `BLOOM_USE_LOCAL` do **not**
produce local CSVs by default. Documentation SHALL describe the opt-in
`BLOOM_STORAGE_BACKEND=local` backend, the `BLOOM_STORAGE_LOCAL_ROOT` root variable (and its
fallback to `BLOOM_OUTPUT_DIR`), the resulting on-disk layout keyed by storage key, and the
warning that backends MUST NOT be mixed for one experiment. Documentation SHALL additionally
describe: `create_signed_url` and the `output_links` field every consumer-tool result carries
(one signed/served URL, hash, and size per output); the `BLOOM_STORAGE_URL` env var the local
backend uses to construct a served URL (and that this requires an operator-configured HTTP
server for the local storage root — bloommcp does not run one); the `BLOOM_PUBLIC_SUPABASE_URL`
env var used to rewrite a Supabase-backed signed URL off the internal Docker host onto a
publicly reachable base; the chosen signed-URL expiry, named by its code constant rather than
restated as an independent number; and the chosen inline-vs-link size threshold, explicitly
flagged as documentation-only guidance (not enforced in code — no tool changes its response
shape based on it).

#### Scenario: Default destination is documented

- **WHEN** a developer reads the storage docs
- **THEN** they learn outputs go to Supabase Storage by default (MinIO-backed in dev), how to
  reach them, and that `BLOOM_OUTPUT_DIR` / `BLOOM_USE_LOCAL` do not by themselves write local CSVs

#### Scenario: Opt-in local backend and its caveats are documented

- **WHEN** a developer wants real CSV/JSON/PNG files on disk
- **THEN** the docs show setting `BLOOM_STORAGE_BACKEND=local` (and optionally
  `BLOOM_STORAGE_LOCAL_ROOT`), describe the on-disk layout keyed by storage key, and warn not
  to mix backends for one experiment (no cross-store view; version ids can collide)

#### Scenario: Signed-URL download and its env vars are documented

- **WHEN** a developer reads the storage docs after this change
- **THEN** they learn that every consumer-tool result carries an `output_links` entry per
  output (URL, `sha256`, `size_bytes`); that `BLOOM_STORAGE_URL` configures the local backend's
  served-URL base and requires a separately-run HTTP server for that root; that
  `BLOOM_PUBLIC_SUPABASE_URL` rewrites a Supabase signed URL off the internal Docker host for
  prod/staging; and the code constant naming the signed-URL expiry (rather than a bare number
  restated in prose)

#### Scenario: The inline-vs-link threshold is documented as guidance, not enforced behavior

- **WHEN** a developer reads the storage docs' description of the inline-vs-link size threshold
- **THEN** the documented number is explicitly labeled as documentation-only guidance for a
  caller applying it themselves, not a behavior any bloommcp tool implements or enforces

## ADDED Requirements

### Requirement: Signed URL Generation

The `StorageBackend` protocol SHALL expose `create_signed_url(key: str, expires_in: int) ->
str`, implemented by both adapters.

`SupabaseStorageBackend.create_signed_url` SHALL call the Supabase Storage client's own
signed-url method with `key` and `expires_in`, extract the URL from its response (a `dict`
whose key casing for the URL — `signedURL` / `signed_url` / `signedUrl` — is not guaranteed
stable across client library versions, so extraction SHALL try each in turn rather than assume
one), and SHALL then rewrite the URL's host from the internal `SUPABASE_URL` base to a public
base read from `BLOOM_PUBLIC_SUPABASE_URL`, a no-op when either variable is unset or the URL is
not on the internal host. A response carrying none of the expected URL keys SHALL be treated as
a failure (propagating, not returning an empty/`None` string).

`LocalStorageBackend.create_signed_url` SHALL return a served URL built from the
`BLOOM_STORAGE_URL` environment variable (`f"{BLOOM_STORAGE_URL.rstrip('/')}/{key}"` — trailing
slashes on the configured base SHALL NOT produce a doubled slash) and SHALL ignore `expires_in`
— the local backend is an opt-in dev feature with no real credential/expiry enforcement,
documented rather than worked around, matching this capability's existing local-backend
caveats. When `BLOOM_STORAGE_URL` is unset, the local backend SHALL raise rather than fabricate
a `file://` URI or otherwise leak an absolute host filesystem path.

`bloom_mcp.supabase_client` SHALL re-export `create_signed_url(key, expires_in)` as a seventh
delegate-to-active-backend helper, following the existing pattern of its siblings exactly. Standing up an HTTP server to serve the local backend's storage root, and standing up
any infrastructure beyond the `BLOOM_PUBLIC_SUPABASE_URL` rewrite for the Supabase backend, are
out of scope of this requirement.

#### Scenario: Supabase backend extracts and returns the signed URL

- **WHEN** `create_signed_url(key, expires_in)` is called against the Supabase backend for an
  existing object
- **THEN** it calls the Supabase Storage client's signed-url method with `key` and
  `expires_in`, and returns the URL extracted from that call's response

#### Scenario: Supabase backend rewrites the internal host to the public base

- **WHEN** the Supabase Storage client's response yields a URL on the internal `SUPABASE_URL`
  host and `BLOOM_PUBLIC_SUPABASE_URL` is set
- **THEN** `create_signed_url` returns that URL with the internal host prefix replaced by
  `BLOOM_PUBLIC_SUPABASE_URL`, leaving the rest of the URL (path, query, signature) unchanged

#### Scenario: The rewrite is a no-op when unconfigured or not applicable

- **WHEN** `BLOOM_PUBLIC_SUPABASE_URL` or `SUPABASE_URL` is unset, or the signed URL returned by
  the client is not on the internal `SUPABASE_URL` host
- **THEN** `create_signed_url` returns the URL unchanged

#### Scenario: A response with no extractable URL is a failure

- **WHEN** the Supabase Storage client's `create_signed_url` response carries none of the
  expected URL keys
- **THEN** `create_signed_url` raises rather than returning `None` or an empty string

#### Scenario: Local backend returns a served URL and ignores expiry

- **WHEN** `create_signed_url(key, expires_in)` is called against the local backend with
  `BLOOM_STORAGE_URL` set
- **THEN** it returns `f"{BLOOM_STORAGE_URL.rstrip('/')}/{key}"` regardless of the `expires_in`
  value passed, with no doubled slash even when `BLOOM_STORAGE_URL` itself ends in `/`

#### Scenario: Local backend fails closed with no path leak when unconfigured

- **WHEN** `create_signed_url` is called against the local backend and `BLOOM_STORAGE_URL` is
  unset
- **THEN** it raises rather than returning a `file://` URI or any string containing an absolute
  host filesystem path

#### Scenario: supabase_client re-exports the seventh helper

- **WHEN** `bloom_mcp.supabase_client.create_signed_url(key, expires_in)` is called
- **THEN** it delegates to the process's active backend exactly like its six existing siblings
  (`upload_file`, `download_file`, `write_json`, `read_json`, `list_prefix`, `delete_files`)
