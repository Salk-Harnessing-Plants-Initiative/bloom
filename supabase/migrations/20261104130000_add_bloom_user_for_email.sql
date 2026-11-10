-- 20261104130000_add_bloom_user_for_email.sql
--
-- bloom_user_for_email(p_email): the one active Bloom account with this Salk email, or NULL.
-- bloommcp calls it to admit people who signed in with Salk through the Claude connector, and
-- refuses on NULL, so anything unexpected returns NULL: input that isn't one printable-ASCII
-- address, a domain other than salk.edu, no account, a deleted, banned, unconfirmed or
-- anonymous account, the workflows login (is_workflows), or two accounts with the address.
-- Matching is equality ignoring case, never a pattern. It returns the account's id, which
-- bloommcp records usage under. bloom_agent, which can't read auth.users, is the only role
-- that may call it.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

CREATE OR REPLACE FUNCTION public.bloom_user_for_email(p_email TEXT)
RETURNS UUID
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    -- RFC 5321's maximum address length.
    v_max_length CONSTANT INTEGER := 254;
    -- The domain check_email_domain allows for every auth.users email.
    v_domain CONSTANT pg_catalog.text := 'salk.edu';
    v_email pg_catalog.text;
    v_ids pg_catalog.uuid[];
BEGIN
    IF p_email IS NULL OR pg_catalog.char_length(p_email) > v_max_length THEN
        RETURN NULL;
    END IF;
    -- One address: printable ASCII only (no spaces), exactly one '@'.
    IF p_email ~ '[^\x21-\x7E]' OR p_email !~ '^[^@]+@[^@]+$' THEN
        RETURN NULL;
    END IF;
    v_email := pg_catalog.lower(p_email);
    IF pg_catalog.split_part(v_email, '@', 2) <> v_domain THEN
        RETURN NULL;
    END IF;

    SELECT pg_catalog.array_agg(u.id) INTO v_ids
    FROM auth.users u
    WHERE pg_catalog.lower(u.email) = v_email
      AND u.deleted_at IS NULL
      AND (u.banned_until IS NULL OR u.banned_until <= pg_catalog.now())
      AND u.email_confirmed_at IS NOT NULL
      AND NOT COALESCE(u.is_anonymous, false)
      -- Admitted only without the flag or with a false value; any other value never raises.
      AND COALESCE(pg_catalog.lower(u.raw_app_meta_data ->> 'is_workflows'), 'false')
          IN ('false', 'f', 'no', 'n', 'off', '0');

    -- Two accounts with one address are ambiguous; never pick one.
    IF pg_catalog.cardinality(v_ids) = 1 THEN
        RETURN v_ids[1];
    END IF;
    RETURN NULL;
END;
$$;

-- Owned by postgres, which can read auth.users, like the other definer functions.
ALTER FUNCTION public.bloom_user_for_email(TEXT) OWNER TO postgres;

REVOKE ALL ON FUNCTION public.bloom_user_for_email(TEXT)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.bloom_user_for_email(TEXT) TO bloom_agent;

-- PostgREST serves RPCs from a cached schema.
NOTIFY pgrst, 'reload schema';

COMMIT;
