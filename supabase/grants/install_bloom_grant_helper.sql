-- Durable mechanism for bloom_* schema-USAGE grants (issue #333; cross-link #341).
--
-- WHY THIS FILE IS NOT A MIGRATION
-- `supabase db push` applies every migration after `SET SESSION ROLE postgres`,
-- and `postgres` is neither a superuser nor a member of `supabase_admin` (the owner
-- of schemas `storage`/`auth`). So a plain `GRANT USAGE ON SCHEMA storage TO
-- bloom_*` inside a migration silently no-ops (`WARNING: no privileges were
-- granted`). A migration also cannot `CREATE`/`ALTER FUNCTION ... OWNER TO
-- supabase_admin` (`ERROR: must be member of role "supabase_admin"`). Therefore the
-- helper that performs the grants must be installed by a SUPERUSER.
--
-- HOW IT IS INSTALLED
--   * Fresh inits (local reset, CI, disaster recovery): mounted into the db
--     container's docker-entrypoint-initdb.d, which runs as the superuser at
--     cluster init (see docker-compose.dev.yml / docker-compose.prod.yml).
--   * Existing persistent volumes (prod, staging, pre-existing local): run this
--     file ONCE as `supabase_admin` — see supabase/grants/README.md.
--
-- The grant SET is applied by a migration that CALLS this helper
-- (supabase/migrations/20260624120000_apply_bloom_schema_usage_via_helper.sql).
-- Idempotent: CREATE OR REPLACE + re-issued REVOKE/GRANT.

CREATE OR REPLACE FUNCTION public.bloom_grant_schema_usage(p_schema text, p_role text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  -- Whitelist arguments: this runs with supabase_admin's authority and lives in a
  -- PostgREST-exposed schema, so bound the blast radius even if EXECUTE ever leaks.
  IF p_schema NOT IN ('storage', 'auth') THEN
    RAISE EXCEPTION
      'bloom_grant_schema_usage: schema % not allowed (storage|auth only)', p_schema;
  END IF;
  IF p_role NOT LIKE 'bloom\_%' THEN
    RAISE EXCEPTION
      'bloom_grant_schema_usage: role % not allowed (bloom_* only)', p_role;
  END IF;
  EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', p_schema, p_role);
END;
$$;

-- Own it as the schema owner so SECURITY DEFINER runs the GRANT with grant authority.
ALTER FUNCTION public.bloom_grant_schema_usage(text, text) OWNER TO supabase_admin;

-- Fail loudly if ownership did not take. A helper accidentally owned by `postgres`
-- would CREATE OR REPLACE successfully yet still no-op grants — the exact bug class
-- this change fixes.
DO $$
BEGIN
  IF (
    SELECT pg_get_userbyid(proowner)
    FROM pg_proc
    WHERE oid = 'public.bloom_grant_schema_usage(text, text)'::regprocedure
  ) <> 'supabase_admin' THEN
    RAISE EXCEPTION
      'bloom_grant_schema_usage must be owned by supabase_admin (install as a superuser)';
  END IF;
END$$;

-- Lock down: superuser-authority grant primitive in a PostgREST-exposed schema
-- (precedent: 20260408000000_create_custom_access_token_hook.sql). Re-issued every
-- run so a drifted ACL self-heals.
REVOKE EXECUTE ON FUNCTION public.bloom_grant_schema_usage(text, text)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.bloom_grant_schema_usage(text, text) TO postgres;
