## Context

Self-hosted Supabase owns schemas `storage` and `auth` with the role
`supabase_admin` (a superuser). The application roles `bloom_user`,
`bloom_admin`, `bloom_agent`, `bloom_writer` need `USAGE` on those schemas so
storage-api can `SET ROLE` into them. `supabase db push` (used by
`make migrate-local`, the prod/staging deploy, and CI `compose-health-check`)
applies every migration after `SET SESSION ROLE postgres`, mirroring Supabase
Cloud. The `bloom_*` roles are themselves **created by migrations**
(`20260414002000_security_groups.sql` for user/admin/agent;
`20260519130000_add_bloom_writer_role.sql` for writer), i.e. by `db push` *after*
container init — so they do not exist at cluster-init time.

## Empirical findings (live dev DB, all probes non-destructive / rolled back)

Run as `postgres` and `supabase_admin` against `bloom_v2_dev-db-dev-1`:

1. **Role topology.** `storage` and `auth` are both owned by `supabase_admin`
   (a superuser). `postgres` is **not** a superuser and **not** a member of
   `supabase_admin`. `postgres` *is* a member of `supabase_storage_admin` and
   `supabase_auth_admin`.
2. **Who can grant schema USAGE.** The `pg_namespace` ACLs show every relevant
   role holds `USAGE` only as `U`/`UC` (no `*` grant option). Granting schema
   `USAGE` requires ownership or grant option, so **only `supabase_admin` (owner +
   superuser) can grant it.** A plain `GRANT USAGE ON SCHEMA storage TO bloom_agent`
   as `postgres` → `WARNING: no privileges were granted`. `SET ROLE
   supabase_storage_admin` / `supabase_auth_admin` then granting → same no-op.
3. **A SECURITY DEFINER function owned by `supabase_admin` cannot be created in a
   `db push` migration.** As `postgres`:
   `CREATE FUNCTION ...; ALTER FUNCTION ... OWNER TO supabase_admin;` →
   `ERROR: must be member of role "supabase_admin"`. The function must therefore be
   *installed by a superuser* — which the init layer is.
4. **Once installed by `supabase_admin`, the SECURITY DEFINER helper works when
   called by `postgres`.** Connected as `supabase_admin`: install the helper
   (owned by `supabase_admin`), `GRANT EXECUTE ... TO postgres`, then
   `REVOKE USAGE ON SCHEMA storage FROM bloom_agent` (as owner — takes;
   `has_schema_privilege` → `f`), `SET LOCAL ROLE postgres`, call the helper →
   `has_schema_privilege` → `t`. **Proven `f`→`t`.**
5. **The downgrade is connection-user-independent.** `migrate-local` connects as
   `supabase_admin`, yet the in-migration grant still no-ops — so `db push`
   downgrades to `postgres` regardless of the login role. The deploy connects as
   `supabase_admin` too, so it downgrades identically; prod's grants are therefore
   historical/manual (task 1 records the confirmation), and any *future* raw
   schema-grant migration would silently no-op in prod as well — which is what the
   CI guard prevents.
6. **`auth`-USAGE asymmetry — settled in #341 as an intentional gap.**
   `has_schema_privilege` shows `bloom_user`/`bloom_admin`/`bloom_agent` hold
   `storage` `USAGE` but **not** `auth` `USAGE`; `auth.uid()` is not
   `SECURITY DEFINER`, so `SET ROLE bloom_user; SELECT auth.uid()` →
   `permission denied for schema auth`. #341 settled that the real request paths do
   not evaluate these policies as those roles, so this is an **intentional
   read-only gap, not a bug to fix here**. This change does **not** widen the matrix
   to cover it; the health check asserts exactly the granted matrix, so the
   asymmetry stays visible.

**Conclusion:** the `SECURITY DEFINER` helper is the right primitive (finding 4),
and finding 3 means it must be **installed by a superuser** — so the natural home
is the **init layer** (`docker-entrypoint-initdb.d`), which runs as the superuser
on every fresh init, rather than a bespoke pre-`db push` step bolted onto three
separate workflows.

## Goals / Non-Goals

- Goals:
  - Apply `bloom_*` schema-`USAGE` via a hardened `SECURITY DEFINER` helper owned by
    `supabase_admin`, **installed by the init layer**, called by a migration.
  - The grant *set* lives in migration history as the single source of truth, with a
    CI anti-drift test against the health-check matrix.
  - Grants applied identically and idempotently across fresh inits (local reset /
    CI / DR); existing persistent prod/staging volumes covered by a checked-in
    one-time manual-apply script.
  - A CI guard that fails any future `GRANT/REVOKE … ON SCHEMA (auth|storage)` in a
    migration (the silent-no-op class that caused #333 and #341).
  - A loud failure (`make check` / CI) when any grant — or the helper — is missing,
    mis-owned, or tampered.
  - Resolve the prod-role sub-question.
- Non-Goals:
  - Creating the helper inside a `db push` migration (infeasible — finding 3).
  - Moving `bloom_*` role *creation* out of migrations into the init layer (bigger
    blast radius; rejected in favor of helper-in-init — see Decisions).
  - Widening the grant matrix to give `bloom_user`/`bloom_admin`/`bloom_agent`
    `auth` USAGE (finding 6 — #341 intentional gap).
  - Changing table/sequence grants (those survive — `postgres` holds them
    `WITH GRANT OPTION`; only schema-level `USAGE` is lost).
  - Re-architecting how `supabase db push` selects its application role.

## Decisions

- **D1 — Install the helper via the init layer, not via three pre-`db push`
  steps.** Mount `supabase/grants/install_bloom_grant_helper.sql` into the db
  service's `docker-entrypoint-initdb.d` in both compose files (alongside the
  existing `roles.sql` mount). `docker-entrypoint-initdb.d` runs as the superuser at
  fresh cluster init, satisfying finding 3 automatically on local-reset / CI / DR —
  no Makefile / `deploy.yml` / `pr-checks.yml` wiring, and the deploy path is left
  untouched. The helper is generic (schema/role are arguments) so it installs at
  init even though the `bloom_*` roles are created later by migrations.
  - *Alternative rejected:* move `bloom_*` role creation **and** grants into the
    init `roles.sql` (no helper needed — init is superuser). More drift-proof and
    more literal to the #341 note, but it pulls role creation out of migrations and
    forces every existing role-creating migration to become guarded/idempotent and
    every env to be reconciled. Too large for this change; the helper-in-init keeps
    role management in migrations and is fully endorsed by the #341 note ("the helper
    … must live in the init layer").

- **D2 — Helper definition + hardening.** `public.bloom_grant_schema_usage(p_schema
  text, p_role text)`: whitelists `p_schema ∈ {storage, auth}` and `p_role LIKE
  'bloom\_%'` (else `RAISE EXCEPTION`), then `EXECUTE format('GRANT USAGE ON SCHEMA
  %I TO %I', p_schema, p_role)` with `SET search_path = ''`. Install asserts
  `proowner = supabase_admin` (a wrong-owner helper would `CREATE OR REPLACE` yet
  still no-op), and `REVOKE EXECUTE … FROM PUBLIC, anon, authenticated` +
  `GRANT EXECUTE … TO postgres` only.
  - *Why hardened:* `public` is PostgREST-exposed
    (`PGRST_DB_SCHEMAS=public,auth,storage`), so an unrevoked function is reachable
    as `POST /rpc/bloom_grant_schema_usage` by `anon`; it grants with superuser
    authority. The whitelist bounds the blast radius even if EXECUTE ever leaks.
    Precedent: `20260408000000_create_custom_access_token_hook.sql`.
  - *Why `public`:* `public` always exists at init; `storage`/`auth` are created
    asynchronously by their APIs. The helper is generic, so `public` + `REVOKE` (the
    repo's existing exposed-function pattern) is the stable home.

- **D3 — Grant set in a migration that calls the helper.** `2026…_apply_bloom_
  schema_usage_via_helper.sql` issues `SELECT bloom_grant_schema_usage(schema, role)`
  for the matrix (timestamp after the role-creating migrations; idempotent). The
  grant *set* is thus in migration history. Because it calls the helper rather than
  running `GRANT … ON SCHEMA`, it is not a silent-no-op and is exempt from the CI
  guard. On a fresh init the helper exists (D1) → succeeds; if absent → errors
  loudly (better than silent no-op).

- **D4 — One-time manual apply for existing persistent volumes.**
  `docker-entrypoint-initdb.d` runs only on a fresh data dir, and prod/staging (and
  any pre-existing local volume) mount a persistent `./volumes/db/data`. For those,
  the operator runs `install_bloom_grant_helper.sql` once as `supabase_admin` —
  documented in a checked-in `supabase/grants/README.md` runbook (promoting the
  ad-hoc setup notes into a reviewed source of truth). The *same* file is the init
  artifact and the manual artifact, so the helper definition has one source. After
  it runs once, the helper persists in the DB and the helper-calling migration (and
  future grant migrations) stick on the next deploy.

- **D5 — CI guard against schema-grant migrations.** A unit test scans
  `supabase/migrations/*.sql` and fails on any `GRANT`/`REVOKE … ON SCHEMA
  (auth|storage)` statement (case/whitespace-tolerant), with a small allowlist for
  the two historical files (`20260428130000`, `20260519130000`). We do **not** edit
  those applied migrations (editing an applied migration breaks `supabase db push`
  history validation); the allowlist carries a comment pointing to the helper-calling
  migration as authoritative. This guard would have caught both #333 and #341.

- **D6 — Single committed grant matrix.** A committed artifact (e.g.
  `supabase/grants/bloom_grant_matrix.json`) is the one source: `check_health.py`
  reads it, the helper-calling migration derives from it, and a CI test asserts the
  migration's grant set equals it (anti-drift).

- **D7 — Detect a wrong-owner/tampered helper in the health check.** Independently of
  install, `check_health.py` asserts the helper exists, is `prosecdef=t`, and is
  owned by `supabase_admin`, plus the full role→schema matrix.

- **D8 — Repair-grant removal is conditional and atomic.** The raw repair grant is on
  PR #330's branch. Remove it only on a base that contains it, never in a commit
  lacking the init helper + helper-calling migration.

## Risks / Trade-offs

- **Existing persistent volumes won't auto-init** → the helper-calling migration
  would error there until the one-time script runs. Mitigation: D4 runbook;
  prod/staging already hold the grants (so the migration is a no-op once the helper
  exists), and the error is loud, not silent.
- The helper *definition* lives outside migration history (in the init artifact).
  Mitigation: it is committed and version-controlled; the migration call, the CI
  guard, and the health check (existence + owner + `prosecdef`) make
  absence/tampering loud.
- A fresh init must mount the helper in both compose files; a missing mount means no
  helper. Mitigation: a compose/shape test can assert the mount exists (mirrors the
  existing init-script mounts), and the health check fails loudly if the helper is
  absent.
- Any `db push` entry point that runs migrations against a volume lacking the helper
  makes the helper-calling migration error. Mitigation: fresh inits get the helper
  via init; persistent envs get it via the one-time script; the error is loud and
  correct, not a silent no-op.
- Matrix drift between the SQL migration and the Python health check. Mitigation: the
  D6 single committed matrix + anti-drift test.

## Migration Plan

1. Land the init-layer helper mount (both compose files) + hardened helper file +
   helper-calling migration + grant matrix + CI guard + health check + tests,
   atomically. Verify with `make verify-dev` (clean reset → up → migrate → check):
   `bloom_agent` gets `storage` `USAGE`, bloommcp persistence write path works,
   helper EXECUTE is `postgres`-only, whitelist rejects bad args.
2. Resolve the sub-question (task 1): inspect `deploy.yml` /
   `scripts/deploy_run_supabase.sh`; confirm prod applies as `postgres` and that
   prod's grants are historical/manual. Record in this file (finding 5).
3. Roll out to existing persistent prod/staging: run
   `supabase/grants/install_bloom_grant_helper.sql` once as `supabase_admin` per the
   `README.md` runbook (idempotent; grants already present), so the next deploy's
   helper-calling migration sticks.
4. Coordinate merge order with PR #330 for the repair-grant removal (atomic).

## Open Questions

- **RESOLVED (task 1):** the prod/staging deploy runs
  `scripts/deploy_run_supabase.sh … 'db push …'`, which invokes
  `supabase db push --db-url postgresql://${PG_USER}@localhost:5432/…` with
  `PG_USER=POSTGRES_USER` (= `supabase_admin`). So it *connects* as `supabase_admin`
  yet `db push` still downgrades to `postgres` for migration application — confirming
  finding 5: a raw schema-grant migration would silently no-op in prod too. Prod's
  current grants were therefore applied **manually as `supabase_admin`** at setup
  (per @blm3886), which this change replaces with the init-layer helper + the
  checked-in one-time runbook.
- Should the helper live in a dedicated unexposed schema instead of `public`?
  Deferred — `public` + `REVOKE EXECUTE FROM PUBLIC` matches the repo's existing
  exposed-function convention.
