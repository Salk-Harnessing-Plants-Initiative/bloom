## ADDED Requirements

### Requirement: Cleaned-Version Resolution Rejects a Foreign Catalog

The reader's cleaned-tier resolution SHALL treat a
`ManifestBackendMismatchError` raised by the manifest read as a **hard
error** that reaches the caller as a typed exception naming both the recorded
and the active backend. Concretely: the resolution helpers
(`_resolve_one_class` / `_resolve_versioned_cleaned`, behind
`load_experiment` version selection and `require_clean=True`) SHALL let
`ManifestBackendMismatchError` propagate (excluded from their generic
catch-and-stringify handling, alongside the existing explicit
`ManifestSchemaError` branch), and **both** reader adapters (`LocalReader`
and `SupabaseReader`) SHALL surface it as `ForeignCatalogError` — a new
subclass of `ExperimentReadError` in the reader-port taxonomy
(`data_access/ports.py`) — rather than discarding the resolution error and
demoting it to their generic conditions, as both do today for resolution
failures. Because every consumer tool already declares
`errors=(ExperimentReadError, …)`, the mismatch then passes through the
`@as_mcp_tool` envelope as a message-preserving structured error with no
per-tool changes.

The mismatch SHALL NOT be treated as a soft miss: resolution SHALL NOT fall
through to a lower-priority cleaned tool class, the legacy un-versioned
cleaned CSV, or the raw input, any of which would silently substitute a
different dataset — the exact silent-revert hazard this resolution path
exists to prevent. In particular it SHALL NOT surface as
`CleanedVersionRequiredError` (whose "run `qc_clean` first" remedy would
direct an agent to commit fresh runs on top of the foreign catalog) nor as
`ExperimentNotFoundError` (which misreports a present-but-foreign catalog as
absent).

`FakeReader` is exempt from representing this failure mode (it has no
manifest or backend concept); the exemption SHALL be recorded with the
reader parity scenarios, and coverage SHALL live in tests that drive both
real reader adapters over a real manifest (e.g. the local backend on a temp
root, with the foreign sentinel produced by hand-patching the stored
manifest).

#### Scenario: A foreign catalog is a hard resolution error, never a fall-through

- **WHEN** cleaned-version resolution for the highest-priority tool class hits
  a `ManifestBackendMismatchError` (the catalog was written by a different
  backend than is now active, escape hatch unset) while a lower-priority
  cleaned class, a legacy cleaned CSV, and a raw input all exist
- **THEN** resolution raises rather than serving any of them — no
  lower-priority tool class, legacy CSV, or raw-input fall-through

#### Scenario: require_clean surfaces the mismatch as ForeignCatalogError in both readers

- **WHEN** `load_experiment(name, require_clean=True)` is called on
  `LocalReader` or on `SupabaseReader` and the experiment's cleaned catalog
  is foreign
- **THEN** the reader raises `ForeignCatalogError` (an
  `ExperimentReadError`) naming both backends — not
  `CleanedVersionRequiredError`'s "run `qc_clean` first" and not
  `ExperimentNotFoundError`'s "not found", the two demotions each adapter's
  error routing performs today for discarded resolution errors

#### Scenario: A consumer tool surfaces the mismatch as a structured envelope

- **WHEN** a consumer tool that loads with `require_clean=True` (e.g.
  `pca_analysis`) is invoked over an experiment whose cleaned catalog is
  foreign
- **THEN** the tool returns a structured `BloomMCPError` whose message names
  the recorded and the active backend (not `internal_error`'s opaque fixed
  message, and not the run-`qc_clean`-first remedy), and no run is persisted

#### Scenario: A foreign tool-class catalog does not hide an experiment's other analyses from listing

- **WHEN** `list_existing_analyses` runs for an experiment whose `outliers`
  catalog is foreign while its `qc` catalog is healthy
- **THEN** the listing still returns the healthy tool class's versions, and
  the foreign class contributes a per-tool-class error entry naming both
  backends — the existing per-tool-class error isolation, pinned by a
  characterization test so this error type can never abort the whole listing

#### Scenario: The escape hatch restores resolution with a warning trail

- **WHEN** `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1` and the same foreign
  catalog is resolved via `require_clean=True`
- **THEN** resolution proceeds as before the guard existed (the cleaned
  version is returned) and each guarded manifest read leaves the
  warning-level log line defined by `bloommcp-storage-backend`'s guard
  requirement

## MODIFIED Requirements

### Requirement: FakeReader Adapter

The system SHALL provide an in-memory `FakeReader` adapter implementing `ExperimentReader`, behaviourally equivalent to `SupabaseReader` for observable outcomes, so the full read path is testable with no live Supabase. `FakeReader` SHALL also expose a test-only failure-injection hook so a mid-read storage failure — the one hazard class `SupabaseReader`'s multi-step cleaned-tier resolution has that a flat in-memory lookup cannot otherwise represent — is exercisable without a live Supabase adapter. One carve-out: the foreign-catalog backend-mismatch failure mode (`ForeignCatalogError`) is exempt from this equivalence — the fake has no manifest or backend concept, so that behavior is covered by real-manifest-path tests against both real adapters instead (see `Cleaned-Version Resolution Rejects a Foreign Catalog`).

#### Scenario: In-memory experiment loads without Supabase

- **WHEN** a test seeds `FakeReader` with a fixture experiment and calls `load_experiment(name)`
- **THEN** it returns the expected frame and declared roles with no network or Supabase access

#### Scenario: Fake and Supabase adapters agree on observable behaviour

- **WHEN** the same scenario set (load, version selection, not-found, empty list, a mid-read storage failure) runs against both `FakeReader` and `SupabaseReader` (on a monkeypatched boundary)
- **THEN** both produce equivalent observable results — return shapes, source labels, not-found signalling, and failure signalling all match

#### Scenario: Fake simulates a mid-load storage failure

- **WHEN** a test calls `FakeReader.fail_next_load(name, version=...)` and then `load_experiment(name, version=...)`
- **THEN** that call raises `ExperimentReadError`, then the hook clears itself so a subsequent call for the same `(name, version)` resolves normally
