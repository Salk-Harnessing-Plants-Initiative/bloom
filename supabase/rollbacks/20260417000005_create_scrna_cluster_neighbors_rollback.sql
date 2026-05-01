-- Rollback for 20260417000005_create_scrna_cluster_neighbors.sql

BEGIN;

DROP TABLE IF EXISTS public.scrna_cluster_neighbors CASCADE;

COMMIT;
