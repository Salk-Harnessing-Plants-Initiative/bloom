## Context

Change C of the sleap-roots ↔ Bloom integration. The cylinder pipeline is being rewritten from
**experiment-level** to **scan-level**: for each scan it builds a video in memory and runs each
root-type SLEAP model, producing **one `.slp` prediction file per root type** (a `.slp` is an
HDF5-backed SLEAP `Labels` file spanning *all* of the scan's frames — there is **not** one file
per frame). These `.slp` files are the per-scan artifacts this table points at.

Prior art in Bloom:
- **Change A** (`#290`, archived `2026-06-16-add-cyl-trait-source-provenance`): added
  `cyl_trait_sources.metadata jsonb` (opaque `Provenance` envelope) + `idempotency_key text`
  (UNIQUE + non-empty CHECK). Validate producer-side; treat the envelope as opaque at the DB.
- **`gravi_images`** (`20260527180700`): the closest structural analog — `scan_id` FK,
  `object_path`, `file_hash`, `file_size_bytes`, `UNIQUE(scan_id)`, role-based RLS.
- **embedtree `bloom_writer`** (`20260622180000`) + gravi role grants (`20260528120400`) + `#341`
  (`bloom_user` read-only): the current canonical RLS role model.
- **Contract `BlobRef`** (`contracts/schema/result_envelope.schema.json`, pinned `v0.1.0a1`): an
  `anyOf` requiring at least one of `s3_location` / `box_link`; properties `kind` (enum), `scan_key`,
  `s3_location`, `box_link`, `checksum`, `file_size`.

Constraints: forward-only migrations (`supabase db push`, no down-runner); manual rollbacks under
`supabase/rollbacks/`; TDD against LOCAL Supabase; tracked TS types regenerated via `make gen-types`.

## Goals / Non-Goals

**Goals**
- A per-scan artifact-pointer table (`cyl_scan_intermediates`) that change D can upsert into.
- Dual pointer (canonical MinIO `s3_location` + human-shareable `box_link`) with integrity columns.
- Strict `kind` / `root_type` vocabularies enforced at the DB.
- Modern role-based RLS from day one (no legacy permissive-authenticated policies).
- Flip the contract migration-match `BlobRef` mapping deferred → active.

**Non-Goals**
- The write-back RPC that populates the table (change D) and its idempotency-equality logic.
- The producer/ingest S3-upload step (change G).
- `viewer_html` and per-scan trait CSV artifacts (viewer deferred; trait numbers go to
  `cyl_scan_traits` via D — a per-scan trait CSV is redundant and intentionally dropped).
- Per-experiment `sleap-roots-analyze-output/` artifacts (separate change, `#28`'s grain).
- Any change to `cyl_scan_traits` (the trait↔blob link is the shared `(source_id, scan_id)`).

## Decisions

### D1 — Table name: `cyl_scan_intermediates`
Parallels `cyl_scan_traits` (per-scan grain), uses the contract's own word ("intermediates"
in the `BlobRef` description). Alternatives `cyl_scan_blobs` / `cyl_scan_artifacts` rejected as
less aligned with the contract vocabulary.

### D2 — Grain: one row per artifact file; `UNIQUE (source_id, scan_id, kind, root_type)`
The multiplicity per scan comes from **root-type models** (primary/lateral/crown), not frames.
Per-run uniqueness (include `source_id`) preserves full history across re-runs — mirroring change
A's per-source model — and gives change D a deterministic upsert key. Alternatives:
- *One row per (scan, kind)* — loses per-root-type pointers/checksums; rejected.
- *Uniqueness on checksum* — `checksum` is nullable in the contract; can't be the sole key; rejected.

### D3 — `kind` and `root_type` are strict CHECK vocabularies (TEXT + CHECK, not a PG enum type)
`kind TEXT CHECK (kind IN ('predictions_slp'))`; `root_type TEXT CHECK (root_type IN
('primary','lateral','crown'))`. TEXT+CHECK mirrors change A's `idempotency_key` CHECK pattern and
keeps `information_schema` `data_type = 'text'` so the migration-match test asserts cleanly by
`data_type` + `contype`. A `CREATE TYPE` enum would surface as `USER-DEFINED` and add migration
ceremony for no gain. `root_type` is the **union** across pipelines; *which* pipeline produced a
row is recoverable from provenance (`source_id → cyl_trait_sources.metadata`), so pipeline identity
is **not** a column here. `root_type` is `NOT NULL` (every `predictions_slp` row has a root type);
revisit nullability only if a future root-type-agnostic `kind` (e.g. `viewer_html`) is added.
**`root_type` is contract-anchored, symmetric with `kind`** (revised after the `v0.1.0a2` re-pin,
which added a required `BlobRef.root_type` enum to the contract). Both vocabularies have a
behavioral CI parity probe (`test_db_{kind,root_type}_vocab_matches_contract_blobref_enum`) asserting
the DB `CHECK` accepts exactly the contract enum — so a contract/DB divergence on either fails CI.
(Earlier in this change's design `root_type` was DB-anchored because the contract did not yet own it;
that is now reversed — the contract is the authority, and changing the `root_type` vocabulary
requires a contract release + re-pin, like `kind`.)

### D4 — At-least-one-location CHECK
`CHECK (s3_location IS NOT NULL OR box_link IS NOT NULL)` mirrors the contract `BlobRef` top-level
`anyOf`. `s3_location` is the **canonical** copy Bloom serves and RLS-controls; `box_link` is a
human-shareable convenience link (may be folder-grained — Box uploads are whole-folder via rclone —
which is acceptable since it is non-canonical). `checksum` + `file_size` tie the MinIO and Box
copies together and detect partial uploads.

### D5 — RLS: modern role model, with `bloom_writer` writes enabled now
`ENABLE ROW LEVEL SECURITY`; `bloom_admin` FOR ALL; `bloom_agent` SELECT; `bloom_user` SELECT
(read-only per `#341`); `bloom_writer` SELECT/INSERT/UPDATE (the ingest/write-back role, as the
embedtree uploader established); table-level `GRANT`s (the gate *before* RLS). We enable
`bloom_writer` writes in this change (not deferred to E) so change D's write-back path can write
the moment it exists; the table is otherwise read-only to users and never used the legacy
permissive-`authenticated` policy, so no later lockdown (change E) is required for it.

Two enforcement subtleties drive how this is **tested**: (a) `security_groups.sql` set
`ALTER DEFAULT PRIVILEGES … GRANT SELECT, INSERT, UPDATE … TO bloom_user`, so the new table
auto-receives a *permissive* `bloom_user` table-level GRANT — meaning RLS (the **absence** of a
`bloom_user` write policy), not the GRANT, is the real write gate; the test therefore asserts there
is **no** `bloom_user`/`bloom_agent` write policy. (b) The migration/test connection runs as
`supabase_admin`, which has `BYPASSRLS`, so introspecting `pg_policies` proves only that policy
*rows exist*, not that they grant/deny anything. The tests therefore exercise each role with
`SET LOCAL ROLE … ; ROLLBACK` (the established `test_embedtree_schema.py` /
`test_schema_usage_grants.py` pattern): `bloom_writer` INSERT/UPDATE succeed; `bloom_user` /
`bloom_agent` INSERT is rejected; all four roles can SELECT. Catalog introspection is kept only as a
cheap drift detector. `bloom_writer` already has membership (`GRANT authenticated TO bloom_writer`,
established repo-wide in `20260622180000`) and default-privilege SELECT/INSERT/UPDATE, and the role
itself is created in `20260519130000` (well before this migration's timestamp), so there is no
"role does not exist" risk; the explicit GRANT here matches the embedtree stance ("policy + GRANT
are independent; issue both") without being a hard prerequisite.

### D6 — Trait↔blob link is implicit via `(source_id, scan_id)`
A trait value (`cyl_scan_traits`) and an artifact (`cyl_scan_intermediates`) produced by the same
run share `source_id` (→ `cyl_trait_sources`) and `scan_id` (→ `cyl_scans`). The link is **FK-backed
on both tables**: `cyl_scan_traits` has carried a `source_id` FK since
`20240731010924_add_source_id_to_cyl_scan_traits.sql` (and `scan_id` since its creation), and this
change gives `cyl_scan_intermediates` the same two FKs — so a trait and an artifact from one run
converge on `(source_id, scan_id)`. No FK is added *between* the two leaf tables: the grain is
mismatched (many traits per `.slp`) and a hard per-trait link, if ever needed, belongs to change D.
Caveat: `cyl_scan_traits.source_id` is **nullable**, so the join is guaranteed only for
pipeline-written rows (change D populates `source_id`); legacy/manual trait rows with a NULL
`source_id` won't join — acceptable, since the artifact table only ever holds pipeline output.

### D7 — Contract enum revision sequencing (and which check actually gates it)
The contract `BlobRef.kind` enum is being revised to `{predictions_slp}` (dropping
`labels`/`h5`/`qc_image`) in `sleap-roots-contracts` (owned separately). This change is authored
against the agreed enum now; the **re-pin to the new contract version is a pre-merge MERGE BLOCKER**
because the migration-match `kind` parity assertion compares the DB CHECK set to the vendored
schema's `BlobRef.kind` enum. **The gate is the integration test** `test_contract_migration_match.py`
(run in the `compose-health-check` CI job against a live DB) — **not** `npm run contracts:check`,
which only verifies `pin.json` ↔ schema `$id` consistency and `contracts/generated/` byte-identity
and never reads the DB. Until the re-pin lands, the DB CHECK is `{predictions_slp}` while the
vendored enum still carries the old values, so the parity assertion would *fail* (not skip).
Mitigation (see tasks): the parity assertion lands **skip-guarded** with the migration and the guard
is removed in the same commit as the re-pin; the structural assertions (table/columns/FKs/CHECK/
UNIQUE) are enum-independent and pass immediately. The table ships standalone regardless.

## Risks / Trade-offs

- **Empty table until change D.** Acceptable and precedented (change A landed before D). The
  migration-match flip + the table's own integration tests give full coverage without a consumer.
- **Re-pin coupling.** If the contract release slips, the `kind` parity assertion would compare
  against the stale enum. Mitigation: treat re-pin as an explicit pre-merge gate (proposal +
  tasks), and keep the structural assertions (table/columns/FKs/CHECK/UNIQUE) independent of the
  enum so they pass regardless.
- **`box_link` granularity is looser than per-file.** Accepted: it is the non-canonical
  human-shareable pointer; `s3_location` is the per-file canonical copy.
- **`UNIQUE` with nullable columns.** All four uniqueness columns are `NOT NULL` for the only
  current `kind` (`predictions_slp`), so the constraint is fully effective today; a future nullable
  `root_type` kind would need `NULLS NOT DISTINCT` or a partial index — revisit then.

## Migration Plan

1. Forward-only `supabase/migrations/<ts>_create_cyl_scan_intermediates.sql` (wrapped in
   `BEGIN; … COMMIT;`, using `CREATE TABLE IF NOT EXISTS` + `DROP POLICY IF EXISTS` before each
   `CREATE POLICY` for re-runnable `supabase db push`): `CREATE TABLE`, the two FKs, the
   at-least-one-location CHECK, the `kind`/`root_type` CHECKs, the UNIQUE, RLS enable, role policies,
   table GRANTs. The `<ts>` MUST exceed the max migration timestamp across **both** `origin/main`
   and `origin/staging` (currently `20260622180000`) — the CI lint only checks against the PR's base
   ref, so a main-targeted PR would not catch a staging out-of-order timestamp; pick `20260625…`.
2. Manual `supabase/rollbacks/<ts>_create_cyl_scan_intermediates_rollback.sql`:
   `BEGIN; DROP TABLE IF EXISTS cyl_scan_intermediates; COMMIT;` (break-glass only).
3. `make gen-types` → regenerate the 4 tracked `database.types.ts`; manually update the orphaned 5th
   (`web/types/database.types.ts`).
4. Re-pin `contracts/` to the revised contract version (pre-merge gate).

Rollback: apply the manual rollback script; data in the table is lost (greenfield, acceptable).

## Open Questions

None blocking. (Resolved in brainstorming: table name, root-type vocabulary, grain, RLS model,
trait-link strategy, enum sequencing.)
