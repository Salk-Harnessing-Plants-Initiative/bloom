-- SINGLE SOURCE OF TRUTH for bloom_* schema-level USAGE grants (issue #333).
--
-- Keep this file updated whenever a bloom_* role needs USAGE on a new schema, and
-- apply it as `supabase_admin` (the owner of schemas storage/auth, a superuser).
--
-- WHEN / HOW TO APPLY
--   * Local (fresh or ongoing): `make migrate-local` (and `make verify-dev`) run
--     this automatically after `supabase db push`.
--   * CI: the `compose-health-check` job applies it after migrations.
--   * Ongoing prod / staging: apply manually when grants change, e.g.
--       docker compose -f docker-compose.prod.yml exec -T db \
--         psql -v ON_ERROR_STOP=1 -U supabase_admin -d postgres \
--         < supabase/grants/schema_grants.sql
--   Run it AFTER migrations have created the bloom_* roles (it grants directly, so
--   a missing role raises rather than skipping).
--
-- WHY THIS IS A STANDALONE FILE, NOT A MIGRATION
-- `supabase db push` applies migrations after `SET SESSION ROLE postgres`. A
-- `GRANT USAGE ON SCHEMA` only takes effect when run by a role with grant authority
-- on that schema (the owner, or a USAGE ... WITH GRANT OPTION holder), so an
-- in-migration grant sticks only if `postgres` holds grant option on the schema —
-- otherwise it silently no-ops ("WARNING: no privileges were granted"). This splits
-- by schema and by supabase/postgres image version:
--   * auth   — no platform image grants `postgres` grant option on auth, so an
--              in-migration auth grant ALWAYS no-ops. bloom_writer's auth USAGE
--              genuinely requires this supabase_admin path on every supported image.
--   * storage— newer images (>= the 2025-07-09
--              `grant_storage_schema_to_postgres_with_grant_option` migration, e.g.
--              prod/CI's 15.14.1.104) grant `postgres` grant option, so an
--              in-migration storage grant would actually stick there. Older images
--              (dev's 15.8.1.060) do NOT, so it no-ops and bloom_agent cannot resolve
--              storage.objects. The grant here is load-bearing on old images and
--              idempotent belt-and-suspenders on new ones — keeping storage + auth in
--              one place keeps behaviour identical across images.
-- Applied as supabase_admin (the owner, outside the db-push role downgrade) every
-- grant sticks. A CI guard (tests/unit/test_schema_usage_grants.py) blocks raw
-- GRANT/REVOKE ... ON SCHEMA (auth|storage) in supabase/migrations/ so this stays the
-- one place they live, on every image.
--
-- Run as supabase_admin (the owner). Idempotent (GRANT USAGE is idempotent; safe to
-- re-run). scripts/check_health.py parses this file and asserts every pair below is
-- actually granted.
--
-- auth USAGE is granted to bloom_writer ONLY. The auth-schema gap for
-- bloom_user/admin/agent is #341's intentional read-only gap — do not add them here
-- without that review.

GRANT USAGE ON SCHEMA storage TO bloom_user, bloom_admin, bloom_agent, bloom_writer, bloom_workflows;
GRANT USAGE ON SCHEMA auth TO bloom_writer;

-- pgmq USAGE for the cyl-video queue's definer role (20260817010000). Only the schema
-- grant lives here — the queue's table grants are in that migration, transactional with
-- the pgmq.create that makes the tables. Guarded on the role: this file is applied on
-- every deploy under ON_ERROR_STOP=1, so a raise here would abort the grants above and
-- fail unrelated deploys on any database where the migration has not run (or was rolled
-- back, or restored from a dump, which does not carry roles).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bloom_video_queue_owner')
     AND to_regnamespace('pgmq') IS NOT NULL THEN
    GRANT USAGE ON SCHEMA pgmq TO bloom_video_queue_owner;
  END IF;
END
$$;
