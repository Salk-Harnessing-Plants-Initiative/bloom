## Context

Self-hosted Supabase owns schemas `storage` and `auth` with the role
`supabase_admin` (a superuser). The application roles `bloom_user`,
`bloom_admin`, `bloom_agent`, `bloom_writer` need `USAGE` on those schemas so
storage-api can `SET ROLE` into them. `supabase db push` (used by
`make migrate-local`, the prod/staging deploy, and CI `compose-health-check`)
applies every migration after `SET SESSION ROLE postgres`. The `bloom_*` roles are
themselves **created by migrations** (`20260414002000_security_groups.sql` for
user/admin/agent; `20260519130000_add_bloom_writer_role.sql` for writer), i.e. by
`db push` *after* container init — so they do not exist at cluster-init time.

## Empirical findings (live dev DB, non-destructive / rolled back)

1. **Only `supabase_admin` (owner + superuser) can grant schema USAGE.** A plain
   `GRANT USAGE ON SCHEMA storage TO bloom_agent` as `postgres` →
   `WARNING: no privileges were granted`; `has_schema_privilege` stays `f`. As
   `supabase_admin` it sticks (`t`). Verified `f`→`t`.
2. **`db push` downgrades regardless of connection user.** `migrate-local` and the
   deploy both connect as `supabase_admin` via `--db-url`, yet the in-migration grant
   still no-ops — so any schema-grant *migration* silently no-ops everywhere, which is
   why prod's grants were applied manually (task 1).
3. **`auth`-USAGE asymmetry is intentional (#341).** `bloom_user/admin/agent` hold
   `storage` but not `auth` USAGE; #341 settled that the real request paths don't
   evaluate their `auth.uid()` policies as those roles, so it is a documented gap, not
   a bug. Only `bloom_writer` gets `auth` USAGE.

## Goals / Non-Goals

- Goals:
  - One file (`schema_grants.sql`) as the single source of truth for `bloom_*`
    schema grants, applied as `supabase_admin`.
  - Grants present and idempotent across local, CI, and prod/staging.
  - A loud failure (`make check` / CI) when a grant is missing.
  - A CI guard keeping raw schema grants out of migrations (where they no-op).
  - Resolve the prod-role sub-question.
- Non-Goals:
  - A `SECURITY DEFINER` helper / a helper-calling migration (rejected — see D1).
  - Mounting grants in the docker init layer (infeasible — roles absent at init, D1).
  - Widening the matrix to give `bloom_user/admin/agent` `auth` USAGE (#341).
  - Changing table/sequence grants (those survive `db push`).

## Decisions

- **D1 — Single grants file applied as `supabase_admin`, after migrations; no
  helper, no migration, no init-layer mount.** The grant authority problem
  (finding 1/2) is solved simply by applying the grants as the owner outside the
  `db push` downgrade. Two alternatives were considered and rejected:
  - *Helper-calling migration + `SECURITY DEFINER` helper installed at init* (the
    earlier design): works automatically through `db push`, but adds a helper, a
    migration, a matrix file, and a runbook — four artifacts for one concept. The
    maintainer chose a single updatable file over that machinery.
  - *Grants in the docker init layer (`docker-entrypoint-initdb.d`)*: would be
    auto-applied on fresh init, but the `bloom_*` roles are created later by `db
    push`, so at init time they don't exist and the grants would skip. Init can't be
    the application point.
  Fresh-stack reproducibility is preserved: `make verify-dev` / `migrate-local`
  (local) and the CI step apply the file right after `db push` (roles exist by then);
  prod/staging apply it manually when grants change.

- **D2 — Plain, parseable grant statements.** `schema_grants.sql` is two
  `GRANT USAGE ON SCHEMA … TO …;` statements (no `DO`/`FOREACH`, no helper), so it is
  human-readable as the matrix and machine-parseable by the health check. It is
  idempotent (`GRANT USAGE` is). It is applied after roles exist, so no `IF EXISTS`
  guard is needed; a missing role raises loudly rather than skipping silently.

- **D3 — Application points (apply the one file as `supabase_admin`):**
  - Local: `migrate-local` pipes it via `psql -U $PG_USER` (= `supabase_admin`) after
    `db push`.
  - CI: a `compose-health-check` step pipes it via `docker compose exec db-prod psql
    -U supabase_admin` after the migration step. `dev-stack-smoke` uses
    `migrate-local`, so it is covered.
  - Prod/staging: manual, per the file header, when grants change.

- **D4 — CI guard against schema-grant migrations.** A unit test fails any
  `supabase/migrations/*.sql` with a raw `GRANT`/`REVOKE … ON SCHEMA (auth|storage)`
  (comment-stripped, case-insensitive), allowlisting + byte-pinning the two historical
  files (editing an applied migration breaks `db push` history validation). This keeps
  `schema_grants.sql` the one place schema grants live and would have caught #333/#341.

- **D5 — Health check parses the single source.** `check_health.py` reads the
  expected `(schema, role)` set straight from `schema_grants.sql` and asserts
  `has_schema_privilege` for each, reporting (not crashing on) an absent role/schema.
  No second machine-readable matrix.

- **D6 — Retire the #330 repair grant atomically.** Delete
  `scripts/sql/repair_storage_grants.sql` and replace its `migrate-local` invocation
  with the `schema_grants.sql` apply, in the same change.

## Risks / Trade-offs

- **Grants no longer ride along with `db push` automatically** — each DB-bringing-up
  path applies the file. Mitigation: wired into `migrate-local` (local + dev-stack-
  smoke) and `compose-health-check`; prod/staging manual is the maintainer's chosen
  workflow; the health check fails loudly if a path forgets.
- **Plain grants require running as `supabase_admin`** — applied as `postgres` they
  silently no-op (the original bug). Mitigation: the file header states it; every
  wired application uses `supabase_admin`; the health check catches a wrong/forgotten
  apply.
- **Future schema grants must go in `schema_grants.sql`, not a migration.**
  Mitigation: the CI guard enforces this.
- **A new env must remember the manual apply.** Mitigation: documented in the file
  header; `make check` fails loudly until applied.

## Migration Plan

1. Land `schema_grants.sql` + `migrate-local` apply + CI step + health check + CI
   guard + tests, and delete the #330 repair grant, atomically.
2. Record the task-1 finding (prod applies as `postgres`; grants were manual).
3. Roll out to existing prod/staging: apply `schema_grants.sql` once as
   `supabase_admin` (idempotent; grants already present), and thereafter whenever the
   file changes.

## Open Questions

- **RESOLVED (task 1):** the prod/staging deploy runs `supabase db push --db-url
  postgresql://${PG_USER}@…` with `PG_USER = supabase_admin`, but `db push` still
  downgrades to `postgres` for application — so prod's grants were applied manually as
  `supabase_admin`. `schema_grants.sql` + the manual re-apply is the durable
  replacement.
