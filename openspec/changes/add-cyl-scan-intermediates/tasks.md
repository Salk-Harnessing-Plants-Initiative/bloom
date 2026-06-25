# Tasks — add-cyl-scan-intermediates (change C, #296)

TDD throughout: write the failing test first (RED), then the migration/DDL to make it pass (GREEN),
then refactor. All DB tests run against LOCAL Supabase (`make migrate-local`) using the `pg_conn`
fixture, modelled on `tests/integration/test_cyl_trait_source_provenance.py` and (for role
enforcement) `tests/integration/test_embedtree_schema.py`.

**Commit hygiene (read first):**
- The RED verification steps (1.5, 2.10) are **local-only** — never commit a red working tree. The
  test edits ship in the **same commit** as the migration that turns them green.
- Atomic commit grouping: `{forward migration + rollback + both test files + the structural
  migration-match flip + skip-guarded parity}` = **one** commit (CI green); regenerated types = one
  commit (or folded in); `{contract re-pin + enable parity guard + README}` = the **gate** commit
  (green only once the `sleap-roots-contracts` release exists).
- **MERGE BLOCKER**: the PR cannot merge until task 6 (contract re-pin) lands and the `kind`-parity
  assertion is green against the new enum. Branch protection also requires a **non-author** approver
  (`enforce_admins=true`) — do **not** self-merge.

## 0. Pre-work / dependency gate

- [ ] 0.1 Confirm the agreed contract `BlobRef.kind` enum (`{predictions_slp}`) with the contract
      owner; track the `sleap-roots-contracts` revision + release as the **MERGE BLOCKER** (task 6).
- [ ] 0.2 Choose the migration timestamp `<ts>` strictly greater than the max 14-digit timestamp
      across **both** `origin/main` and `origin/staging` (currently `20260622180000`) — `scripts/
      lint_migrations.sh` only checks the PR's base ref, so guarding against staging out-of-order is
      manual. Today is 2026-06-25 → use `20260625HHMMSS`.

## 1. Oracle / migration-match test first (RED)

- [ ] 1.1 In `tests/integration/test_contract_migration_match.py`, flip the `BlobRef` mapping from
      `status="deferred"` to `status="active"`, naming `db_table = "cyl_scan_intermediates"`. Extend
      the active-mapping sweep to assert the table + the mapped columns/types. **Confirm `DEFERRED`
      remains non-empty** afterward (source_id FK, RPC key equality, contract_version, scan_key) so
      the parametrized `test_deferred_mapping_is_skipped` still exercises real rows.
- [ ] 1.2 Add active assertions that the two foreign keys exist by `contype = 'f'` / `confrelid`
      (`source_id`→`cyl_trait_sources`, `scan_id`→`cyl_scans`) and that the at-least-one-location
      CHECK exists. Raise the `checked >=` floor so the sweep can't pass vacuously.
- [ ] 1.3 Add a **negative regression test**: inside a rolled-back transaction, drop one
      `cyl_scan_intermediates` FK (or the location CHECK) and assert the active sweep raises — covers
      the "A regression in a built mapping fails the check" scenario for the new mapping.
- [ ] 1.4 Add the `kind`-parity assertion as a **behavioral INSERT probe** (no constraint-text
      parsing — Postgres rewrites `IN (...)` to `= ANY(ARRAY[...])`, which is brittle): for each
      candidate in `contract_enum ∪ {'h5','labels','qc_image','bogus'}`, attempt an INSERT in a
      `SAVEPOINT/ROLLBACK TO` (with seeded parents + valid `root_type`/location) and collect the
      accepted set; assert it equals `BlobRef.kind` from the vendored schema. **Skip-guard** it
      (`pytest.mark.skipif`/`xfail` with reason) while the vendored enum != `{predictions_slp}`; the
      guard is removed in the task 6 re-pin commit (the vendored enum is still the old 4-value set
      until then).
- [ ] 1.5 Run the suite; confirm the structural assertions (1.1–1.3) FAIL (table absent) and 1.4 is
      skipped — RED.

## 2. Table-shape integration tests first (RED)

Create `tests/integration/test_cyl_scan_intermediates.py` (model on
`test_cyl_trait_source_provenance.py` for column/CHECK/FK introspection and on
`test_embedtree_schema.py` for `SET LOCAL ROLE` enforcement; use `information_schema` for column
types, `pg_constraint` by `contype`/`confrelid`, `SAVEPOINT`/`rollback()` between violation cases).

- [ ] 2.0 **FK-parent seed helper** (in-transaction, rolled back — modelled on `embedtree_seed`):
      inserts **two** `cyl_trait_sources (name)` rows (`source_a`, `source_b`) and **one** `cyl_scans`
      row via `INSERT INTO cyl_scans DEFAULT VALUES RETURNING id` (its FK columns are all nullable, so
      no deeper seeding needed). Seed within `pg_conn`'s own uncommitted txn so child inserts see the
      parents; everything rolls back. All insert-based tests below obtain valid FK ids from it.
- [ ] 2.1 Columns + types exist (`source_id`/`scan_id`/`file_size` `bigint`;
      `kind`/`root_type`/`s3_location`/`box_link`/`checksum` `text`).
- [ ] 2.2 A fully specified `predictions_slp` row (seeded `source_id`, `scan_id`) inserts and
      round-trips; a second row with `checksum`/`file_size` NULL also inserts (nullable integrity
      columns).
- [ ] 2.3 Foreign keys: `source_id`→`cyl_trait_sources`, `scan_id`→`cyl_scans` (by
      `contype='f'`/`confrelid`); a row with a non-existent `scan_id` is rejected (FK violation).
- [ ] 2.4 At-least-one-location CHECK: both locations NULL → rejected; only `box_link` → accepted.
- [ ] 2.5 Strict vocabularies: `kind = 'h5'` rejected; `root_type = 'seminal'` rejected; **each**
      valid `root_type` (`primary`/`lateral`/`crown`) accepted (assert the full set against the
      CHECK — the CHECK is the source of truth for the vocabulary).
- [ ] 2.6 `UNIQUE (source_id, scan_id, kind, root_type)`: insert `(source_a, scan, predictions_slp,
      primary)`; a duplicate of the same 4-tuple is rejected (UniqueViolation); `(source_b, scan,
      predictions_slp, primary)` — same scan/kind/root_type, **different source** — inserts
      successfully (history preserved across runs).
- [ ] 2.7 **RLS enforcement via `SET LOCAL ROLE`** (each case `BEGIN; SET LOCAL ROLE …; …; ROLLBACK`;
      `pg_conn` is `supabase_admin`/`BYPASSRLS`, so catalog rows alone prove nothing):
  - [ ] 2.7a Positive read — `bloom_admin`, `bloom_agent`, `bloom_user`, `bloom_writer` can each
        `SELECT count(*)` from the table.
  - [ ] 2.7b Writer write — under `bloom_writer`, INSERT of a fully valid (seeded-parent) row
        succeeds, and a subsequent UPDATE succeeds. (Load-bearing proof of design D5.)
  - [ ] 2.7c Read-only write denial — under `bloom_user` (and `bloom_agent`), INSERT is rejected.
  - [ ] 2.7d Drift detector — `pg_policies`/`pg_class.relrowsecurity` show RLS enabled and exactly
        the expected policy set, with **no** `bloom_user`/`bloom_agent` write policy.
  - [ ] 2.7e Sanity: assert the target `bloom_*` roles are not themselves `BYPASSRLS` (else 2.7c
        false-passes).
- [ ] 2.8 Forward migration is **additive**: assert `cyl_trait_sources` and `cyl_scans` retain their
      prior column shape after the migration (the new table doesn't alter existing objects).
- [ ] 2.9 Rollback script restores prior shape — reuse change A's **exact** BEGIN/COMMIT strip regex
      (`^\s*(BEGIN|COMMIT)\s*;\s*$`, CRLF-safe via `splitlines()`), apply the body inside the txn,
      assert `cyl_scan_intermediates` is gone, then `rollback()`.
- [ ] 2.10 Run the suite; confirm all of these FAIL — RED.

## 3. Forward migration (GREEN)

- [ ] 3.1 Write `supabase/migrations/<ts>_create_cyl_scan_intermediates.sql`, wrapped in
      `BEGIN; … COMMIT;`:
      `CREATE TABLE IF NOT EXISTS cyl_scan_intermediates` (`id BIGINT PK GENERATED BY DEFAULT AS
      IDENTITY`; `source_id BIGINT NOT NULL REFERENCES cyl_trait_sources(id)`;
      `scan_id BIGINT NOT NULL REFERENCES cyl_scans(id)`;
      `kind TEXT NOT NULL CHECK (kind IN ('predictions_slp'))`;
      `root_type TEXT NOT NULL CHECK (root_type IN ('primary','lateral','crown'))`;
      `s3_location TEXT`, `box_link TEXT`, `checksum TEXT`, `file_size BIGINT`;
      `CHECK (s3_location IS NOT NULL OR box_link IS NOT NULL)`;
      `UNIQUE (source_id, scan_id, kind, root_type)`).
- [ ] 3.2 Add RLS: `ENABLE ROW LEVEL SECURITY`; `bloom_admin` FOR ALL; `bloom_agent` SELECT;
      `bloom_user` SELECT; `bloom_writer` SELECT/INSERT/UPDATE (each `DROP POLICY IF EXISTS` first,
      mirroring `gravi_images` / the embedtree writer migration). **No** write policy for
      `bloom_user`/`bloom_agent`.
- [ ] 3.3 Add table-level GRANTs: `SELECT` to `bloom_user`, `bloom_agent`; `SELECT, INSERT, UPDATE`
      to `bloom_writer`; `SELECT, INSERT, UPDATE, DELETE` to `bloom_admin`. (Public-schema table
      GRANTs stick under `supabase db push` since `postgres` owns `public`. `bloom_user`'s standing
      default-privilege write GRANT is intentionally not the write gate — RLS is.)
- [ ] 3.4 `make migrate-local`; run both test files; iterate to GREEN (1.4 parity stays skipped).

## 4. Manual rollback (GREEN)

- [ ] 4.1 Write `supabase/rollbacks/<ts>_create_cyl_scan_intermediates_rollback.sql`:
      `BEGIN; DROP TABLE IF EXISTS cyl_scan_intermediates; COMMIT;` with a break-glass comment.
- [ ] 4.2 Confirm task 2.9 (rollback test) is GREEN.

## 5. Regenerate tracked types (GREEN)

- [ ] 5.1 With the dev DB up + migrated, `make gen-types` → regenerate the 4 tracked
      `database.types.ts` (`packages/bloom-fs`, `packages/bloom-js`, `packages/bloom-nextjs-auth`,
      `web/lib`).
- [ ] 5.2 Manually update the orphaned 5th: `web/types/database.types.ts` (gen-types does not write
      it). Keep atomic with 5.1 — a partial update where only 4 of 5 files have the table is an
      inconsistency.
- [ ] 5.3 Verify `cyl_scan_intermediates` appears in **all five** files with the expected
      Row/Insert/Update/Relationships shape, and `git diff` on the 4 generated files is otherwise
      empty (no unrelated drift). **Note: CI does not run `gen-types`**, so a missed/partial regen
      ships silently — this is a manual gate.

## 6. Contract re-pin (MERGE BLOCKER / gate commit)

- [ ] 6.1 Once `sleap-roots-contracts` is released with the revised `BlobRef.kind` enum, re-pin
      `contracts/` via the consume-pin procedure (regenerate, do **not** hand-edit): update
      `pin.json`, `schema/result_envelope.schema.json`, `generated/result-envelope.ts`.
- [ ] 6.2 Update `contracts/README.md`: bump the documented "Currently pinned" version, refresh the
      `v0.1.0a1`-vs-`v0.1.0` note, and correct the change-C codegen-caveat enum from
      `(predictions_slp|labels|h5|qc_image)` to `(predictions_slp)`.
- [ ] 6.3 Remove the skip-guard on the task 1.4 `kind`-parity probe (same commit as 6.1).
- [ ] 6.4 Re-run `npm run contracts:check` (drift guard: pin/`$id` + `generated/` byte-identity) and
      the migration-match `kind` parity assertion; confirm both GREEN against the new enum.

## 7. Validate + finalize

- [ ] 7.1 `openspec validate add-cyl-scan-intermediates --strict` passes.
- [ ] 7.2 Full local CI parity for the touched surface per `/pre-merge`: migration lint, integration
      tests (both files), `npm run contracts:check`, `tsc --noEmit` / `next build`, and a
      `git diff`-clean check on the 4 generated `database.types.ts` (no uncommitted gen-types drift).
- [ ] 7.3 Self-review the diff; confirm no `cyl_scan_traits` change and no out-of-scope artifacts.
- [ ] 7.4 At archive (step 9), retire the leftover `Purpose: TBD` lines in
      `openspec/specs/cyl-trait-writeback/spec.md` and `openspec/specs/contract-pinning/spec.md`.
