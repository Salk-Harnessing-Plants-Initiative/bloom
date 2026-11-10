-- 20261106180000_add_bloom_agent_data_reads.sql
--
-- What bloommcp's data tools need before Claude can answer questions about Bloom's data:
-- - bloom_agent reads every public table with row security. 18 had a SELECT grant but no policy
--   for it (most only TO authenticated), so it saw no rows; the two video job queues had neither,
--   and get the same columns the web roles have. cyl_experiment_trait_count_changes stays closed:
--   internal bookkeeping for the trait-count refresh. Adds no writes.
-- - bloom_agent's statements stop after 15 s, so one broad question can't hold the database.
-- - Trigram indexes on genes.gene_id, symbol and standard_name, so a substring gene search
--   doesn't scan all of genes (778 K rows on prod).
-- - bloom_table_row_estimates: the planner's row estimate for public tables, for bloommcp to pick
--   an exact count on small tables and an approximate one on large ones. bloom_agent only.
-- Additive. Forward-only; rollback in supabase/rollbacks/.

BEGIN;

-- Read policies for bloom_agent on the tables it had none on.
DROP POLICY IF EXISTS agent_read_genes ON public.genes;
CREATE POLICY agent_read_genes ON public.genes FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_assemblies ON public.assemblies;
CREATE POLICY agent_read_assemblies ON public.assemblies FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_gene_candidate_support ON public.gene_candidate_support;
CREATE POLICY agent_read_gene_candidate_support ON public.gene_candidate_support
    FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_gene_candidate_scientists ON public.gene_candidate_scientists;
CREATE POLICY agent_read_gene_candidate_scientists ON public.gene_candidate_scientists
    FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_ortho_gene_id_map ON public.ortho_gene_id_map;
CREATE POLICY agent_read_ortho_gene_id_map ON public.ortho_gene_id_map
    FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_gene_patents ON public.gene_patents;
CREATE POLICY agent_read_gene_patents ON public.gene_patents FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_gene_progress_notes ON public.gene_progress_notes;
CREATE POLICY agent_read_gene_progress_notes ON public.gene_progress_notes
    FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_cyl_scan_videos ON public.cyl_scan_videos;
CREATE POLICY agent_read_cyl_scan_videos ON public.cyl_scan_videos FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_gene_orthologs ON public.gene_orthologs;
CREATE POLICY agent_read_gene_orthologs ON public.gene_orthologs FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_phenotypers ON public.phenotypers;
CREATE POLICY agent_read_phenotypers ON public.phenotypers FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_plate_plant_traits_list ON public.plate_plant_traits_list;
CREATE POLICY agent_read_plate_plant_traits_list ON public.plate_plant_traits_list FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_plates_exp ON public.plates_exp;
CREATE POLICY agent_read_plates_exp ON public.plates_exp FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_plates_scan_trait ON public.plates_scan_trait;
CREATE POLICY agent_read_plates_scan_trait ON public.plates_scan_trait FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_plates_trait_source ON public.plates_trait_source;
CREATE POLICY agent_read_plates_trait_source ON public.plates_trait_source FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_translation_candidates ON public.translation_candidates;
CREATE POLICY agent_read_translation_candidates ON public.translation_candidates FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_translation_lines ON public.translation_lines;
CREATE POLICY agent_read_translation_lines ON public.translation_lines FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_translation_project_users ON public.translation_project_users;
CREATE POLICY agent_read_translation_project_users ON public.translation_project_users FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_translation_projects ON public.translation_projects;
CREATE POLICY agent_read_translation_projects ON public.translation_projects FOR SELECT TO bloom_agent USING (true);

-- Video job queues: the columns the web roles read; error_message (internal hosts and roles),
-- msg_id and reads (queue plumbing) stay withheld.
GRANT SELECT (
  id, experiment_id, plate_id, wave_number, status, stage, frames_done, frames_total,
  error_code, requested_by, created_at, started_at, updated_at, completed_at
) ON public.gravi_plate_video_jobs TO bloom_agent;
GRANT SELECT (
  id, scan_id, experiment_id, status, stage, frames_done, frames_total,
  error_code, requested_by, created_at, started_at, updated_at, completed_at
) ON public.cyl_video_jobs TO bloom_agent;
DROP POLICY IF EXISTS agent_read_gravi_plate_video_jobs ON public.gravi_plate_video_jobs;
CREATE POLICY agent_read_gravi_plate_video_jobs ON public.gravi_plate_video_jobs
    FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS agent_read_cyl_video_jobs ON public.cyl_video_jobs;
CREATE POLICY agent_read_cyl_video_jobs ON public.cyl_video_jobs
    FOR SELECT TO bloom_agent USING (true);

-- PostgREST applies the switched-to role's statement_timeout over authenticator's 8 s, but reads
-- role settings only at startup or on 'reload config' (sent below).
ALTER ROLE bloom_agent SET statement_timeout = '15s';

-- Gene search.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS genes_gene_id_trgm ON public.genes USING gin (gene_id gin_trgm_ops);
CREATE INDEX IF NOT EXISTS genes_symbol_trgm ON public.genes USING gin (symbol gin_trgm_ops);
CREATE INDEX IF NOT EXISTS genes_standard_name_trgm
    ON public.genes USING gin (standard_name gin_trgm_ops);

-- Row estimates. pg_class is readable by every role, so no SECURITY DEFINER is needed.
CREATE OR REPLACE FUNCTION public.bloom_table_row_estimates(p_tables TEXT[])
RETURNS TABLE (table_name TEXT, row_estimate BIGINT)
LANGUAGE sql
STABLE
SET search_path = ''
AS $$
    -- reltuples is -1 until a table is first analyzed: unknown, not -1 rows.
    SELECT c.relname::pg_catalog.text,
           CASE WHEN c.reltuples < 0 THEN NULL ELSE c.reltuples::pg_catalog.int8 END
      FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
       AND c.relkind IN ('r', 'p', 'm')
       AND c.relname = ANY (p_tables)
     ORDER BY c.relname;
$$;

COMMENT ON FUNCTION public.bloom_table_row_estimates(TEXT[]) IS
  'Planner row estimates (pg_class.reltuples) for the named public tables; NULL when never analyzed. bloommcp uses them to choose exact or approximate counts. bloom_agent only.';

REVOKE ALL ON FUNCTION public.bloom_table_row_estimates(TEXT[])
    FROM PUBLIC, anon, authenticated, service_role, bloom_user, bloom_admin;
GRANT EXECUTE ON FUNCTION public.bloom_table_row_estimates(TEXT[]) TO bloom_agent;

NOTIFY pgrst, 'reload schema';
NOTIFY pgrst, 'reload config';

COMMIT;
