-- Rollback for 20260930050000_add_cyl_trait_recipe_key.sql.
-- Change: add-cyl-trait-recipe-key (bloom#935, bloom#937).
--
-- This is the STAGING HOT-APPLY only. Applying it by hand leaves 20260930050000
-- recorded as applied in supabase_migrations.schema_migrations, so CI, fresh
-- stacks and the next promotion would still apply it. A durable rollback is a new
-- forward migration whose body is this file. After a hand-apply, run
--   supabase migration repair --status reverted 20260930050000
-- or `db push` refuses the checkout.
--
-- ORDER: apply the later rollbacks of this change first (dataset recipe mode,
-- recipe reads, write-back stamping). plpgsql does not track column
-- dependencies, so dropping the columns while a function still uses them would
-- succeed here and fail every later call. The guard below refuses instead.
--
-- LOSES: recipe_key, recipe_key_version and scan_id (derivable again), and the
-- argo_workflow_name / cyl_pipeline_run_id stamps (not derivable).

BEGIN;

DO $$
DECLARE
    v_users text;
BEGIN
    SELECT string_agg(p.oid::regprocedure::text, ', ' ORDER BY p.oid::regprocedure::text)
      INTO v_users
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
       AND p.prokind IN ('f', 'p')
       AND p.oid <> ALL (array_remove(ARRAY[
               to_regprocedure('public.cyl_trait_recipe_payload_v1(jsonb)')::oid,
               to_regprocedure('public.cyl_trait_recipe_key_v1(jsonb)')::oid,
               to_regprocedure('public.cyl_backfill_trait_source_recipe_identity()')::oid
           ], NULL))
       AND p.prosrc ~ '(recipe_key|cyl_pipeline_run_id|cyl_trait_recipe_key_v1|cyl_trait_recipe_payload_v1)';
    IF v_users IS NOT NULL THEN
        RAISE EXCEPTION 'rollback refused: % still reference(s) the recipe columns or helpers; '
                        'apply the later add-cyl-trait-recipe-key rollbacks first', v_users;
    END IF;
END $$;

DROP FUNCTION IF EXISTS public.cyl_backfill_trait_source_recipe_identity();
DROP FUNCTION IF EXISTS public.cyl_trait_recipe_key_v1(jsonb);
DROP FUNCTION IF EXISTS public.cyl_trait_recipe_payload_v1(jsonb);

DROP INDEX IF EXISTS public.cyl_pipeline_run_scans_argo_workflow_name_idx;

-- Dropping the columns drops their constraints and indexes with them.
ALTER TABLE public.cyl_trait_sources
    DROP COLUMN IF EXISTS cyl_pipeline_run_id,
    DROP COLUMN IF EXISTS argo_workflow_name,
    DROP COLUMN IF EXISTS scan_id,
    DROP COLUMN IF EXISTS recipe_key_version,
    DROP COLUMN IF EXISTS recipe_key;

NOTIFY pgrst, 'reload schema';

COMMIT;
