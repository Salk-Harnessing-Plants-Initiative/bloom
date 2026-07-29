## 1. Failing tests first (TDD)

All tests live in `tests/integration/test_cyl_trait_source_provenance.py` (pytest), using
the existing `pg_conn` psycopg fixture from `tests/integration/conftest.py`. They run in
CI's `compose-health-check` job after migrations are applied
(`uv run --extra test pytest tests/integration/ -v`). Do NOT use `supabase/tests/` (its
pgTAP file is not wired into CI, and `implement-database-testing` plans to recreate that
dir). Each test below maps to a spec scenario and is red until section 2 lands.

**Commit grouping (commit safety):** CI applies migrations *before* collecting tests, so
the section-1 tests and the section-2 migration MUST land in the **same commit** — never
commit the tests alone (that commit would be red in CI). The red-first TDD loop is local
only (task 3.2).

**psycopg3 note:** `pg_conn` runs with `autocommit=False`, so a constraint violation aborts
the open transaction; after asserting an expected error you MUST `pg_conn.rollback()` (or
use `SAVEPOINT`/`ROLLBACK TO SAVEPOINT`) before the next statement, or it raises
`InFailedSqlTransaction`. Assert errors via the exception classes
`psycopg.errors.UniqueViolation` / `psycopg.errors.CheckViolation` (or `exc.sqlstate ==
"23505"` / `"23514"` — psycopg3 exposes `.sqlstate`, not psycopg2's `.pgcode`).

- [x] 1.1 Column existence: assert via `information_schema.columns` that
      `cyl_trait_sources` has `metadata` (`data_type = 'jsonb'`) and `idempotency_key`
      (`data_type = 'text'`).
- [x] 1.2 jsonb round-trip: insert a row with `metadata = '{"a":1}'::jsonb`, read it back,
      assert `jsonb_typeof(metadata) = 'object'` and the value round-trips unchanged (guards
      against the column being `text` instead of `jsonb`).
- [x] 1.2b Opaque jsonb: insert a non-object jsonb (`'[1,2]'::jsonb` and/or `'42'::jsonb`)
      into `metadata` and assert it is accepted (proves "no DB-layer shape validation").
- [x] 1.3 UNIQUE: inserting two rows with the same non-null `idempotency_key` raises
      `UniqueViolation` (23505) — use a `SAVEPOINT` so the first (good) insert survives the
      second's failure within one transaction; two rows with `NULL` `idempotency_key` both
      succeed.
- [x] 1.4 Empty-string CHECK: inserting `idempotency_key = ''` raises `CheckViolation`
      (23514), then `rollback()`. (This is the contract's `default: ""` — the highest-risk
      value.)
- [x] 1.5 Additive safety + legacy rows valid: an old-style
      `INSERT INTO cyl_trait_sources (name) VALUES (...)` still succeeds post-migration, and
      reading that row back asserts `metadata IS NULL AND idempotency_key IS NULL` (covers
      both the "existing inserts keep working" and "legacy source rows remain valid"
      scenarios).
- [x] 1.6 Constraint identity (optional but recommended): assert the named constraints
      `cyl_trait_sources_idempotency_key_key` (UNIQUE) and
      `cyl_trait_sources_idempotency_key_nonempty` (CHECK) exist via `pg_constraint`, so
      change D can rely on the names for `ON CONFLICT`.
- [x] 1.7 Rollback (self-contained, since CI only rolls forward): in one transaction on
      `pg_conn`, apply the rollback script body, assert the two columns + both constraints
      are gone, then `ROLLBACK` so the suite leaves the DB untouched.

## 2. Forward migration + rollback script

- [x] 2.1 Scaffold the migration: `make new-migration name=add_cyl_trait_source_provenance`
      (timestamp prefix must exceed the current max so `scripts/lint_migrations.sh` passes).
- [x] 2.2 Write the forward migration (additive only):
      add `metadata jsonb`; add `idempotency_key text`;
      `ADD CONSTRAINT cyl_trait_sources_idempotency_key_key UNIQUE (idempotency_key)`;
      `ADD CONSTRAINT cyl_trait_sources_idempotency_key_nonempty CHECK (idempotency_key IS NULL OR length(idempotency_key) > 0)`.
- [x] 2.3 Write the companion rollback at
      `supabase/rollbacks/<same-version>_add_cyl_trait_source_provenance_rollback.sql`,
      modeled on existing rollbacks (`BEGIN; … COMMIT;`, `DROP CONSTRAINT IF EXISTS` ×2 then
      `DROP COLUMN IF EXISTS` ×2). This is a manual artifact (not applied by CI).

## 3. Verify

- [x] 3.1 Apply locally (`make migrate-local` / `supabase db reset`); confirm a clean apply
      and that a second `db push` is a no-op (idempotent).
- [x] 3.2 Run section 1's tests and confirm green.
- [x] 3.3 Regenerate and commit Supabase TS types: `make gen-types` (syncs **4**
      `database.types.ts` files: `packages/bloom-fs`, `packages/bloom-js`,
      `packages/bloom-nextjs-auth`, `web/lib`), then **manually** update the orphaned 5th,
      `web/types/database.types.ts` (gen-types does not write it). Confirm each shows
      `cyl_trait_sources` with `metadata` + `idempotency_key`.
- [x] 3.4 Run repo lint/format and `openspec validate add-cyl-trait-source-provenance --strict`.
