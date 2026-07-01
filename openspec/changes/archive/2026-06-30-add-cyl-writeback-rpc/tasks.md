# Tasks — cyl write-back RPC (D) + RLS lockdown (E)

TDD throughout: write the failing test first, confirm RED against the local Supabase stack
(`make migrate-local` applies migrations; `make test-integration` runs the suite), then implement to
GREEN. **RED is a local working loop, not a commit boundary:** on this protected, staging-first repo
every pushed commit must keep CI green, so the new tests and the migration land **in the same commit**
(do not push a standalone red-tests commit). D and E land in **one** migration file so they co-deploy
atomically. New test file uses the `pg_conn` fixture with `pytest.importorskip("psycopg")` at module
top; a seed helper inserts `cyl_scans` and `cyl_images` rows (with a real `scan_id`) so the
`image_ids → scan_id` resolution path is exercisable, then rolls back.

## 1. Proposal & specs (this change)

- [x] 1.1 Author `proposal.md`, `design.md`, and spec deltas (`cyl-trait-writeback`,
  `contract-pinning`). Validate `openspec validate add-cyl-writeback-rpc --strict`.

## 2. RED — write-back RPC behavior tests (`tests/integration/test_cyl_writeback_rpc.py`)

Write these first; each must FAIL before the migration exists. Build a minimal valid `ResultEnvelope`
jsonb helper and a `cyl_scans`/`cyl_images` seed helper.

- [x] 2.1 **Happy path**: a valid envelope writes one `cyl_trait_sources` row (non-null `name`,
  `metadata` = the provenance object, `idempotency_key` set), N `cyl_scan_traits` rows (each with
  `source_id`, the resolved `scan_id`, and a resolved `trait_id`), and M `cyl_scan_intermediates` rows.
- [x] 2.2 **Source name**: `cyl_trait_sources.name` is non-null and deterministic — re-deriving from
  the same envelope yields the same label (from `pipeline_run_id` when present, else a stable
  key-derived label).
- [x] 2.3 **Return value shape**: the returned `jsonb` has `{source_id, scan_id, trait_count,
  blob_count, was_noop}`; `was_noop` is false on first call, true on re-delivery.
- [x] 2.4 **Idempotent re-delivery**: calling twice with the same envelope yields exactly one source
  row and no duplicate trait/blob rows.
- [x] 2.5 **Immutable provenance**: re-delivery with the same `idempotency_key` but a different
  `metadata` payload does NOT overwrite the stored `cyl_trait_sources.metadata` (first-writer-wins).
- [x] 2.6 **Re-delivery is a pure no-op (short-circuit)**: a second call with the same envelope
  writes nothing further — including a re-delivery with a changed `checksum`/`file_size` for the same
  `(source_id, scan_id, kind, root_type)`, which leaves the existing blob row unchanged (no UPDATE, no
  2nd INSERT) — and returns `was_noop=true`.
- [x] 2.7 **Trait re-delivery does not raise**: re-delivering the same run does not raise on the
  `cyl_scan_traits` `(scan_id, source_id, trait_id)` uniqueness and creates no duplicate.
- [x] 2.8 **contract_version mismatch is rejected**; matching version is accepted.
- [x] 2.9 **Empty / absent idempotency_key is rejected**; the written source row satisfies the
  invariant `idempotency_key == metadata->>'idempotency_key'` (the RPC writes both from one field).
- [x] 2.10 **Trait-name registry (auto-register)**: an unseen `TraitValue.name` creates a `cyl_traits`
  row and the `cyl_scan_traits` row references its `trait_id`; an existing name reuses the id with no
  duplicate registry row. **Cross-delivery idempotency**: after a first envelope registers name `foo`,
  a second envelope (different `idempotency_key`, resolvable scan) carrying `foo` reuses the same
  `cyl_traits.id` (`SELECT count(*) FROM cyl_traits WHERE name='foo'` = 1).
- [x] 2.11 **grain**: an envelope with a `TraitValue` `grain = "image"` is rejected; a `TraitValue`
  that **omits** `grain` is accepted as scan-grain and written.
- [x] 2.12 **NaN/inf/null/overflow → NULL**: values of JSON null, numeric NaN/inf, the strings
  `"NaN"`/`"Infinity"`, **and a finite number beyond `real` range (e.g. `1e40`)** all land as SQL
  `NULL`; a finite in-range value round-trips (to `real` precision). (Pins the post-cast finite check.)
- [x] 2.13 **Scan resolution**: multi-image envelope whose `image_ids` all belong to one scan resolves
  to that `scan_id`; cross-scan (two distinct `scan_id`) is rejected; unknown id (no match), empty
  `image_ids`, non-numeric id, and partial match (some ids unknown) are each rejected cleanly; **a
  duplicate `image_id` whose distinct ids all belong to one scan is accepted** (no false rejection).
- [x] 2.14 **Envelope self-consistency / structure**: a `traits[].scan_key` or `blobs[].scan_key`
  that differs from `provenance.scan_key` is rejected; a non-object jsonb / missing `provenance` /
  missing `inputs` is rejected cleanly; an envelope with empty `traits` and `blobs` writes only the
  source row (counts = 0).
- [x] 2.15 **All-or-nothing incl. registry**: an envelope carrying an **unseen** `TraitValue.name`
  (forcing a `cyl_traits` auto-register) **plus** one later constraint-violating blob row (out-of-vocab
  `kind`) aborts the whole call; assert on a fresh txn that no `cyl_trait_sources` row for the key, no
  `cyl_scan_traits`, **no `cyl_traits` row for the new name**, and no `cyl_scan_intermediates` persist.
- [x] 2.16 **Same key, different scan**: deliver envelope 1 (key `K`, scan `S1`), then envelope 2 (same
  `K`, `image_ids` resolving to `S2 ≠ S1`). Assert: one `cyl_trait_sources` row for `K`; the second
  returns `was_noop=true`; **no `cyl_scan_traits`/`cyl_scan_intermediates` rows at `scan_id=S2`** (the
  short-circuit fires before any write — the key, not the scan, is the identity).

## 3. RED — SECURITY DEFINER hardening + E lockdown (RPC is the sole writer)

- [x] 3.1 **Catalog hardening assertions**: `prosecdef = true`; `proconfig` pins `search_path`;
  `proowner` is `postgres` and `pg_roles.rolbypassrls` is true for it (so a future owner-change that
  loses BYPASSRLS fails loudly); none of the three tables has `relforcerowsecurity = true`.
- [x] 3.2 **EXECUTE grants (exact set)**: `PUBLIC` cannot execute; exactly `bloom_writer`,
  `service_role`, `bloom_admin` hold `EXECUTE` (via `has_function_privilege`).
- [x] 3.3 **Sole-writer**: under `SET LOCAL ROLE bloom_writer` (and `bloom_user`/`authenticated`), a
  direct `INSERT`/`UPDATE` into each of the three tables is **rejected**, while the **same data via the
  RPC in the same assumed-role transaction succeeds**. Guard with an assertion that `bloom_writer`,
  `bloom_user`, `authenticated` are not `BYPASSRLS` (else the denial false-passes). Note `authenticated`
  direct-insert rejection is meaningful only on the two older tables.
- [x] 3.4 `bloom_writer` retains `SELECT` on all three tables.

## 4. RED — update existing tests to the new truth

- [x] 4.1 `tests/integration/test_cyl_scan_intermediates.py`: **invert** `test_writer_can_insert_and_update`
  (rename to `test_writer_cannot_insert_or_update_directly`, expect `psycopg.errors.InsufficientPrivilege`/
  RLS denial) and fix the module docstring. In `test_expected_policy_set_*`: **remove**
  `("bloom_writer","INSERT")` and `("bloom_writer","UPDATE")` from `expected`, and **add `bloom_writer`
  to the `forbidden` comprehension's role filter** so it forbids `bloom_writer` `INSERT`/`UPDATE`/
  `DELETE`/`ALL` (not just edit the literal sets). Surviving writer policy on intermediates is
  `writer_read_cyl_scan_intermediates` (SELECT).
- [x] 4.2 `tests/integration/test_contract_migration_match.py`: flip the three deferred rows
  (key equality, `contract_version` validation, `scan_key`→scan resolution) to active; assert their
  **structural** support — the write-back RPC function exists in `pg_proc`, and the
  `cyl_images.scan_id → cyl_scans.id` FK exists. Add a **regression** test (drop the function / FK in a
  SAVEPOINT → check fails → roll back), mirroring the existing BlobRef regression test. Keep change B's
  `cyl_image_traits.source_id` FK row deferred.

## 5. GREEN — migration (RPC + RLS lockdown), one file

- [x] 5.1 Add `supabase/migrations/<ts>_add_cyl_writeback_rpc.sql`:
  - `CREATE OR REPLACE FUNCTION public.insert_cyl_result_envelope(envelope jsonb) RETURNS jsonb`,
    `LANGUAGE plpgsql`, `SECURITY DEFINER`, `SET search_path = pg_catalog, public, pg_temp` — implement
    structural/contract-version/idempotency/scan_key-consistency validation → scan resolution → the
    **source gate** (`INSERT ... ON CONFLICT (idempotency_key) DO NOTHING RETURNING id`; set `name`):
    if no id returned, **short-circuit** returning `was_noop=true`; otherwise → trait registry
    get-or-create + `cyl_scan_traits` insert (`ON CONFLICT (scan_id, source_id, trait_id) DO NOTHING`;
    post-cast finite check; `coalesce(grain,'scan')` gate) → `cyl_scan_intermediates` insert. All
    validation rejections `RAISE` (no catch-and-continue). Schema-qualify all writes; bind values via
    `jsonb_to_recordset`/parameters (no `format()` on data); return the summary jsonb.
  - `ALTER FUNCTION public.insert_cyl_result_envelope(jsonb) OWNER TO postgres;`
  - `REVOKE EXECUTE ... FROM PUBLIC;` `GRANT EXECUTE ... TO bloom_writer, service_role, bloom_admin;`
  - **E**: `DROP POLICY IF EXISTS` the legacy `authenticated` INSERT policies
    (`"Authenticated users can insert cyl_trait_sources"`, `"Authenticated users can insert cyl_scan_traits"`)
    and the six `bloom_writer` write policies (`writer_insert_cyl_trait_sources`,
    `writer_update_cyl_trait_sources`, `writer_insert_cyl_scan_traits`, `writer_update_cyl_scan_traits`,
    `writer_insert_cyl_scan_intermediates`, `writer_update_cyl_scan_intermediates`). Leave SELECT/admin
    policies intact.
- [x] 5.2 Run `make migrate-local`; iterate the function until tasks 2–4 are GREEN.
- [x] 5.3 **Per-migration `db push` idempotency**: assert applying the migration body a second time is a
  clean no-op (the `CREATE OR REPLACE`/`DROP ... IF EXISTS`/`REVOKE`/`GRANT`/`ALTER OWNER` lines are all
  re-runnable); the existing `test_db_push_is_idempotent` stays green.

## 6. GREEN — rollback, docs, generated types

- [x] 6.1 Add `supabase/rollbacks/<ts>_add_cyl_writeback_rpc_rollback.sql`:
  `DROP FUNCTION IF EXISTS public.insert_cyl_result_envelope(jsonb)` and re-create the dropped policies
  with **byte-exact** prior names/definitions — legacy `authenticated` INSERT (`WITH CHECK (true)`),
  the three `writer_insert_*` (`FOR INSERT ... WITH CHECK (true)`), and the three `writer_update_*`
  (`FOR UPDATE ... USING (true) WITH CHECK (true)` — **both** clauses). Add
  `test_rollback_restores_prior_policies` (apply rollback body in an uncommitted txn; assert function
  gone + all dropped policies recreated **with matching `polqual`/`polwithcheck` expressions**, not just
  names; ROLLBACK).
- [x] 6.2 Update `_WIKI/SUPABASE/README.md`: amend the `bloom_writer` row's **grants cell** (no longer
  "SELECT/INSERT/UPDATE on ~57 of 58 … essentially everything" — SELECT-only on the cyl trait/blob
  triad) **and its Notes tagline** (the "Write anywhere … the code calling it IS the scope, not the DB"
  line is now false for these three tables — the DB is the gate, via the RPC); add a short "Write-back
  RPC" subsection (`insert_cyl_result_envelope`: SECURITY DEFINER owned by postgres, pinned
  `search_path`, REVOKE-from-PUBLIC, EXECUTE to `bloom_writer`/`service_role`/`bloom_admin`).
- [x] 6.3 Regenerate the tracked Supabase types (`make gen-types`, db-dev up + migration applied) so the
  new function appears under `Functions`; commit the **four** gen-types targets (`packages/bloom-fs/...`,
  `packages/bloom-js/...`, `packages/bloom-nextjs-auth/...`, `web/lib/database.types.ts`) **and manually
  update the orphaned fifth copy `web/types/database.types.ts`** (gen-types does not write it; change C
  updated it the same way) — all **five** must carry the new `insert_cyl_result_envelope` `Functions`
  entry.

## 7. Verify

- [x] 7.1 `openspec validate add-cyl-writeback-rpc --strict` passes.
- [x] 7.2 Full integration suite green (`make test-integration`), including the new
  `tests/integration/test_cyl_writeback_rpc.py`, the migration-match check, and the updated change-C
  RLS tests.
- [x] 7.3 Lint/format clean (`make lint` / repo formatters); `test_db_push_is_idempotent` green.
- [x] 7.4 Confirm forward-only migration + rollback restores prior schema (function + policies) on a DB
  where the migration had been applied.
