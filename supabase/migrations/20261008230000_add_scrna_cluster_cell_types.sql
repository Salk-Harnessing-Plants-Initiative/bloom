-- 20261008230000_add_scrna_cluster_cell_types.sql
--
-- The predicted cell types of each cluster in a single-cell dataset. A dataset loaded by cluster
-- number keeps its clusters as "0", "1", "2" in scrna_clusters; this table names what each one
-- is predicted to be, where each prediction came from, and the share of the cluster's cells that
-- carry it. A cluster may have several cell types, each once. A dataset with no rows here is
-- shown as it is today.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

-- 1. Table ---------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.scrna_cluster_cell_types (
  dataset_id BIGINT      NOT NULL,
  cluster_id TEXT        NOT NULL,
  cell_type  TEXT        NOT NULL,
  -- Where the prediction came from: a reference atlas, a paper or a method.
  source     TEXT,
  -- The share of the cluster's cells that carry this cell type, when it was measured.
  fraction   REAL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. Keys ----------------------------------------------------------------------------------

DO $$
DECLARE
  existing TEXT;
BEGIN
  SELECT pg_get_constraintdef(oid) INTO existing
    FROM pg_constraint
   WHERE conname = 'scrna_cluster_cell_types_pkey'
     AND conrelid = 'public.scrna_cluster_cell_types'::regclass;
  IF existing IS NULL THEN
    ALTER TABLE public.scrna_cluster_cell_types
      ADD CONSTRAINT scrna_cluster_cell_types_pkey PRIMARY KEY (dataset_id, cluster_id, cell_type);
  ELSIF existing <> 'PRIMARY KEY (dataset_id, cluster_id, cell_type)' THEN
    RAISE EXCEPTION 'scrna_cluster_cell_types_pkey is %, expected PRIMARY KEY (dataset_id, cluster_id, cell_type)', existing;
  END IF;
END $$;

-- The cluster it names. CASCADE: a reload that drops a cluster drops its prediction too.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conname = 'scrna_cluster_cell_types_cluster_fkey'
       AND conrelid = 'public.scrna_cluster_cell_types'::regclass
  ) THEN
    ALTER TABLE public.scrna_cluster_cell_types
      ADD CONSTRAINT scrna_cluster_cell_types_cluster_fkey
      FOREIGN KEY (dataset_id, cluster_id)
      REFERENCES public.scrna_clusters (dataset_id, cluster_id)
      ON DELETE CASCADE ON UPDATE CASCADE;
  END IF;
END $$;

-- 3. Checks --------------------------------------------------------------------------------

-- The trim set is spelled out: btrim(x) with one argument strips ordinary spaces only.
ALTER TABLE public.scrna_cluster_cell_types
  DROP CONSTRAINT IF EXISTS scrna_cluster_cell_types_cell_type_not_blank,
  ADD CONSTRAINT scrna_cluster_cell_types_cell_type_not_blank
    CHECK (btrim(cell_type, E' \t\n\r\f\u000b ') <> ''),
  DROP CONSTRAINT IF EXISTS scrna_cluster_cell_types_source_not_blank,
  ADD CONSTRAINT scrna_cluster_cell_types_source_not_blank
    CHECK (source IS NULL OR btrim(source, E' \t\n\r\f\u000b ') <> ''),
  DROP CONSTRAINT IF EXISTS scrna_cluster_cell_types_lengths,
  ADD CONSTRAINT scrna_cluster_cell_types_lengths
    CHECK (length(cell_type) <= 100 AND length(source) <= 200),
  DROP CONSTRAINT IF EXISTS scrna_cluster_cell_types_fraction_range,
  ADD CONSTRAINT scrna_cluster_cell_types_fraction_range
    CHECK (fraction IS NULL OR (fraction > 0 AND fraction <= 1));

COMMENT ON TABLE public.scrna_cluster_cell_types IS
  'The predicted cell types of each cluster in a single-cell dataset, one row per cluster and '
  'cell type. '
  'Readable by anyone who can see the dataset, including anonymous visitors.';
COMMENT ON COLUMN public.scrna_cluster_cell_types.source IS
  'Where the prediction came from -- a reference atlas, a paper or a method. NULL when not given.';
COMMENT ON COLUMN public.scrna_cluster_cell_types.fraction IS
  'The share of the cluster''s cells that carry this cell type, above 0 and at most 1. NULL '
  'when not measured.';

-- 4. Access --------------------------------------------------------------------------------

-- Explicit grants: default privileges only cover objects made by the role that set them.
REVOKE ALL ON public.scrna_cluster_cell_types
  FROM anon, authenticated, bloom_user, bloom_agent, bloom_writer, bloom_workflows;
GRANT SELECT ON public.scrna_cluster_cell_types TO anon, authenticated, bloom_user, bloom_agent;
GRANT SELECT, INSERT, UPDATE ON public.scrna_cluster_cell_types TO bloom_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.scrna_cluster_cell_types TO bloom_admin;

ALTER TABLE public.scrna_cluster_cell_types ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS anon_read_scrna_cluster_cell_types ON public.scrna_cluster_cell_types;
CREATE POLICY anon_read_scrna_cluster_cell_types
  ON public.scrna_cluster_cell_types FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS authenticated_read_scrna_cluster_cell_types ON public.scrna_cluster_cell_types;
CREATE POLICY authenticated_read_scrna_cluster_cell_types
  ON public.scrna_cluster_cell_types FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS user_read_scrna_cluster_cell_types ON public.scrna_cluster_cell_types;
CREATE POLICY user_read_scrna_cluster_cell_types
  ON public.scrna_cluster_cell_types FOR SELECT TO bloom_user USING (true);

DROP POLICY IF EXISTS agent_read_scrna_cluster_cell_types ON public.scrna_cluster_cell_types;
CREATE POLICY agent_read_scrna_cluster_cell_types
  ON public.scrna_cluster_cell_types FOR SELECT TO bloom_agent USING (true);

-- bloom_writer is the ingest role: it loads the predictions with the dataset.
DROP POLICY IF EXISTS writer_select_scrna_cluster_cell_types ON public.scrna_cluster_cell_types;
CREATE POLICY writer_select_scrna_cluster_cell_types
  ON public.scrna_cluster_cell_types FOR SELECT TO bloom_writer USING (true);

DROP POLICY IF EXISTS writer_insert_scrna_cluster_cell_types ON public.scrna_cluster_cell_types;
CREATE POLICY writer_insert_scrna_cluster_cell_types
  ON public.scrna_cluster_cell_types FOR INSERT TO bloom_writer WITH CHECK (true);

DROP POLICY IF EXISTS writer_update_scrna_cluster_cell_types ON public.scrna_cluster_cell_types;
CREATE POLICY writer_update_scrna_cluster_cell_types
  ON public.scrna_cluster_cell_types FOR UPDATE TO bloom_writer USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS admin_all_scrna_cluster_cell_types ON public.scrna_cluster_cell_types;
CREATE POLICY admin_all_scrna_cluster_cell_types
  ON public.scrna_cluster_cell_types FOR ALL TO bloom_admin USING (true) WITH CHECK (true);

COMMIT;
