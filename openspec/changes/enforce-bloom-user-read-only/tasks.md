## 0. Prerequisite ordering

- [x] 0.1 #333 (`fix-bloom-schema-usage-grants`) merged to `staging` (the
      `test_schema_usage_grants.py` guard is present; capability archives separately).
      Rebased this branch onto the updated `staging`.
- [x] 0.2 Pick the migration timestamp `> 20260622180000` (the latest on staging).
      Use `20260624000000` (today is 2026-06-24).

## 1. Confirm scope (BREAKING-change due diligence, no code)

- [x] 1.1 On the live dev DB, confirmed `bloom_user`'s only intended write path is
      `public.experiment_progress_logs` (INSERT + UPDATE). The four `auth.uid()`
      update policies are inert (auth.uid() raises) and `user_update_accessions`
      (`USING (true)`) has no live caller.
- [x] 1.2 Grepped web + bloommcp + services. Two web write features touch non-exempt
      tables — `genes/page.tsx` (UPDATE `genes`) and
      `geneCandidatesPage/CurrentStatusUpdate.tsx` (UPDATE `gene_candidates`) — but
      both only ever functioned as `bloom_writer` (unaffected: its UPDATE grants are
      direct, not via `bloom_user`). As `bloom_user` both already failed
      pre-migration (inert `auth.uid()` policy / missing UPDATE policy → 0 rows). No
      real feature regresses.

## 2. Write the test first (red)

- [x] 2.1 Add `tests/integration/test_bloom_user_read_only.py`, mirroring the
      `tests/integration/test_schema_usage_grants.py` precedent: use the `pg_conn`
      fixture (connects as `supabase_admin`), switch identity with
      `SET LOCAL ROLE bloom_user`, and `pg_conn.rollback()` in a `finally` so no
      state leaks. Assert (one test per spec scenario):
  - `UPDATE public.species` as `bloom_user` raises `psycopg.errors.InsufficientPrivilege`
    (SQLSTATE **42501**) via `pytest.raises` — pinning that the _table-level UPDATE
    grant_ is gone, not merely the RLS policy (a policy-only removal yields 0-rows,
    a weaker guarantee). After rolling back the aborted txn, a fresh superuser
    `SELECT` shows the row unchanged.
  - `UPDATE public.experiment_progress_logs` as `bloom_user` succeeds (rolled back);
    `has_table_privilege('bloom_user','public.experiment_progress_logs','UPDATE')` is true.
  - `has_table_privilege('bloom_user','public.species', …)`: SELECT true, INSERT true,
    UPDATE false (read+insert preserved; revoke was UPDATE-only).
  - `ALTER DEFAULT PRIVILEGES` bite: in a rolled-back txn, `CREATE TABLE public._adp_probe(...)`
    as the migration-applying role, then assert `bloom_user` UPDATE false, SELECT/INSERT
    true on it; roll back so it doesn't persist.
  - `pg_policies`: the five `user_update_*` policies are absent;
    `user_update_experiment_progress_logs` is present.
  - Counterfactual (structural, do **not** grant `auth` USAGE): assert
    `has_table_privilege('bloom_user','public.species','UPDATE')` false **and** no
    `user_update_*` policy on the five tables — proving a later `auth` USAGE grant
    can't re-enable writes. Comment cites #341/#333 (the auth gap and its
    `test_auth_usage_absent` guard own that invariant).
  - Regression guard: `bloom_admin` UPDATE on `public.species` true; `bloom_agent`
    UPDATE false (neither perturbed).
- [x] 2.2 Run the test against the current (un-migrated) `staging` stack; confirm it
      fails for the right reason — today `bloom_user` still holds the blanket UPDATE
      grant, so the privilege assertions are red. (Red phase stays local; commit green.)

## 3. Migration (green)

- [x] 3.1 Add `supabase/migrations/20260624000000_bloom_user_read_only_cleanup.sql`
      (single `BEGIN; … COMMIT;`, idempotent):
  - `REVOKE UPDATE ON ALL TABLES IN SCHEMA public FROM bloom_user;`
  - `ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE UPDATE ON TABLES FROM bloom_user;`
    (explicit `FOR ROLE postgres` — the original default-priv entry is postgres-keyed;
    a bare revoke can silently no-op. See design.md.)
  - `GRANT UPDATE ON public.experiment_progress_logs TO bloom_user;`
  - `DROP POLICY IF EXISTS user_update_accessions ON public.accessions;`
  - `DROP POLICY IF EXISTS user_update_chat_threads ON public.chat_threads;`
  - `DROP POLICY IF EXISTS user_update_cyl_experiments ON public.cyl_experiments;`
  - `DROP POLICY IF EXISTS user_update_gene_candidates ON public.gene_candidates;`
  - `DROP POLICY IF EXISTS user_update_species ON public.species;`
  - Header comment: explains #341; states it is forward-only (no editing applied
    migrations); notes it **supersedes** the now-stale
    `bloom_user: SELECT, INSERT, UPDATE` description in
    `20260414002000_security_groups.sql` (immutable); records that the default-priv
    entry is postgres-keyed (per `pg_default_acl`) so the `FOR ROLE postgres` revoke
    matches; includes the reverse DDL for forward-fix rollback.
- [x] 3.2 Apply (`make migrate-local`) and confirm the test from §2 now passes (green).

## 4. Docs

- [x] 4.1 Update `_WIKI/SUPABASE/README.md` `bloom_user` `public.*` grants row:
      "SELECT + INSERT everywhere; **no table-level UPDATE on `public.*` except
      `experiment_progress_logs`**". Cross-link the `database-role-grants` spec +
      the new migration. Edit only the `public.*` cell — leave the `storage.objects`
      cell (out of scope).

## 5. Verify

- [x] 5.1 `uv run --extra test pytest tests/integration/test_bloom_user_read_only.py -v`
      (after `make migrate-local`).
- [x] 5.2 `uv run --extra test pytest tests/unit/test_schema_usage_grants.py` — the
      #333 raw-schema-grant CI guard stays green (6 passed); the new migration is
      public-only, not flagged, and not added to the allowlist.
- [x] 5.3 `uv run --extra test pytest tests/integration/test_migrations.py::test_db_push_is_idempotent`
      — proves the new migration re-runs as a no-op.
- [x] 5.4 `bash scripts/lint_migrations.sh origin/staging` — filename pattern + a
      14-digit timestamp strictly greater than staging's max.
- [ ] 5.5 `uv run pre-commit run --all-files` (black + ruff + prettier + gitleaks).
- [ ] 5.6 Note in the PR that prod/staging apply this via the normal migration
      pipeline (forward-only public DDL — no manual superuser step, unlike #333's
      `schema_grants.sql`). `check_health.py` is **not** extended (it is a fast
      liveness probe; the privilege contract lives in this integration test).

## 6. Wrap up

- [x] 6.1 Opened PR #346 against `staging`; body notes the #333 dependency,
      `Closes #341`, the task-1 due diligence, and the reverse DDL.
- [ ] 6.2 Update issue #341 with the resolution (read-only cleanup landed; auth gap
      intentional and unchanged).
- [ ] 6.3 After deploy, archive this change (`openspec archive
enforce-bloom-user-read-only`).
