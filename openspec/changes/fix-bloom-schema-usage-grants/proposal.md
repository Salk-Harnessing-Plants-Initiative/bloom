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
(root-caused on PR #330; see issue #333). Prod/staging hold the grants today only
because they were applied **manually as `supabase_admin`** at setup (confirmed in
task 1).

## What Changes

Make schema grants a **single source of truth** — one file,
`supabase/grants/schema_grants.sql`, that we keep updated and apply as
`supabase_admin` (the schema owner, a superuser) whenever it changes. Applied as
the owner (outside the `db push` role downgrade), the grants stick. This replaces
the temporary #330 repair grant and is the one place schema grants live.

- **`supabase/grants/schema_grants.sql` (new — the single source).** Plain,
  readable, idempotent grants:
  `GRANT USAGE ON SCHEMA storage TO bloom_user, bloom_admin, bloom_agent, bloom_writer;`
  and `GRANT USAGE ON SCHEMA auth TO bloom_writer;`. Header documents that it MUST
  run as `supabase_admin` and must be applied *after* migrations create the roles.
- **Applied as `supabase_admin`, after `db push`, in every path that brings up a DB:**
  - Local: `make migrate-local` (and therefore `make verify-dev`) pipes it after
    `supabase db push`.
  - CI: a new `Apply bloom_* schema-USAGE grants` step in `pr-checks.yml`
    `compose-health-check`, after the migration step. (The `dev-stack-smoke` job
    already runs `migrate-local`, so it is covered.)
  - Ongoing prod / staging: applied **manually when grants change** — the file is
    the reviewed source of truth, replacing ad-hoc setup notes. When we add a future
    table that needs a `bloom_*` role to have schema access, we update this one file
    and re-apply it.
- **Why not the migration path / a `SECURITY DEFINER` helper / the docker init
  layer:** a migration's grant no-ops (db push downgrades to `postgres`); the
  `bloom_*` roles are created *by* migrations, so the docker init layer
  (`docker-entrypoint-initdb.d`) runs *before* they exist and cannot grant to them.
  Applying one file as `supabase_admin` right after migrations is the simplest thing
  that works for both fresh and ongoing stacks, with a single source to update.
- **CI guard (kept; would have caught #333 *and* #341).** A test fails any
  `supabase/migrations/*.sql` containing a raw `GRANT`/`REVOKE … ON SCHEMA
  (auth|storage)` (those silently no-op under `db push`), keeping
  `schema_grants.sql` the one place schema grants live. The two historical offenders
  are allowlisted + byte-pinned (we do not edit applied migrations — that breaks
  `db push` history validation).
- **Health-check guardrail.** `scripts/check_health.py` (`make check`) and the
  integration dev-stack-smoke assert each `bloom_*` role holds its expected schema
  `USAGE`, with the expected set **parsed from `schema_grants.sql`** (single source).
  A silent no-op (file never applied, or applied as the wrong role) fails loudly.
  **BREAKING:** `make check` now fails on a stack missing these grants (intended).
- **Retire the #330 repair grant.** Deletes `scripts/sql/repair_storage_grants.sql`
  and replaces its post-`db push` invocation in `migrate-local` with the
  `schema_grants.sql` apply. (#330 is merged to `staging`, so this is done here.)
- Resolve the open sub-question (task 1): confirm/record that the prod/staging
  deploy applies migrations as the downgraded `postgres` and that prod's grants were
  applied manually as `supabase_admin`.

### Scope (#341)

`bloom_writer` gets `auth` `USAGE`; `bloom_user`/`bloom_admin`/`bloom_agent` do
**not** — #341 settled that as an intentional read-only gap (their `auth.uid()` RLS
paths are not evaluated as those roles). A guard test + the health-check matrix pin
this so the asymmetry stays visible.

## Impact

- Affected specs: new capability `database-role-grants`.
- Affected code:
  - `supabase/grants/schema_grants.sql` (new — the single source; **not** under
    `supabase/migrations/`)
  - `Makefile` (`migrate-local`: apply it as `supabase_admin` after `db push`)
  - `.github/workflows/pr-checks.yml` (`compose-health-check`: apply it after
    migrations)
  - `scripts/check_health.py` (+ unit tests) — parses `schema_grants.sql` and asserts
    the grants
  - `tests/unit/test_schema_usage_grants.py` (CI guard + grant-set + wiring),
    `tests/integration/test_schema_usage_grants.py` (grants present, #341 gap,
    raw-grant-as-postgres no-op)
  - Deletes `scripts/sql/repair_storage_grants.sql` (#330 repair grant)
- Merge-order dependency: **PR #330** (merged to `staging`; this branch merges it and
  retires its repair grant).
- **#341 (settled, not reopened):** auth-schema gap for `bloom_user/admin/agent` is
  intentional; not widened here.
- Related (not modified here): `development-environment` (`fix-local-dev-setup`)
  owns `migrate-local` / `make check`; `deploy-migrations`
  (`add-deploy-migration-runner`) owns the deploy `db push` step (prod/staging apply
  `schema_grants.sql` manually when grants change).
