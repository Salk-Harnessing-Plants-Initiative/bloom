> **Commit safety:** the grants file, its application in `migrate-local` + CI, the
> CI guard, the health check, and the #330 repair-grant removal land together so the
> stack never has neither grant path between commits.

## 1. Resolve the open sub-question (prod application role)

- [x] 1.1 Confirmed the prod/staging deploy (`scripts/deploy_run_supabase.sh` →
      `supabase db push --db-url postgresql://${PG_USER}@…`, `PG_USER=supabase_admin`)
      still downgrades to `postgres` for migration application.
- [x] 1.2 Recorded that prod/staging grants were applied **manually as
      `supabase_admin`** at setup (per @blm3886). See `design.md` Open Questions.

## 2. Single source of truth: `schema_grants.sql`

- [x] 2.1 Add `supabase/grants/schema_grants.sql` (committed; **not** under
      `supabase/migrations/`): plain idempotent
      `GRANT USAGE ON SCHEMA storage TO bloom_user, bloom_admin, bloom_agent, bloom_writer;`
      + `GRANT USAGE ON SCHEMA auth TO bloom_writer;`. Header: MUST run as
      `supabase_admin`, after migrations create the roles; when/how to apply
      (local/CI/manual); #341 note (auth → writer only).

## 3. Apply it as supabase_admin in every DB-bringing-up path

- [x] 3.1 `migrate-local`: pipe `schema_grants.sql` via `psql -U $PG_USER`
      (= `supabase_admin`) **after** `supabase db push`. (Covers `make verify-dev`
      and the `dev-stack-smoke` CI job, which runs `migrate-local`.)
- [x] 3.2 `pr-checks.yml` `compose-health-check`: add an `Apply bloom_* schema-USAGE
      grants` step (psql as `supabase_admin` into `db-prod`) after the migration step.
- [x] 3.3 Document the prod/staging manual apply in the `schema_grants.sql` header
      (apply when grants change).

## 4. CI guard against raw schema grants in migrations

- [x] 4.1 `tests/unit/test_schema_usage_grants.py`: fail any
      `supabase/migrations/*.sql` with a raw `GRANT`/`REVOKE … ON SCHEMA
      (auth|storage)` (comment-stripped), allowlisting + byte-pinning `20260428130000`
      and `20260519130000`. Plus: `schema_grants.sql` grants the expected matrix;
      `auth` only to `bloom_writer`; `migrate-local` applies it after `db push`; CI
      applies it.

## 5. Health-check guardrail

- [x] 5.1 (test-first) Unit test for `schema_usage_problems(expected, observed)` —
      partial case (3 of 4 → 4th reported); and `load_grant_matrix` parses
      `schema_grants.sql`.
- [x] 5.2 `check_schema_usage(conn)` in `scripts/check_health.py`: parse expected
      pairs from `schema_grants.sql`, assert `has_schema_privilege` per pair, report
      (not crash on) an absent role/schema. Wired into `main()`.
- [x] 5.3 Integration tests (`tests/integration/test_schema_usage_grants.py`): grants
      present for the matrix; `auth` USAGE absent for user/admin/agent (#341); raw
      grant as `postgres` no-ops while the same grant as `supabase_admin` sticks.

## 6. Retire the #330 repair grant

- [x] 6.1 #330 merged to `staging`; merged it in and **deleted
      `scripts/sql/repair_storage_grants.sql`**, replacing its post-`db push`
      invocation in `migrate-local` with the `schema_grants.sql` apply.

## 7. Validation

- [x] 7.1 `openspec validate fix-bloom-schema-usage-grants --strict` passes.
- [x] 7.2 Verified on a from-scratch stack: fresh `volumes/db/data` wipe →
      `migrate-local` (`db push` + apply `schema_grants.sql` as `supabase_admin`) →
      `make check` healthy. Final matrix: `bloom_user/admin/agent/writer` hold
      `storage` `USAGE`; only `bloom_writer` holds `auth`; no repair grant runs.
- [x] 7.3 Local CI relevant to this change: `tests/unit` (273 passed, 1 skipped),
      `pip-audit` clean; integration tests pass against the live stack.
- [x] 7.4 #341 settled as an intentional read-only gap (no grant added here).
- [ ] 7.5 Update issue #333 noting the single-source `schema_grants.sql` mechanism.
