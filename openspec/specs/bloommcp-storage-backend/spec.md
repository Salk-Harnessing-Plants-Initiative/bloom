# bloommcp-storage-backend Specification

## Purpose
Introduces a backend-agnostic object-storage abstraction into bloom-mcp so that the same analysis
pipeline can write and read artifacts through Supabase Storage (the deployed default) or a local
filesystem (opt-in for development). The five helper functions in `bloom_mcp.supabase_client`
(`upload_file`, `download_file`, `write_json`, `read_json`, `list_prefix`) remain the call surface;
existing consumers and the in-memory test fake require no modification. Backend selection is driven
by the `BLOOM_STORAGE_BACKEND` environment variable (`supabase` by default, `local` for the
filesystem backend) and is resolved lazily at first use, preserving the package's side-effect-free
import contract. The local backend writes files laid out by storage key under a configurable root
(`BLOOM_STORAGE_LOCAL_ROOT`, falling back to `BLOOM_OUTPUT_DIR`), enforces path-traversal
containment, uses atomic writes on POSIX, and guarantees byte-identical provenance records across
both backends.
## Requirements
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

### Requirement: Backend Selection via BLOOM_STORAGE_BACKEND

The system SHALL select the active object-storage backend from the `BLOOM_STORAGE_BACKEND`
environment variable, defaulting to `supabase` when unset. A value of `local` SHALL select
the local-filesystem backend. An unrecognized value SHALL fail fast at server startup with a
clear error naming the offending value and the accepted values, rather than failing mid-run.
Backend selection SHALL be resolved lazily (never at import) and SHALL read no environment
variable and touch no filesystem at import time, preserving the package's side-effect-free
import contract. When the backend is unset or `supabase`, the write and output-read behavior
SHALL be byte-for-byte identical to the prior Supabase-only behavior, and no local output
files SHALL be produced.

#### Scenario: Default is Supabase and unchanged

- **WHEN** `BLOOM_STORAGE_BACKEND` is unset or set to `supabase`
- **THEN** analysis outputs and the manifest are written to and read from Supabase Storage
  exactly as before, and no files are written to the local root

#### Scenario: Local backend is opt-in

- **WHEN** `BLOOM_STORAGE_BACKEND=local`
- **THEN** the object-storage boundary is served by the local-filesystem backend for the
  remainder of the process

#### Scenario: Invalid backend value fails fast at startup

- **WHEN** `BLOOM_STORAGE_BACKEND` is set to an unrecognized value (e.g. `locel`) and the
  server runs its startup validation
- **THEN** startup validation raises a clear error naming the offending value and the
  accepted values, rather than starting and failing on the first storage call

#### Scenario: Import stays side-effect-free

- **WHEN** `import bloom_mcp.server` runs in a fresh interpreter with no bloom environment set
- **THEN** the import succeeds without reading `BLOOM_STORAGE_BACKEND`, resolving a backend,
  or touching the filesystem — backend resolution happens only on first use, not at import

### Requirement: Local Filesystem Backend

The local-filesystem backend SHALL implement the object-storage interface by mapping each
storage key to a path under the configured local root, so that an analysis run produces its
outputs (CSV, JSON, PNG) and its `manifest.json` as real files laid out by storage key.
Writes SHALL overwrite an existing key in place (upsert semantics matching the Supabase
client) with idempotent parent-directory creation. `list_prefix` SHALL return the bare
immediate-child names directly under a prefix — files and first-level subdirectory names,
with no trailing slash and no path prefix — matching `os.listdir`, the in-memory fake, and
the deployed Supabase listing; an empty prefix SHALL list the root and a missing prefix SHALL
yield an empty list, not an error. Reads of a missing key SHALL raise the same not-found
condition callers already handle. Storage keys SHALL be treated as `/`-separated logical
paths regardless of host OS, and the backend SHALL reject — before any I/O — any key whose
resolved real path is not contained within the resolved real root (covering absolute-path
keys, `..` traversal, and symlink escapes). Agent-facing errors SHALL NOT leak absolute host
filesystem paths; detail SHALL be logged server-side only.

#### Scenario: A run under the local backend writes real files by key

- **WHEN** an analysis run commits with `BLOOM_STORAGE_BACKEND=local`
- **THEN** each staged output and the manifest appear as real files under the local root at
  paths mirroring their storage keys (e.g. `<root>/bloommcp_output/qc_<stem>/v1_.../_cleaned.csv`
  and `<root>/bloommcp_output/qc_<stem>/manifest.json`)

#### Scenario: Output-read paths resolve against local files

- **WHEN** manifest resolution, the versioned-cleaned lookup (`_resolve_versioned_cleaned`,
  which `download_file`s the cleaned CSV), and MCP read tools run with
  `BLOOM_STORAGE_BACKEND=local` after a committed run
- **THEN** `read_manifest`, `get_run("latest")`, and the cleaned-CSV download all succeed
  against the local files and return the committed run's data

#### Scenario: list_prefix returns bare immediate children

- **WHEN** `list_prefix(prefix)` is called against the local backend
- **THEN** it returns the bare names of entries directly under `<root>/<prefix>/` (files and
  first-level subdirectory names, no trailing slash, no path prefix) — so a version directory
  is returned as `v3_2026-…` (matching `startswith(f"{entry.id}_")`) and a sibling file as
  `manifest.json` (matching the membership check) — lists the root for an empty prefix, and
  returns an empty list, not an error, for a prefix that does not exist

#### Scenario: Missing key raises not-found

- **WHEN** `download_file` or `read_json` is called for a key with no backing file
- **THEN** the local backend raises, matching the not-found behavior callers already gate on
  via `list_prefix`

#### Scenario: Writes overwrite in place

- **WHEN** the same key is written twice (e.g. a manifest rewritten as `latest` advances)
- **THEN** the second write's content fully replaces the first, and re-creating an existing
  parent directory does not error

#### Scenario: Keys cannot escape the root

- **WHEN** a key that would resolve outside the configured root is passed to the local backend
  — for example `../../etc/passwd`, an absolute path such as `/etc/x`, or a path through a
  symlink under the root pointing elsewhere
- **THEN** the backend rejects it (raising, performing no I/O), verified by resolving the
  joined real path against the real root, and logical keys use `/` separators on every host OS

#### Scenario: Errors do not leak host paths

- **WHEN** a missing-key or permission error occurs under the local backend and surfaces to
  the caller
- **THEN** the agent-facing message carries no absolute host filesystem path, and the full
  detail is available only in server-side logs

### Requirement: Local Backend Write Atomicity

On **POSIX filesystems**, the local-filesystem backend SHALL write every object — and
especially `manifest.json`, the single catalog for all versions of an experiment —
atomically against interruption, by writing to a temporary file on the same filesystem as
the root (in the target's directory), `fsync`-ing it, and renaming it into place with
`os.replace`. A crash, kill, or disk-full condition mid-write SHALL leave the on-disk file
as either the complete prior content or the complete new content, never a truncated or
partially-written file, so a reader never observes a corrupt manifest. This guarantee is
POSIX-scoped: on Windows/NTFS `os.replace` over an existing file is not guaranteed atomic
(and may raise if a reader holds the target open); the backend is an opt-in dev feature and
production stays on Supabase Storage, so the caveat is documented rather than worked around.

#### Scenario: Interrupted manifest write leaves a whole file (POSIX)

- **WHEN** the local backend writes `manifest.json` (or any object) on a POSIX filesystem and
  the process is interrupted mid-write
- **THEN** the on-disk file is either the complete prior content or the complete new content —
  never truncated — because the backend writes a temp file on the root's filesystem and
  `os.replace`s it into place

#### Scenario: Temp file shares the root filesystem

- **WHEN** the local backend prepares an atomic write
- **THEN** the temporary file is created in the target's directory (or otherwise on the root's
  filesystem), not in a separate mount such as `/tmp`, so the rename is a true atomic replace
  rather than a non-atomic cross-mount copy

### Requirement: Local Root Resolution

When `BLOOM_STORAGE_BACKEND=local`, the local root SHALL be `BLOOM_STORAGE_LOCAL_ROOT` when
set, otherwise `BLOOM_OUTPUT_DIR`. Because `BLOOM_OUTPUT_DIR` is already a required, validated
directory, the root always resolves under the local backend. The resolved root SHALL be
validated at startup (exists and is a writable directory) through the same boot-time
validation the server already runs (`experiment_utils.validate_env`, called by
`server.main()`), with the same fail-fast discipline as the other required directories.

#### Scenario: Root resolves from the dedicated variable

- **WHEN** `BLOOM_STORAGE_BACKEND=local` and `BLOOM_STORAGE_LOCAL_ROOT` is set to a writable directory
- **THEN** the local backend roots all files under that directory

#### Scenario: Root falls back to BLOOM_OUTPUT_DIR

- **WHEN** `BLOOM_STORAGE_BACKEND=local` and `BLOOM_STORAGE_LOCAL_ROOT` is unset
- **THEN** the local backend roots all files under `BLOOM_OUTPUT_DIR` (already required and,
  in dev, mounted at `./bloommcp/data/ANALYSIS_OUTPUT`)

#### Scenario: Unusable root fails fast at startup

- **WHEN** `BLOOM_STORAGE_BACKEND=local` and the resolved root does not exist or is not a
  writable directory, and the server runs its boot-time validation
- **THEN** that validation raises a clear error naming the resolved root, rather than failing
  on the first write

### Requirement: Backend Parity and Provenance Integrity

Switching backends SHALL NOT change what is recorded for a run. For the same run, the local
and Supabase backends SHALL produce a byte-identical serialized `manifest.json` (identical
`seed`, `agent`, `environment`, `code_versions`, `outputs`, `output_keys`, and
`output_sha256`) — except `storage_backend`, which SHALL instead record whichever backend
most recently wrote the manifest and therefore legitimately differs between the two — because
provenance is built above the storage seam. The bytes the local backend writes SHALL be
verbatim copies of the staged bytes (no newline or encoding translation), so the recorded
`output_sha256` equals the SHA-256 of the artifact on disk. `download_file` SHALL copy bytes
to the caller's destination and SHALL NOT expose or mutate the canonical file under the root.
The local backend SHALL provide the same single-writer, last-write-wins, no-compare-and-swap
semantics as the Supabase path — no stronger, no weaker. A backend is not a migration: the two
stores are independent catalogs, and mixing backends for one experiment (flipping
`BLOOM_STORAGE_BACKEND` mid-history) splits its version history and can re-allocate colliding
version ids — this SHALL be documented as a non-goal, not silently relied upon. Because the two
stores are physically disjoint, no single manifest can ever itself contain entries from both
backends, so full cross-backend detection is infeasible without contacting the inactive
backend (out of scope); instead, every manifest write SHALL stamp a `storage_backend` field
naming the backend that produced it, and allocating a fresh catalog (no existing manifest for
the (experiment, tool_class) pair) SHALL log an informational message naming the experiment,
tool class, and active backend — the only locally-observable signal that a split may be
starting. This SHALL be logged at a level below warning/error (informational), since it fires
on every brand-new experiment's first commit — the common, non-mixing case — and a
warning-level log would page on-call in any environment alerting on warning-and-above for a
near-always-benign event. This signal SHALL NOT be relied upon to catch every mixing event:
it fires only when no manifest yet exists for the active backend, so a repeated flip back to a
backend that already has a catalog (e.g. `supabase` → `local` → `supabase`) SHALL NOT log again
on the return trip, even though history diverged in between.

#### Scenario: Manifest and provenance are byte-identical across backends

- **WHEN** the same run is committed through the Supabase-fake boundary and through the local
  backend
- **THEN** the serialized `manifest.json` bytes are identical (same provenance fields and
  per-artifact hash/key maps) other than `storage_backend`, which reflects each backend's own
  name, and all logical keys use `/` separators regardless of host OS

#### Scenario: Recorded hash equals the bytes on disk

- **WHEN** a run is committed under `BLOOM_STORAGE_BACKEND=local`
- **THEN** for each artifact, `sha256(<file under the root>)` equals the `output_sha256`
  recorded in the manifest, because the backend copied the staged bytes verbatim

#### Scenario: download_file does not expose the canonical file

- **WHEN** `download_file(key, dest)` runs under the local backend
- **THEN** it copies the bytes to `dest` and leaves the backing file under the root unmodified
  and unlinked-to, so the caller's tmp-file lifetime management cannot delete or mutate the
  canonical artifact

#### Scenario: Mixed-backend history split is a documented non-goal

- **WHEN** an experiment has versions committed under `supabase` and `BLOOM_STORAGE_BACKEND`
  is then flipped to `local` (or vice versa)
- **THEN** the behavior is documented as unsupported — the local read sees only the local
  catalog, `next_version_id` may re-allocate a colliding `v<N>`, and the docs warn against
  mixing backends for one experiment — rather than the split being silent, and the flip is
  additionally observable via the fresh-catalog log line and the `storage_backend` field
  recorded on each store's own manifest

#### Scenario: Manifest records which backend wrote it

- **WHEN** `write_manifest` serializes a manifest, under either backend
- **THEN** the written JSON's `storage_backend` field equals the active backend's name
  (`supabase` or `local`), read from `storage_backend.selected_backend_name()` at write time —
  so inspecting either store's `manifest.json` directly identifies which backend produced it,
  without needing to know which backend is currently configured

#### Scenario: Fresh-catalog allocation logs an informational message

- **WHEN** `SupabaseResultStore.commit` reads the manifest for an (experiment, tool_class) pair
  and finds none (a fresh catalog is about to be created — i.e. `v1` is being allocated)
- **THEN** it logs (at info level, not warning — this is the common case for a genuinely new
  experiment, not just a mixing event, and warning-level would page on-call for routine
  new-experiment onboarding) a message naming the experiment, tool class, and active backend,
  noting that any history for this experiment under a different backend is now invisible from
  this catalog going forward — logged, not raised, so the commit still succeeds

#### Scenario: Repeated backend flips do not repeatedly signal

- **WHEN** an experiment is committed under `supabase`, `BLOOM_STORAGE_BACKEND` is flipped to
  `local` (logging the fresh-catalog message for `local`'s new catalog), a run is committed
  under `local`, and `BLOOM_STORAGE_BACKEND` is then flipped back to `supabase`
- **THEN** the return trip to `supabase` logs nothing, because `supabase`'s own manifest still
  exists from before the flip — a known, documented limitation of the fresh-catalog signal (it
  detects only the first write to a backend's own catalog, not every divergence), not a silently
  unstated gap

#### Scenario: Local layout is disjoint from the legacy cleaned-CSV fallback

- **WHEN** a run commits under `BLOOM_STORAGE_BACKEND=local` with the root at `BLOOM_OUTPUT_DIR`
- **THEN** its files land under the `bloommcp_output/` prefix
  (`<root>/bloommcp_output/qc_<stem>/…`) and never at the legacy fallback path
  `<BLOOM_OUTPUT_DIR>/qc_<stem>/<stem>_cleaned.csv`, so the legacy `load_experiment_data`
  fallback cannot misread a local-backend artifact as an un-versioned cleaned CSV

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

### Requirement: Local Backend Test Coverage

The local-filesystem backend SHALL be covered by tests exercising the same object-storage
interface the in-memory fake already uses, with no live Supabase. Coverage SHALL include: a
parity check that the same write → read → list → manifest round-trip produces a byte-identical
manifest across the Supabase-fake boundary and the local backend (the in-memory fake is the
parity oracle); at least one workflow run end-to-end under `BLOOM_STORAGE_BACKEND=local`
asserting real files on disk, successful read-back through `_resolve_versioned_cleaned`, and
`sha256(on-disk) == output_sha256`; and an explicit guard that the default (unset) path writes
no local files while still using the faked Supabase boundary. The existing fixture-based suites
SHALL remain green after the helper bodies are re-pointed to the backend.

#### Scenario: Backend parity yields a byte-identical manifest

- **WHEN** the same round-trip (write outputs, write and read the manifest, list a prefix,
  resolve `get_run("latest")`) runs against the in-memory Supabase fake and against the local
  backend on a temp root
- **THEN** both produce equivalent observable results and a byte-identical serialized manifest,
  with logical keys using `/` separators regardless of host OS

#### Scenario: Workflow round-trips through real local files with hash-equality

- **WHEN** a workflow runs end-to-end with `BLOOM_STORAGE_BACKEND=local` against a temp root
- **THEN** its outputs and manifest exist as real files under the root laid out by storage key,
  the read path reads the committed run back through `_resolve_versioned_cleaned`, and each
  artifact's on-disk SHA-256 equals the manifest's `output_sha256` — with no network or
  Supabase access

#### Scenario: Default path writes no local files

- **WHEN** a commit runs with `BLOOM_STORAGE_BACKEND` unset and the `fake_supabase_storage`
  boundary active
- **THEN** the faked Supabase store receives the bytes and the temp local root contains no
  output files, guarding the opt-in default

#### Scenario: Existing fixture-based suites stay green

- **WHEN** the `supabase_client` helper bodies are re-pointed to delegate to the active backend
- **THEN** the fixture-based suites (`test_store_parity`, `test_supabase_result_store`,
  `test_supabase_reader`, `test_workflow_persistence`) still pass unchanged, because the fake
  monkeypatches the same module-level helper names

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

### Requirement: Backend Selection Boot Visibility

The server SHALL print which object-storage backend is active (`local` or `supabase`) at
startup, alongside the existing authentication-mode message. This is an observability addition
only — it SHALL NOT alter backend selection, fail-fast validation, or resolution/precedence
behavior defined by `Backend Selection via BLOOM_STORAGE_BACKEND`.

#### Scenario: Active backend is printed at boot

- **WHEN** `main()` starts the server, in either backend mode
- **THEN** a log line states which backend is active (`local` or `supabase`) before the server
  begins accepting requests

#### Scenario: No change to selection or fail-fast behavior

- **WHEN** `BLOOM_STORAGE_BACKEND` is unset, `supabase`, `local`, or an unrecognized value
- **THEN** the boot-visibility print does not change which backend is selected, whether startup
  validation fails fast, or any behavior described by `Backend Selection via
  BLOOM_STORAGE_BACKEND` — it only adds a message describing the outcome already determined by
  that requirement

