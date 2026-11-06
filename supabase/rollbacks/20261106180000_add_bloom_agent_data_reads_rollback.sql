-- Rollback for 20261106180000_add_bloom_agent_data_reads.sql
-- Manual break-glass only: this repo applies migrations forward via `supabase db push`
-- (no automated down-runner). bloom_agent loses the read policies added there and its time limit, the
-- gene trigram indexes and the row-estimate function go. pg_trgm stays (other indexes use it).
-- Safe to run twice.

BEGIN;

DROP FUNCTION IF EXISTS public.bloom_table_row_estimates(TEXT[]);

DROP INDEX IF EXISTS public.genes_gene_id_trgm;
DROP INDEX IF EXISTS public.genes_symbol_trgm;
DROP INDEX IF EXISTS public.genes_standard_name_trgm;

ALTER ROLE bloom_agent RESET statement_timeout;

DROP POLICY IF EXISTS agent_read_genes ON public.genes;
DROP POLICY IF EXISTS agent_read_assemblies ON public.assemblies;
DROP POLICY IF EXISTS agent_read_gene_candidate_support ON public.gene_candidate_support;
DROP POLICY IF EXISTS agent_read_gene_candidate_scientists ON public.gene_candidate_scientists;
DROP POLICY IF EXISTS agent_read_ortho_gene_id_map ON public.ortho_gene_id_map;
DROP POLICY IF EXISTS agent_read_gene_patents ON public.gene_patents;
DROP POLICY IF EXISTS agent_read_gene_progress_notes ON public.gene_progress_notes;
DROP POLICY IF EXISTS agent_read_gravi_plate_video_jobs ON public.gravi_plate_video_jobs;
DROP POLICY IF EXISTS agent_read_cyl_video_jobs ON public.cyl_video_jobs;
REVOKE SELECT ON public.gravi_plate_video_jobs FROM bloom_agent;
REVOKE SELECT ON public.cyl_video_jobs FROM bloom_agent;
DROP POLICY IF EXISTS agent_read_cyl_scan_videos ON public.cyl_scan_videos;
DROP POLICY IF EXISTS agent_read_gene_orthologs ON public.gene_orthologs;
DROP POLICY IF EXISTS agent_read_phenotypers ON public.phenotypers;
DROP POLICY IF EXISTS agent_read_plate_plant_traits_list ON public.plate_plant_traits_list;
DROP POLICY IF EXISTS agent_read_plates_exp ON public.plates_exp;
DROP POLICY IF EXISTS agent_read_plates_scan_trait ON public.plates_scan_trait;
DROP POLICY IF EXISTS agent_read_plates_trait_source ON public.plates_trait_source;
DROP POLICY IF EXISTS agent_read_translation_candidates ON public.translation_candidates;
DROP POLICY IF EXISTS agent_read_translation_lines ON public.translation_lines;
DROP POLICY IF EXISTS agent_read_translation_project_users ON public.translation_project_users;
DROP POLICY IF EXISTS agent_read_translation_projects ON public.translation_projects;

NOTIFY pgrst, 'reload schema';
NOTIFY pgrst, 'reload config';

COMMIT;
