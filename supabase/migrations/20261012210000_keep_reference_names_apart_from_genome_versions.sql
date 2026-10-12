-- 20261012210000_keep_reference_names_apart_from_genome_versions.sql
--
-- A Cell Ranger run started on a genome version is named <genome>.v<N>, and that name is part
-- of its run key, which is its output folder. A run started by a reference name alone may no
-- longer use a name of that form, so it can't share a run key or output folder with a run on a
-- genome version. Genome names already can't end in .v<N>, so the two never overlap.
-- Also closes the request function to service_role, so only bloom_workflows may start a run.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

-- Fails, changing nothing, if an existing run already breaks the rule.
ALTER TABLE public.rnaseq_runs
    DROP CONSTRAINT IF EXISTS rnaseq_runs_reference_not_a_genome_version;
ALTER TABLE public.rnaseq_runs
    ADD CONSTRAINT rnaseq_runs_reference_not_a_genome_version CHECK (
        genome_version_id IS NOT NULL
        OR workflow_type <> 'scrna-cellranger'
        OR params ->> 'reference' !~ '\.v[0-9]+$'
    );

REVOKE EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID, JSONB, TEXT[], TEXT, JSONB, BIGINT)
    FROM service_role;

COMMIT;
