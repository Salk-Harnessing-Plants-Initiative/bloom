## MODIFIED Requirements

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
`BLOOM_STORAGE_URL` environment variable when it is set
(`f"{BLOOM_STORAGE_URL.rstrip('/')}/{key}"` — trailing slashes on the configured base SHALL NOT
produce a doubled slash) and SHALL ignore `expires_in` — the local backend is an opt-in dev
feature with no real credential/expiry enforcement, documented rather than worked around,
matching this capability's existing local-backend caveats. **When `BLOOM_STORAGE_URL` is
unset, `LocalStorageBackend.create_signed_url` SHALL instead default its base to
`bloom_mcp.storage_backend.self_serve_base_url()` (`BLOOMMCP_PUBLIC_URL` when set, else
`http://localhost:8811`) plus `/output`, rather than raising.** `bloom_mcp.server.build_app()`
SHALL mount `starlette.staticfiles.StaticFiles` at `/output`, serving the same directory
`storage_backend.local_output_root()` resolves (whichever tier — `BLOOM_STORAGE_LOCAL_ROOT`,
`<BLOOM_LOCAL_ROOT>/output`, or the `BLOOM_OUTPUT_DIR` bridge — produced it), whenever
`BLOOM_STORAGE_BACKEND=local`, so a signed URL built from either the default or an explicit
`BLOOM_STORAGE_URL` pointing at this same address actually resolves standalone (`uv run
bloom-mcp`, no docker-compose). This default and mount apply in every local-backend
configuration (the granular explicit-override tier included), not only when `BLOOM_LOCAL_ROOT`
is set — `create_signed_url` is called only at runtime, on an instance that already exists
because the local backend was selected, carrying no import-time-purity constraint. The `/output`
mount is unauthenticated, matching the existing `/health` route's precedent; it is not gated by
`BLOOMMCP_API_KEY` or OAuth.

`bloom_mcp.supabase_client` SHALL re-export `create_signed_url(key, expires_in)` as a seventh
delegate-to-active-backend helper, following the existing pattern of its siblings exactly.
**Standing up an HTTP server to serve the local backend's storage root is no longer out of
scope** — see the mount described above — but standing up any infrastructure beyond the
`BLOOM_PUBLIC_SUPABASE_URL` rewrite for the Supabase backend remains out of scope of this
requirement.

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

#### Scenario: Local backend returns a served URL and ignores expiry when BLOOM_STORAGE_URL is set

- **WHEN** `create_signed_url(key, expires_in)` is called against the local backend with
  `BLOOM_STORAGE_URL` set
- **THEN** it returns `f"{BLOOM_STORAGE_URL.rstrip('/')}/{key}"` regardless of the `expires_in`
  value passed, with no doubled slash even when `BLOOM_STORAGE_URL` itself ends in `/`

#### Scenario: Unset BLOOM_STORAGE_URL defaults to bloommcp's own address instead of raising

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, `BLOOM_STORAGE_URL` is unset, and `BLOOMMCP_PUBLIC_URL`
  is also unset
- **THEN** `create_signed_url("bloommcp_output/qc_x/v1/_cleaned.csv", 3600)` returns
  `http://localhost:8811/output/bloommcp_output/qc_x/v1/_cleaned.csv` rather than raising —
  superseding this requirement's prior "fails closed... raises rather than returning a `file://`
  URI" behavior for the unset case specifically; an unset `BLOOM_STORAGE_URL` is no longer a
  failure mode

#### Scenario: BLOOMMCP_PUBLIC_URL overrides the hardcoded default host

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, `BLOOM_STORAGE_URL` is unset, and
  `BLOOMMCP_PUBLIC_URL=https://bloommcp.example.internal` is set
- **THEN** `create_signed_url("k", 3600)` returns `https://bloommcp.example.internal/output/k`

#### Scenario: supabase_client re-exports the seventh helper

- **WHEN** `bloom_mcp.supabase_client.create_signed_url(key, expires_in)` is called
- **THEN** it delegates to the process's active backend exactly like its six existing siblings
  (`upload_file`, `download_file`, `write_json`, `read_json`, `list_prefix`, `delete_files`)

#### Scenario: The /output mount is present only in local mode

- **WHEN** `bloom_mcp.server.build_app()` is called with `BLOOM_STORAGE_BACKEND` unset or
  `supabase`
- **THEN** the resulting app has no route mounted at `/output`

#### Scenario: The /output mount actually serves the resolved local root

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, the local output root resolves to a directory
  containing a real file at `bloommcp_output/qc_x/v1/_cleaned.csv`, and a test client issues
  `GET /output/bloommcp_output/qc_x/v1/_cleaned.csv` against `build_app()`'s returned app
- **THEN** the response is `200` with that file's bytes

#### Scenario: The /output mount serves whichever tier resolved the local root

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, whether via an explicitly-set `BLOOM_STORAGE_LOCAL_ROOT`
  or via the `<BLOOM_LOCAL_ROOT>/output` default
- **THEN** the `/output` mount serves files from whichever directory
  `storage_backend.local_output_root()` resolves for that configuration
