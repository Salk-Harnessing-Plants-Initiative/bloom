## MODIFIED Requirements

### Requirement: Per-Output Signed Links And Size At Commit

`ResultStore.commit(run, outputs)` SHALL return a `StoredRun` whose `output_links: dict[str,
OutputLink]` carries one entry per `outputs` entry, keyed identically, each an `OutputLink` with
the artifact's storage `key`, its `sha256` (matching `output_sha256`), and its non-negative
`size_bytes` (a legitimate zero-byte artifact is not rejected — only an empty `outputs` dict is).
Exactly one of `url`/`path` SHALL be populated, never both and never neither: for every backend
except the local backend, `url` SHALL be a signed/served URL from the active `StorageBackend`'s
`create_signed_url`, and `path` SHALL be `None`; for the local backend (`BLOOM_STORAGE_BACKEND=
local`), `commit` SHALL NOT call `create_signed_url` at all — `path` SHALL instead be the
resolved absolute filesystem path (`storage_backend.local_output_root()` joined with the key),
and `url` SHALL be `None`. This holds for every local-backend configuration (the granular
explicit-override tier included), not only the `BLOOM_LOCAL_ROOT` tier. This field SHALL be
populated only by `commit` — `get_run` and `list_runs` SHALL return `output_links` as an empty
dict (including when the resolved run was recorded before this capability existed, e.g. a legacy
v2 manifest entry with no `output_sha256`/`output_keys`), so that resolving or listing
potentially many historical runs never eagerly generates signed URLs for artifacts other than the
one a caller's own `commit` call just produced. On the non-local path, a failure to generate or
extract a usable signed URL for any output — including a signing-client response that carries
none of its expected URL keys, or one that returns an empty/`None` URL — SHALL fail the whole
`commit` call (surfacing as `CommitFailedError`, following the same best-effort-cleanup path an
upload failure already takes) rather than committing with a partial or `None` URL. None of
`output_links` SHALL be persisted into the manifest `VersionEntry` — it is computed at request
time from data already in hand (the freshly hashed staged bytes, the freshly uploaded key, and
for the local backend, the already-known local root) rather than a fresh signing call, so
existing manifest/provenance fields and cross-backend manifest-byte-identity are unaffected.

Before signing (or, for the local backend, pathing) any output, `commit` SHALL verify that every
key it is about to use falls within the prefix `commit` itself computed for this run
(`{output_root}/{tool_class}_{stem}/{version_dir}/`) — the same prefix its own `key_for` closure
used to build every `output_keys` entry and to upload the corresponding bytes moments earlier. A
key outside that prefix indicates a structural bug (never a caller-input condition, since
`outputs` names only relative paths within the run's own staging directory) and SHALL fail the
whole `commit` call via the same `CommitFailedError` fail-closed/cleanup path a signing failure
already takes — never a bare signed URL or resolved path for an unverified key. This guarantee
SHALL hold identically for `FakeResultStore`, which SHALL compute and check the equivalent prefix
from its own `key_for` construction, so a test against the fake exercises the same structural
guarantee the real adapter provides. `FakeResultStore` is unaffected by the local-backend path
branch above — it never uploads real bytes or calls `storage_backend.active_backend()`, so it
always synthesizes a `url` exactly as before, regardless of the selected backend.

#### Scenario: Commit returns a signed link per output on the default (non-local) backend

- **WHEN** a consumer writes outputs into the run's staging directory and calls
  `commit(run, outputs)` on the default (Supabase) backend
- **THEN** the returned `StoredRun.output_links` has one entry per `outputs` entry, each
  carrying a non-empty `url`, a `None` `path`, the same `sha256` as `output_sha256` for that
  name, and a non-negative `size_bytes`

#### Scenario: Commit returns a resolved path per output on the local backend

- **WHEN** a consumer writes outputs into the run's staging directory and calls
  `commit(run, outputs)` with `BLOOM_STORAGE_BACKEND=local`
- **THEN** the returned `StoredRun.output_links` has one entry per `outputs` entry, each
  carrying a `None` `url` and a non-empty `path` equal to
  `str(storage_backend.local_output_root() / key)` for that output's key, and
  `create_signed_url` is never called

#### Scenario: get_run and list_runs do not carry signed links or paths

- **WHEN** `get_run(experiment, tool_class, run_ref)` or `list_runs(experiment, tool_class)` is
  called for a previously committed run — including a legacy run recorded before this
  capability existed (e.g. a v2 manifest entry with no `output_sha256`/`output_keys`)
- **THEN** the returned `StoredRun`(s) have `output_links == {}`, regardless of how many
  historical versions or outputs exist, and regardless of the active backend

#### Scenario: A signing failure fails the whole commit on the non-local path

- **WHEN** the active (non-local) backend's `create_signed_url` raises, or returns a response
  with no extractable URL, for any one output during `commit`
- **THEN** `commit` raises `CommitFailedError`, best-effort cleans up any objects already
  uploaded for this call, and records no new version — mirroring an upload failure

#### Scenario: A None/empty URL from url_for fails the whole commit

- **WHEN** `url_for` (not `path_for`) is the closure in use and it returns `None` or an empty
  string for any one output
- **THEN** `build_output_links` raises before constructing any `OutputLink`, and `commit`
  converts this to `CommitFailedError` via the same fail-closed/cleanup path

#### Scenario: The fake store returns a shape-equivalent link without touching a real backend

- **WHEN** `FakeResultStore.commit(...)` is called, with any value of `BLOOM_STORAGE_BACKEND`
- **THEN** the returned `StoredRun.output_links` has the same keys, `sha256`, and `size_bytes` a
  real commit would produce, with a synthesized (non-network) `url` and a `None` `path` — no
  call to `storage_backend.active_backend()` is made

#### Scenario: Manifest bytes are unaffected

- **WHEN** a run commits and `output_links` is populated on the returned `StoredRun`
- **THEN** the written `manifest.json`'s `VersionEntry` for this run contains no `output_links`,
  URL, path, or size key, and every other field matches the same commit's pre-change
  golden/fixture manifest byte-for-byte (no schema version change)

#### Scenario: A key outside this run's own prefix is never signed or pathed

- **WHEN** `commit` is (by test injection — no legitimate call path produces this) about to use a
  key that does not start with this run's own `{output_root}/{tool_class}_{stem}/{version_dir}/`
  prefix, on either the signing or the pathing branch
- **THEN** `commit` raises (never calling `create_signed_url` or `path_for` for that key), the
  failure surfaces as `CommitFailedError` via the same fail-closed/cleanup path a signing failure
  already takes, and no version is recorded

#### Scenario: Every real call site's keys satisfy the scoping check

- **WHEN** any of the 8 consumer tools (`qc_clean`, `qc_inspect`, `pca_analysis`,
  `remove_outliers`, `descriptive_stats`, `cross_experiment_correlations`, `umap_analysis`,
  `clustering`) commits a run through either `SupabaseResultStore` or `FakeResultStore`, on any
  backend
- **THEN** the scoping check passes for every output with no behavior change from before this
  requirement — the existing test suite for each tool requires no modification
