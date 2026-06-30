## Context

`bloom_user` is intended read-only, but its privilege set doesn't reflect that:
a blanket `GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public`
(`20260414002000_security_groups.sql`) plus five `user_update_*` RLS policies, four
of which gate on `created_by = auth.uid()` and are inert only because the role lacks
`auth` USAGE (#341). This change removes the UPDATE grant and the dead policies. It
is a security/privilege change with migration-mechanism subtleties (default-privilege
role-matching, prod/local parity) and a hard coordination constraint with #333 —
hence this design note.

## Goals / Non-Goals

- **Goals:** `bloom_user` read-only on `public` except `experiment_progress_logs`;
  read-only enforced by the actual privilege set, not a withheld grant; future
  tables don't silently re-grant UPDATE; behavior identical in local and prod/staging.
- **Non-Goals:** changing INSERT/SELECT; touching `bloom_admin`/`bloom_agent`;
  revoking the analogous inert `storage.objects` UPDATE grant (separate `storage`
  concern, deferred); granting any `auth` USAGE.

## Decisions

- **Forward migration on postgres-owned objects.** The five policy tables
  (`accessions`, `chat_threads`, `cyl_experiments`, `gene_candidates`, `species`) and
  `experiment_progress_logs` are all **postgres-owned** (verified against the live
  dev DB: all 71 public tables owned by `postgres`). `db push` connects as
  `supabase_admin` but executes migration DDL as `postgres`. So `REVOKE`/`DROP POLICY`
  here apply cleanly — unlike #333's `auth`/`storage` grants, which no-op because
  those schemas are owned by `supabase_admin`, not `postgres`. This is why the
  cleanup is a normal migration and **not** `schema_grants.sql` material.

- **`ALTER DEFAULT PRIVILEGES FOR ROLE postgres` (not a bare revoke).** Default
  privileges are keyed to the role that issued them. The original
  `ALTER DEFAULT PRIVILEGES … GRANT … TO bloom_user` was issued by `postgres`
  (confirmed: `pg_default_acl` shows `bloom_user=arw/postgres`). Empirically verified
  on the live DB: a bare `ALTER DEFAULT PRIVILEGES … REVOKE … FROM bloom_user` run as
  `supabase_admin` **silently no-ops** (edits a separate supabase_admin-keyed row and
  leaves the postgres-keyed `w` bit intact); the same statement run as `postgres`, or
  any statement with explicit `FOR ROLE postgres`, correctly strips the bit. We use
  the explicit `FOR ROLE postgres` form so the statement is correct regardless of
  which superuser the runner happens to execute as — removing the single residual
  no-op risk.

- **`GRANT UPDATE` after the blanket `REVOKE`.** Re-grant the one intended write
  path (`experiment_progress_logs`) after the revoke, so ordering yields the exact
  end state (one UPDATE grant on the role).

- **Idempotent.** `DROP POLICY IF EXISTS` + naturally-idempotent `REVOKE`/`GRANT`,
  in a single `BEGIN … COMMIT`. Verified re-runnable (second application is a clean
  no-op; emits a NOTICE on already-absent policies).

## Alternatives considered

- **Edit the original migrations.** Rejected — `db push` history validation breaks if
  applied migration bytes change.
- **Put it in `schema_grants.sql` (the #333 mechanism).** Rejected — that file is for
  grants that no-op under `db push` (auth/storage schema USAGE). These public-schema
  statements apply correctly via the normal pipeline; routing them through the
  manual superuser file would be wrong and lose the migration's history record.
- **Bare `ALTER DEFAULT PRIVILEGES` revoke.** Rejected — silent no-op risk if the
  runner ever applies migrations as `supabase_admin` rather than impersonating
  `postgres` (reproduced live).

## Risks / Trade-offs

- **Silent no-op in prod: LOW.** Mechanism is identical local↔prod/staging (all
  connect as `supabase_admin`, impersonate `postgres` for DDL; objects postgres-owned;
  default-ACL postgres-keyed). The explicit `FOR ROLE postgres` removes the only
  residual divergence risk.
- **BREAKING regression: LOW, contingent on task-1 due diligence.** Risk lives
  entirely in the assumption that no feature uses `bloom_user` UPDATE outside
  `experiment_progress_logs`. The `auth.uid()` policies are already inert; the
  `accessions USING (true)` path is believed unused — task 1 confirms via live DB +
  code grep before the migration lands.

## Migration Plan

- New migration `20260624000000_bloom_user_read_only_cleanup.sql` (timestamp >
  #333's `20260622180000`). Merge #333 to `staging` first (founds the
  `database-role-grants` capability in `specs/`).
- **Rollback = forward-fix** (repo ships no down-migrations). Reverse DDL, to keep in
  the migration header / PR body for on-call use:
  ```sql
  GRANT UPDATE ON ALL TABLES IN SCHEMA public TO bloom_user;
  ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT UPDATE ON TABLES TO bloom_user;
  -- plus re-CREATE POLICY for any dropped user_update_* policy found to be load-bearing
  ```

## Open Questions

- None blocking. `storage.objects` UPDATE revoke is deferred to a follow-up (out of
  #341 scope).
