## ADDED Requirements

### Requirement: Cleaned-Version Resolution Rejects a Foreign Catalog

The reader's cleaned-tier resolution SHALL treat a
`ManifestBackendMismatchError` raised by the manifest read as a **hard
error** carrying a message that names both the recorded and the active
backend. This covers `_resolve_versioned_cleaned` / `_resolve_one_class` —
the path behind `load_experiment` version selection and `require_clean=True`
— handled explicitly, alongside the existing
explicit `ManifestSchemaError` handling, not left to a generic catch-all
wrapper. It SHALL NOT be treated as a soft miss: resolution SHALL NOT fall
through to a lower-priority cleaned tool class, the legacy un-versioned
cleaned CSV, or the raw input, any of which would silently substitute a
different dataset — the exact silent-revert hazard this resolution path
exists to prevent.

In particular, under `require_clean=True` the mismatch SHALL NOT be demoted to
the "no cleaned version exists — run `qc_clean` first" condition
(`CleanedVersionRequiredError`): that remedy would direct an agent to commit
fresh runs on top of the foreign catalog. The consumer tool boundary
(`@as_mcp_tool`) SHALL surface the mismatch as a structured `BloomMCPError`
(never `internal_error`'s opaque fixed message) whose message names both
backends, so the caller learns the actual condition and remedy.

`FakeReader` is exempt from representing this failure mode (it has no
manifest or backend concept); the exemption SHALL be recorded with the
reader parity scenarios, and coverage SHALL live in tests that drive the real
resolution path over a real manifest (e.g. the local backend on a temp root).

#### Scenario: A foreign catalog is a hard resolution error, never a fall-through

- **WHEN** cleaned-version resolution for the highest-priority tool class hits
  a `ManifestBackendMismatchError` (the catalog was written by a different
  backend than is now active, escape hatch unset)
- **THEN** resolution reports a hard error naming both backends and does not
  fall through to a lower-priority tool class, the legacy cleaned CSV, or the
  raw input

#### Scenario: require_clean surfaces the mismatch, not a run-qc_clean remedy

- **WHEN** `load_experiment(name, require_clean=True)` is called and the
  experiment's cleaned catalog is foreign
- **THEN** the reader signals the backend mismatch (naming both backends)
  rather than `CleanedVersionRequiredError` — the caller is not told to run
  `qc_clean` first, which would invite committing new runs against the
  foreign catalog

#### Scenario: A consumer tool surfaces the mismatch as a structured envelope

- **WHEN** a consumer tool that loads with `require_clean=True` (e.g.
  `pca_analysis`) is invoked over an experiment whose cleaned catalog is
  foreign
- **THEN** the tool returns a structured `BloomMCPError` whose message names
  the recorded and the active backend (not `internal_error`'s opaque fixed
  message, and not the run-`qc_clean`-first remedy), and no run is persisted

#### Scenario: The escape hatch restores resolution with a warning trail

- **WHEN** `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1` and the same foreign
  catalog is resolved via `require_clean=True`
- **THEN** resolution proceeds as before the guard existed (the cleaned
  version is returned) and each guarded manifest read leaves the
  warning-level log line defined by `bloommcp-storage-backend`'s guard
  requirement
