-- Rollback for 20260417000004_create_scrna_cluster_stats.sql

BEGIN;

DROP TABLE IF EXISTS public.scrna_cluster_stats CASCADE;

COMMIT;
