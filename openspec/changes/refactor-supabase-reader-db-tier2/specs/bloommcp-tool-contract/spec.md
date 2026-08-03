## RENAMED Requirements

- FROM: `### Requirement: Additive Manifest Schema v3`
- TO: `### Requirement: Additive Manifest Schema v4`

## MODIFIED Requirements

### Requirement: Additive Manifest Schema v4

The manifest `VersionEntry` and `CodeVersions` schema SHALL advance from version 2 through
version 4 **additively** under the existing `extra="forbid"` strictness: the existing
`outputs: dict[str, str]` field SHALL be retained unchanged, and the per-artifact content
hashes and logical keys SHALL be carried in optional sibling collections (e.g.
`output_sha256: dict[str, str]`, `output_keys: dict[str, str]`, keyed by the same logical
output name) added at v3. `VersionEntry` SHALL additionally carry optional `source_id:
Optional[int]` and `source_name: Optional[str]` fields, added at v4, identifying which
Bloom database source (`cyl_trait_sources` row) a DB-backed raw read resolved — the
replacement identity signal for reads that no longer have an on-disk path to
content-address via `RawSourced`. All new `VersionEntry` fields across v3 and v4 (`seed`,
`agent`, `environment`, the per-artifact sibling collections, `source_id`, `source_name`)
and new `CodeVersions` fields SHALL be optional so that previously-written **v2** and
**v3** manifests — including their string-valued `outputs` — continue to validate and read
without error. `output_sha256` values SHALL be hex-encoded SHA-256 over the exact
pre-upload artifact bytes (app-computed, never the object-store ETag), populated at commit
by the `ResultStore`. `CURRENT_SCHEMA_VERSION` SHALL be `4`, and the schema-version guard
SHALL accept any manifest whose version is less than or equal to the known version and
SHALL reject one that is newer. Because new v4 entries would trip `extra="forbid"` if read
by pre-v4 code, a deployment SHALL upgrade readers before any writer emits v4 — this is a
live-write deploy gate, recorded here.

`ExperimentBlock` (`filename`, `source_path`, `input_sha256`) is unchanged by this bump: a
DB-backed raw read with no on-disk path already produces `source_path=""`/
`input_sha256=""` on the existing string-typed fields (the same values a path-less adapter
like `FakeReader` already produces today), so no type change is needed there. The new
`source_id`/`source_name` identity lives on `VersionEntry` instead, alongside the other
per-run provenance fields (`seed`, `agent`), not on the per-experiment `ExperimentBlock`.

#### Scenario: Old v2 manifest with string outputs still reads under v4 code

- **WHEN** a manifest recorded under schema version 2 — including a `VersionEntry` with a
  populated string-valued `outputs` (e.g. `{"cleaned": "_cleaned.csv"}`) and no v3/v4
  fields — is loaded by the v4 code
- **THEN** it validates and loads without error, and its absent v3/v4 fields default to
  unset rather than failing `extra="forbid"` validation

#### Scenario: Old v3 manifest still reads under v4 code

- **WHEN** a manifest recorded under schema version 3 — with `seed`/`agent`/`environment`/
  `output_sha256`/`output_keys` populated and no `source_id`/`source_name` — is loaded by
  the v4 code
- **THEN** it validates and loads without error, and its absent `source_id`/`source_name`
  default to `None` rather than failing `extra="forbid"` validation

#### Scenario: New manifests are written at schema version 4

- **WHEN** a new manifest is created after this change
- **THEN** `CURRENT_SCHEMA_VERSION` is `4`, a freshly built `Manifest` reports
  `manifest_schema_version == 4`, and a `VersionEntry` can carry `source_id`/`source_name`
  alongside the retained v3 fields

#### Scenario: A newer manifest version is rejected

- **WHEN** the schema-version guard reads a manifest declaring a version newer than the
  known version (e.g. `5`)
- **THEN** it raises a schema error rather than silently accepting unknown structure

#### Scenario: A v4 VersionEntry round-trips through JSON

- **WHEN** a `VersionEntry` carrying the new v4 fields (`source_id`, `source_name`) plus
  the retained v3 fields (`seed`, `agent`, `environment`, `output_sha256`, `output_keys`)
  and the retained v2 `outputs` string map is dumped with `model_dump(mode="json")` and
  re-validated
- **THEN** the reconstructed entry equals the original exactly

### Requirement: Provenance Maps Into The Manifest VersionEntry

bloom-mcp SHALL provide a single mapping from a contract-time `Provenance` to a manifest
`VersionEntry` (schema v4) so that provenance has one home in the manifest, not a parallel
record. The mapping SHALL be unit-testable without a live Supabase connection and without
performing a live manifest write (the live write, and the population of per-artifact
`output_sha256` / `key`, are the `ResultStore`'s responsibility). The mapping SHALL
preserve the existing v2 `VersionEntry` fields (`id`, `created_at`, `tool`, `params`,
`based_on_version`, `code_versions`, `outputs`, `user_label`, `version_dir`) and the v3
fields (`seed`, `agent`, `environment`). `Provenance` SHALL carry optional `source_id`/
`source_name` fields, populated when the active `ExperimentReader` adapter is
`SourceSelectable` and resolved a source for the read the provenance record was stamped
for; the mapping SHALL carry them through to the `VersionEntry` unchanged (including when
both are `None`, for a reader with no source-versioned substrate).

#### Scenario: Mapping yields a v4 VersionEntry with contract-time fields set

- **WHEN** a contract-time `Provenance` record is mapped to a `VersionEntry`
- **THEN** the entry carries `seed`, `agent`, the extended `code_versions`, the
  `environment` pointer, and `source_id`/`source_name`; it preserves the existing v2 fields
  (`id`, `created_at`, `tool`, `params`, `based_on_version`, `code_versions`, `outputs`,
  `user_label`, `version_dir`); and its per-artifact `output_sha256` / `key` collections
  are empty (to be filled at commit)

#### Scenario: A reader with no source-versioned substrate maps to no source identity

- **WHEN** a contract-time `Provenance` record is stamped while the active reader is not
  `SourceSelectable` (e.g. `FakeReader`, `LocalReader`)
- **THEN** the mapped `VersionEntry`'s `source_id` and `source_name` are both `None`, not a
  fabricated value
