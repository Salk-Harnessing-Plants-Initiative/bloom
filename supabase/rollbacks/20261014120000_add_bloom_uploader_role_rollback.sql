-- Rollback for 20261014120000_add_bloom_uploader_role.sql
-- Manual break-glass only; nothing runs it automatically. Sign-in maps is_uploader back to
-- bloom_user, and the role, its policies, grants and ownership checks are removed. Genome
-- versions and datasets uploaders already loaded are kept. Take bloom_uploader out of
-- supabase/grants/schema_grants.sql before applying that file again.

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE FUNCTION public.custom_access_token_hook(event jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
AS $function$
DECLARE
  claims       JSONB;
  is_admin     BOOLEAN;
  is_writer    BOOLEAN;
  is_workflows BOOLEAN;
BEGIN
  claims := event->'claims';

  SELECT COALESCE(
    (SELECT (raw_app_meta_data->>'is_admin')::boolean
     FROM auth.users WHERE id = (claims->>'sub')::uuid),
    false
  ) INTO is_admin;

  SELECT COALESCE(
    (SELECT (raw_app_meta_data->>'is_writer')::boolean
     FROM auth.users WHERE id = (claims->>'sub')::uuid),
    false
  ) INTO is_writer;

  SELECT COALESCE(
    (SELECT (raw_app_meta_data->>'is_workflows')::boolean
     FROM auth.users WHERE id = (claims->>'sub')::uuid),
    false
  ) INTO is_workflows;

  -- bloom_agent is set out-of-band via static JWT in BLOOM_AGENT_KEY, not here.
  IF is_workflows THEN
    claims := jsonb_set(claims, '{role}', '"bloom_workflows"');
  ELSIF is_admin THEN
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

-- Every policy naming bloom_uploader, including the read copies.
DO $$
DECLARE
  r RECORD;
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bloom_uploader') THEN
    RETURN;
  END IF;
  FOR r IN
    SELECT schemaname, tablename, policyname FROM pg_policies
     WHERE 'bloom_uploader' = ANY (roles)
  LOOP
    EXECUTE format('DROP POLICY %I ON %I.%I', r.policyname, r.schemaname, r.tablename);
  END LOOP;
  -- Revokes every grant to the role in this database; it owns nothing.
  EXECUTE 'DROP OWNED BY bloom_uploader';
END
$$;

DROP FUNCTION IF EXISTS public.uploader_may_load_scrna_dataset(BIGINT);
DROP FUNCTION IF EXISTS public.uploader_may_reopen_scrna_dataset(BIGINT);
DROP FUNCTION IF EXISTS public.uploader_may_upload_genome_file(TEXT);
DROP FUNCTION IF EXISTS public.uploader_owns_object(TEXT);

DROP ROLE IF EXISTS bloom_uploader;

NOTIFY pgrst, 'reload schema';

COMMIT;
