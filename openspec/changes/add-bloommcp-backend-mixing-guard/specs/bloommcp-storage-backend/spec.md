## MODIFIED Requirements

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
