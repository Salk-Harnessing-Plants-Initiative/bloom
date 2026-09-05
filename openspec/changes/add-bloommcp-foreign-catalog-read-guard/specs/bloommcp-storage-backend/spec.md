## ADDED Requirements

### Requirement: Foreign-Catalog Manifest Read Guard

Every manifest read through `bloom_mcp.manifest.read_manifest` SHALL compare
the resolved manifest's `storage_backend` sentinel, when present and
non-empty, against `storage_backend.active_backend_name()` (the same function
that stamps the sentinel at write time, so stamp and check cannot disagree).
`read_manifest` is the single chokepoint behind `AnalysisDir.get_version`,
`AnalysisDir.read_manifest`, and `AnalysisDir.list_versions`, and therefore
behind `get_run`, `list_runs`, `create_run`, `commit`'s allocation and
re-check reads, `get_download_links`, and the reader's cleaned-tier
resolution — so the comparison structurally covers every one of those paths
with no per-call-site opt-in. On a mismatch — a *foreign catalog*: a manifest
written by a backend other than the one now serving it — the read SHALL fail
closed by raising `ManifestBackendMismatchError` (defined beside
`ManifestSchemaError` in `bloom_mcp.manifest`) instead of returning the
manifest. The error message SHALL name both backends and the logical catalog
identity (the manifest's storage prefix, e.g. `bloommcp_output/qc_<stem>`)
and SHALL carry the remedy, and SHALL NOT contain any absolute host
filesystem path.

The sentinel comparison SHALL run only after the existing schema validation
(`validate_schema` and `Manifest.model_validate`) has accepted the manifest:
a manifest that is both schema-incompatible and foreign SHALL surface as
`ManifestSchemaError`, preserving the existing error precedence. A manifest
whose `storage_backend` field is absent, `None`, or empty (written before
manifest schema v5, or stripped) SHALL pass unguarded — failing it would
brick every pre-#572 catalog — and this limitation SHALL be documented (the
window closes when the catalog's next commit re-stamps the manifest).

Setting `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1` SHALL downgrade the failure
to a warning-level log line **per guarded read** (naming both backends and
the catalog prefix; never once-per-process) that returns the manifest — the
sanctioned path for deliberately inspecting an offline copy of another
backend's catalog. The escape hatch sanctions **reads only**: the
`ResultStore` write path (`create_run`, `commit`) SHALL reject a foreign
catalog even when the hatch is set (see `bloommcp-result-store`'s
`Foreign-Catalog Mismatch Surfaces as a Distinguishable Structured Error`),
so the hatch can never sanction extending or re-stamping a foreign catalog.
The variable's accepted values are unset, empty/whitespace (≡ unset, so the
dev-compose `${VAR:-}` passthrough pattern delivers the default), `0`, and
`1`; any other value SHALL fail fast at server startup through the same
boot-time validation as `BLOOM_STORAGE_BACKEND`, naming the offending value
and the accepted values. At guard time, only the exact value `1` enables the
hatch — an invalid value that escaped boot validation (e.g. the env mutated
mid-run) SHALL keep the guard fail-closed, never silently enable it. The
variable SHALL be read lazily at guard or validation time on every call
(never at import, and never memoized — unlike the backend object itself),
preserving the package's side-effect-free import contract and the per-read
warning semantics.

This guard is a self-consistency check, not full mixing detection: two
physically disjoint catalogs each remain self-consistent, so a backend flip
A → B → A still resolves A's own (possibly stale) `latest` with no mismatch —
that residual SHALL remain documented as locally undetectable (per #395/#572),
alongside what the guard does catch (copied, restored, synced, or
shared-root catalogs, and sentinel tampering).

#### Scenario: A foreign catalog fails closed

- **WHEN** `read_manifest` resolves a manifest whose `storage_backend`
  sentinel names a backend other than `active_backend_name()` and
  `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST` is unset (or `0`)
- **THEN** the read raises `ManifestBackendMismatchError` naming both the
  recorded and the active backend and the catalog's storage prefix, does not
  return the manifest, and leaks no absolute host filesystem path

#### Scenario: A matching sentinel reads as before

- **WHEN** `read_manifest` resolves a manifest whose sentinel equals the
  active backend's name (with or without the escape hatch set)
- **THEN** the manifest is returned exactly as before this change, and no new
  log record is emitted at any level

#### Scenario: A manifest with no usable sentinel passes

- **WHEN** `read_manifest` resolves a manifest whose `storage_backend` field
  is absent, `None`, or empty (e.g. written before schema v5)
- **THEN** the manifest is returned and no error is raised — the guard
  protects only sentinel-carrying catalogs, a documented limitation

#### Scenario: Schema validation takes precedence over the guard

- **WHEN** `read_manifest` reads a manifest that is both schema-incompatible
  (e.g. a newer `manifest_schema_version`) and foreign
- **THEN** it raises `ManifestSchemaError`, exactly as before this change —
  the sentinel comparison runs only on a schema-valid manifest

#### Scenario: The escape hatch downgrades to a warning on every read

- **WHEN** `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1` and `read_manifest`
  resolves the same foreign catalog twice in one process
- **THEN** both reads return the manifest and each emits its own
  warning-level log record naming the recorded backend, the active backend,
  and the catalog prefix — per guarded read, never once-per-process (the
  one-time-signal failure mode of #572's fresh-catalog log is exactly what
  this change exists to avoid)

#### Scenario: An empty value behaves as unset

- **WHEN** `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST` is set to `""` or
  whitespace (as the dev-compose `${VAR:-}` interpolation delivers when a
  developer has not opted in) and a foreign catalog is read
- **THEN** boot validation passes and the guard stays active (the read
  raises), identical to the variable being unset

#### Scenario: An invalid escape-hatch value fails fast at startup

- **WHEN** `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST` is set to an unrecognized
  value (e.g. `yes`) and the server runs its boot-time validation
- **THEN** validation raises a clear error naming the offending value and the
  accepted values (unset/empty, `0`, `1`), rather than the guard misreading
  the intent mid-run

#### Scenario: An invalid value at guard time keeps the guard closed

- **WHEN** the env var holds an unrecognized value (e.g. `yes`) at
  manifest-read time — it escaped boot validation because the environment
  was mutated after startup — and a foreign catalog is read
- **THEN** the guard stays fail-closed (the read raises); only the exact
  value `1` enables the hatch

#### Scenario: Import stays side-effect-free even with an invalid value set

- **WHEN** `import bloom_mcp.server` runs in a fresh interpreter with
  `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=yes` (an invalid value) in the
  environment and no other bloom env set
- **THEN** the import succeeds — the variable is read only at manifest-read
  or boot-validation time, so an invalid value cannot crash import (the only
  observable proof the read is lazy rather than import-time-with-default)

#### Scenario: The guard and its limits are documented together

- **WHEN** a developer reads the storage docs after this change
- **THEN** they learn what the guard rejects (a catalog served by a backend
  that did not write it), the `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1` escape
  hatch (reads only, with its per-read warning trail, and that in
  containerized deployments the variable reaches the process only where
  compose passes it through — dev does; staging/prod deliberately do not),
  that pre-v5 manifests pass unguarded, and that an A → B → A flip across
  disjoint catalogs remains undetectable locally (#395/#572 non-goal,
  unchanged)

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
on the return trip, even though history diverged in between. The stamped sentinel is
additionally enforced at read time by the `Foreign-Catalog Manifest Read Guard` requirement
(this capability), which fails a read whose manifest names a different backend than the
active one.

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
  (`supabase` or `local`), read from `storage_backend.active_backend_name()` at write time —
  derived from the backend object `active_backend()` actually resolved, not an independent
  env re-read (the deployed behavior since PR #572's review) — so inspecting either store's
  `manifest.json` directly identifies which backend produced it, without needing to know
  which backend is currently configured

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
