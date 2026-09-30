-- Add a recipe key, a scan link and run stamps to cyl_trait_sources.
-- Change: add-cyl-trait-recipe-key (bloom#935, bloom#937).
--
-- WHY: pipeline write-back stores one cyl_trait_sources row per scan, so an
--   experiment's latest values are a per-scan patchwork that can mix models and
--   code. Nothing records how a source was computed except the opaque metadata
--   jsonb, and nothing records which Bloom run or Argo Workflow wrote it.
--
-- WHAT:
--   1. cyl_trait_recipe_payload_v1(jsonb) / cyl_trait_recipe_key_v1(jsonb): the
--      recipe key v1 is the sha256 of the contracts idempotency payload minus its
--      per-scan inputs (scan_key, images_checksum, param_hash). Both never raise
--      and return NULL for a non-object argument.
--   2. Five nullable columns: recipe_key, recipe_key_version, scan_id,
--      argo_workflow_name, cyl_pipeline_run_id. Named CHECKs and FKs
--      (ON DELETE SET NULL), and three indexes.
--   3. cyl_backfill_trait_source_recipe_identity(): sets recipe_key,
--      recipe_key_version and scan_id where NULL. Called here and again by the
--      write-back migration, to cover sources written between the two.
--      argo_workflow_name and cyl_pipeline_run_id are never backfilled: nothing
--      recorded them.
--
-- Additive and re-runnable. Never reads cyl_scan_traits.
-- Manual rollback (staging hot-apply only -- see its header):
--   supabase/rollbacks/20260929230000_add_cyl_trait_recipe_key_rollback.sql

BEGIN;

SET LOCAL lock_timeout = '5s';

-- 1. Helpers ----------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.cyl_trait_recipe_payload_v1(metadata jsonb)
RETURNS jsonb
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog, public
AS $fn$
    SELECT CASE
        WHEN metadata IS NULL OR jsonb_typeof(metadata) <> 'object' THEN NULL
        ELSE jsonb_build_object(
                 'models', coalesce((
                     SELECT jsonb_agg(t.triple ORDER BY t.triple::text COLLATE "C")
                       FROM (
                           SELECT jsonb_build_array(
                                      m ->> 'registry_id', m ->> 'version', m ->> 'weights_checksum'
                                  ) AS triple
                             FROM jsonb_array_elements(
                                      CASE WHEN jsonb_typeof(metadata -> 'predict_models') = 'array'
                                           THEN metadata -> 'predict_models'
                                           ELSE '[]'::jsonb END
                                  ) AS m
                       ) AS t
                 ), '[]'::jsonb),
                 'predict_code_sha', metadata ->> 'predict_code_sha',
                 'traits_code_sha', metadata ->> 'traits_code_sha'
             )
             || CASE
                    WHEN jsonb_typeof(metadata -> 'predict_output_params') = 'object'
                         AND metadata -> 'predict_output_params' <> '{}'::jsonb
                    THEN jsonb_build_object('predict_output_params', metadata -> 'predict_output_params')
                    ELSE '{}'::jsonb
                END
    END
$fn$;

CREATE OR REPLACE FUNCTION public.cyl_trait_recipe_key_v1(metadata jsonb)
RETURNS text
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog, public
AS $fn$
    SELECT encode(sha256(convert_to(public.cyl_trait_recipe_payload_v1(metadata)::text, 'UTF8')), 'hex')
$fn$;

ALTER FUNCTION public.cyl_trait_recipe_payload_v1(jsonb) OWNER TO postgres;
ALTER FUNCTION public.cyl_trait_recipe_key_v1(jsonb) OWNER TO postgres;
REVOKE EXECUTE ON FUNCTION public.cyl_trait_recipe_payload_v1(jsonb) FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.cyl_trait_recipe_key_v1(jsonb) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.cyl_trait_recipe_payload_v1(jsonb)
    TO bloom_agent, bloom_user, bloom_admin, authenticated;
GRANT EXECUTE ON FUNCTION public.cyl_trait_recipe_key_v1(jsonb)
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

-- 2. Columns, constraints, indexes -------------------------------------------

ALTER TABLE public.cyl_trait_sources
    ADD COLUMN IF NOT EXISTS recipe_key text,
    ADD COLUMN IF NOT EXISTS recipe_key_version smallint,
    ADD COLUMN IF NOT EXISTS scan_id bigint,
    ADD COLUMN IF NOT EXISTS argo_workflow_name text,
    ADD COLUMN IF NOT EXISTS cyl_pipeline_run_id bigint;

ALTER TABLE public.cyl_trait_sources
    DROP CONSTRAINT IF EXISTS cyl_trait_sources_recipe_key_format_check;
ALTER TABLE public.cyl_trait_sources
    ADD CONSTRAINT cyl_trait_sources_recipe_key_format_check
    CHECK (recipe_key IS NULL OR recipe_key ~ '^[0-9a-f]{64}$' OR recipe_key ~ '^legacy:[0-9]+$');

ALTER TABLE public.cyl_trait_sources
    DROP CONSTRAINT IF EXISTS cyl_trait_sources_recipe_key_version_check;
ALTER TABLE public.cyl_trait_sources
    ADD CONSTRAINT cyl_trait_sources_recipe_key_version_check
    CHECK (recipe_key_version IS NULL OR recipe_key_version = 1);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'cyl_trait_sources_scan_id_fkey'
           AND conrelid = 'public.cyl_trait_sources'::regclass
    ) THEN
        ALTER TABLE public.cyl_trait_sources
            ADD CONSTRAINT cyl_trait_sources_scan_id_fkey FOREIGN KEY (scan_id)
            REFERENCES public.cyl_scans (id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'cyl_trait_sources_cyl_pipeline_run_id_fkey'
           AND conrelid = 'public.cyl_trait_sources'::regclass
    ) THEN
        ALTER TABLE public.cyl_trait_sources
            ADD CONSTRAINT cyl_trait_sources_cyl_pipeline_run_id_fkey FOREIGN KEY (cyl_pipeline_run_id)
            REFERENCES public.cyl_pipeline_runs (id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS cyl_trait_sources_recipe_key_idx
    ON public.cyl_trait_sources (recipe_key);
CREATE INDEX IF NOT EXISTS cyl_trait_sources_scan_id_idx
    ON public.cyl_trait_sources (scan_id);
CREATE INDEX IF NOT EXISTS cyl_pipeline_run_scans_argo_workflow_name_idx
    ON public.cyl_pipeline_run_scans (argo_workflow_name);

-- 3. Backfill ----------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.cyl_backfill_trait_source_recipe_identity()
RETURNS integer
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $fn$
DECLARE
    v_unresolved integer;
BEGIN
    -- Recipe key: the v1 hash for an object metadata, a legacy pseudo-recipe otherwise.
    UPDATE public.cyl_trait_sources s
       SET recipe_key = CASE WHEN jsonb_typeof(s.metadata) = 'object'
                             THEN public.cyl_trait_recipe_key_v1(s.metadata)
                             ELSE 'legacy:' || s.id END,
           recipe_key_version = 1
     WHERE s.recipe_key IS NULL;

    -- Scan: the write-back RPC's own rule. Every image id numeric, every one an
    -- image with a scan, exactly one distinct scan. Anything else stays NULL.
    WITH candidates AS (
        SELECT s.id, s.metadata -> 'inputs' -> 'image_ids' AS image_ids
          FROM public.cyl_trait_sources s
         WHERE s.scan_id IS NULL
           AND jsonb_typeof(s.metadata) = 'object'
           AND jsonb_typeof(s.metadata -> 'inputs' -> 'image_ids') = 'array'
    ), elements AS (
        SELECT c.id, e.value AS image_id
          FROM candidates c, jsonb_array_elements_text(c.image_ids) AS e
    ), resolved AS (
        SELECT el.id,
               bool_and(el.image_id ~ '^[0-9]{1,18}$') AS all_numeric,
               count(DISTINCT el.image_id) AS n_requested,
               count(DISTINCT i.id) AS n_matched,
               count(DISTINCT i.scan_id) AS n_scans,
               min(i.scan_id) AS scan_id
          FROM elements el
          LEFT JOIN public.cyl_images i
                 ON i.id = CASE WHEN el.image_id ~ '^[0-9]{1,18}$' THEN el.image_id::bigint END
                AND i.scan_id IS NOT NULL
         GROUP BY el.id
    )
    UPDATE public.cyl_trait_sources s
       SET scan_id = r.scan_id
      FROM resolved r
     WHERE s.id = r.id
       AND r.all_numeric
       AND r.n_matched = r.n_requested
       AND r.n_scans = 1;

    SELECT count(*) INTO v_unresolved
      FROM public.cyl_trait_sources
     WHERE jsonb_typeof(metadata) = 'object' AND scan_id IS NULL;
    RAISE NOTICE 'cyl recipe backfill: % source(s) with unresolved image_ids', v_unresolved;
    RETURN v_unresolved;
END;
$fn$;

ALTER FUNCTION public.cyl_backfill_trait_source_recipe_identity() OWNER TO postgres;
REVOKE EXECUTE ON FUNCTION public.cyl_backfill_trait_source_recipe_identity()
    FROM PUBLIC, anon, authenticated, service_role;

SELECT public.cyl_backfill_trait_source_recipe_identity();

NOTIFY pgrst, 'reload schema';

COMMIT;
