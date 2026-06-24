> **Commit safety:** the init-layer helper mount (both compose files), the helper
> file, the helper-calling migration, the CI guard, the health check, and any
> repair-grant removal MUST land together (one PR / squashed) so the stack never has
> neither grant path between commits. Never delete a repair grant in a commit
> lacking the helper migration.

## 1. Resolve the open sub-question (prod application role)

- [x] 1.1 Inspect `.github/workflows/deploy.yml` + `scripts/deploy_run_supabase.sh`
      to confirm the role `supabase db push` applies migrations as in prod/staging
      (finding 5 indicates it downgrades to `postgres` regardless of the
      `supabase_admin` connection user).
- [x] 1.2 Confirm prod/staging hold the `bloom_*` schema-`USAGE` grants because they
      were applied **manually as `supabase_admin`** at setup (per @blm3886). Record
      both findings in `design.md` (Open Questions → resolved).

## 2. SECURITY DEFINER helper (hardened, init-installable)

- [x] 2.1 Add `supabase/grants/install_bloom_grant_helper.sql` (committed; **not**
      under `supabase/migrations/`): `CREATE OR REPLACE FUNCTION
      public.bloom_grant_schema_usage(p_schema text, p_role text) RETURNS void
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=''` whose body **whitelists**
      (`p_schema IN ('storage','auth')`, `p_role LIKE 'bloom\_%'`, else
      `RAISE EXCEPTION`) then `EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', …)`.
- [x] 2.2 In the same file: `ALTER FUNCTION … OWNER TO supabase_admin;` then assert
      `proowner = supabase_admin` (fail loudly if not);
      `REVOKE EXECUTE ON FUNCTION … FROM PUBLIC, anon, authenticated;`
      `GRANT EXECUTE ON FUNCTION … TO postgres;`. Header comment: installed as the
      superuser by the init layer (and once manually on persistent volumes), why
      (link #333 + #341 + `design.md`); idempotent (`CREATE OR REPLACE`, re-issued
      REVOKE/GRANT). The file MUST run cleanly when no `bloom_*` role exists yet (it
      creates only the generic helper; it does not reference any `bloom_*` role).
- [x] 2.3 Re-confirm on a fresh dev DB after the file lands: `prosecdef=t`,
      `proowner=supabase_admin`, `anon`/`authenticated`/`bloom_*` lack EXECUTE, a
      `postgres`-role call flips a revoked grant `f`→`t`, and a bad-arg call
      (`('vault','bloom_user')` / `('auth','postgres')`) raises.

## 3. Install via the init layer + one-time manual apply

- [x] 3.1 Mount `install_bloom_grant_helper.sql` into the db service's
      `docker-entrypoint-initdb.d` in **both** `docker-compose.dev.yml` and
      `docker-compose.prod.yml` (alongside the existing `roles.sql` mount, e.g.
      `…/init-scripts/99-bloom-grant-helper.sql`), so a fresh cluster init installs
      the helper as the superuser.
- [x] 3.2 Add `supabase/grants/README.md`: the one-time manual-apply runbook for
      existing persistent volumes (prod, staging, pre-existing local) — run
      `install_bloom_grant_helper.sql` once as `supabase_admin` (psql exec); note it
      is idempotent and that fresh inits do not need it. This is the source of truth
      that replaces ad-hoc setup notes.
- [x] 3.3 Add a YAML/shape test asserting the helper-install mount exists in both
      compose files' db service (mirror the existing init-script mount expectations).

## 4. Helper-calling migration (the grant set = source of truth)

- [x] 4.1 Add `supabase/migrations/2026MMDDHHMMSS_apply_bloom_schema_usage_via_helper.sql`
      (timestamp `> 20260622180000` **and** `> 20260519130000` so the roles exist)
      calling the helper for the complete set: `storage` `USAGE` →
      `bloom_user`/`bloom_admin`/`bloom_agent`/`bloom_writer`; `auth` `USAGE` →
      `bloom_writer`. Uses `SELECT bloom_grant_schema_usage(…)` (not raw `GRANT … ON
      SCHEMA`). Transaction-wrapped; idempotent; errors loudly if the helper is absent.
- [x] 4.2 Add a committed grant-matrix artifact (e.g.
      `supabase/grants/bloom_grant_matrix.json`) listing the role→schema pairs
      (storage: user/admin/agent/writer; auth: writer only — **no** auth for
      user/admin/agent per #341); the migration and `check_health.py` both derive
      from it.
- [x] 4.3 Confirm the new migration passes `tests/integration/test_lint_migrations.py`
      (filename/timestamp) and is recorded by `test_migrations.py`. Do **not** edit
      the historical grant migrations (editing applied migrations breaks `db push`
      history validation).

## 5. CI guard against raw schema grants in migrations

- [x] 5.1 (test-first) Add a `tests/unit/` test that scans `supabase/migrations/*.sql`
      and fails on any `GRANT`/`REVOKE … ON SCHEMA (auth|storage)` (case/whitespace
      tolerant), allowlisting `20260428130000` and `20260519130000` with a comment
      pointing to the helper-calling migration. Assert the helper-calling migration is
      **not** flagged (it calls the helper) and that the two allowlisted files are not
      edited (byte-stable).

## 6. Health-check guardrail (test-first)

- [x] 6.1 (test-first) Add a pytest unit test for a pure helper that, given the grant
      matrix (from 4.2) and observed grants, returns missing pairs — including a
      partial case (3 of 4 roles granted → the 4th reported). No DB / no docker;
      mirror the `migration_problems` pattern and existing `test_check_health`.
- [x] 6.2 Add `check_schema_usage(conn)` to `scripts/check_health.py`: assert each
      `bloom_*` role holds its expected schema `USAGE` (`has_schema_privilege`, driven
      by the matrix) and assert the helper exists, is `prosecdef=t`, and is owned by
      `supabase_admin`. Wire into `main()`/`_report`. Handle an absent role/schema
      gracefully (report a problem, do not crash).
- [x] 6.3 (test-first) Add integration tests in `tests/integration/` mapped to the
      spec scenarios: helper exists + owner + `prosecdef`; grants stuck for all four
      `storage` roles + `bloom_writer` `auth`; `auth` USAGE **absent** for
      user/admin/agent; in a rolled-back txn `SET LOCAL ROLE postgres` and assert a
      raw grant stays `f` while a helper call flips `f`→`t`; helper-calling migration
      re-apply changes no privileges; `anon`/`bloom_*` cannot EXECUTE the helper.
- [x] 6.4 Add a CI test asserting the helper-calling migration's grant set equals the
      committed matrix (anti-drift).

## 7. Retire the #330 repair grant (conditional, atomic)

- [x] 7.1 **Conditional on PR #330:** if the base contains the raw `make migrate-local`
      repair grant (base #323 commit `f5b89ca`), delete it (the init helper +
      helper-calling migration replace it, covering the `bloom_writer` widening). If
      absent on this branch's base, no-op — do not invent a removal. Coordinate merge
      order with #330; never remove it in a commit lacking the helper path.
- [ ] 7.2 Run `make verify-dev` (clean reset → up → migrate → check): `bloom_agent`
      holds `storage` `USAGE`, the bloommcp persistence write path no longer hits
      `relation "objects" does not exist`, and no raw repair grant runs.

## 8. Validation

- [x] 8.1 `openspec validate fix-bloom-schema-usage-grants --strict` passes.
- [ ] 8.2 `make check` and local CI (`/run-ci-locally`) pass on a freshly reset stack.
- [x] 8.3 Separate issue for the `bloom_user`/`bloom_admin`/`bloom_agent` `auth.uid()`
      / `auth` USAGE gap (finding 6) → filed as #341, **settled as an intentional
      read-only gap** (no grant added here).
- [ ] 8.4 Update issue #333 marking the checklist done, noting the mechanism landed in
      the init layer (helper installed at fresh init + one-time manual apply for
      persistent volumes; migrations call it) and that #341 is cross-linked/settled.
