## MODIFIED Requirements

### Requirement: Generated TypeScript types match the pinned schema

Bloom SHALL commit TypeScript types generated from the pinned `result_envelope.schema.json`
(`ResultEnvelope`, `Provenance`, `TraitValue`, `BlobRef`, and their sub-definitions) under
`contracts/generated/`, produced by a deterministic, exact-version-pinned codegen tool. The
generated file SHALL be excluded from repository prettier formatting and pinned to LF line endings
so the codegen tool's output is the single authority over its bytes across operating systems. A CI
drift guard SHALL regenerate the types from the pinned schema, normalize line endings, and fail
when the committed types are not byte-identical to the regenerated output. Because the codegen does
not emit the schema `$id` into the types, a re-pin that only re-stamps the `$id` MUST regenerate
byte-identical types so the guard passes with no type change; conversely, a change to any contract
field MUST produce a different generated output so the guard fails. A re-pin that carries a **real
contract revision** (a field added, removed, or retyped — not a `$id`-only restamp) MUST regenerate
**and commit** the correspondingly changed types, which then pass the guard against the newly
committed output; this is the reviewed counterpart of the `$id`-only no-op and is how an intended
revision is distinguished from unreviewed drift. The committed types SHALL be valid TypeScript
(type-checkable). These contract types are distinct from the Supabase `database.types.ts` generated
from the database.

#### Scenario: Drift guard passes when committed types match the pinned schema

- **WHEN** the drift guard regenerates the types from the vendored schema and compares them
  (line-ending-normalized) to the committed `contracts/generated/` output
- **THEN** they are byte-identical and the guard exits zero

#### Scenario: Drift guard fails when committed types diverge from the pinned schema

- **WHEN** the committed generated types differ from regenerating from the vendored schema (the
  schema changed without regenerating, or the types were hand-edited)
- **THEN** the guard exits non-zero and reports the difference

#### Scenario: A $id-only re-pin regenerates identical types

- **WHEN** the vendored schema is re-pinned to a new version whose only change is the
  version-stamped `$id` and the types are regenerated
- **THEN** the regenerated types are byte-identical to the previous committed types and the drift
  guard passes

#### Scenario: A real contract field change fails the guard

- **WHEN** a contract field changes (a property is added/removed or a type changes) in addition to
  or instead of the `$id`, and the types are regenerated
- **THEN** the regenerated types differ from the committed types and the drift guard exits non-zero

#### Scenario: A real additive re-pin regenerates expanded types that pass the guard

- **WHEN** the vendored schema is re-pinned to a new version whose payload adds one or more optional
  fields (a real revision, not a `$id`-only restamp) and the types are regenerated **and committed**
- **THEN** the regenerated types gain exactly those fields, differ from the previous committed types,
  and the drift guard exits zero against the newly committed output (a reviewed revision, not drift)

#### Scenario: Generated types are valid TypeScript

- **WHEN** the committed `contracts/generated/` types are type-checked
- **THEN** they compile without type errors
