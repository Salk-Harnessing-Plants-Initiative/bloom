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
the artifact's storage `key`, a signed/served `url` from the active `StorageBackend`'s
`create_signed_url`, its `sha256` (matching `output_sha256`), and its non-negative `size_bytes`
(a legitimate zero-byte artifact is not rejected — only an empty `outputs` dict is). This field
SHALL be populated only by `commit` — `get_run` and `list_runs` SHALL return `output_links` as an
empty dict (including when the resolved run was recorded before this capability existed, e.g. a
legacy v2 manifest entry with no `output_sha256`/`output_keys`), so that resolving or listing
potentially many historical runs never eagerly generates signed URLs for artifacts other than the
one a caller's own `commit` call just produced. A failure to generate or extract a usable signed
URL for any output — including a signing-client response that carries none of its expected URL
keys — SHALL fail the whole `commit` call (surfacing as `CommitFailedError`, following the same
best-effort-cleanup path an upload failure already takes) rather than committing with a partial or
`None` URL. None of `output_links` SHALL be persisted into the manifest `VersionEntry` — it is
computed at request time from data already in hand (the freshly hashed staged bytes, the freshly
uploaded key) and a fresh signing call, so existing manifest/provenance fields and cross-backend
manifest-byte-identity are unaffected.

Before signing any output, `commit` SHALL verify that every key it is about to sign falls within
the prefix `commit` itself computed for this run (`{output_root}/{tool_class}_{stem}/
{version_dir}/`) — the same prefix its own `key_for` closure used to build every `output_keys`
entry and to upload the corresponding bytes moments earlier. A key outside that prefix indicates a
structural bug (never a caller-input condition, since `outputs` names only relative paths within
the run's own staging directory) and SHALL fail the whole `commit` call via the same
`CommitFailedError` fail-closed/cleanup path a signing failure already takes — never a bare
signed URL for an unverified key. This guarantee SHALL hold identically for `FakeResultStore`,
which SHALL compute and check the equivalent prefix from its own `key_for` construction, so a test
against the fake exercises the same structural guarantee the real adapter provides.

#### Scenario: Commit returns a signed link per output

- **WHEN** a consumer writes outputs into the run's staging directory and calls
  `commit(run, outputs)`
- **THEN** the returned `StoredRun.output_links` has one entry per `outputs` entry, each
  carrying a non-empty `url`, the same `sha256` as `output_sha256` for that name, and a
  non-negative `size_bytes`

#### Scenario: get_run and list_runs do not carry signed links

- **WHEN** `get_run(experiment, tool_class, run_ref)` or `list_runs(experiment, tool_class)` is
  called for a previously committed run — including a legacy run recorded before this
  capability existed (e.g. a v2 manifest entry with no `output_sha256`/`output_keys`)
- **THEN** the returned `StoredRun`(s) have `output_links == {}`, regardless of how many
  historical versions or outputs exist

#### Scenario: A signing failure fails the whole commit

- **WHEN** the active backend's `create_signed_url` raises, or returns a response with no
  extractable URL, for any one output during `commit`
- **THEN** `commit` raises `CommitFailedError`, best-effort cleans up any objects already
  uploaded for this call, and records no new version — mirroring an upload failure

#### Scenario: The fake store returns a shape-equivalent link without touching a real backend

- **WHEN** `FakeResultStore.commit(...)` is called
- **THEN** the returned `StoredRun.output_links` has the same keys, `sha256`, and `size_bytes` a
  real commit would produce, with a synthesized (non-network) URL — no call to
  `storage_backend.active_backend()` is made

#### Scenario: Manifest bytes are unaffected

- **WHEN** a run commits and `output_links` is populated on the returned `StoredRun`
- **THEN** the written `manifest.json`'s `VersionEntry` for this run contains no `output_links`,
  URL, or size key, and every other field matches the same commit's pre-change golden/fixture
  manifest byte-for-byte (no schema version change)

#### Scenario: A key outside this run's own prefix is never signed

- **WHEN** `commit` is (by test injection — no legitimate call path produces this) about to sign a
  key that does not start with this run's own `{output_root}/{tool_class}_{stem}/{version_dir}/`
  prefix
- **THEN** `commit` raises (never calling `create_signed_url` for that key), the failure surfaces
  as `CommitFailedError` via the same fail-closed/cleanup path a signing failure already takes,
  and no version is recorded

#### Scenario: Every real call site's keys satisfy the scoping check

- **WHEN** any of the 8 consumer tools (`qc_clean`, `qc_inspect`, `pca_analysis`,
  `remove_outliers`, `descriptive_stats`, `cross_experiment_correlations`, `umap_analysis`,
  `clustering`) commits a run through either `SupabaseResultStore` or `FakeResultStore`
- **THEN** the scoping check passes for every output with no behavior change from before this
  requirement — the existing test suite for each tool requires no modification

