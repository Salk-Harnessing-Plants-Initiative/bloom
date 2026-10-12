-- Rollback for 20261012210000_keep_reference_names_apart_from_genome_versions.sql
-- Manual break-glass only; nothing runs it automatically. Drops the reference-name rule and
-- lets service_role call the request function again, as before.

BEGIN;

ALTER TABLE public.rnaseq_runs
    DROP CONSTRAINT IF EXISTS rnaseq_runs_reference_not_a_genome_version;

GRANT EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID, JSONB, TEXT[], TEXT, JSONB, BIGINT)
    TO service_role;

COMMIT;
