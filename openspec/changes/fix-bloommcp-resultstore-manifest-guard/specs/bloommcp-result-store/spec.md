## MODIFIED Requirements

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

#### Scenario: A transient manifest read failure during create_run, list_runs, or get_run surfaces a structured error

- **WHEN** the underlying manifest read (`AnalysisDir.read_manifest`/`list_versions`/`get_version`) raises a generic storage/network exception during `create_run`, `list_runs`, or `get_run`
- **THEN** the adapter catches it at that call site, logs the original exception server-side (no host path/URL leak — the raised error's own message is exc-free), and raises a `ManifestReadError` instead of letting the raw exception escape; this guard is independent per call site and does not depend on `commit()`'s own hardened try/except or on any particular caller's error handling

#### Scenario: A schema-incompatible manifest during create_run, list_runs, or get_run surfaces a distinguishable structured error

- **WHEN** the underlying manifest read raises `ManifestSchemaError` (the manifest's schema version is newer than this server understands) during `create_run`, `list_runs`, or `get_run`
- **THEN** the adapter catches it at that call site, logs it server-side, and raises `ManifestIncompatibleError` — a subclass of `ManifestReadError`, so every existing `except ManifestReadError`/`except ResultStoreError`/`except Exception` still catches it, while a caller that needs to distinguish "storage flaked" from "manifest schema unsupported" can `isinstance()`-check for the narrower type

### Requirement: FakeResultStore Adapter

The system SHALL provide an in-memory `FakeResultStore` adapter implementing `ResultStore`, behaviourally equivalent to `SupabaseResultStore` for observable outcomes — including its commit-failure, duplicate-version-id, and manifest-read-failure semantics, not only its happy path — so the full write path, including failure handling, is testable with no live Supabase.

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

#### Scenario: Fake simulates a manifest read failure via an explicit injection hook

- **WHEN** a test calls `fail_next_read(experiment, tool_class)` on `FakeResultStore` and then calls `create_run`, `list_runs`, or `get_run` for that same `(experiment, tool_class)`
- **THEN** that one call raises `ManifestReadError` (mirroring `SupabaseResultStore`'s guard for a real manifest-read failure) and the hook clears itself, so a subsequent call for the same key succeeds normally — since the fake's flat in-memory store has no real read to fail organically, this is its only way to exercise the same failure mode

#### Scenario: Fake and Supabase adapters agree on observable behaviour

- **WHEN** a shared scenario set — create→commit→get_run latest; per-artifact hash/key fill; not-found; lifecycle misuse; v2-manifest back-compat; injected commit-failure retry; the realistic (sequential-interleaving) duplicate-id reallocation; injected manifest-read failure — runs against both `FakeResultStore` and `SupabaseResultStore` (on a monkeypatched boundary) from one shared scenario body
- **THEN** both produce equivalent observable results on every scenario, including the failure-injection and collision cases, with assertions covering each backend's non-shared logic (version/directory namespacing, `latest` resolution, id reallocation) rather than only the shared `hash_outputs` output, and all logical storage keys use `/` separators regardless of host OS
- **AND** the exhaustion and late-collision edge cases described in the prior scenario are verified equivalently, but independently, on each backend — each adapter's own test suite proves the same "nothing recorded, safely retryable" contract without requiring a single shared harness capable of forcing both backends into that edge case identically
