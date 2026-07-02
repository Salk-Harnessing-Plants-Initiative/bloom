## ADDED Requirements

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

### Requirement: FakeResultStore Adapter

The system SHALL provide an in-memory `FakeResultStore` adapter implementing `ResultStore`, behaviourally equivalent to `SupabaseResultStore` for observable outcomes, so the full write path is testable with no live Supabase.

#### Scenario: In-memory create and commit without Supabase

- **WHEN** a test calls `create_run` then `commit` on `FakeResultStore`
- **THEN** it records a versioned run with provenance and artifact links retrievable via `list_runs`/`get_run`, with no network or Supabase access

#### Scenario: Fake and Supabase adapters agree on observable behaviour

- **WHEN** the same scenario set (create→commit→get_run latest; per-artifact hash/key fill; not-found; lifecycle misuse) runs against both `FakeResultStore` and `SupabaseResultStore` (on a monkeypatched boundary)
- **THEN** both produce equivalent observable results, and all logical storage keys use `/` separators regardless of host OS

### Requirement: Workflows Repointed to the ResultStore Port

Existing workflows SHALL persist results through the `ResultStore` port, constructing and passing a `Provenance`, and SHALL continue to produce structurally equivalent versioned outputs after the repoint.

#### Scenario: Workflow persists via the port with equivalent structure

- **WHEN** a workflow (qc, stats, dimred, clustering, or outlier) persists its outputs through an injected `ResultStore`
- **THEN** the produced version-directory layout, uploaded object keys, and `outputs` map match the pre-repoint `AnalysisWriter` path on the same inputs, with the v3 provenance fields (`seed`/`agent`/`environment`/per-artifact maps) now additively present rather than byte-identical

#### Scenario: Version id is available before commit for output naming

- **WHEN** a workflow that names output files using the version id (e.g. dimred, clustering plots) runs through the port
- **THEN** it reads the allocated version id from the `RunHandle` before commit, producing the same version-stamped filenames as before
