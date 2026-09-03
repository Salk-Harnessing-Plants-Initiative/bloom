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
`except ResultStoreError` / `except Exception` handler still catches it, while
a caller that needs to distinguish "storage flaked" from "this catalog was
written by a different backend" can `isinstance()`-check for the narrower
type. The raised error's message SHALL name both the recorded and the active
backend and SHALL NOT leak host paths or URLs.

Because the guard fires at the manifest read, a `create_run` against a foreign
catalog SHALL fail before any staging or upload happens, and a `commit` whose
manifest read resolves a foreign catalog SHALL fail without writing any object
or manifest — in particular it SHALL NOT re-stamp (and thereby silently take
ownership of) the foreign catalog, which is what an unguarded
`write_manifest` overwrite would do.

`FakeResultStore` is exempt: it never constructs a real `Manifest` and has no
backend concept (per #572's design), so this failure mode cannot be
represented in the fake or in the shared parity scenario set. That exemption
SHALL be recorded where the parity suite defines its shared scenarios, and the
guard's adapter-level behavior SHALL instead be proven against the real
manifest path (the local backend on a temp root and/or the
`_FakeSbStorageClient` harness, which run real backend dispatch).

#### Scenario: get_run("latest") over a foreign catalog raises the distinguishable error

- **WHEN** `get_run(experiment, tool_class, "latest")` resolves a manifest
  whose `storage_backend` sentinel names a backend other than the active one
  (escape hatch unset)
- **THEN** the adapter raises `CatalogBackendMismatchError` whose message
  names both backends — an `isinstance()` check distinguishes it from a
  generic `ManifestReadError`, while existing `except ManifestReadError`
  handlers still catch it — and no host path or URL is leaked

#### Scenario: create_run against a foreign catalog fails before any write

- **WHEN** `create_run` is called for an (experiment, tool_class) pair whose
  existing manifest is foreign
- **THEN** the call raises `CatalogBackendMismatchError` before any staging
  directory is handed out for upload and before any object or manifest write,
  so nothing is recorded against the foreign catalog

#### Scenario: A commit never re-stamps a foreign catalog

- **WHEN** a commit's own manifest read (allocation or pre-write re-check)
  resolves a foreign catalog
- **THEN** the commit fails structurally — no version entry is appended and
  `write_manifest` is never reached, so the foreign catalog's sentinel is not
  overwritten with the active backend's name — and the failure surfaces
  through the adapter's existing hardened commit error path rather than as a
  raw traceback

#### Scenario: The fake's exemption is explicit, not silent

- **WHEN** the shared Fake/Supabase parity scenario set is inspected
- **THEN** the foreign-catalog mismatch case is recorded as exempt for
  `FakeResultStore` (no manifest, no backend concept), with the adapter-level
  coverage living in real-manifest-path tests instead — so the gap is a
  documented boundary, not missing coverage
