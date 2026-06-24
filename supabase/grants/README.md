# `bloom_*` schema-USAGE grants — the durable mechanism (#333)

`bloom_user`, `bloom_admin`, `bloom_agent`, and `bloom_writer` need `USAGE` on the
`storage` schema (and `bloom_writer` also on `auth`) so storage-api can `SET ROLE`
into them to resolve `storage.objects`. Granting this correctly is subtle because
**`supabase db push` applies migrations as the downgraded `postgres` role**, which
is neither a superuser nor a member of `supabase_admin` (the owner of
`storage`/`auth`). A plain `GRANT USAGE ON SCHEMA … TO bloom_*` in a migration
therefore **silently no-ops** (`WARNING: no privileges were granted`).

## How it works

| Artifact | Role |
| --- | --- |
| [`install_bloom_grant_helper.sql`](install_bloom_grant_helper.sql) | Installs the `SECURITY DEFINER` helper `public.bloom_grant_schema_usage(schema, role)` **owned by `supabase_admin`**. Runs the grant with the owner's authority no matter which role calls it. Hardened: arguments whitelisted, `EXECUTE` revoked from `PUBLIC`/`anon`/`authenticated` and granted only to `postgres`. |
| [`bloom_grant_matrix.json`](bloom_grant_matrix.json) | Single source of truth for the role→schema grant set. Consumed by the migration and by `scripts/check_health.py`. |
| `supabase/migrations/20260624120000_apply_bloom_schema_usage_via_helper.sql` | **Calls** the helper for the matrix. The grant set lives in migration history. |

The helper is installed by a **superuser**, two ways:

1. **Fresh cluster init (automatic).** `install_bloom_grant_helper.sql` is mounted
   into the db container's `docker-entrypoint-initdb.d` in both
   `docker-compose.dev.yml` and `docker-compose.prod.yml`, so it runs as the
   superuser on every fresh init — local `make verify-dev` resets, CI's
   `compose-health-check`, and disaster-recovery rebuilds.
2. **Existing persistent volumes (one-time manual).** `docker-entrypoint-initdb.d`
   runs **only on a fresh data directory**, so prod, staging, and any pre-existing
   local volume will **not** re-init. Install the helper once, as `supabase_admin`
   (see below). This is the durable, reviewed replacement for the ad-hoc manual
   grant that was applied at prod setup.

## One-time manual apply (existing prod / staging / pre-existing local)

Run the helper install once as `supabase_admin` (the schema owner). It is
idempotent — safe to re-run.

```bash
# From inside the db container (adjust container name per environment):
docker compose -f docker-compose.prod.yml exec -T db \
  psql -v ON_ERROR_STOP=1 -U supabase_admin -d postgres \
  < supabase/grants/install_bloom_grant_helper.sql
```

After the helper exists, the next `supabase db push` runs
`20260624120000_apply_bloom_schema_usage_via_helper.sql`, whose helper calls then
apply the grant set (idempotent — prod/staging already hold these grants from the
original manual setup, so re-applying changes nothing).

## Guardrails

- **`scripts/check_health.py`** (`make check`) asserts each `bloom_*` role holds its
  expected schema `USAGE` (from the matrix) and that the helper exists, is
  `SECURITY DEFINER`, and is owned by `supabase_admin`.
- A **CI guard** (`tests/unit/test_no_schema_grant_in_migrations.py`) fails any new
  `supabase/migrations/*.sql` that contains a raw `GRANT`/`REVOKE … ON SCHEMA
  (auth|storage)` (it would silently no-op). Add grants by calling the helper
  instead.

## Scope note (#341)

`bloom_user`, `bloom_admin`, and `bloom_agent` are **not** granted `auth` `USAGE`.
Issue #341 settled this as an intentional read-only gap (their `auth.uid()` RLS
paths are not evaluated as those roles). Only `bloom_writer` gets `auth` `USAGE`.
