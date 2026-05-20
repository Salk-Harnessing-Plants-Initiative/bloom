-- bloom_writer role: INSERT/UPDATE/SELECT for users flagged is_writer=true.
--
-- Adds a fourth Postgres role alongside bloom_admin, bloom_user, bloom_agent.
-- Routed via the custom_access_token_hook based on
-- auth.users.raw_user_meta_data->>'is_writer'. Inherits bloom_user, so all
-- existing bloom_user RLS policies in 20260506000001_bloom_role_rls_policies.sql
-- continue to apply via Postgres role membership.
--
-- Initial use case: Bloom Desktop writes scans + images to production from
-- approved scientist accounts. The role is intentionally generic ("writer")
-- so additional write-capable clients can reuse it.
--
-- Idempotent — safe to re-apply on environments where this was applied manually.

BEGIN;

-- 1. Create role if missing
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bloom_writer') THEN
    CREATE ROLE bloom_writer NOLOGIN;
  END IF;
END$$;

-- 2. Membership: inherit bloom_user (RLS policies on bloom_user apply via membership)
GRANT bloom_user TO bloom_writer;
-- Supabase machinery can SET ROLE to bloom_writer when JWT claim says so
GRANT bloom_writer TO authenticator;
GRANT bloom_writer TO postgres;

-- 3. public schema grants
GRANT USAGE ON SCHEMA public TO bloom_writer;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO bloom_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO bloom_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE ON TABLES TO bloom_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO bloom_writer;

-- 4. auth schema — needed for auth.uid() in RLS policies + lookups
GRANT USAGE ON SCHEMA auth TO bloom_writer;
GRANT SELECT ON auth.users TO bloom_writer;

-- 5. storage schema — image uploads write storage.objects + companion .info rows
GRANT USAGE ON SCHEMA storage TO bloom_writer;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA storage TO bloom_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA storage TO bloom_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA storage
  GRANT SELECT, INSERT, UPDATE ON TABLES TO bloom_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA storage
  GRANT USAGE, SELECT ON SEQUENCES TO bloom_writer;

-- 6. JWT hook — adds is_writer branch. Hierarchy: admin > writer > user.
CREATE OR REPLACE FUNCTION public.custom_access_token_hook(event jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
AS $function$
DECLARE
  claims    JSONB;
  is_admin  BOOLEAN;
  is_writer BOOLEAN;
BEGIN
  claims := event->'claims';

  SELECT COALESCE(
    (SELECT (raw_user_meta_data->>'is_admin')::boolean
     FROM auth.users WHERE id = (claims->>'sub')::uuid),
    false
  ) INTO is_admin;

  SELECT COALESCE(
    (SELECT (raw_user_meta_data->>'is_writer')::boolean
     FROM auth.users WHERE id = (claims->>'sub')::uuid),
    false
  ) INTO is_writer;

  -- Note: bloom_agent is NOT set here. The LangChain agent uses a pre-generated
  -- static JWT with role=bloom_agent (stored in BLOOM_AGENT_KEY env var) rather
  -- than going through GoTrue. Do not add a bloom_agent branch to this hook.
  IF is_admin THEN
    claims := jsonb_set(claims, '{role}', '"bloom_admin"');
  ELSIF is_writer THEN
    claims := jsonb_set(claims, '{role}', '"bloom_writer"');
  ELSE
    claims := jsonb_set(claims, '{role}', '"bloom_user"');
  END IF;

  event := jsonb_set(event, '{claims}', claims);
  RETURN event;
END;
$function$;

-- 7. RLS policies on every public table for bloom_writer.
-- Skip tables from Langraph implementation. 
-- check Postgres applies before allowing CREATE POLICY.
DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN
    SELECT tablename
    FROM pg_tables
    WHERE schemaname = 'public'
      AND pg_has_role(current_user, tableowner, 'MEMBER')
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS writer_select_%I ON public.%I', r.tablename, r.tablename);
    EXECUTE format('CREATE POLICY writer_select_%I ON public.%I FOR SELECT TO bloom_writer USING (true)', r.tablename, r.tablename);

    EXECUTE format('DROP POLICY IF EXISTS writer_insert_%I ON public.%I', r.tablename, r.tablename);
    EXECUTE format('CREATE POLICY writer_insert_%I ON public.%I FOR INSERT TO bloom_writer WITH CHECK (true)', r.tablename, r.tablename);

    EXECUTE format('DROP POLICY IF EXISTS writer_update_%I ON public.%I', r.tablename, r.tablename);
    EXECUTE format('CREATE POLICY writer_update_%I ON public.%I FOR UPDATE TO bloom_writer USING (true) WITH CHECK (true)', r.tablename, r.tablename);
  END LOOP;
END$$;

-- 8. RLS policies on storage.objects + storage.buckets for bloom_writer.
-- Image uploads via Supabase Storage API hit storage.objects directly; without
-- a matching INSERT policy the storage-api returns 403. buckets is SELECT-only
-- for bucket config lookup.
DROP POLICY IF EXISTS writer_select_objects ON storage.objects;
CREATE POLICY writer_select_objects ON storage.objects
  FOR SELECT TO bloom_writer USING (true);

DROP POLICY IF EXISTS writer_insert_objects ON storage.objects;
CREATE POLICY writer_insert_objects ON storage.objects
  FOR INSERT TO bloom_writer WITH CHECK (true);

DROP POLICY IF EXISTS writer_update_objects ON storage.objects;
CREATE POLICY writer_update_objects ON storage.objects
  FOR UPDATE TO bloom_writer USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS writer_select_buckets ON storage.buckets;
CREATE POLICY writer_select_buckets ON storage.buckets
  FOR SELECT TO bloom_writer USING (true);

COMMIT;
