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

**Trust boundary (documentation only — no runtime behavior change here):** neither
implementation of `create_signed_url` SHALL perform, nor is required to perform, any check that
`key` is within a scope the calling context is authorized to access — this is a generic
object-storage primitive with no concept of "run" or "experiment" ownership. The one production
caller, `ResultStore.commit()`, is responsible for restricting `key` to its own authorized scope
before calling this primitive (see `bloommcp-result-store`'s "Per-Output Signed Links And Size At
Commit" requirement for that enforcement). A future caller of `create_signed_url` outside
`ResultStore.commit()` SHALL NOT assume this primitive itself provides any ownership guarantee.

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

#### Scenario: The primitive itself performs no ownership check

- **WHEN** `create_signed_url` is called directly (bypassing `ResultStore.commit()`) with any
  syntactically valid key string
- **THEN** neither backend implementation rejects it on ownership/scope grounds — this primitive
  provides signing/serving only; scope enforcement is the caller's responsibility, documented here
  so a reader of this file alone learns where the actual guarantee lives
