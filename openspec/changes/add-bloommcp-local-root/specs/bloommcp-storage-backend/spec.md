## MODIFIED Requirements

### Requirement: Backend Selection via BLOOM_STORAGE_BACKEND

The system SHALL select the active object-storage backend from the `BLOOM_STORAGE_BACKEND`
environment variable, defaulting to `supabase` when unset. A value of `local` SHALL select
the local-filesystem backend. An unrecognized value SHALL fail fast at server startup with a
clear error naming the offending value and the accepted values, rather than failing mid-run.
Backend selection — constructing and memoizing a concrete `StorageBackend` instance via
`active_backend()` — SHALL be resolved lazily (never at import) and SHALL touch no filesystem
at import time, preserving the package's side-effect-free import contract. The cheap
`is_local_backend()` / `selected_backend_name()` accessors MAY be read at
`bloom_mcp.experiment_utils` import time, but only when the new `BLOOM_LOCAL_ROOT` variable
(unset by default) is itself set — so `import bloom_mcp.server` continues to read no
environment variable at import in every deployment that has not opted into
`BLOOM_LOCAL_ROOT`, and `is_local_backend()` never raises regardless of the value of
`BLOOM_STORAGE_BACKEND` (including an unrecognized one), so this opt-in read cannot itself
break import purity. When the backend is unset or `supabase`, the write and output-read
behavior SHALL be byte-for-byte identical to the prior Supabase-only behavior, and no local
output files SHALL be produced.

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

#### Scenario: Import stays side-effect-free when BLOOM_LOCAL_ROOT is unset

- **WHEN** `import bloom_mcp.server` runs in a fresh interpreter with `BLOOM_LOCAL_ROOT` unset
  (regardless of `BLOOM_STORAGE_BACKEND`)
- **THEN** the import succeeds without resolving a backend, touching the filesystem, or
  reading `BLOOM_STORAGE_BACKEND` — `bloom_mcp.experiment_utils`'s module-level `PLOTS_DIR`
  computation does not call `is_local_backend()` when `BLOOM_LOCAL_ROOT` is unset

#### Scenario: The BLOOM_LOCAL_ROOT opt-in read never raises, even on an invalid backend value

- **WHEN** `import bloom_mcp.server` runs with `BLOOM_LOCAL_ROOT` set and `BLOOM_STORAGE_BACKEND`
  set to an unrecognized value
- **THEN** the import still succeeds — `is_local_backend()` only compares the value to `"local"`
  and never raises, so the opt-in read cannot turn an invalid value into an import-time crash;
  the unrecognized-value error still surfaces only at boot validation / first backend
  construction

### Requirement: Local Root Resolution

When `BLOOM_STORAGE_BACKEND=local`, the local root SHALL resolve in this order:
`BLOOM_STORAGE_LOCAL_ROOT` when explicitly set; otherwise `<BLOOM_LOCAL_ROOT>/output` when the
single `BLOOM_LOCAL_ROOT` variable is set; otherwise `BLOOM_OUTPUT_DIR` (the pre-existing
bridge-only, deprecated default). `BLOOM_OUTPUT_DIR` is required as a directory only in that
third case, or when the default Supabase backend is active — `BLOOM_LOCAL_ROOT` mode does not
require it. `BLOOM_LOCAL_ROOT` SHALL take effect only when `BLOOM_STORAGE_BACKEND=local`; it
SHALL be inert for the default Supabase backend, even if left set in the environment. The
resolved root SHALL be validated at startup through the same boot-time validation the server
already runs (`experiment_utils.validate_env`, called by `server.main()`): an explicitly-set
`BLOOM_STORAGE_LOCAL_ROOT`, or the `BLOOM_OUTPUT_DIR` bridge fallback, SHALL be required to
already exist as a writable directory (fail-fast, unchanged); the `<BLOOM_LOCAL_ROOT>/output`
default SHALL instead be created (`mkdir(parents=True, exist_ok=True)`) if missing, after
confirming the top-level `BLOOM_LOCAL_ROOT` itself exists and is writable.

#### Scenario: Root resolves from the dedicated variable

- **WHEN** `BLOOM_STORAGE_BACKEND=local` and `BLOOM_STORAGE_LOCAL_ROOT` is set to a writable directory
- **THEN** the local backend roots all files under that directory

#### Scenario: Root resolves from BLOOM_LOCAL_ROOT when the dedicated variable is unset

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, `BLOOM_STORAGE_LOCAL_ROOT` is unset, and
  `BLOOM_LOCAL_ROOT` is set to an existing writable directory
- **THEN** the local backend roots all files under `<BLOOM_LOCAL_ROOT>/output`, creating that
  subfolder if it does not already exist

#### Scenario: Root falls back to BLOOM_OUTPUT_DIR

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, `BLOOM_STORAGE_LOCAL_ROOT` is unset, and
  `BLOOM_LOCAL_ROOT` is also unset
- **THEN** the local backend roots all files under `BLOOM_OUTPUT_DIR` (already required and,
  in dev, mounted at `./bloommcp/data/ANALYSIS_OUTPUT`)

#### Scenario: Unusable root fails fast at startup

- **WHEN** `BLOOM_STORAGE_BACKEND=local` and the effective root fails validation — an
  explicitly-set `BLOOM_STORAGE_LOCAL_ROOT` or the `BLOOM_OUTPUT_DIR` bridge fallback that does
  not exist or is not a writable directory, or a `BLOOM_LOCAL_ROOT` that does not exist or is
  not writable — and the server runs its boot-time validation
- **THEN** that validation raises a clear error naming the offending path, rather than failing
  on the first write

#### Scenario: A BLOOM_LOCAL_ROOT-derived output subfolder blocked by a non-directory file

- **WHEN** `BLOOM_LOCAL_ROOT` is a valid, writable directory but `<BLOOM_LOCAL_ROOT>/output`
  already exists as a regular file rather than a directory
- **THEN** boot raises a clear, caller-safe error rather than letting `mkdir`'s raw
  `FileExistsError` propagate
