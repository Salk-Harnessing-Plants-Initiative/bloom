# Add cyl write-back RPC (change D) + RLS lockdown (change E)

## Why

Changes A and C built the destination schema for sleap-roots pipeline results — `cyl_trait_sources`
(provenance + idempotency anchor), `cyl_scan_traits` (long-format trait values with `source_id`),
and `cyl_scan_intermediates` (per-scan blob pointers) — but nothing **writes** a pipeline
`ResultEnvelope` into them yet, and the legacy write paths are still open. Today any authenticated
user can `INSERT` into `cyl_trait_sources` / `cyl_scan_traits` (legacy permissive policies), and the
`bloom_writer` role can `INSERT`/`UPDATE` all three tables directly — bypassing every contract
validation. That is a forgery hole: a row with a fabricated `metadata` / `idempotency_key` or a
non-finite trait value could be written with no provenance integrity.

This change closes the loop with a single sanctioned, idempotent write path. Change **D** adds an
in-database `SECURITY DEFINER` RPC that ingests one `ResultEnvelope` and writes it transactionally;
change **E** locks down the three tables so that RPC (plus break-glass `bloom_admin`) is the **only**
writer. D and E **co-land in the same migration/deploy** — never D-without-E (forgery hole stays
open) and never E-without-D (no write path → write-back broken). This realizes roadmap A2 "all
writes go through the sanctioned, idempotent service-role RPC."

## What Changes

- **ADD** an idempotent `SECURITY DEFINER` write-back RPC (`insert_cyl_result_envelope`) that takes
  one `ResultEnvelope` `jsonb` and, in a single transaction:
  1. validates `provenance.contract_version` equals the pinned contract version (`v0.1.0a2`);
  2. requires a non-empty `provenance.idempotency_key` and enforces it equals
     `metadata->>'idempotency_key'` (RPC-only invariant, safe because E makes the RPC the sole writer);
  3. resolves the scan as `provenance.inputs.image_ids → cyl_images.scan_id → cyl_scans.id`, requiring
     exactly one distinct scan (rejects none / cross-scan / empty);
  4. inserts `cyl_trait_sources` as an **atomic gate** (`ON CONFLICT (idempotency_key) DO NOTHING
     RETURNING id`, setting the `NOT NULL` `name` to a deterministic provenance-derived label); if no
     row is returned the run was already ingested, so the RPC **short-circuits to a pure no-op**
     (`was_noop = true`, nothing further written, provenance never overwritten);
  5. (only when it created the source) for each `TraitValue` — accepting an omitted `grain` as `scan`
     and rejecting any explicit `grain != "scan"` — resolves `name → cyl_traits.id` by get-or-create
     (auto-register) and writes `cyl_scan_traits (scan_id, source_id, trait_id, value)`, each `value`
     finite-or-`NULL` via a post-cast finite check (`'NaN'::real`/`'Infinity'::real` are valid in
     Postgres, and a finite float64 beyond float4 range overflows to `Infinity` on cast — so the check
     runs on the cast result), `ON CONFLICT (scan_id, source_id, trait_id) DO NOTHING`;
  6. writes `BlobRef` rows into `cyl_scan_intermediates` (`UNIQUE(source_id, scan_id, kind, root_type)`);
  7. validates envelope self-consistency (every `traits[].scan_key`/`blobs[].scan_key` equals
     `provenance.scan_key`) and rejects a structurally malformed envelope cleanly; the whole ingest is
     a single all-or-nothing transaction (the `cyl_traits` auto-registers roll back with it on any
     failure).
- **BREAKING (intended, no real regression):** lock the three tables so the RPC is the sole writer —
  drop the legacy permissive `authenticated` `INSERT` policies on `cyl_trait_sources` /
  `cyl_scan_traits`, and drop `bloom_writer`'s `INSERT`/`UPDATE` policies on all three tables
  (`bloom_writer` keeps `SELECT`). `bloom_admin` retains full access (break-glass); `service_role`
  is unaffected. `bloom_writer` is granted `EXECUTE` on the RPC instead of direct writes.
- **MODIFY** the `cyl_scan_intermediates` role-access requirement (from change C) to reflect that
  `bloom_writer` is now `SELECT`-only on that table and writes go through the RPC.
- **MODIFY** the migration-matches-contract CI check to flip three previously-deferred mappings
  (RPC-enforced key equality, `contract_version` validation, `scan_key` resolution) from deferred to
  active.
- Forward-only migration with a companion manual rollback under `supabase/rollbacks/`.

## Impact

- Affected specs: `cyl-trait-writeback` (ADDED: write-back RPC; ADDED: RPC is the sole writer;
  MODIFIED: intermediates role-based access control), `contract-pinning` (MODIFIED: database schema
  matches the pinned contract).
- Affected code: new migration under `supabase/migrations/` (the RPC + RLS lockdown), companion
  rollback under `supabase/rollbacks/`; new integration test `tests/integration/test_cyl_writeback_rpc.py`;
  updates to `tests/integration/test_cyl_scan_intermediates.py` (the `bloom_writer`-can-write and
  policy-set assertions now expect SELECT-only) and `tests/integration/test_contract_migration_match.py`
  (flip three deferred mapping rows to active); regenerate the **four** `make gen-types` targets
  **plus** manually update the orphaned fifth tracked copy `web/types/database.types.ts` (gen-types
  does not write it) — **all five** carry a `Functions` block for the new RPC; update
  `_WIKI/SUPABASE/README.md` (the `bloom_writer` "write anywhere" row + tagline, and a new write-back
  RPC subsection).
- Also written by the RPC (not locked down): `cyl_traits` (the trait-name registry) is auto-populated
  via get-or-create; it is a shared low-sensitivity vocabulary and is intentionally **out of scope**
  for E's lockdown. Trait-name *correctness* is a documented **trust boundary** — `sleap-roots-contracts`
  validates names producer-side but defaults unknown names to a warning, and Bloom does not re-validate;
  registry pollution from a producer typo is an accepted residual risk.
- Not changed: the `bloom_writer` role, its JWT hook, its global grants, or its
  `cyl_scans`/`cyl_images`/`storage.objects` writes (Bloom Desktop is unaffected). `bloommcp`'s
  `sleap-roots-contracts>=0.1.0a1` Python pin is left as-is (no new runtime consumer).
- Coordination: surgical carve-out of `bloom_writer` on three pipeline-only tables; does not touch
  the `database-role-grants` capability or the in-flight `bloom_user` read-only work (#346).
