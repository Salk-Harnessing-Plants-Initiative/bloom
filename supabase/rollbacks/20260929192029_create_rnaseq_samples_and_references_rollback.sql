-- Rollback for 20260929192029_create_rnaseq_samples_and_references.sql (run by hand).
-- Drops the two tables; the folders in S3 and existing runs are untouched.

BEGIN;

DROP TABLE IF EXISTS public.rnaseq_samples;
DROP TABLE IF EXISTS public.rnaseq_references;

COMMIT;
