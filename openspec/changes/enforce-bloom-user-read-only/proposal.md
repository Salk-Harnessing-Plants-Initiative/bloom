## Why

`bloom_user` is meant to be read-only, but still holds a blanket UPDATE grant plus
five `user_update_*` RLS policies that are inert only because the role lacks `auth`
USAGE (four gate on `created_by = auth.uid()`, which raises `permission denied for
schema auth`). That is security-by-a-withheld-grant — if anyone grants `bloom_user`
`auth` USAGE for an unrelated reason, those dormant write policies silently
re-activate. Make read-only _explicit_ by removing the dead grant and policies so
the role's privileges **are** the design, not an accident of a missing grant (#341).

See [design.md](./design.md) for the migration-mechanism analysis (why the
default-privileges revoke matches, prod/local parity) and cross-change
coordination with #333.

## What Changes

A single new migration makes `bloom_user`'s write surface match the intended
design: **read + insert, no update, with one deliberate exception** —
`public.experiment_progress_logs` (the gene-page "Progress" panel writes to it via
a `USING (true)` policy that never touches the `auth` schema; confirmed in-use by
the maintainer).

- **Revoke the blanket UPDATE grant.**
  `REVOKE UPDATE ON ALL TABLES IN SCHEMA public FROM bloom_user;` (covers the
  `security_groups` blanket grant _and_ the per-table scrna grants in one
  statement) and
  `ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE UPDATE ON TABLES FROM bloom_user;`
  so future tables don't silently re-grant UPDATE. The `FOR ROLE postgres` is
  load-bearing: the original `GRANT`/default-privilege entry was issued by `postgres`
  (verified via `pg_default_acl`), so the revoke must target the same role to cancel
  it — a bare revoke run as `supabase_admin` would silently no-op (see design.md).
- **Re-grant the one intentional write path.**
  `GRANT UPDATE ON public.experiment_progress_logs TO bloom_user;` — its
  `user_update_experiment_progress_logs` policy (`FOR UPDATE … USING (true)`) and
  `user_insert`/`user_read` policies are retained unchanged.
- **Drop the now-dead `user_update_*` policies** so read-only is explicit, not
  enforced by a withheld grant. Five policies, all on `public`:
  `user_update_accessions` (`USING (true)`), `user_update_chat_threads`,
  `user_update_cyl_experiments`, `user_update_gene_candidates`,
  `user_update_species` (the last four `USING (created_by = auth.uid())` — the
  inert ones from #341). `experiment_progress_logs`'s update policy is **kept**.
- **No `auth` USAGE is granted.** #341 settled that `bloom_user`/`bloom_admin`/
  `bloom_agent` keep the intentional `auth`-schema gap; this change does **not**
  widen it. It removes the dependency on that gap instead of relying on it.
- **Implemented as a new forward migration, never by editing applied migrations.**
  Editing `20260414002000_security_groups.sql` / `20260506000001_…` would break
  `supabase db push` history validation. The cleanup is plain public-schema DDL
  (DROP POLICY / REVOKE on **postgres-owned** objects), so — unlike #333's
  schema-USAGE grants, which no-op because `auth`/`storage` are _not_ postgres-owned
  — it applies correctly: `db push` executes DDL as `postgres`, which owns these
  tables/policies. It therefore does **not** belong in `schema_grants.sql` and is not
  caught by #333's raw-schema-grant CI guard (it touches `public`, not
  `auth`/`storage`).

### Scope / Non-Goals

- **INSERT is unchanged.** `bloom_user` keeps INSERT (the `user_insert_*` policies
  stay). The #341 thread scopes the cleanup to UPDATE only.
- **`storage.objects` UPDATE is out of scope.** `bloom_user` also holds an inert
  `GRANT … UPDATE ON storage.objects` (its storage policies are all SELECT) — the
  same landmine pattern, but the #341 thread did not cover storage and revoking it
  is a separate `storage`-schema concern. Noted here, deferred.
- **`bloom_admin` / `bloom_agent` unchanged.** Admin keeps full CRUD; agent stays
  read-only. Neither is affected.

## Impact

- Affected specs: `database-role-grants` (ADDED — two requirements: `bloom_user`
  holds UPDATE only on `experiment_progress_logs`; inert UPDATE policies are
  removed). The capability is **founded** by the in-flight #333 change
  `fix-bloom-schema-usage-grants`; this delta extends it.
- **Merge/archive order (hard, for spec coherence):** #333 must merge to `staging`
  **first** — it materializes `database-role-grants` in `openspec/specs/`. This
  change's migration timestamp must sort **after** #333's latest
  (`20260622180000`); use a `2026-06-24`+ timestamp (e.g. `20260624000000`). The two
  are runtime-independent (this migration needs neither `schema_grants.sql` nor the
  #333 CI guard to apply); only spec-archival and timestamp ordering depend on #333.
- Affected code:
  - `supabase/migrations/20260624000000_bloom_user_read_only_cleanup.sql` (new,
    idempotent; header documents #341 + that it supersedes the now-stale
    `bloom_user: SELECT, INSERT, UPDATE` description in the immutable
    `20260414002000_security_groups.sql`)
  - `tests/integration/test_bloom_user_read_only.py` (new)
- Affected docs:
  - `_WIKI/SUPABASE/README.md` — the `bloom_user` `public.*` grants row (the
    human-facing "(today)" source of truth) becomes factually wrong; update it to
    "SELECT + INSERT everywhere; UPDATE only on `experiment_progress_logs`" and
    cross-link the `database-role-grants` spec. (No `CHANGELOG.md` exists in the
    repo — none added.)
- **BREAKING (intended), but no real feature regresses (confirmed in task 1).** Any
  code path that updates `public` tables as `bloom_user` (other than
  `experiment_progress_logs`) is now denied. Due-diligence grep found two web write
  features — `genes/page.tsx` (UPDATE `genes`) and
  `geneCandidatesPage/CurrentStatusUpdate.tsx` (UPDATE `gene_candidates`) — but both
  only ever functioned as **`bloom_writer`**, which this migration leaves untouched
  (its UPDATE grants are direct, not via `bloom_user`). As `bloom_user` those updates
  already failed pre-migration: `gene_candidates` via the inert `auth.uid()` policy
  (errors), `genes` via a missing `user_update_*` policy (RLS yields 0 rows). The
  `accessions` `USING (true)` path is likewise unused. So removing the grant changes
  nothing observable for real users — it makes the already-true read-only design
  explicit.
- Settles #341 (the read-only cleanup half; the auth-decision + grant-mechanism
  half lives in #333).
- Branch targets `staging` (repo is staging-first). Related: #333
  (`fix-bloom-schema-usage-grants`), #330, #323.
