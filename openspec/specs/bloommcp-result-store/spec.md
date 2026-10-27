# bloommcp-result-store Specification

## Purpose
TBD - created by archiving change add-bloommcp-persistence-ports. Update Purpose after archive.
## Requirements
### Requirement: ResultStore Port

The system SHALL define a backend-agnostic `ResultStore` port exposing `create_run(experiment, tool, params, provenance, user_label)`, `commit(run, outputs)`, `list_runs(experiment, tool)`, and `get_run(experiment, tool, run_ref)`. `create_run` SHALL return a `RunHandle` exposing the allocated version id, the staging directory that consumers write outputs into, and the manifest path consumers surface in responses. `commit` SHALL return a `StoredRun` whose run reference is **opaque** (backend-specific concepts — `tool_class` naming, `v<N>`, the `latest` pointer, object keys — live in the adapter, not the port). Consumers SHALL depend only on this port — never on `AnalysisWriter`, `AnalysisDir`, or `supabase` directly.

#### Scenario: Create exposes a writable staging surface and version id

- **WHEN** a consumer calls `create_run(experiment, tool, params, provenance)`
- **THEN** the returned `RunHandle` exposes the allocated version id and a staging directory path the consumer can write output files into before commit

#### Scenario: Commit records a versioned run and returns its links

- **WHEN** a consumer writes outputs into the run's staging directory and calls `commit(run, outputs)`
- **THEN** the store records a new version for that experiment and tool and returns a `StoredRun` describing the committed run reference, its manifest path, and its artifact links

#### Scenario: get_run resolves the most recent run

- **WHEN** two `create_run`→`commit` cycles complete for the same experiment and tool
- **THEN** `list_runs(experiment, tool)` returns both in order, `get_run(experiment, tool, "latest")` resolves to the second, and `get_run` for the first run's reference resolves to the first

#### Scenario: Unknown run reference is reported through the contract

- **WHEN** `get_run(experiment, tool, run_ref)` is called for a reference or tool with no recorded run
- **THEN** it surfaces a structured not-found condition (no raw traceback), and `list_runs` for an experiment with no runs returns an empty list

#### Scenario: Lifecycle misuse is rejected

- **WHEN** `commit` is called twice on the same `RunHandle`, or with a handle that was never created by `create_run`
- **THEN** the store rejects the call rather than silently double-recording or corrupting the manifest

#### Scenario: Write consumers depend only on the port

- **WHEN** `tools/workflows/_helpers.py` and the five workflows are inspected
- **THEN** none import `AnalysisWriter`, `AnalysisDir`, or `supabase` directly; each receives a `ResultStore`

### Requirement: Provenance Persisted at Commit

The `ResultStore` SHALL persist the Tier 1 `Provenance` into the committed run's v3 manifest entry by building the `VersionEntry` via `Provenance.to_version_entry`, so `seed`, `agent`, `environment`, and `code_versions` are recorded — closing the gap where `AnalysisWriter.commit` hand-rolls a provenance-lossy entry.

#### Scenario: Provenance fields round-trip into the version entry

- **WHEN** a run carrying a stamped `Provenance` is committed
- **THEN** the committed manifest entry equals `provenance.to_version_entry(version_id=...)` for `tool`, `params`, `seed`, `agent`, `environment`, and `code_versions`, with the resolved (non-null) seed recorded

#### Scenario: Input hash stays on the experiment block

- **WHEN** a run is committed
- **THEN** the input content hash is recorded on the manifest `ExperimentBlock` (not duplicated onto the `VersionEntry`), preserving the deployed manifest shape

### Requirement: SupabaseResultStore Adapter

The system SHALL provide a `SupabaseResultStore` adapter implementing `ResultStore` that wraps `AnalysisWriter`/`AnalysisDir` for versioning, staging, and upload, persisting runs as versioned `bloommcp_output/<tool_class>_<stem>/v<N>/` directories with a v3 `manifest.json`, and tolerating pre-existing v2 manifests on read.

#### Scenario: Commit writes a versioned directory and advances latest

- **WHEN** `SupabaseResultStore.commit(run, outputs)` is called
- **THEN** it uploads the staged outputs under the versioned directory, appends the provenance-built `VersionEntry`, and advances the manifest `latest`

#### Scenario: Per-artifact hashes are computed over the uploaded bytes

- **WHEN** a run whose contract-time `Provenance` has empty `output_sha256`/`output_keys` is committed
- **THEN** for each artifact the adapter records `output_sha256` as the SHA-256 of the exact bytes uploaded (not an S3/MinIO ETag) and `output_keys` as the logical Supabase key (`bloommcp_output/...`, never a physical MinIO/S3 id), and `outputs`, `output_sha256`, and `output_keys` share an identical key-set

#### Scenario: Reads tolerate a pre-existing v2 manifest

- **WHEN** `list_runs`/`get_run` are called against an experiment whose stored `manifest.json` is schema v2
- **THEN** they return the historical run without error, with the v3-only fields (`seed`, `agent`, `environment`, per-artifact maps) defaulted, and a subsequent commit appends a v3 entry alongside the v2 entries

#### Scenario: Commit failure cleans up and does not corrupt the manifest

- **WHEN** an artifact upload or manifest write raises mid-commit
- **THEN** the adapter surfaces a structured error (no traceback leak), cleans up the staging directory, and does not leave the manifest advanced to a partially-written version; the inherited single-writer / no-CAS limitation (concurrent commits may clobber an entry) is documented, not silently relied upon

#### Scenario: A generic manifest read failure during create_run, list_runs, or get_run surfaces a structured error

- **WHEN** the underlying manifest read (`AnalysisDir.read_manifest`/`list_versions`/`get_version`) raises any exception other than `ManifestSchemaError` — a storage/network blip, but also a corrupt/shape-invalid `manifest.json` or a permanent permission denial — during `create_run`, `list_runs`, or `get_run`
- **THEN** the adapter catches it at that call site, logs the original exception server-side (no host path/URL leak — the raised error's own message is exc-free), and raises a `ManifestReadError` instead of letting the raw exception escape, without claiming the failure is transient or safe to retry; this guard is independent per call site and does not depend on `commit()`'s own hardened try/except or on any particular caller's error handling

#### Scenario: A schema-incompatible manifest during create_run, list_runs, or get_run surfaces a distinguishable structured error

- **WHEN** the underlying manifest read raises `ManifestSchemaError` (the manifest's schema version is missing or newer than this server understands) during `create_run`, `list_runs`, or `get_run`
- **THEN** the adapter catches it at that call site, logs it server-side, and raises `ManifestIncompatibleError` — a subclass of `ManifestReadError`, so every existing `except ManifestReadError`/`except ResultStoreError`/`except Exception` still catches it, while a caller that needs to distinguish "storage flaked" from "manifest schema unsupported" can `isinstance()`-check for the narrower type

### Requirement: FakeResultStore Adapter

The system SHALL provide an in-memory `FakeResultStore` adapter implementing `ResultStore`, behaviourally equivalent to `SupabaseResultStore` for observable outcomes — including its commit-failure and duplicate-version-id failure semantics, not only its happy path — so the full write path, including failure handling, is testable with no live Supabase.

#### Scenario: In-memory create and commit without Supabase

- **WHEN** a test calls `create_run` then `commit` on `FakeResultStore`
- **THEN** it records a versioned run with provenance and artifact links retrievable via `list_runs`/`get_run`, with no network or Supabase access

#### Scenario: Fake simulates a mid-commit failure with the same retry contract as Supabase

- **WHEN** a test injects a commit failure on `FakeResultStore` (via its failure-injection hook, at any point up to and including after every output is recorded) and calls `commit`
- **THEN** the call raises, nothing partial is recorded (`list_runs` for that experiment/tool is unaffected), the run handle remains open and its staging directory intact, and calling `commit` again on the same handle succeeds — the same contract `SupabaseResultStore.commit` provides on a real upload or manifest-write failure

#### Scenario: Fake reallocates on an immediate duplicate-id collision, like Supabase

- **WHEN** a test injects a version-id collision on `FakeResultStore` (simulating another writer having already claimed the id `create_run` allocated) and calls `commit`
- **THEN** the fake reallocates to the next free id before recording the run, so the committed run lands on a distinct id from the collision and neither run's recorded outputs/hashes are overwritten — the same contract `SupabaseResultStore.commit`'s pre-upload reallocation guard provides

#### Scenario: Fake fails safely, without recording anything, when reallocation is exhausted or a collision is detected late

- **WHEN** a test injects a version-id collision on `FakeResultStore` that either (a) persists across every bounded reallocation attempt, or (b) only becomes visible after the fake's pre-record check has already passed (a "late" collision, analogous to another writer's commit landing during this commit's in-flight window)
- **THEN** the fake raises a structured failure with nothing recorded and no partial state, and the run remains retryable exactly as `SupabaseResultStore.commit` behaves on retry-exhaustion or a late/pre-write collision

#### Scenario: Fake and Supabase adapters agree on observable behaviour

- **WHEN** a shared scenario set — create→commit→get_run latest; per-artifact hash/key fill; not-found; lifecycle misuse; v2-manifest back-compat; injected commit-failure retry; the realistic (sequential-interleaving) duplicate-id reallocation — runs against both `FakeResultStore` and `SupabaseResultStore` (on a monkeypatched boundary) from one shared scenario body
- **THEN** both produce equivalent observable results on every scenario, including the failure-injection and collision cases, with assertions covering each backend's non-shared logic (version/directory namespacing, `latest` resolution, id reallocation) rather than only the shared `hash_outputs` output, and all logical storage keys use `/` separators regardless of host OS
- **AND** the exhaustion and late-collision edge cases described in the prior scenario are verified equivalently, but independently, on each backend — each adapter's own test suite proves the same "nothing recorded, safely retryable" contract without requiring a single shared harness capable of forcing both backends into that edge case identically

### Requirement: Workflows Repointed to the ResultStore Port

Existing workflows SHALL persist results through the `ResultStore` port, constructing and passing a `Provenance`, and SHALL continue to produce structurally equivalent versioned outputs after the repoint.

#### Scenario: Workflow persists via the port with equivalent structure

- **WHEN** a workflow (qc, stats, dimred, clustering, or outlier) persists its outputs through an injected `ResultStore`
- **THEN** the produced version-directory layout, uploaded object keys, and `outputs` map match the pre-repoint `AnalysisWriter` path on the same inputs, with the v3 provenance fields (`seed`/`agent`/`environment`/per-artifact maps) now additively present rather than byte-identical

#### Scenario: Version id is available before commit for output naming

- **WHEN** a workflow that names output files using the version id (e.g. dimred, clustering plots) runs through the port
- **THEN** it reads the allocated version id from the `RunHandle` before commit, producing the same version-stamped filenames as before

### Requirement: Live Supabase Persistence Smoke

A live smoke SHALL drive at least one workflow end-to-end through the real
`SupabaseResultStore` and `SupabaseReader` against the running dev stack (Supabase +
storage-api + MinIO) and assert the write-path guarantees the persistence layer provides:
a committed run lands in storage with a v3 manifest carrying resolved provenance, each
recorded content hash equals the bytes actually stored, `get_run("latest")` reads the
committed run back and advances on a second commit, and `import bloom_mcp` is clean with
no Supabase env. The smoke SHALL exit non-zero and name the failing check on any violated
guarantee, so a regression fails the job rather than passing silently.

#### Scenario: Committed run lands with a v3 manifest and resolved provenance

- **WHEN** the smoke drives a stochastic workflow (clustering/kmeans, which resolves
  `seed=42`) through the real `SupabaseResultStore` and reads the `manifest.json` back
  from storage via the real read path
- **THEN** the manifest's schema version equals 3 and its latest `VersionEntry` carries a
  non-null `seed` equal to 42, an `agent` equal to `bloom_agent`, a populated
  `environment`, and non-empty `output_sha256` and `output_keys` maps sharing one key-set

#### Scenario: Recorded hash equals the bytes actually stored

- **WHEN** the smoke downloads each object named in the latest entry's `output_keys` from
  the bucket and hashes the returned bytes
- **THEN** each `sha256(downloaded bytes)` equals the corresponding `output_sha256` value
  recorded in the manifest

#### Scenario: get_run("latest") reads back and advances on a second commit

- **WHEN** the smoke calls `get_run(experiment, tool_class, "latest")` after the first
  commit, then runs the workflow a second time
- **THEN** the first `get_run("latest")` resolves the committed run, and after the second
  run `latest` advances from `v1` to `v2`

#### Scenario: Import is clean with no Supabase env

- **WHEN** the smoke runs `import bloom_mcp` (including the Tier-2 `_ports` composition
  root that constructs adapters at module load) in a subprocess with `SUPABASE_URL` and
  `BLOOM_AGENT_KEY` removed from the environment, before configuring the live env
- **THEN** the import succeeds with no error, proving the Tier-0 lazy-validation contract
  holds for the real composition root

#### Scenario: A violated guarantee fails the smoke

- **WHEN** any asserted guarantee does not hold — for example a downloaded object's hash
  does not match the recorded `output_sha256`, the seed is null, or the workflow returns
  an error
- **THEN** the smoke routes the failure through its per-check summary and exits non-zero,
  naming the failing check, rather than passing or aborting with an unlabelled traceback

### Requirement: Persistence Smoke CI Gate

The live smoke SHALL be packaged as a single reusable `make bloommcp-smoke` target so the
local pre-merge step and the CI gate run identical assertions and cannot drift. CI SHALL
invoke `make bloommcp-smoke` only after the dev stack is up and migrated (`make dev-up`,
`make migrate-local`) — the storage-schema grants the bloommcp write path needs are
applied by `make migrate-local`. CI SHALL retain a regression-guard test asserting the
gate's presence and ordering so it cannot be silently deleted or hollowed out.

#### Scenario: CI gates the smoke via the shared target after migration

- **WHEN** the dev-stack CI job has brought the stack up and run `make migrate-local`
- **THEN** the same job runs `make bloommcp-smoke` after the migration step, and a
  persistence regression fails that job

#### Scenario: Gate presence and ordering are regression-guarded

- **WHEN** the `tests/unit/` suite parses `.github/workflows/pr-checks.yml`
- **THEN** it asserts (by step presence and relative order, never a fixed index) that a
  job runs `make migrate-local` before `make bloommcp-smoke` and retains an
  `if: always()` stack-teardown step — failing the PR if the gate is removed, reordered
  before migration, or stripped of cleanup

### Requirement: Per-Output Signed Links And Size At Commit

`ResultStore.commit(run, outputs)` SHALL return a `StoredRun` whose `output_links: dict[str,
OutputLink]` carries one entry per `outputs` entry, keyed identically, each an `OutputLink` with
the artifact's storage `key`, its `sha256` (matching `output_sha256`), and its non-negative
`size_bytes` (a legitimate zero-byte artifact is not rejected — only an empty `outputs` dict is).
Exactly one of `url`/`path` SHALL be populated, never both and never neither: for every backend
except the local backend, `url` SHALL be a signed/served URL from the active `StorageBackend`'s
`create_signed_url`, and `path` SHALL be `None`; for the local backend (`BLOOM_STORAGE_BACKEND=
local`), `commit` SHALL NOT call `create_signed_url` at all — `path` SHALL instead be the
resolved absolute filesystem path (the active `LocalStorageBackend`'s own traversal-guarded
`resolve_path(key)`, via `storage_backend.active_backend()`), and `url` SHALL be `None`. This holds for every local-backend configuration (the granular
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
  `str(storage_backend.active_backend().resolve_path(key))` for that output's key, and
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

### Requirement: Re-Signing An Already-Committed Run's Download Links

The `ResultStore` Protocol SHALL provide `get_download_links(experiment, tool_class,
run_ref="latest") -> StoredRun`, resolving a previously committed run through the same
manifest/record lookup `get_run` uses and returning it with a freshly built `output_links`
populated — unlike `list_runs`, which always returns `output_links == {}`. This is a
deliberate, caller-opted-in exception to that existing behavior, not a change to it: a caller
must call `get_download_links` by name to get signed links for a run it did not just commit
itself. This capability SHALL NOT persist anything, and SHALL NOT change the manifest,
`Provenance`, or `VersionEntry` schema in any way — every value it returns is either already
persisted (`output_sha256`, `output_keys`, `manifest_path`, `params`, `based_on_version`) or
resolved fresh at call time (`url`, `size_bytes`).

Before signing or sizing any output, `get_download_links` SHALL recompute the expected
object-key prefix fresh from `(experiment, tool_class, the resolved run's version_dir)` and
verify every persisted `output_key` falls within it, for both the `create_signed_url` and
`get_object_size` calls that follow; a key outside that prefix indicates corrupt manifest
data or a resolution bug (never a caller-input condition) and SHALL raise
`CorruptRunLinksError` rather than looking it up or signing it. This check is independent of,
and SHALL NOT depend on the merge order of, `add-bloommcp-signed-url-key-scoping`'s (#598)
analogous write-side guard on `commit`.

For a resolved run whose `output_keys` is empty (a legacy entry recorded before per-artifact
keys existed — e.g. a v2 manifest entry), `get_download_links` SHALL return `output_links == {}`
rather than raising, since there is no key to sign or size. For a resolved run with populated
`output_keys`, each output's `sha256` SHALL come from the persisted `output_sha256`, `url`
SHALL come from the active `StorageBackend`'s `create_signed_url` (the same fixed
`SIGNED_URL_EXPIRES_SECONDS` expiry `commit` already signs with — no per-call expiry
parameter), and `size_bytes` SHALL be resolved live via `StorageBackend.get_object_size` for
every output on every call — uniformly for a run committed a moment ago or long before this
capability existed, with no persisted size field of any kind. A failure to sign or size any
one output SHALL fail the whole call (propagating a clear error) rather than returning a
partially-populated `output_links` with no indication some outputs were silently skipped.

`get_run` (and therefore `get_download_links`, which calls it internally) SHALL also attach the
resolved run's own `params` (its exact recorded tool-call kwargs) and `based_on_version` to the
returned `StoredRun`, sourced from the same single `VersionEntry` the rest of the resolution
already reads — never from any other run for the same `(experiment, tool_class)`.
**`list_runs` and `commit` SHALL leave `params == {}` and `based_on_version == ""`** (their
`StoredRun`s' dataclass defaults): these two fields are populated only inside `get_run` itself,
never inside `StoredRun.from_version_entry`, specifically so `list_runs` — which backs
`list_existing_analyses`, an always-included discovery tool that returns every historical run's
`StoredRun` verbatim — never discloses one run's `params` while resolving a different one, nor
turns an always-on tool into a cross-run params leak. This SHALL hold regardless of whether
`output_keys` is populated — a run's `params`/`based_on_version` were part of the manifest
schema from the start (unlike `seed`/`agent`/`environment`, `output_sha256`/`output_keys`, all
v3-additive), so even the oldest recorded run has them.

**A signed link to the run's own `manifest.json` (a prior design of this same requirement,
`manifest_url`) SHALL NOT be provided by this method.** `manifest.json` is keyed only by
`(experiment, tool_class)` — never by `run_ref` — so a signed link to it cannot be scoped to
the single resolved run: it would expose every run ever committed for that pair, including each
one's own `params`/`source_id`/`source_name`/`based_on_version`, not just the one the caller
asked about. `params`/`based_on_version` above exist specifically to serve the same
provenance-verification need without that cross-run exposure.

`FakeResultStore` SHALL implement the same resolution, prefix guard, empty-`output_keys`
short-circuit for `output_links`, and single-run-scoped `params`/`based_on_version` attachment
in `get_run`, and SHALL produce a real (not fabricated) `size_bytes` for any run it itself
recorded via its own private, in-memory record of each output's byte size captured at commit
time (identical `hash_outputs` computation the real adapter also performs) — without making any
call to `StorageBackend`, since it never uploads real bytes for a live lookup to meaningfully
target. Because this adapter's `list_runs`/`get_run` share one in-memory list populated once at
`commit()` time (unlike the real adapter, which re-reads the manifest fresh on every call), it
SHALL keep `params`/`based_on_version` out of that shared list and instead resolve them for
`get_run` from a private, commit-time-populated side table keyed by
`(experiment, tool_class, run_ref)` — the same pattern its existing `size_bytes` bookkeeping
already uses — so `list_runs` never gains them by construction, not by convention.

An unresolvable `(experiment, tool_class, run_ref)` SHALL raise `RunNotFoundError`, identically
to `get_run`.

#### Scenario: A caller gets fresh links for a run committed in a prior session

- **WHEN** `get_download_links(experiment, tool_class, "latest")` is called for a run that was
  committed and whose signed URLs have since expired
- **THEN** it returns a `StoredRun` whose `output_links` carries a fresh, working `url` per
  output, each with the correct `sha256` and a live-resolved `size_bytes`, and whose `params`/
  `based_on_version` match that run's own recorded values

#### Scenario: An explicit run_ref resolves the same as get_run

- **WHEN** `get_download_links(experiment, tool_class, run_ref)` is called with a specific
  version id (not `"latest"`)
- **THEN** it resolves the same run `get_run(experiment, tool_class, run_ref)` would, with
  freshly signed `output_links` and that same run's `params`/`based_on_version` attached

#### Scenario: size_bytes is always resolved live, never persisted

- **WHEN** `get_download_links` resolves any run with populated `output_keys` — regardless of
  when it was committed
- **THEN** every `size_bytes` comes from a live `StorageBackend.get_object_size` call, and no
  manifest, `Provenance`, or `VersionEntry` field is read, written, or created for this
  purpose

#### Scenario: A legacy run with no recorded keys yields no output links, but still its own params

- **WHEN** `get_download_links` resolves a run whose `output_keys` is empty (e.g. a v2 manifest
  entry recorded before per-artifact keys existed)
- **THEN** it returns the resolved `StoredRun` with `output_links == {}`, without raising, and
  with that run's own `params`/`based_on_version` still populated — these fields were recorded
  regardless of manifest schema version, unlike the v3-only fields absent from a v2 entry

#### Scenario: A retired tool_class is still resolvable

- **WHEN** `get_download_links` is called with a `tool_class` that has since been retired from
  active use (e.g. `"stats"`) but still has historical runs recorded
- **THEN** it resolves and re-signs that run's links exactly as it would for an active
  tool_class

#### Scenario: Unknown run reference is reported through the contract

- **WHEN** `get_download_links(experiment, tool_class, run_ref)` is called for a reference or
  tool_class with no recorded run
- **THEN** it raises `RunNotFoundError`, identically to `get_run`

#### Scenario: A key outside the run's own scope is never signed or sized

- **WHEN** `get_download_links` resolves a run whose persisted `output_keys` includes a key (by
  test injection — no legitimate call path produces this) that does not fall under the freshly
  recomputed `(experiment, tool_class, version_dir)` prefix
- **THEN** it raises `CorruptRunLinksError` without calling `create_signed_url` or
  `get_object_size` for that key

#### Scenario: A single output's failure aborts the whole call

- **WHEN** any one output's `create_signed_url` or `get_object_size` call raises (for example,
  the object was deleted from storage after the manifest still lists it)
- **THEN** `get_download_links` raises rather than returning a partially-populated
  `output_links` for the other outputs

#### Scenario: get_run and list_runs never disclose params across runs

- **WHEN** two runs exist for the same `(experiment, tool_class)`, each committed with distinct
  `params`
- **THEN** `get_run(experiment, tool_class, run_ref=<either>)` returns only that run's own
  `params`/`based_on_version`, never the other's, and `list_runs(experiment, tool_class)`
  returns `params == {}`/`based_on_version == ""` for every entry regardless of which run each
  entry describes

#### Scenario: The fake store never calls StorageBackend

- **WHEN** `FakeResultStore.get_download_links(...)` is called for any run it has recorded
- **THEN** every `size_bytes` comes from that run's own recorded byte size (captured
  internally at commit time, identically to the real adapter's `hash_outputs` computation), and
  no call to `StorageBackend` of any kind is made

