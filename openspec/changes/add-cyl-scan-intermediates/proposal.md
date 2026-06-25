## Why

The sleap-roots cylinder pipeline produces per-scan artifacts (SLEAP `.slp` prediction files,
one per root type) that must be addressable from Bloom: served from Bloom's own object storage
under RLS, cross-linked to a human-shareable Box copy, and tied back to the pipeline run that
produced them. Change A (`#290`) gave each run a provenance/idempotency anchor on
`cyl_trait_sources`; change D (the write-back RPC, `#296`'s sibling) will write trait **numbers**
into `cyl_scan_traits` and artifact **pointers** somewhere — but there is no table for those
pointers yet. The contract's `BlobRef` (in the pinned `result_envelope.schema.json`) describes
exactly these rows and the contract-pin migration-match test already carries a **deferred** row
reserving this table. This change builds the table so change D has a destination.

## What Changes

- **Add `cyl_scan_intermediates`** — a per-scan artifact-pointer table. One row per artifact file
  (today: one `.slp` per root type per scan). Columns: `source_id` (FK → `cyl_trait_sources`),
  `scan_id` (FK → `cyl_scans`), `kind`, `root_type`, `s3_location`, `box_link`, `checksum`,
  `file_size`.
  - **Dual pointer**: `s3_location` is the canonical Bloom MinIO copy (Bloom serves + RLS-controls
    it); `box_link` is the human-shareable Box link. `checksum` + `file_size` tie the two copies
    together and detect partial uploads.
  - **At-least-one-location CHECK**: `s3_location IS NOT NULL OR box_link IS NOT NULL` (mirrors the
    contract `BlobRef` `anyOf`).
  - **Strict vocabularies via CHECK**: `kind IN ('predictions_slp')`; `root_type IN
    ('primary','lateral','crown')`, `NOT NULL` for `predictions_slp`. The pipeline that produced a
    row is recoverable from provenance (`cyl_trait_sources.metadata` via `source_id`), so it is not
    a column here.
  - **Idempotency / uniqueness**: `UNIQUE (source_id, scan_id, kind, root_type)` — per-run rows
    (history preserved across re-runs, like change A) and a clean upsert key for change D.
  - **RLS (role model, matching `gravi_images` / embedtree)**: `bloom_admin` FOR ALL; `bloom_agent`
    + `bloom_user` SELECT; `bloom_writer` SELECT/INSERT/UPDATE (the ingest/write-back role); plus
    table-level GRANTs.
- **Flip the contract migration-match mapping** for `BlobRef` from **deferred → active**: assert the
  `cyl_scan_intermediates` table exists with the expected columns/types, the two FKs (by
  `contype`/`confrelid`), the at-least-one-location CHECK, the UNIQUE, and that the DB `kind` set
  matches the pinned contract `BlobRef.kind` enum.
- **Forward-only additive migration** + manual rollback under `supabase/rollbacks/`; regenerate the
  4 tracked `database.types.ts` files via `make gen-types` and the orphaned 5th
  (`web/types/database.types.ts`).

Out of scope: the write-back RPC that populates this table (change D); the producer/ingest
S3-upload step (change G); `viewer_html` / per-scan trait CSV artifacts (viewer deferred to later
work; trait numbers land as `cyl_scan_traits` rows, not blobs); per-experiment
`sleap-roots-analyze-output/` artifacts (separate change at `#28`'s grain). No change to
`cyl_scan_traits` — the trait↔blob link is the shared `(source_id, scan_id)`.

## Dependency / sequencing

The DB `kind` CHECK must match the **revised** contract `BlobRef.kind` enum (`{predictions_slp}` —
dropping `labels`/`h5`/`qc_image`). The contract revision + release + re-pin
(`sleap-roots-contracts`) is owned separately. This change is built against the agreed enum; the
re-pin to the new contract version is a **pre-merge MERGE BLOCKER** — the migration-match `kind`
parity assertion compares the DB CHECK to the vendored schema's enum. The gate is the integration
test `test_contract_migration_match.py` (the `compose-health-check` CI job), **not**
`npm run contracts:check` (which only verifies pin/`$id` consistency and `generated/` byte-identity,
never reading the DB). The parity assertion lands **skip-guarded** with the migration and is enabled
in the same commit as the re-pin, so CI stays green throughout; the structural assertions are
enum-independent. The table itself ships **standalone** (no consumer required), exactly as change A
landed before change D.

## Impact

- Affected specs: `cyl-trait-writeback` (ADD the intermediates table requirements);
  `contract-pinning` (MODIFY the database-schema-matches-contract requirement to activate the
  `BlobRef` mapping).
- Affected code:
  - `supabase/migrations/<ts>_create_cyl_scan_intermediates.sql` (new, forward-only)
  - `supabase/rollbacks/<ts>_create_cyl_scan_intermediates_rollback.sql` (new, manual)
  - `tests/integration/test_cyl_scan_intermediates.py` (new — columns, CHECKs, FKs, UNIQUE, RLS)
  - `tests/integration/test_contract_migration_match.py` (flip the `BlobRef` deferred row → active)
  - `contracts/` re-pin (pre-merge gate): `pin.json`, `schema/result_envelope.schema.json`,
    `generated/result-envelope.ts`, and `contracts/README.md` (bump the documented pinned version —
    spec-mandated — and correct the now-stale `kind` enum in its change-C codegen caveat)
  - `packages/bloom-fs/src/types/database.types.ts`, `packages/bloom-js/src/types/database.types.ts`,
    `packages/bloom-nextjs-auth/src/lib/database.types.ts`, `web/lib/database.types.ts`,
    `web/types/database.types.ts` (regenerated)
