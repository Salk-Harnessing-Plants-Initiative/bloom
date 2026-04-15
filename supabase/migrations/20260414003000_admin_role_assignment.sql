-- =============================================================================
-- Migration 003: Admin Role Assignment via JWT Hook
-- Maps user metadata to custom roles (bloom_user, bloom_admin).
--
-- To make a user admin: UPDATE auth.users SET raw_user_meta_data =
--   raw_user_meta_data || '{"is_admin": true}' WHERE email = 'admin@salk.edu';
-- =============================================================================

BEGIN;

-- -------------------
-- 1. Create function to assign role based on user metadata
-- -------------------

CREATE OR REPLACE FUNCTION public.custom_access_token_hook(event JSONB)
RETURNS JSONB AS $$
DECLARE
  claims JSONB;
  is_admin BOOLEAN;
BEGIN
  claims := event->'claims';

  -- Check if user has is_admin flag in metadata
  SELECT COALESCE(
    (SELECT (raw_user_meta_data->>'is_admin')::boolean
     FROM auth.users
     WHERE id = (claims->>'sub')::uuid),
    false
  ) INTO is_admin;

  -- Set the role claim
  IF is_admin THEN
    claims := jsonb_set(claims, '{role}', '"bloom_admin"');
  ELSE
    claims := jsonb_set(claims, '{role}', '"bloom_user"');
  END IF;

  -- Update the claims in the event
  event := jsonb_set(event, '{claims}', claims);

  RETURN event;
END;
$$ LANGUAGE plpgsql;

-- Grant execute to supabase_auth_admin (GoTrue uses this role)
GRANT EXECUTE ON FUNCTION public.custom_access_token_hook TO supabase_auth_admin;

-- Revoke from public for security
REVOKE EXECUTE ON FUNCTION public.custom_access_token_hook FROM public;

COMMIT;
