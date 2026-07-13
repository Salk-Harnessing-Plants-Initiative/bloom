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

The system SHALL define a backend-agnostic object-storage interface covering the exact five
helpers bloommcp's write and output-read paths depend on — `upload_file`, `download_file`,
`write_json`, `read_json`, and `list_prefix` — with at least two implementations: a Supabase
Storage backend (the deployed default) and a local-filesystem backend. The public
`bloom_mcp.supabase_client` helper functions SHALL remain the call surface and delegate to
the process's active backend, so existing consumers (`storage.writer`, `storage.manifest`,
`result_store.supabase_store`, `experiment_utils`) and the in-memory test fake keep working
without modification. This interface SHALL cover object storage only. PostgREST/table access
via `get_postgrest_client` — including `read_input_csv`, which rides that client — and
raw-experiment-input reads from the local `BLOOM_TRAITS_DIR` are outside the seam and SHALL
remain unchanged regardless of the selected storage backend.

#### Scenario: Callers and the test fake are unchanged

- **WHEN** the storage backend abstraction is introduced
- **THEN** `storage.writer`, `storage.manifest`, `result_store.supabase_store`, and
  `experiment_utils` still import the same `bloom_mcp.supabase_client` helper names, and
  the existing `fake_supabase_storage` fixture still substitutes the boundary by
  monkeypatching those five module-level names in `supabase_client` and `manifest`

#### Scenario: Table reads and input reads are unaffected by the storage backend

- **WHEN** any storage backend is selected
- **THEN** `get_postgrest_client` and its table reads, `read_input_csv`, and raw-input reads
  from `BLOOM_TRAITS_DIR` continue to behave exactly as before, because the backend selection
  governs only the five object-storage helpers

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
`output_sha256`), because provenance is built above the storage seam. The bytes the local
backend writes SHALL be verbatim copies of the staged bytes (no newline or encoding
translation), so the recorded `output_sha256` equals the SHA-256 of the artifact on disk.
`download_file` SHALL copy bytes to the caller's destination and SHALL NOT expose or mutate
the canonical file under the root. The local backend SHALL provide the same single-writer,
last-write-wins, no-compare-and-swap semantics as the Supabase path — no stronger, no weaker.
A backend is not a migration: the two stores are independent catalogs, and mixing backends for
one experiment (flipping `BLOOM_STORAGE_BACKEND` mid-history) splits its version history and
can re-allocate colliding version ids — this SHALL be documented as a non-goal, not silently
relied upon.

#### Scenario: Manifest and provenance are byte-identical across backends

- **WHEN** the same run is committed through the Supabase-fake boundary and through the local
  backend
- **THEN** the serialized `manifest.json` bytes are identical (same provenance fields and
  per-artifact hash/key maps), and all logical keys use `/` separators regardless of host OS

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
  mixing backends for one experiment — rather than the split being silent

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
warning that backends MUST NOT be mixed for one experiment.

#### Scenario: Default destination is documented

- **WHEN** a developer reads the storage docs
- **THEN** they learn outputs go to Supabase Storage by default (MinIO-backed in dev), how to
  reach them, and that `BLOOM_OUTPUT_DIR` / `BLOOM_USE_LOCAL` do not by themselves write local CSVs

#### Scenario: Opt-in local backend and its caveats are documented

- **WHEN** a developer wants real CSV/JSON/PNG files on disk
- **THEN** the docs show setting `BLOOM_STORAGE_BACKEND=local` (and optionally
  `BLOOM_STORAGE_LOCAL_ROOT`), describe the on-disk layout keyed by storage key, and warn not
  to mix backends for one experiment (no cross-store view; version ids can collide)

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

