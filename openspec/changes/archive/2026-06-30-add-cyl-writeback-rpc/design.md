# Design — cyl write-back RPC (D) + RLS lockdown (E)

## Context

Roadmap A2 changes D + E. Builds on change A (`cyl_trait_sources.metadata`/`idempotency_key`),
change C (`cyl_scan_intermediates`), and the pinned contract `sleap-roots-contracts v0.1.0a2`
(vendored at `contracts/schema/result_envelope.schema.json`). The producer (sleap-roots pipeline)
emits a `ResultEnvelope`; Bloom is the consumer. Design settled with the user and reconciled against
an adversarial review and the **live** database schema (several facts below were verified directly,
because the original exploration of `cyl_scan_traits` was stale).

### Verified live-schema facts (load-bearing)

- `cyl_trait_sources(id, name TEXT NOT NULL, metadata jsonb, idempotency_key text)`; `UNIQUE(idempotency_key)`,
  non-empty CHECK on `idempotency_key`. **`name` is `NOT NULL`** — the RPC must set it.
- `cyl_scan_traits(id, scan_id NOT NULL, value real, source_id, trait_id integer→cyl_traits(id))` —
  **there is no `name` column**; trait identity is normalized through `trait_id`.
  `UNIQUE(scan_id, source_id, trait_id)` (`scan_source_trait_uniqueness`).
- `cyl_traits(id SERIAL PK, name TEXT NOT NULL UNIQUE)` — the trait-name registry.
- `cyl_scan_intermediates(... UNIQUE(source_id, scan_id, kind, root_type))` (change C).
- Ownership / RLS: `cyl_trait_sources` & `cyl_scan_traits` owned by `postgres`; `cyl_scan_intermediates`
  owned by `supabase_admin`; **no table sets `FORCE ROW LEVEL SECURITY`**. Role `postgres` has
  `rolbypassrls = true` and holds `INSERT` on all three tables. `service_role` exists (`BYPASSRLS`).
  `bloom_writer`/`bloom_user`/`bloom_admin` are **not** `BYPASSRLS`.

## Decisions

### D1 — In-database `SECURITY DEFINER` RPC over one `jsonb` envelope, owner pinned to `postgres`

One `plpgsql` function `insert_cyl_result_envelope(envelope jsonb) RETURNS jsonb` does all work in a
single transaction. It is `SECURITY DEFINER` because change E removes direct write access from every
callable role. The migration SHALL pin ownership explicitly (`ALTER FUNCTION ... OWNER TO postgres`)
so the write identity is deterministic regardless of who applies the migration: `postgres` has
`rolbypassrls = true` and `INSERT` on all three tables, so the function body writes cleanly after E.
Hardening: `SECURITY DEFINER`, `SET search_path = pg_catalog, public, pg_temp`, all table references
schema-qualified (`public.<table>`), `REVOKE EXECUTE ... FROM PUBLIC`, `GRANT EXECUTE ... TO
bloom_writer, service_role, bloom_admin`. The function builds writes from envelope values using
`jsonb_to_recordset`/parameterized expressions (never `format()` string-interpolation of data) to
avoid injection. A regression guard: the three tables must **not** acquire `FORCE ROW LEVEL SECURITY`
(that would re-subject the `postgres`-owned function to RLS and break the only writer) — asserted by
test. Returns a `jsonb` summary: `{ source_id, scan_id, trait_count, blob_count, was_noop }`.

Alternative rejected: thin SQL + app-side orchestration. It would move the single-transaction
guarantee into application code and split validation across the language seam.

### D2 — Pure-no-op re-delivery via an atomic source gate (all-or-nothing)

The whole ingest is one transaction (all-or-nothing): any validation/constraint failure aborts the
call, so no partial source/trait/blob/registry rows persist. The source insert is the **atomic gate**:
`INSERT INTO public.cyl_trait_sources (name, metadata, idempotency_key) VALUES (...) ON CONFLICT
(idempotency_key) DO NOTHING RETURNING id`. If a row is returned, **this** transaction created the
source and is the writer — it proceeds to write all traits and blobs. If no row is returned, a prior
(or concurrent-then-committed) delivery already wrote the run in full, so the RPC **short-circuits**:
it writes nothing further and returns `was_noop = true` (with `scan_id` null — see D4). Re-delivery of
an already-ingested run is therefore a **pure no-op** — source, traits, and blobs are all immutable and
nothing (not even a blob pointer) is rewritten. The stored `metadata`/provenance is never overwritten.
The gate runs **before** scan resolution (D4) so a re-delivery short-circuits without re-resolving
images — making re-delivery robust even if the original `image_ids` were since deleted, and avoiding a
misleading `scan_id` from the new delivery. Intra-envelope duplicates (two traits with the same name,
or two blobs with the same `kind`/`root_type`) are a malformed envelope and are **rejected** by the
respective UNIQUE constraints (no silent dedup, symmetric across traits and blobs); the returned
`trait_count`/`blob_count` therefore equal rows actually written.

This is safe under crash/partial/concurrent delivery precisely because of atomicity: a
`cyl_trait_sources` row becomes visible only when the transaction commits, and that commit includes
the source **with** all its traits and blobs — so a `cyl_trait_sources` row exists if and only if a
prior delivery fully completed. An unfinished attempt commits nothing and rolls back entirely; a retry
sees no source, wins the gate, and writes the full envelope. Concurrent same-key deliveries serialize
on `UNIQUE(idempotency_key)`: the loser's `ON CONFLICT DO NOTHING` blocks until the winner
commits/aborts, then either gets no row (winner committed → short-circuit) or proceeds (winner aborted
→ becomes the writer). The gate's "did I create the source?" signal is exactly "must I populate
traits/blobs?".

This choice also resolves the pathological **same-key-resolves-to-a-different-scan** case (only
possible if Bloom's own image→scan mapping changed between deliveries, since `scan_key` and
`images_checksum` are both in the producer's key hash): the short-circuit fires before any trait/blob
write, so a re-delivery can never attach an existing run's source to a second scan. Rationale for
immutability over `DO UPDATE`: volatile provenance fields (`produced_at`, `argo_node_id`,
`worker_request_id`) are not in the key hash, so the same key can legitimately re-arrive with a
different `metadata` blob — overwriting would silently rewrite the provenance-of-origin record under a
fixed identity. (The original brief's "artifact UPDATEs in place" is intentionally dropped: in this
flow the producer sends a complete envelope, so blob-pointer enrichment is not a change-D concern;
any later enrichment is a separate admin/change-G path.) Every RPC-written `cyl_scan_traits` row
carries a non-null `source_id` (re-selected from the gate), so the `ON CONFLICT (scan_id, source_id,
trait_id)` arbiter is always exercised. All "clean rejection" validation paths `RAISE` (aborting the
txn), never catch-and-continue, so no registry/source row from a rejected envelope can survive.

### D3 — Idempotency key is opaque; never recomputed

`provenance.idempotency_key` is a producer-side deterministic sha256 (`compute_idempotency_key` in
`talmolab/sleap-roots-contracts`, **not** vendored into Bloom). The RPC reads it, rejects empty/absent
(the DB CHECK forbids `''`, which would collapse all keyless runs onto one row), and enforces
`idempotency_key == metadata->>'idempotency_key'` (the dedup-anchor column must equal the value inside
the stored Provenance jsonb — both written from the same envelope field, so the check is an invariant
guard). Re-implementing the hash would couple Bloom to the producer's internals.

### D4 — Scan resolution via `image_ids`, hardened

The contract carries **no** `scan_id` — only `provenance.scan_key` (producer-side, opaque to Bloom)
and `provenance.inputs.image_ids` (Bloom's `cyl_images.id` as strings). The RPC resolves the scan
from `image_ids` and SHALL NOT use `scan_key` for resolution. Resolution runs **only on the write
path** (after the source gate, D2), so a no-op re-delivery returns `scan_id = null`. Hardening
(review-driven):
- parse `image_ids` defensively — a non-numeric id yields a clean "unresolvable" rejection, not a raw
  `22P02` cast error (filter/validate before casting to `bigint`);
- de-duplicate the requested ids first, then require that the count of **distinct** matched
  `cyl_images` rows with a **non-null** `scan_id` equals the count of **distinct** requested ids (so
  unknown/partial ids are rejected, but a legitimately repeated `image_id` in the array does **not**
  cause a false rejection);
- require **exactly one** distinct non-null `scan_id`; reject zero (unresolvable/empty) and >1
  (cross-scan). The single resolved `scan_id` is used for all trait and blob rows.

### D5 — Trait write through the registry (auto-register)

`cyl_scan_traits` is normalized: it carries `trait_id`, not a trait name. For each `TraitValue` the
RPC resolves `name → cyl_traits.id` by **get-or-create**: `INSERT INTO public.cyl_traits (name)
VALUES (...) ON CONFLICT (name) DO NOTHING`, then select the id (auto-register, matching the legacy
ingest design — and the `insert_gravi_scan_metadata` get-or-create precedent). It then writes
`INSERT INTO public.cyl_scan_traits (scan_id, source_id, trait_id, value) ... ON CONFLICT
(scan_id, source_id, trait_id) DO NOTHING` — so a re-delivered trait is a no-op rather than a
unique-violation abort. The RPC therefore also writes `cyl_traits` (via its `SECURITY DEFINER` owner);
`cyl_traits` is a shared low-sensitivity vocabulary and is **out of scope** for E's lockdown (forging
a trait *name* is not a provenance/value-integrity risk; locking it would disturb the legacy
trait-entry flow).

**Trust boundary (trait-name correctness).** Auto-register trusts the producer for trait-name
correctness. `sleap-roots-contracts` ships a trait-definitions registry (`registry.py`'s
`validate_trait`, `trait_definitions.yaml`), but its `on_unknown` default is **`"warn"`, not
`"error"`**, so an unknown/typo name only warns producer-side and still flows through — and Bloom does
**not** re-validate names at the in-database write boundary. The residual risk is **registry
pollution**: a producer typo (`primary_root_legnth`) silently creates a permanent `cyl_traits` entry
(get-or-create never deletes). This is an **accepted residual risk routed to producer-side
validation**, documented here as a trust boundary — *not* a guarantee Bloom enforces. (Bloom defends
trait *values* and *provenance* at the boundary, but trusts the producer for trait *identity*.)

### D6 — `cyl_trait_sources.name` population

`name` is `NOT NULL`; the RPC sets a deterministic, provenance-derived label so the source row is
human-attributable: `provenance.pipeline_run_id` when present, else a stable label derived from the
**full** run identity (`'sleap-roots:' || idempotency_key` — the column is `text`, so the full
64-char sha256 is used rather than a lossy 12-char prefix, guaranteeing a 1:1 label↔identity mapping).
`idempotency_key` is always non-empty (validated), so the fallback is always non-null. The label is
written once (re-delivery short-circuits) and `name` is not an identity column (identity is the
`UNIQUE` `idempotency_key`).

### D7 — Finite-or-null trait values (explicit finite check)

Each `TraitValue.value` is `anyOf: [number, null]`; the contract normalizes NaN/inf → null. The RPC
writes `value` only when the JSON value is a **number** (`jsonb_typeof(v->'value') = 'number'`);
anything else — JSON null, a (non-conforming) string such as `"NaN"`/`"Infinity"`/`"1.5"`/`"abc"`, a
bool, etc. — maps to SQL `NULL`. A JSON number can never be NaN/inf (JSON has no such literals), so
the only remaining hazard is **range overflow**: a finite float64 exceeding `real` range (≈3.4e38)
raises `numeric_value_out_of_range` on the narrowing `::real` cast, caught in a tight `BEGIN…EXCEPTION`
block that maps it to `NULL`. Finite in-range values round-trip to `real` precision (float64 traits
narrow to ~7 significant digits; values beyond float4 range normalize to `NULL` — both silent
narrowings, acceptable because such magnitudes are non-physical for these phenotype traits;
pre-existing `real` column from change A). The `jsonb_typeof` guard (vs. a bare cast-and-catch) avoids
silently coercing a non-conforming string-number like `"1.5"` into a stored `1.5`.

### D8 — Envelope self-consistency (scan_key) and structural validation

The contract repeats `scan_key` on `provenance`, every `traits[]`, and every `blobs[]`. The RPC SHALL
validate that every `traits[].scan_key` and `blobs[].scan_key` equals `provenance.scan_key`, rejecting
a mis-assembled cross-scan envelope (the one self-consistency anchor the envelope provides). It SHALL
also reject a structurally invalid envelope (not a JSON object, or missing `provenance`/`inputs`)
cleanly rather than leaking an unhandled plpgsql error. `TraitValue.grain` (`scan`|`image`, contract
default `"scan"`, not required) SHALL be validated with `coalesce(v->>'grain', 'scan')` so an
**omitted** grain is treated as `scan` and accepted, while any explicit `grain != 'scan'` is
**rejected** (image-grain traits belong in `cyl_image_traits`, which is change B and deferred —
silently writing them as scan traits would corrupt trait semantics).
Out-of-vocabulary `kind`/`root_type` surface as the `cyl_scan_intermediates` CHECK violation that
aborts the whole txn (consistent with all-or-nothing); the contract enum and DB CHECK must stay in
lockstep.

### E1 — RLS lockdown scope: the RPC is the sole writer (surgical)

Make the RPC the only write path to the three tables, scoped so nothing else regresses. Exact policy
names verified against the live catalog:
- **Drop** the legacy permissive `authenticated` INSERT policies:
  `"Authenticated users can insert cyl_trait_sources"` and `"Authenticated users can insert cyl_scan_traits"`.
- **Drop** `bloom_writer`'s write policies: `writer_insert_cyl_trait_sources`,
  `writer_update_cyl_trait_sources`, `writer_insert_cyl_scan_traits`, `writer_update_cyl_scan_traits`
  (from the `20260519130000` loop) and `writer_insert_cyl_scan_intermediates`,
  `writer_update_cyl_scan_intermediates` (from change C).
- **Keep** the `bloom_writer` SELECT policies — note the asymmetric names: `writer_select_cyl_trait_sources`
  and `writer_select_cyl_scan_traits` (loop) but **`writer_read_cyl_scan_intermediates`** (change C did
  not use the loop). Keep `admin_all_*` and the `agent_read_*`/`user_read_*` read policies.
- **Keep** `bloom_admin` (`FOR ALL`, break-glass). `service_role` is `BYPASSRLS` (Supabase superuser
  class) and is intentionally out of the RLS gate; it is granted `EXECUTE` as a convenience for
  service-token callers.
- **Grant** `bloom_writer` (and `service_role`, `bloom_admin`) `EXECUTE` on the RPC.

After the drops, RLS denies writes to every non-admin role even though some hold a standing
table-level GRANT — RLS, not the GRANT, is the write gate (the change-C model). Belt-and-suspenders
`REVOKE` of the table-level GRANT is **not** done (dropping the policy already denies the write; keeps
the migration minimal). Verified safe: no runtime code writes these three tables (web: none;
langchain: read-only; only the admin test-data loader), and `bloom_writer`'s real job — Bloom Desktop
writing `cyl_scans`/`cyl_images`/`storage.objects` — is on different tables, untouched.

### E2 — Change C's RLS spec and tests are updated, not contradicted

Change C shipped `bloom_writer` `SELECT`/`INSERT`/`UPDATE` on `cyl_scan_intermediates` and tests that
assert it. This change supersedes that posture: the `cyl_scan_intermediates` role-access requirement
is `MODIFIED` to `bloom_writer` `SELECT`-only, and `test_cyl_scan_intermediates.py` is updated —
`test_writer_can_insert_and_update` is **inverted** to assert direct INSERT/UPDATE is now rejected
(and renamed), the module docstring is corrected, and the policy-set drift detector's `expected` set
drops the two `bloom_writer` write pairs while `forbidden` is extended to include `bloom_writer`
INSERT/UPDATE/DELETE/ALL. Intentional, recorded reversal.

## Migration & rollback

Forward-only single migration `supabase/migrations/<ts>_add_cyl_writeback_rpc.sql`: creates the RPC,
`ALTER FUNCTION ... OWNER TO postgres`, its grants, and performs the E policy drops — D and E in one
file so they co-land atomically. Idempotent-safe: `CREATE OR REPLACE FUNCTION`, `DROP POLICY IF
EXISTS`, idempotent `REVOKE`/`GRANT`/`ALTER ... OWNER`. Companion
`supabase/rollbacks/<ts>_add_cyl_writeback_rpc_rollback.sql` (manual break-glass) drops the function
(`DROP FUNCTION IF EXISTS insert_cyl_result_envelope(jsonb)`, with the arg signature) and **re-creates
the dropped policies with byte-exact prior names and definitions**: the two legacy `authenticated`
INSERT policies (`FOR INSERT TO authenticated WITH CHECK (true)`), the three `writer_insert_*` policies
(`FOR INSERT TO bloom_writer WITH CHECK (true)`), and the three `writer_update_*` policies
(`FOR UPDATE TO bloom_writer USING (true) WITH CHECK (true)` — UPDATE policies carry **both** a `USING`
and a `WITH CHECK` clause; re-creating with only one is not catalog-equivalent). The
`test_rollback_restores_prior_policies` test asserts policy expression equality (`polqual`/
`polwithcheck` via `pg_get_expr`), not merely policy-name presence, so a non-equivalent re-create is
caught.

## Risks / trade-offs

- **`SECURITY DEFINER` surface.** Mitigated by pinned owner + pinned `search_path`, schema-qualified
  writes, parameterized value binding (no `format()` on data), `REVOKE ... FROM PUBLIC`, narrow
  `GRANT EXECUTE`, the no-`FORCE RLS` guard, and strict in-function validation before any write.
- **Reviewer coordination.** `bloom_writer` is @blm3886's (Benfica's) design; the carve-out is scoped
  to three pipeline-only tables and leaves Bloom Desktop writes intact — called out for review.
- **Test churn in a just-merged capability (change C).** Accepted: the `MODIFIED` requirement + test
  updates keep spec and code in sync rather than letting them drift.
- **`cyl_traits` auto-register widens the RPC's write surface** to the registry table; accepted because
  trait names are low-sensitivity and producer-validated, and locking the registry is out of scope.
