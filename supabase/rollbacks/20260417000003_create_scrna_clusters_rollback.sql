-- Rollback for 20260417000003_create_scrna_clusters.sql
-- Drops the scrna_clusters table and all dependent objects.

BEGIN;

DROP TABLE IF EXISTS public.scrna_clusters CASCADE;

COMMIT;
