-- Rollback of 20261014120000_let_members_upload_genomes.sql
--
-- Members can no longer start or finish genome versions or add genome files, and
-- start_genome_version no longer checks whether its caller is a member. Genomes and versions
-- members uploaded are kept.

BEGIN;

SET LOCAL lock_timeout = '5s';

DROP POLICY IF EXISTS user_insert_own_genome_upload ON storage.objects;

REVOKE EXECUTE ON FUNCTION public.start_genome_version(TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)
    FROM bloom_user;
REVOKE EXECUTE ON FUNCTION public.finish_genome_version(BIGINT, TEXT, BIGINT, TEXT, BIGINT)
    FROM bloom_user;

DROP FUNCTION IF EXISTS public.member_may_upload_genome_file(TEXT, TEXT);

CREATE OR REPLACE FUNCTION public.start_genome_version(
    p_genome TEXT,
    p_species_id BIGINT DEFAULT NULL,
    p_description TEXT DEFAULT NULL,
    p_assembly TEXT DEFAULT NULL,
    p_annotation TEXT DEFAULT NULL,
    p_source_url TEXT DEFAULT NULL,
    p_notes TEXT DEFAULT NULL
) RETURNS TABLE(version_id BIGINT, version INTEGER, fasta_path TEXT, gtf_path TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
#variable_conflict use_column
DECLARE
    v_user UUID := auth.uid();
    v_genome public.genome_references%ROWTYPE;
    v_row public.genome_reference_versions%ROWTYPE;
BEGIN
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'sign in to upload a genome' USING ERRCODE = '42501';
    END IF;
    IF p_genome IS NULL OR NOT public.genome_name_ok(p_genome) THEN
        RAISE EXCEPTION 'invalid genome name %: use 1 to 64 lowercase letters, digits, ''.'', '
            '''_'' or ''-'', starting with a letter or digit, with no ''__'' and not ending in '
            '''.v'' and a number', p_genome USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_genome FROM public.genome_references g WHERE g.name = p_genome;
    IF NOT FOUND THEN
        IF p_species_id IS NULL THEN
            RAISE EXCEPTION 'genome % is new; give its species', p_genome USING ERRCODE = '22023';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.species s WHERE s.id = p_species_id AND s.deleted_at IS NULL
        ) THEN
            RAISE EXCEPTION 'no species with id % (it may have been deleted)', p_species_id
                USING ERRCODE = '22023';
        END IF;
        -- Another upload may create the same genome first; then this one uses it.
        INSERT INTO public.genome_references (name, species_id, description, created_by)
        VALUES (p_genome, p_species_id, p_description, v_user)
        ON CONFLICT (name) DO NOTHING;
    END IF;

    -- The row lock numbers concurrent uploads of one genome one after another.
    SELECT * INTO v_genome FROM public.genome_references g WHERE g.name = p_genome FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'genome % was removed while starting the upload', p_genome
            USING ERRCODE = '55000';
    END IF;
    IF p_species_id IS NOT NULL AND p_species_id <> v_genome.species_id THEN
        RAISE EXCEPTION 'genome % belongs to species %, not %',
            p_genome, v_genome.species_id, p_species_id USING ERRCODE = '22023';
    END IF;
    IF p_description IS NOT NULL AND p_description IS DISTINCT FROM v_genome.description THEN
        RAISE EXCEPTION 'genome % already has the description %', p_genome,
            coalesce(quote_literal(v_genome.description), 'none') USING ERRCODE = '22023';
    END IF;

    -- genome_reference_versions_number sets the version number and the paths.
    INSERT INTO public.genome_reference_versions (
        genome_id, version, fasta_path, gtf_path, assembly, annotation, source_url, notes,
        created_by
    ) VALUES (
        v_genome.id, 0, '', '', p_assembly, p_annotation, p_source_url, p_notes, v_user
    )
    RETURNING * INTO v_row;

    RETURN QUERY SELECT v_row.id, v_row.version, v_row.fasta_path, v_row.gtf_path;
END;
$$;

NOTIFY pgrst, 'reload schema';

COMMIT;
