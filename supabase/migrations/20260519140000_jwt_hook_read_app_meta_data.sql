-- JWT hook: read role flags from raw_app_meta_data instead of raw_user_meta_data.
--
-- raw_user_meta_data is user-writable from the browser via
-- supabase.auth.updateUser({ data: { ... } }). Reading is_admin / is_writer
-- from it lets any authenticated user grant themselves bloom_admin or
-- bloom_writer by setting the flag on themselves and re-logging in.
--
-- raw_app_meta_data is service-role-only. Users can read their own but not
-- write it from any client SDK. That's where role flags belong.
--
-- This migration:
-- 1. Backfills any is_admin / is_writer flags currently in raw_user_meta_data
--    over to raw_app_meta_data so existing admins/writers don't lose access.
-- 2. Replaces custom_access_token_hook to read from raw_app_meta_data.
--
-- Idempotent — safe to re-apply.

BEGIN;

-- 1. Backfill existing is_admin flags
UPDATE auth.users
SET raw_app_meta_data = COALESCE(raw_app_meta_data, '{}'::jsonb)
                        || jsonb_build_object('is_admin', true)
WHERE (raw_user_meta_data->>'is_admin')::boolean = true
  AND COALESCE((raw_app_meta_data->>'is_admin')::boolean, false) IS DISTINCT FROM true;

-- 2. Backfill existing is_writer flags
UPDATE auth.users
SET raw_app_meta_data = COALESCE(raw_app_meta_data, '{}'::jsonb)
                        || jsonb_build_object('is_writer', true)
WHERE (raw_user_meta_data->>'is_writer')::boolean = true
  AND COALESCE((raw_app_meta_data->>'is_writer')::boolean, false) IS DISTINCT FROM true;

-- 3. Replace hook to read from raw_app_meta_data
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
    (SELECT (raw_app_meta_data->>'is_admin')::boolean
     FROM auth.users WHERE id = (claims->>'sub')::uuid),
    false
  ) INTO is_admin;

  SELECT COALESCE(
    (SELECT (raw_app_meta_data->>'is_writer')::boolean
     FROM auth.users WHERE id = (claims->>'sub')::uuid),
    false
  ) INTO is_writer;

  -- bloom_agent is set out-of-band via static JWT in BLOOM_AGENT_KEY, not here.
  -- Hierarchy: admin > writer > user. Both flags set → admin wins.
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

COMMIT;
