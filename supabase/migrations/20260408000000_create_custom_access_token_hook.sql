-- Custom access token hook for GoTrue.
-- Called on every token issue/refresh to enrich the JWT claims.
-- Currently a pass-through; extend later to assign bloom_user/bloom_admin roles.
CREATE OR REPLACE FUNCTION public.custom_access_token_hook(event jsonb)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  claims jsonb;
BEGIN
  claims := event->'claims';

  -- Return the claims unchanged (no-op for now).
  -- To add custom roles later:
  --   claims := jsonb_set(claims, '{user_role}', '"bloom_user"');
  event := jsonb_set(event, '{claims}', claims);
  RETURN event;
END;
$$;

-- Grant execute to supabase_auth_admin so GoTrue can call it.
GRANT USAGE ON SCHEMA public TO supabase_auth_admin;
GRANT EXECUTE ON FUNCTION public.custom_access_token_hook(jsonb) TO supabase_auth_admin;

-- Revoke from public/anon/authenticated for security.
REVOKE EXECUTE ON FUNCTION public.custom_access_token_hook(jsonb) FROM authenticated, anon, public;
