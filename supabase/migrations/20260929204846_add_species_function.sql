-- 20260929204846_add_species_function.sql
--
-- add_species: lets a signed-in scientist add a species from the scRNA job form.
-- A direct INSERT by bloom_user fails, because species' set_created_by trigger calls
-- auth.uid() and bloom_user has no USAGE on the auth schema. This function runs as its
-- owner, takes created_by from the caller's login, and stores the names in one casing
-- (Genus, epithet, Common name). It never adds a second copy of a species: if the genus
-- and species, or the common name, match one already stored (compared in lower case),
-- it returns that species instead.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

-- Dropped first, so re-running this file replaces the function whatever its return type.
DROP FUNCTION IF EXISTS public.add_species(TEXT, TEXT, TEXT);

-- result is 'added' (a new row) or 'existing' (the same species, in any casing).
CREATE FUNCTION public.add_species(
    p_genus TEXT,
    p_species TEXT,
    p_common_name TEXT
) RETURNS TABLE (id BIGINT, common_name TEXT, genus TEXT, species TEXT, result TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
-- The output columns share names with species' columns; references mean the columns.
#variable_conflict use_column
DECLARE
    v_common_name_max CONSTANT INTEGER := 100;
    v_user UUID := auth.uid();
    v_genus TEXT := btrim(p_genus);
    v_species TEXT := lower(btrim(p_species));
    v_common_name TEXT := regexp_replace(btrim(p_common_name), '\s+', ' ', 'g');
BEGIN
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'sign in to add a species' USING ERRCODE = '42501';
    END IF;
    IF v_genus IS NULL OR v_genus !~ '^[A-Za-z]+$' THEN
        RAISE EXCEPTION 'invalid genus: %', p_genus USING ERRCODE = '22023';
    END IF;
    IF v_species IS NULL OR v_species !~ '^[a-z]+(-[a-z]+)*$' THEN
        RAISE EXCEPTION 'invalid species: %', p_species USING ERRCODE = '22023';
    END IF;
    IF v_common_name IS NULL OR v_common_name = ''
       OR char_length(v_common_name) > v_common_name_max THEN
        RAISE EXCEPTION 'invalid common name: %', p_common_name USING ERRCODE = '22023';
    END IF;
    v_genus := upper(left(v_genus, 1)) || lower(substr(v_genus, 2));
    v_common_name := upper(left(v_common_name, 1)) || substr(v_common_name, 2);

    -- The same genus and species, or the same common name, compared in lower case.
    RETURN QUERY
        SELECT s.id, s.common_name, s.genus, s.species, 'existing'
        FROM public.species s
        WHERE s.deleted_at IS NULL
          AND ((lower(s.genus) = lower(v_genus) AND lower(s.species) = v_species)
               OR lower(s.common_name) = lower(v_common_name))
        ORDER BY (lower(s.genus) = lower(v_genus) AND lower(s.species) = v_species) DESC, s.id
        LIMIT 1;
    IF FOUND THEN
        RETURN;
    END IF;

    BEGIN
        RETURN QUERY
            INSERT INTO public.species (genus, species, common_name, created_by)
            VALUES (v_genus, v_species, v_common_name, v_user)
            RETURNING species.id, species.common_name, species.genus, species.species, 'added';
    EXCEPTION WHEN unique_violation THEN
        -- Added by someone else since the lookup, or held by a removed species.
        RETURN QUERY
            SELECT s.id, s.common_name, s.genus, s.species, 'existing'
            FROM public.species s
            WHERE s.deleted_at IS NULL
              AND ((s.genus = v_genus AND s.species = v_species)
                   OR s.common_name = v_common_name)
            ORDER BY s.id
            LIMIT 1;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'a removed species already has this name' USING ERRCODE = '23505';
        END IF;
    END;
END;
$$;

-- Signed-in scientists, writers and admins; not anon, and not the plain authenticated role.
REVOKE ALL ON FUNCTION public.add_species(TEXT, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.add_species(TEXT, TEXT, TEXT)
    TO bloom_user, bloom_writer, bloom_admin;

COMMIT;
