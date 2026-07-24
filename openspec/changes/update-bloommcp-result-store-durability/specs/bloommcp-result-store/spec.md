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

#### Scenario: Commit failure cleans up local staging and orphaned remote objects

- **WHEN** an artifact upload or the manifest write raises mid-commit
- **THEN** the adapter surfaces a structured error (no traceback leak), cleans up the local staging directory, does not leave the manifest advanced to a partially-written version, and best-effort deletes every output object it had already uploaded for this run before raising

#### Scenario: A cleanup failure never masks the original commit error

- **WHEN** the best-effort delete of already-uploaded objects itself fails (e.g. a second network error) during the cleanup described above
- **THEN** the adapter still raises the original `CommitFailedError` (not the delete failure), and the delete failure is logged server-side only

## ADDED Requirements

### Requirement: Manifest Write Guards Against Duplicate Version IDs

The `SupabaseResultStore` adapter SHALL guard `commit` against the single-writer/no-CAS limitation inherited from `AnalysisWriter` by finalizing `version_id`, `version_dir`, and every derived object key *together, before any upload*: it SHALL re-read the manifest fresh at the start of `commit` and, while the current `version_id` already has an entry, re-allocate the next free id (and its corresponding `version_dir`) against that fresh read, bounded, before uploading anything. Immediately before writing the manifest it SHALL re-read once more; if the finalized `version_id` has since acquired an entry, it SHALL treat this as an ordinary commit failure (best-effort cleanup of what this attempt uploaded, then `CommitFailedError`) rather than overwrite or relabel an existing entry. This closes the duplicate-id / mismatched-provenance failure mode for sequential interleaved commits (the realistic shape under bloom-mcp's single-process topology); it does not provide storage-level atomic compare-and-swap for genuinely simultaneous multi-instance writers — that residual race window (and its trigger for a future fix) is documented, not silently relied upon.

#### Scenario: Interleaved commits never produce a duplicate version id or mismatched provenance

- **WHEN** two `create_run` → `commit` cycles interleave such that both allocate the same `version_id` from the same pre-collision manifest state, and the two `commit()` calls run sequentially (the first's manifest write completes before the second's upload begins)
- **THEN** both commits succeed, the manifest ends up with two distinct version entries, and each entry's `id`, `version_dir`, and `output_keys`/`output_sha256` are mutually consistent with each other and with the bytes actually stored for that run — neither run's uploaded objects are overwritten by the other

#### Scenario: Non-colliding commits are unaffected

- **WHEN** a single `create_run` → `commit` cycle runs with no other writer interleaved
- **THEN** it commits on the first attempt with no id reallocation and no observable behavior change from before this guard existed

#### Scenario: Retry exhaustion before any upload fails safely

- **WHEN** the pre-upload collision check collides on every reallocation attempt up to the bounded limit
- **THEN** the adapter raises `CommitFailedError` (no raw traceback leak) before uploading anything, rather than looping unbounded, uploading under a still-colliding path, or silently overwriting an existing entry

#### Scenario: A collision detected just before the manifest write is treated as a retryable failure

- **WHEN** the pre-write check finds that the finalized `version_id` acquired an entry during this commit's upload window
- **THEN** the adapter best-effort deletes the objects this attempt uploaded and raises `CommitFailedError`, and a subsequent retry on the same `RunHandle` succeeds by allocating a fresh, genuinely free id
