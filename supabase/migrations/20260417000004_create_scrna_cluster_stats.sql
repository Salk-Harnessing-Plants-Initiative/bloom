-- Migration: create_scrna_cluster_stats
-- Phase 1 of Expression Explorer (add-scrna-expression-schema).
-- Precomputed per-cluster statistics written at ingest time so the
-- drill-down panel can render without per-cluster Storage round-trips.
--
-- FK on (dataset_id, cluster_id) → scrna_clusters with ON DELETE CASCADE
-- so re-ingest that drops a cluster automatically removes its stats row.

BEGIN;

CREATE TABLE IF NOT EXISTS public.scrna_cluster_stats (
  dataset_id    BIGINT NOT NULL,
  cluster_id    TEXT   NOT NULL,
  cell_count    INT    NOT NULL,
  pct           REAL   NOT NULL,
  centroid_x    REAL,
  centroid_y    REAL,
  centroid_pc1  REAL,
  centroid_pc2  REAL,
  centroid_pc3  REAL,
  centroid_pc4  REAL,
  centroid_pc5  REAL,
  markers       JSONB,
  PRIMARY KEY (dataset_id, cluster_id),
  FOREIGN KEY (dataset_id, cluster_id)
    REFERENCES public.scrna_clusters (dataset_id, cluster_id)
    ON DELETE CASCADE
);

ALTER TABLE public.scrna_cluster_stats ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can select scrna_cluster_stats" ON public.scrna_cluster_stats;
CREATE POLICY "Authenticated users can select scrna_cluster_stats"
  ON public.scrna_cluster_stats AS permissive
  FOR SELECT TO authenticated
  USING (true);

DROP POLICY IF EXISTS "Authenticated users can insert scrna_cluster_stats" ON public.scrna_cluster_stats;
CREATE POLICY "Authenticated users can insert scrna_cluster_stats"
  ON public.scrna_cluster_stats AS permissive
  FOR INSERT TO authenticated
  WITH CHECK (true);

DROP POLICY IF EXISTS "Authenticated users can update scrna_cluster_stats" ON public.scrna_cluster_stats;
CREATE POLICY "Authenticated users can update scrna_cluster_stats"
  ON public.scrna_cluster_stats AS permissive
  FOR UPDATE TO authenticated
  USING (true);

DROP POLICY IF EXISTS "Anon users can select scrna_cluster_stats" ON public.scrna_cluster_stats;
CREATE POLICY "Anon users can select scrna_cluster_stats"
  ON public.scrna_cluster_stats AS permissive
  FOR SELECT TO anon
  USING (true);

DROP POLICY IF EXISTS user_read_scrna_cluster_stats ON public.scrna_cluster_stats;
CREATE POLICY user_read_scrna_cluster_stats
  ON public.scrna_cluster_stats FOR SELECT TO bloom_user USING (true);

DROP POLICY IF EXISTS admin_all_scrna_cluster_stats ON public.scrna_cluster_stats;
CREATE POLICY admin_all_scrna_cluster_stats
  ON public.scrna_cluster_stats FOR ALL TO bloom_admin USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS agent_read_scrna_cluster_stats ON public.scrna_cluster_stats;
CREATE POLICY agent_read_scrna_cluster_stats
  ON public.scrna_cluster_stats FOR SELECT TO bloom_agent USING (true);

COMMIT;
