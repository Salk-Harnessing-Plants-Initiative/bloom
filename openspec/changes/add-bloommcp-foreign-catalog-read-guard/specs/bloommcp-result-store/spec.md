## ADDED Requirements

### Requirement: Foreign-Catalog Mismatch Surfaces as a Distinguishable Structured Error

The `SupabaseResultStore` adapter SHALL, when the underlying manifest read
raises `ManifestBackendMismatchError` (the manifest's `storage_backend`
sentinel names a backend other than the active one — see
`bloommcp-storage-backend`'s `Foreign-Catalog Manifest Read Guard`) during
`create_run`, `list_runs`, `get_run`, or `commit`, catch it at that call
site, log it server-side, and raise `CatalogBackendMismatchError` — a subclass of
`ManifestReadError`, exactly mirroring how `ManifestSchemaError` maps to
`ManifestIncompatibleError` — so every existing `except ManifestReadError` /
`except ResultStoreError` / `except Exception` handler (and every consumer
tool's existing `errors=(…, ManifestReadError)` declaration) still catches
it, while a caller that needs to distinguish "storage flaked" from "this
catalog was written by a different backend" can `isinstance()`-check for the
narrower type. The raised error's message SHALL name both the recorded and
the active backend and SHALL NOT leak host paths or URLs.

The write path SHALL reject a foreign catalog **regardless of the
`BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST` escape hatch** (which sanctions reads
only): `create_run` and `commit` SHALL verify the sentinel of the manifest
they read — through the same shared `foreign_sentinel` predicate the read
guard uses — even when the manifest-layer guard was downgraded to a warning,
so a foreign catalog is never extended and never re-stamped — the silent
take-over an unguarded `write_manifest` overwrite would perform. A
`create_run` against a foreign catalog SHALL fail before any staging or
upload happens. On the commit path the guarantee is two checks with
different windows, stated honestly: the allocation-time check fails before
any object is written; the pre-write re-check — whose purpose is a catalog
that turned foreign mid-commit — fires after uploads, in which case the
already-uploaded objects are best-effort deleted (the same cleanup an upload
failure takes) and the manifest is never advanced or re-stamped in either
case. The commit-path failure SHALL surface as `CatalogBackendMismatchError`
itself — mirroring the existing do-not-retry `KeyScopeGuardError` handling,
not the generic `CommitFailedError` whose message suggests a transient,
retryable condition — because a foreign catalog is a permanent state that a
retry cannot fix; accordingly the staging directory SHALL be torn down (a
handle has no `__del__` to reclaim it, and no retry can ever use it), and the
refusal SHALL log a single warning without a traceback (the structured type
fully describes a configuration condition; discovery paths sweep many tool
classes per call).

Additionally, once a foreign catalog has been **served** in this process
(under the escape hatch — see `bloom_mcp.manifest.foreign_read_served`),
`create_run` and `commit` SHALL refuse **every** subsequent write, including
into a different, native catalog, surfacing as `CatalogBackendMismatchError`
with a restart remedy: provenance has no field recording an input's storage
backend, so a commit derived from foreign-read data would otherwise land
with clean provenance (and `remove_outliers` would record a
`based_on_version` that exists only in the foreign catalog — an
affirmatively false lineage pointer). An inspection process is read-only
from the first foreign read on.

A commit onto a catalog whose sentinel is absent or empty (pre-#572) SHALL
proceed — the pass-through documented in the guard requirement — and SHALL
log, at info level, that a previously unstamped catalog is being adopted and
stamped for the active backend, since the fresh-catalog log never fires for
an existing manifest and the adoption would otherwise be forensically
invisible.

`FakeResultStore` is exempt: it never constructs a real `Manifest` and has no
backend concept (per #572's design), so this failure mode cannot be
represented in the fake or in the shared parity scenario set. That exemption
SHALL be recorded where the parity suite defines its shared scenarios, and
the guard's adapter-level behavior SHALL instead be proven against the real
manifest path (the local backend on a temp root and/or the
`_FakeSbStorageClient` harness, which run real backend dispatch, with the
foreign sentinel produced by hand-patching the stored manifest — a
flip-and-read across the physically disjoint stores can never produce one).

#### Scenario: Every read call site raises the distinguishable error

- **WHEN** `get_run(experiment, tool_class, "latest")`, `list_runs`, or
  `create_run` resolves a manifest whose `storage_backend` sentinel names a
  backend other than the active one (escape hatch unset)
- **THEN** the adapter raises `CatalogBackendMismatchError` whose message
  names both backends — an `isinstance()` check distinguishes it from a
  generic `ManifestReadError`, while existing `except ManifestReadError`
  handlers still catch it — and no host path or URL is leaked

#### Scenario: create_run against a foreign catalog fails before any write

- **WHEN** `create_run` is called for an (experiment, tool_class) pair whose
  existing manifest is foreign — with or without the escape hatch set
- **THEN** the call raises `CatalogBackendMismatchError` before any staging
  directory is handed out for upload and before any object or manifest write,
  so nothing is recorded against the foreign catalog

#### Scenario: A commit never re-stamps a foreign catalog

- **WHEN** a commit's allocation-time manifest read resolves a foreign
  catalog — with or without the escape hatch set
- **THEN** the commit raises `CatalogBackendMismatchError` (not a
  `CommitFailedError` claiming a transient, retryable condition), no object
  is uploaded and no version entry is appended, `write_manifest` is never
  reached — the foreign catalog's sentinel is not overwritten with the
  active backend's name — and the staging directory is torn down

#### Scenario: A catalog that turns foreign mid-upload is cleaned up, never written

- **WHEN** the catalog's sentinel turns foreign between commit's allocation
  check and its pre-write re-check (e.g. injected as a side effect of an
  upload) — with or without the escape hatch set
- **THEN** the commit raises `CatalogBackendMismatchError`, the objects
  uploaded during that window are best-effort deleted, the manifest is
  neither advanced nor re-stamped, and the staging directory is torn down —
  removing the pre-write re-check must fail this scenario

#### Scenario: After a foreign read, the whole process is read-only

- **WHEN** a foreign catalog has been served under
  `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1` in this process, and `create_run`
  or `commit` is then called for a **different, native** catalog
- **THEN** the call raises `CatalogBackendMismatchError` naming the foreign
  read and a restart remedy, before any staging or upload for a `create_run`
  (and with the staging directory torn down for a `commit` on an
  already-open handle) — foreign-derived outputs never land with clean
  provenance

#### Scenario: Adopting an unstamped catalog is logged

- **WHEN** a commit appends to an existing manifest whose sentinel is absent
  or empty (pre-#572)
- **THEN** the commit succeeds and stamps the catalog for the active backend,
  and an info-level log names the adoption — the previously invisible case
  the guard's pass-through permits

#### Scenario: The fake's exemption is explicit, not silent

- **WHEN** the shared Fake/Supabase parity scenario set is inspected
- **THEN** the foreign-catalog mismatch case is recorded as exempt for
  `FakeResultStore` (no manifest, no backend concept), with the adapter-level
  coverage living in real-manifest-path tests instead — so the gap is a
  documented boundary, not missing coverage

## MODIFIED Requirements

### Requirement: FakeResultStore Adapter

The system SHALL provide an in-memory `FakeResultStore` adapter implementing `ResultStore`, behaviourally equivalent to `SupabaseResultStore` for observable outcomes — including its commit-failure and duplicate-version-id failure semantics, not only its happy path — so the full write path, including failure handling, is testable with no live Supabase. One carve-out: the foreign-catalog backend-mismatch failure mode (`CatalogBackendMismatchError`) is exempt from this equivalence — the fake constructs no real `Manifest` and has no backend concept, so that behavior is covered by real-manifest-path tests instead (see `Foreign-Catalog Mismatch Surfaces as a Distinguishable Structured Error`).

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
