-- JWT hook: adding a bloom_workflows branch so the workflows service actually runs
-- least-privilege at runtime.
--
-- The workflows API signs in through GoTrue as its own app user. Without this
-- branch, custom_access_token_hook (20260519140000) forces the JWT role claim to
-- bloom_admin/bloom_writer/bloom_user, so PostgREST/Storage SET ROLE bloom_user
-- and the dedicated bloom_workflows role + column-level grants never take effect.
--
-- bloom_workflows is a dedicated, non-interactive service identity — orthogonal
-- to the human admin > writer > user hierarchy — so it is matched first. The flag
-- lives in raw_app_meta_data (service-role-only, not user-writable from any client
-- SDK), same trust model as is_admin / is_writer.
--
-- Idempotent — safe to re-apply.

BEGIN;

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

COMMIT;
