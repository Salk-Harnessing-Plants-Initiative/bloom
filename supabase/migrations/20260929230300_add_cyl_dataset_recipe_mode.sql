-- Record a recipe on cyl_datasets, and add a recipe mode to create_cyl_dataset.
-- Change: add-cyl-trait-recipe-key (bloom#935).
--
-- WHY: create_cyl_dataset freezes one trait_source_id's rows. Pipeline write-back
--   stores one source per scan, so a dataset built from one pipeline source holds one
--   scan. A dataset should be able to hold one recipe across every scan that has it.
--
-- WHAT:
--   1. cyl_datasets.recipe_key (named CHECK: hex, legacy:<id>, 'unattributed' or
--      NULL), backfilled from each dataset's trait_source_id.
--   2. create_cyl_dataset gains recipe_key text DEFAULT NULL; exactly one of
--      trait_source_id and recipe_key must be given. Recipe mode freezes, per scan,
--      the rows of that scan's source of the recipe (_cyl_trait_recipe_presence,
--      20260929230200). Source mode is unchanged and now records its source's
--      recipe_key. Adding an argument changes the signature, so the 5-argument form
--      is dropped first; the new argument has a default, so bloomctl's named 5-key
--      call still resolves. SECURITY INVOKER and statement_timeout = 0 are kept, and
--      the EXECUTE ACL is re-granted explicitly as it was (PUBLIC, anon,
--      authenticated, service_role).
--
-- No database- or role-level settings (20240904033106 also altered those; not copied).
-- Manual rollback (staging hot-apply only -- see its header):
--   supabase/rollbacks/20260929230300_add_cyl_dataset_recipe_mode_rollback.sql

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE public.cyl_datasets ADD COLUMN IF NOT EXISTS recipe_key text;

ALTER TABLE public.cyl_datasets
    DROP CONSTRAINT IF EXISTS cyl_datasets_recipe_key_format_check;
ALTER TABLE public.cyl_datasets
    ADD CONSTRAINT cyl_datasets_recipe_key_format_check
    CHECK (recipe_key IS NULL OR recipe_key ~ '^[0-9a-f]{64}$'
           OR recipe_key ~ '^legacy:[0-9]+$' OR recipe_key = 'unattributed');

UPDATE public.cyl_datasets d
   SET recipe_key = ts.recipe_key
  FROM public.cyl_trait_sources ts
 WHERE ts.id = d.trait_source_id
   AND d.recipe_key IS NULL;

DROP FUNCTION IF EXISTS public.create_cyl_dataset(text, bigint, bigint, json, json);

CREATE OR REPLACE FUNCTION public.create_cyl_dataset(
    name text,
    experiment_id bigint,
    trait_source_id bigint,
    qc_set_name json,
    timepoints json,
    recipe_key text DEFAULT NULL
)
RETURNS void
LANGUAGE plpgsql
SECURITY INVOKER
SET statement_timeout TO '0'
AS $$
DECLARE
    cyl_dataset_id bigint; -- ID of the new dataset
    qc_set_id bigint;
    _name text := name;
    _experiment_id bigint := experiment_id;
    _timepoints json := timepoints;
    _qc_set_name json := qc_set_name;
    _trait_source_id bigint := trait_source_id;
    _recipe_key text := recipe_key;  -- aliased: the argument shares the columns' name
    _stored_key text;
BEGIN
    IF (_trait_source_id IS NULL) = (_recipe_key IS NULL) THEN
        RAISE EXCEPTION 'create_cyl_dataset: give exactly one of trait_source_id and recipe_key';
    END IF;
    IF _recipe_key IS NOT NULL AND _recipe_key <> 'unattributed' AND NOT EXISTS (
        SELECT 1 FROM cyl_trait_sources ts WHERE ts.recipe_key = _recipe_key
    ) THEN
        RAISE EXCEPTION 'create_cyl_dataset: unknown recipe_key %', _recipe_key;
    END IF;

    -- Get the qc_set_id
    IF _qc_set_name IS NOT NULL THEN
        SELECT id INTO qc_set_id
        FROM cyl_qc_sets
        WHERE cyl_qc_sets.name = _qc_set_name->>'name'::text;
    ELSE
        qc_set_id := NULL;
    END IF;

    IF _trait_source_id IS NOT NULL THEN
        SELECT ts.recipe_key INTO _stored_key FROM cyl_trait_sources ts WHERE ts.id = _trait_source_id;
    ELSE
        _stored_key := _recipe_key;
    END IF;

    -- Create the set
    INSERT INTO cyl_datasets (name, experiment_id, timepoints, cyl_qc_set_id, trait_source_id, recipe_key)
    VALUES (_name, _experiment_id, _timepoints, qc_set_id, _trait_source_id, _stored_key)
    RETURNING id INTO cyl_dataset_id;

    IF _trait_source_id IS NOT NULL THEN
        -- Source mode: one source's rows (unchanged from 20240904033106).
        INSERT INTO cyl_dataset_traits (trait_id, dataset_id)
        SELECT cyl_scan_traits.id as trait_id, cyl_dataset_id as dataset_id
        FROM cyl_scan_traits
        JOIN cyl_scans_extended
        ON cyl_scan_traits.scan_id = cyl_scans_extended.scan_id
        LEFT JOIN (
            SELECT DISTINCT cyl_qc_codes.plant_id
            FROM cyl_qc_codes
            JOIN cyl_qc_set_codes ON cyl_qc_codes.id = cyl_qc_set_codes.code_id
            JOIN cyl_qc_sets ON cyl_qc_sets.id = cyl_qc_set_codes.set_id
            WHERE cyl_qc_sets.name = _qc_set_name->>'name'::text
        ) AS qc_filtered ON cyl_scans_extended.plant_id = qc_filtered.plant_id
        WHERE cyl_scan_traits.source_id = _trait_source_id
        AND cyl_scans_extended.experiment_id = _experiment_id
        AND (_timepoints IS NULL OR cyl_scans_extended.plant_age_days IN (SELECT json_array_elements_text(_timepoints)::int))
        AND (qc_set_id IS NULL OR qc_filtered.plant_id IS NULL);
    ELSE
        -- Recipe mode: each scan's source of the recipe (its NULL-source rows for
        -- 'unattributed'), as the recipe reads define it.
        INSERT INTO cyl_dataset_traits (trait_id, dataset_id)
        SELECT cyl_scan_traits.id as trait_id, cyl_dataset_id as dataset_id
        FROM cyl_scan_traits
        JOIN cyl_scans_extended
        ON cyl_scan_traits.scan_id = cyl_scans_extended.scan_id
        JOIN public._cyl_trait_recipe_presence(ARRAY[_experiment_id], NULL) AS chosen
        ON chosen.scan_id = cyl_scan_traits.scan_id
        AND chosen.recipe_key = _recipe_key
        AND cyl_scan_traits.source_id IS NOT DISTINCT FROM chosen.source_id
        LEFT JOIN (
            SELECT DISTINCT cyl_qc_codes.plant_id
            FROM cyl_qc_codes
            JOIN cyl_qc_set_codes ON cyl_qc_codes.id = cyl_qc_set_codes.code_id
            JOIN cyl_qc_sets ON cyl_qc_sets.id = cyl_qc_set_codes.set_id
            WHERE cyl_qc_sets.name = _qc_set_name->>'name'::text
        ) AS qc_filtered ON cyl_scans_extended.plant_id = qc_filtered.plant_id
        WHERE cyl_scans_extended.experiment_id = _experiment_id
        AND (_timepoints IS NULL OR cyl_scans_extended.plant_age_days IN (SELECT json_array_elements_text(_timepoints)::int))
        AND (qc_set_id IS NULL OR qc_filtered.plant_id IS NULL);
    END IF;
END;
$$;

ALTER FUNCTION public.create_cyl_dataset(text, bigint, bigint, json, json, text) OWNER TO postgres;
REVOKE ALL ON FUNCTION public.create_cyl_dataset(text, bigint, bigint, json, json, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.create_cyl_dataset(text, bigint, bigint, json, json, text)
    TO PUBLIC, anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';

COMMIT;
