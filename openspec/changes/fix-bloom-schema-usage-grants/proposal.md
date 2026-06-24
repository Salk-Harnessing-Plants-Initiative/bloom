## Why

`bloom_*` roles need `USAGE` on schema `storage` (and `auth` for `bloom_writer`)
so storage-api can `SET ROLE` into them to resolve `storage.objects`. The
migrations that grant this (`20260428130000_storage_grants_for_bloom_roles.sql`,
`20260519130000_add_bloom_writer_role.sql`) **silently no-op on the local dev
stack**: `supabase db push` applies every migration after `SET SESSION ROLE
postgres`, and `postgres` is not a superuser and not a member of `supabase_admin`
(the owner of `storage`/`auth`). The grant emits `WARNING: no privileges were
granted` — a warning, not an error — so `migrate-local` stays green while
`bloom_agent` never gets schema `USAGE`, surfacing later as
`relation "objects" does not exist` in the bloommcp persistence write path
(root-caused on PR #330; see issue #333).

Per the cross-link from **#341** (and @blm3886's note that the **prod grants were
applied manually as `supabase_admin` at setup** precisely because `db push` runs
as the downgraded `postgres`): the durable fix for schema-`USAGE` grants belongs
**here**, and it belongs in the **privileged init layer**, not a migration. A
migration cannot `CREATE`/`ALTER FUNCTION … OWNER TO supabase_admin` (role
`postgres` isn't a member of the owner), and a plain `GRANT … ON SCHEMA` in a
migration silently no-ops — so the **helper that performs the grants must be
installed by a superuser**, which the init layer (`docker-entrypoint-initdb.d`)
is, on every fresh cluster init.

This change owns the **`storage` grants + the general grant mechanism**. The
`auth`-schema side for `bloom_user`/`bloom_admin`/`bloom_agent` is **#341's
intentional read-only gap — no grant is added here** (those roles' `auth.uid()`
RLS paths are not exercised as those roles; documented, not widened).
`bloom_writer` keeps its `auth` `USAGE` (legitimately needed).

## What Changes

Install a hardened `SECURITY DEFINER` helper owned by `supabase_admin` **via the
init layer** (runs as the superuser at cluster init), and apply the grant set from
a migration that **calls** the helper (so the grant runs with the owner's
authority no matter which role `db push` downgrades to). One mechanism, version
controlled, auto-applied on every fresh init (local reset / CI / DR), so the
grants stop drifting from prod.

- **Helper definition (`supabase/grants/install_bloom_grant_helper.sql`, new).**
  `public.bloom_grant_schema_usage(p_schema, p_role)` — `SECURITY DEFINER`, owned
  by `supabase_admin`, `SET search_path=''`. Body **whitelists** its arguments
  (`p_schema ∈ {storage, auth}`, `p_role LIKE 'bloom\_%'`, else `RAISE EXCEPTION`)
  then `EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', …)`. The install
  `REVOKE EXECUTE … FROM PUBLIC, anon, authenticated` and `GRANT EXECUTE … TO
  postgres` only, and asserts `proowner = supabase_admin` after creation (a
  wrong-owner helper would `CREATE OR REPLACE` yet still no-op grants). Hardened
  because `public` is PostgREST-exposed (`POST /rpc/…`) and the helper grants with
  superuser authority; precedent: `20260408000000_create_custom_access_token_hook.sql`.
- **Install via the init layer (the change vs. the prior design).** Mount that
  same file into the db service's `docker-entrypoint-initdb.d` in **both**
  `docker-compose.dev.yml` and `docker-compose.prod.yml` (alongside the existing
  `roles.sql` mount), so on every **fresh** cluster init the superuser installs the
  helper automatically — no bespoke pre-`db push` step in the Makefile / `deploy.yml`
  / `pr-checks.yml`. The helper is generic (target schema/role are arguments) and
  references no `bloom_*` role at creation, so it installs cleanly at init even
  though the roles are created later by migrations.
- **One-time manual apply for existing persistent volumes
  (`supabase/grants/README.md` runbook + the same `install_bloom_grant_helper.sql`
  as the checked-in source of truth).** `docker-entrypoint-initdb.d` runs **only on
  a fresh data dir**, so existing prod/staging (and any pre-existing local volume)
  will **not** re-init. For those, run `install_bloom_grant_helper.sql` once as
  `supabase_admin` (the prod grants were already applied this way at setup). This
  **promotes the ad-hoc setup notes into a checked-in script** so the manual step
  has a single, reviewed source instead of living in someone's shell history. After
  it runs once, future grant migrations that call the helper stick.
- **Grant set in a migration that calls the helper
  (`supabase/migrations/2026…_apply_bloom_schema_usage_via_helper.sql`, new).**
  Calls the helper for the **complete** set — `storage` `USAGE` to
  `bloom_user`/`bloom_admin`/`bloom_agent`/`bloom_writer`, `auth` `USAGE` to
  `bloom_writer` — so the grant *set* is source-controlled in migration history.
  Idempotent; timestamped after the role-creating migrations. On a fresh init the
  helper already exists (init layer) so this succeeds; if the helper is absent it
  **fails loudly** rather than silently no-oping. (Uses `SELECT
  bloom_grant_schema_usage(…)`, not a literal `GRANT … ON SCHEMA`, so it is not a
  no-op and is exempt from the guard below.)
- **CI guard (would have caught #333 *and* #341).** Add a test that **fails any
  `supabase/migrations/*.sql` containing `GRANT`/`REVOKE … ON SCHEMA (auth|storage)`**
  — those silently no-op under `db push`. The two historical offenders
  (`20260428130000`, `20260519130000`) are **allowlisted** (already applied
  everywhere; we do not edit applied migrations — that would break `db push`
  history validation) with a comment pointing to the helper-calling migration as
  the authoritative path.
- **Health-check guardrail.** Extend `scripts/check_health.py` (`make check`) and
  the integration dev-stack-smoke to assert each `bloom_*` role holds its expected
  schema `USAGE` (driven by a single committed grant matrix the helper-calling
  migration also derives from, with a CI anti-drift test) and that the helper
  exists, is `SECURITY DEFINER`, and is owned by `supabase_admin`. A silent no-op or
  a missing/mis-owned helper then fails loudly. **BREAKING:** `make check` now fails
  on a stack missing these grants (intended).
- **Retire the #330 repair grant (done — #330 is now merged to `staging`).** The
  raw `make migrate-local` repair grant shipped as `scripts/sql/repair_storage_grants.sql`
  (PR #330). This branch merges `staging` and **deletes that file**, replacing the
  post-`db push` raw grant with a pre-`db push` install of the durable helper as
  `supabase_admin` in `migrate-local` (so existing local volumes still self-heal;
  fresh inits use the init layer). The grant set now comes from the helper-calling
  migration, covering the `bloom_writer` widening. Helper + removal land together so
  the stack never has neither path.
- Resolve the open sub-question (task 1): confirm/record that the prod/staging
  deploy applies migrations as the downgraded `postgres` and that prod's grants were
  applied manually as `supabase_admin` — so future schema-grant migrations are known
  to need the helper, not raw `GRANT`.

## Impact

- Affected specs: new capability `database-role-grants`.
- Affected code:
  - `supabase/grants/install_bloom_grant_helper.sql` (new — hardened helper;
    **not** under `supabase/migrations/`; mounted into the init layer + used as the
    one-time manual-apply script)
  - `supabase/grants/README.md` (new — the one-time manual-apply runbook for
    existing persistent prod/staging volumes, source of truth)
  - `supabase/migrations/2026…_apply_bloom_schema_usage_via_helper.sql` (new — calls
    the helper for the complete set; the grant set lives in migration history)
  - `docker-compose.dev.yml` + `docker-compose.prod.yml` (mount the helper install
    into the db service `docker-entrypoint-initdb.d`)
  - `scripts/check_health.py` (+ unit tests) and a committed grant-matrix artifact
  - `tests/unit/` (the migration-grant CI guard; the matrix anti-drift test) and
    `tests/integration/` (dev-stack-smoke grant + helper assertions)
- Merge-order dependency: **PR #330** (owns the repair grant this supersedes).
- **#341 (settled, not reopened):** `bloom_user`/`bloom_admin`/`bloom_agent` lack
  `auth` `USAGE`; #341 settled this as an **intentional read-only gap** (no grant
  needed — those roles' `auth.uid()` paths are not exercised as those roles). This
  change does **not** widen the matrix to cover it; the health check asserts exactly
  the matrix granted, so the asymmetry stays visible and intentional.
- Related (not modified here): `development-environment` (`fix-local-dev-setup`)
  owns `migrate-local` / `make check`; `deploy-migrations`
  (`add-deploy-migration-runner`) owns the deploy `db push` step (now untouched —
  no pre-`db push` helper step needed).
