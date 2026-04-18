-- Migration: create_scrna_cluster_neighbors
-- Phase 1 of Expression Explorer (add-scrna-expression-schema).
-- Precomputed top-K nearest clusters per cluster, ranked by similarity.
-- Similarity = 1.0 / (1.0 + euclidean_distance(centroids)), values in (0, 1].
--
-- Two FKs on (dataset_id, cluster_id) and (dataset_id, neighbor_cluster_id)
-- both CASCADE on delete so removing either end of a neighbor pair at
-- re-ingest cleans up the relationship automatically.
-- CHECK constraint forbids self-reference.

BEGIN;

CREATE TABLE IF NOT EXISTS public.scrna_cluster_neighbors (
  dataset_id          BIGINT   NOT NULL,
  cluster_id          TEXT     NOT NULL,
  neighbor_cluster_id TEXT     NOT NULL,
  rank                SMALLINT NOT NULL,
  similarity          REAL     NOT NULL,
  PRIMARY KEY (dataset_id, cluster_id, neighbor_cluster_id),
  CHECK (cluster_id <> neighbor_cluster_id),
  CHECK (similarity > 0 AND similarity <= 1),
  FOREIGN KEY (dataset_id, cluster_id)
    REFERENCES public.scrna_clusters (dataset_id, cluster_id)
    ON DELETE CASCADE,
  FOREIGN KEY (dataset_id, neighbor_cluster_id)
    REFERENCES public.scrna_clusters (dataset_id, cluster_id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_scrna_cluster_neighbors_lookup
  ON public.scrna_cluster_neighbors (dataset_id, cluster_id, rank);

ALTER TABLE public.scrna_cluster_neighbors ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can select scrna_cluster_neighbors" ON public.scrna_cluster_neighbors;
CREATE POLICY "Authenticated users can select scrna_cluster_neighbors"
  ON public.scrna_cluster_neighbors AS permissive
  FOR SELECT TO authenticated
  USING (true);

DROP POLICY IF EXISTS "Authenticated users can insert scrna_cluster_neighbors" ON public.scrna_cluster_neighbors;
CREATE POLICY "Authenticated users can insert scrna_cluster_neighbors"
  ON public.scrna_cluster_neighbors AS permissive
  FOR INSERT TO authenticated
  WITH CHECK (true);

DROP POLICY IF EXISTS "Authenticated users can update scrna_cluster_neighbors" ON public.scrna_cluster_neighbors;
CREATE POLICY "Authenticated users can update scrna_cluster_neighbors"
  ON public.scrna_cluster_neighbors AS permissive
  FOR UPDATE TO authenticated
  USING (true);

DROP POLICY IF EXISTS "Anon users can select scrna_cluster_neighbors" ON public.scrna_cluster_neighbors;
CREATE POLICY "Anon users can select scrna_cluster_neighbors"
  ON public.scrna_cluster_neighbors AS permissive
  FOR SELECT TO anon
  USING (true);

DROP POLICY IF EXISTS user_read_scrna_cluster_neighbors ON public.scrna_cluster_neighbors;
CREATE POLICY user_read_scrna_cluster_neighbors
  ON public.scrna_cluster_neighbors FOR SELECT TO bloom_user USING (true);

DROP POLICY IF EXISTS admin_all_scrna_cluster_neighbors ON public.scrna_cluster_neighbors;
CREATE POLICY admin_all_scrna_cluster_neighbors
  ON public.scrna_cluster_neighbors FOR ALL TO bloom_admin USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS agent_read_scrna_cluster_neighbors ON public.scrna_cluster_neighbors;
CREATE POLICY agent_read_scrna_cluster_neighbors
  ON public.scrna_cluster_neighbors FOR SELECT TO bloom_agent USING (true);

COMMIT;
