-- RLS policies for bloom_user, bloom_admin, bloom_agent across public + storage.
--
-- The custom_access_token_hook in 20260428130000_storage_grants_for_bloom_roles.sql's
-- companion work assigns role=bloom_user (or bloom_admin) on every login. Without
-- matching RLS policies on each table, PostgREST queries return zero rows even
-- when the user is authenticated — INNER JOINs to those tables collapse the
-- whole result set, which is why /app/phenotypes pages rendered blank prior
-- to this migration on environments where these policies hadn't been added by
-- hand.
--
-- Patterns:
--   admin_all_<table>     bloom_admin  ALL     USING (true) WITH CHECK (true)
--   agent_read_<table>    bloom_agent  SELECT  USING (true) or (deleted_at IS NULL)
--   user_read_<table>     bloom_user   SELECT  USING (true) or (deleted_at IS NULL)
--   user_insert_<table>   bloom_user   INSERT  WITH CHECK (true)
--   user_update_<table>   bloom_user   UPDATE  USING (created_by = auth.uid())
--   storage user_read_*   bloom_user   SELECT  per-bucket scope
--
-- bloom_user has no DELETE on any table; bloom_agent is SELECT-only.
-- Each CREATE POLICY is preceded by DROP POLICY IF EXISTS so this migration
-- is idempotent and safe to re-run on environments where the policies already
-- exist (e.g. production, where they were applied manually).

BEGIN;

-- ─── public.accessions ────────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_accessions ON public.accessions;
CREATE POLICY admin_all_accessions ON public.accessions FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_accessions ON public.accessions;
CREATE POLICY agent_read_accessions ON public.accessions FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_insert_accessions ON public.accessions;
CREATE POLICY user_insert_accessions ON public.accessions FOR INSERT TO bloom_user WITH CHECK (true);
DROP POLICY IF EXISTS user_read_accessions ON public.accessions;
CREATE POLICY user_read_accessions ON public.accessions FOR SELECT TO bloom_user USING (true);
DROP POLICY IF EXISTS user_update_accessions ON public.accessions;
CREATE POLICY user_update_accessions ON public.accessions FOR UPDATE TO bloom_user USING (true);

-- ─── public.chat_threads ──────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_chat_threads ON public.chat_threads;
CREATE POLICY admin_all_chat_threads ON public.chat_threads FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_chat_threads ON public.chat_threads;
CREATE POLICY agent_read_chat_threads ON public.chat_threads FOR SELECT TO bloom_agent USING ((deleted_at IS NULL));
DROP POLICY IF EXISTS user_insert_chat_threads ON public.chat_threads;
CREATE POLICY user_insert_chat_threads ON public.chat_threads FOR INSERT TO bloom_user WITH CHECK (true);
DROP POLICY IF EXISTS user_read_chat_threads ON public.chat_threads;
CREATE POLICY user_read_chat_threads ON public.chat_threads FOR SELECT TO bloom_user USING ((deleted_at IS NULL));
DROP POLICY IF EXISTS user_update_chat_threads ON public.chat_threads;
CREATE POLICY user_update_chat_threads ON public.chat_threads FOR UPDATE TO bloom_user USING ((created_by = auth.uid()));

-- ─── public.cyl_camera_settings ───────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_cyl_camera_settings ON public.cyl_camera_settings;
CREATE POLICY admin_all_cyl_camera_settings ON public.cyl_camera_settings FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_camera_settings ON public.cyl_camera_settings;
CREATE POLICY agent_read_cyl_camera_settings ON public.cyl_camera_settings FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_camera_settings ON public.cyl_camera_settings;
CREATE POLICY user_read_cyl_camera_settings ON public.cyl_camera_settings FOR SELECT TO bloom_user USING (true);

-- ─── public.cyl_dataset_traits ────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_cyl_dataset_traits ON public.cyl_dataset_traits;
CREATE POLICY admin_all_cyl_dataset_traits ON public.cyl_dataset_traits FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_dataset_traits ON public.cyl_dataset_traits;
CREATE POLICY agent_read_cyl_dataset_traits ON public.cyl_dataset_traits FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_dataset_traits ON public.cyl_dataset_traits;
CREATE POLICY user_read_cyl_dataset_traits ON public.cyl_dataset_traits FOR SELECT TO bloom_user USING (true);

-- ─── public.cyl_datasets ──────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_cyl_datasets ON public.cyl_datasets;
CREATE POLICY admin_all_cyl_datasets ON public.cyl_datasets FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_datasets ON public.cyl_datasets;
CREATE POLICY agent_read_cyl_datasets ON public.cyl_datasets FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_datasets ON public.cyl_datasets;
CREATE POLICY user_read_cyl_datasets ON public.cyl_datasets FOR SELECT TO bloom_user USING (true);

-- ─── public.cyl_experiments ───────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_cyl_experiments ON public.cyl_experiments;
CREATE POLICY admin_all_cyl_experiments ON public.cyl_experiments FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_experiments ON public.cyl_experiments;
CREATE POLICY agent_read_cyl_experiments ON public.cyl_experiments FOR SELECT TO bloom_agent USING ((deleted_at IS NULL));
DROP POLICY IF EXISTS user_insert_cyl_experiments ON public.cyl_experiments;
CREATE POLICY user_insert_cyl_experiments ON public.cyl_experiments FOR INSERT TO bloom_user WITH CHECK (true);
DROP POLICY IF EXISTS user_read_cyl_experiments ON public.cyl_experiments;
CREATE POLICY user_read_cyl_experiments ON public.cyl_experiments FOR SELECT TO bloom_user USING ((deleted_at IS NULL));
DROP POLICY IF EXISTS user_update_cyl_experiments ON public.cyl_experiments;
CREATE POLICY user_update_cyl_experiments ON public.cyl_experiments FOR UPDATE TO bloom_user USING ((created_by = auth.uid()));

-- ─── public.cyl_image_traits ──────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_cyl_image_traits ON public.cyl_image_traits;
CREATE POLICY admin_all_cyl_image_traits ON public.cyl_image_traits FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_image_traits ON public.cyl_image_traits;
CREATE POLICY agent_read_cyl_image_traits ON public.cyl_image_traits FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_image_traits ON public.cyl_image_traits;
CREATE POLICY user_read_cyl_image_traits ON public.cyl_image_traits FOR SELECT TO bloom_user USING (true);

-- ─── public.cyl_images ────────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_cyl_images ON public.cyl_images;
CREATE POLICY admin_all_cyl_images ON public.cyl_images FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_images ON public.cyl_images;
CREATE POLICY agent_read_cyl_images ON public.cyl_images FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_images ON public.cyl_images;
CREATE POLICY user_read_cyl_images ON public.cyl_images FOR SELECT TO bloom_user USING (true);

-- ─── public.cyl_plants ────────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_cyl_plants ON public.cyl_plants;
CREATE POLICY admin_all_cyl_plants ON public.cyl_plants FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_plants ON public.cyl_plants;
CREATE POLICY agent_read_cyl_plants ON public.cyl_plants FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_plants ON public.cyl_plants;
CREATE POLICY user_read_cyl_plants ON public.cyl_plants FOR SELECT TO bloom_user USING (true);

-- ─── public.cyl_qc_codes ──────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_cyl_qc_codes ON public.cyl_qc_codes;
CREATE POLICY admin_all_cyl_qc_codes ON public.cyl_qc_codes FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_qc_codes ON public.cyl_qc_codes;
CREATE POLICY agent_read_cyl_qc_codes ON public.cyl_qc_codes FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_qc_codes ON public.cyl_qc_codes;
CREATE POLICY user_read_cyl_qc_codes ON public.cyl_qc_codes FOR SELECT TO bloom_user USING (true);

-- ─── public.cyl_qc_set_codes ──────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_cyl_qc_set_codes ON public.cyl_qc_set_codes;
CREATE POLICY admin_all_cyl_qc_set_codes ON public.cyl_qc_set_codes FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_qc_set_codes ON public.cyl_qc_set_codes;
CREATE POLICY agent_read_cyl_qc_set_codes ON public.cyl_qc_set_codes FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_qc_set_codes ON public.cyl_qc_set_codes;
CREATE POLICY user_read_cyl_qc_set_codes ON public.cyl_qc_set_codes FOR SELECT TO bloom_user USING (true);

-- ─── public.cyl_qc_sets ───────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_cyl_qc_sets ON public.cyl_qc_sets;
CREATE POLICY admin_all_cyl_qc_sets ON public.cyl_qc_sets FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_qc_sets ON public.cyl_qc_sets;
CREATE POLICY agent_read_cyl_qc_sets ON public.cyl_qc_sets FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_qc_sets ON public.cyl_qc_sets;
CREATE POLICY user_read_cyl_qc_sets ON public.cyl_qc_sets FOR SELECT TO bloom_user USING (true);

-- ─── public.cyl_scan_traits ───────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_cyl_scan_traits ON public.cyl_scan_traits;
CREATE POLICY admin_all_cyl_scan_traits ON public.cyl_scan_traits FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_scan_traits ON public.cyl_scan_traits;
CREATE POLICY agent_read_cyl_scan_traits ON public.cyl_scan_traits FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_scan_traits ON public.cyl_scan_traits;
CREATE POLICY user_read_cyl_scan_traits ON public.cyl_scan_traits FOR SELECT TO bloom_user USING (true);

-- ─── public.cyl_scanners ──────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_cyl_scanners ON public.cyl_scanners;
CREATE POLICY admin_all_cyl_scanners ON public.cyl_scanners FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_scanners ON public.cyl_scanners;
CREATE POLICY agent_read_cyl_scanners ON public.cyl_scanners FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_scanners ON public.cyl_scanners;
CREATE POLICY user_read_cyl_scanners ON public.cyl_scanners FOR SELECT TO bloom_user USING (true);

-- ─── public.cyl_scans ─────────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_cyl_scans ON public.cyl_scans;
CREATE POLICY admin_all_cyl_scans ON public.cyl_scans FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_scans ON public.cyl_scans;
CREATE POLICY agent_read_cyl_scans ON public.cyl_scans FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_scans ON public.cyl_scans;
CREATE POLICY user_read_cyl_scans ON public.cyl_scans FOR SELECT TO bloom_user USING (true);

-- ─── public.cyl_scientists ────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_cyl_scientists ON public.cyl_scientists;
CREATE POLICY admin_all_cyl_scientists ON public.cyl_scientists FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_scientists ON public.cyl_scientists;
CREATE POLICY agent_read_cyl_scientists ON public.cyl_scientists FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_scientists ON public.cyl_scientists;
CREATE POLICY user_read_cyl_scientists ON public.cyl_scientists FOR SELECT TO bloom_user USING (true);

-- ─── public.cyl_trait_sources ─────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_cyl_trait_sources ON public.cyl_trait_sources;
CREATE POLICY admin_all_cyl_trait_sources ON public.cyl_trait_sources FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_trait_sources ON public.cyl_trait_sources;
CREATE POLICY agent_read_cyl_trait_sources ON public.cyl_trait_sources FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_trait_sources ON public.cyl_trait_sources;
CREATE POLICY user_read_cyl_trait_sources ON public.cyl_trait_sources FOR SELECT TO bloom_user USING (true);

-- ─── public.cyl_traits ────────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_cyl_traits ON public.cyl_traits;
CREATE POLICY admin_all_cyl_traits ON public.cyl_traits FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_traits ON public.cyl_traits;
CREATE POLICY agent_read_cyl_traits ON public.cyl_traits FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_traits ON public.cyl_traits;
CREATE POLICY user_read_cyl_traits ON public.cyl_traits FOR SELECT TO bloom_user USING (true);

-- ─── public.cyl_waves ─────────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_cyl_waves ON public.cyl_waves;
CREATE POLICY admin_all_cyl_waves ON public.cyl_waves FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_waves ON public.cyl_waves;
CREATE POLICY agent_read_cyl_waves ON public.cyl_waves FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_waves ON public.cyl_waves;
CREATE POLICY user_read_cyl_waves ON public.cyl_waves FOR SELECT TO bloom_user USING (true);

-- ─── public.gene_candidates ───────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_gene_candidates ON public.gene_candidates;
CREATE POLICY admin_all_gene_candidates ON public.gene_candidates FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_gene_candidates ON public.gene_candidates;
CREATE POLICY agent_read_gene_candidates ON public.gene_candidates FOR SELECT TO bloom_agent USING ((deleted_at IS NULL));
DROP POLICY IF EXISTS user_insert_gene_candidates ON public.gene_candidates;
CREATE POLICY user_insert_gene_candidates ON public.gene_candidates FOR INSERT TO bloom_user WITH CHECK (true);
DROP POLICY IF EXISTS user_read_gene_candidates ON public.gene_candidates;
CREATE POLICY user_read_gene_candidates ON public.gene_candidates FOR SELECT TO bloom_user USING ((deleted_at IS NULL));
DROP POLICY IF EXISTS user_update_gene_candidates ON public.gene_candidates;
CREATE POLICY user_update_gene_candidates ON public.gene_candidates FOR UPDATE TO bloom_user USING ((created_by = auth.uid()));

-- ─── public.people ────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_people ON public.people;
CREATE POLICY admin_all_people ON public.people FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_people ON public.people;
CREATE POLICY agent_read_people ON public.people FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_people ON public.people;
CREATE POLICY user_read_people ON public.people FOR SELECT TO bloom_user USING (true);

-- ─── public.scrna_cells ───────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_scrna_cells ON public.scrna_cells;
CREATE POLICY admin_all_scrna_cells ON public.scrna_cells FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_scrna_cells ON public.scrna_cells;
CREATE POLICY agent_read_scrna_cells ON public.scrna_cells FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_scrna_cells ON public.scrna_cells;
CREATE POLICY user_read_scrna_cells ON public.scrna_cells FOR SELECT TO bloom_user USING (true);

-- ─── public.scrna_cluster_neighbors ───────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_scrna_cluster_neighbors ON public.scrna_cluster_neighbors;
CREATE POLICY admin_all_scrna_cluster_neighbors ON public.scrna_cluster_neighbors FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_scrna_cluster_neighbors ON public.scrna_cluster_neighbors;
CREATE POLICY agent_read_scrna_cluster_neighbors ON public.scrna_cluster_neighbors FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_scrna_cluster_neighbors ON public.scrna_cluster_neighbors;
CREATE POLICY user_read_scrna_cluster_neighbors ON public.scrna_cluster_neighbors FOR SELECT TO bloom_user USING (true);

-- ─── public.scrna_cluster_stats ───────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_scrna_cluster_stats ON public.scrna_cluster_stats;
CREATE POLICY admin_all_scrna_cluster_stats ON public.scrna_cluster_stats FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_scrna_cluster_stats ON public.scrna_cluster_stats;
CREATE POLICY agent_read_scrna_cluster_stats ON public.scrna_cluster_stats FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_scrna_cluster_stats ON public.scrna_cluster_stats;
CREATE POLICY user_read_scrna_cluster_stats ON public.scrna_cluster_stats FOR SELECT TO bloom_user USING (true);

-- ─── public.scrna_clusters ────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_scrna_clusters ON public.scrna_clusters;
CREATE POLICY admin_all_scrna_clusters ON public.scrna_clusters FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_scrna_clusters ON public.scrna_clusters;
CREATE POLICY agent_read_scrna_clusters ON public.scrna_clusters FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_scrna_clusters ON public.scrna_clusters;
CREATE POLICY user_read_scrna_clusters ON public.scrna_clusters FOR SELECT TO bloom_user USING (true);

-- ─── public.scrna_counts ──────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_scrna_counts ON public.scrna_counts;
CREATE POLICY admin_all_scrna_counts ON public.scrna_counts FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_scrna_counts ON public.scrna_counts;
CREATE POLICY agent_read_scrna_counts ON public.scrna_counts FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_scrna_counts ON public.scrna_counts;
CREATE POLICY user_read_scrna_counts ON public.scrna_counts FOR SELECT TO bloom_user USING (true);

-- ─── public.scrna_datasets ────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_scrna_datasets ON public.scrna_datasets;
CREATE POLICY admin_all_scrna_datasets ON public.scrna_datasets FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_scrna_datasets ON public.scrna_datasets;
CREATE POLICY agent_read_scrna_datasets ON public.scrna_datasets FOR SELECT TO bloom_agent USING ((deleted_at IS NULL));
DROP POLICY IF EXISTS user_insert_scrna_datasets ON public.scrna_datasets;
CREATE POLICY user_insert_scrna_datasets ON public.scrna_datasets FOR INSERT TO bloom_user WITH CHECK (true);
DROP POLICY IF EXISTS user_read_scrna_datasets ON public.scrna_datasets;
CREATE POLICY user_read_scrna_datasets ON public.scrna_datasets FOR SELECT TO bloom_user USING ((deleted_at IS NULL));

-- ─── public.scrna_genes ───────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_scrna_genes ON public.scrna_genes;
CREATE POLICY admin_all_scrna_genes ON public.scrna_genes FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_scrna_genes ON public.scrna_genes;
CREATE POLICY agent_read_scrna_genes ON public.scrna_genes FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_scrna_genes ON public.scrna_genes;
CREATE POLICY user_read_scrna_genes ON public.scrna_genes FOR SELECT TO bloom_user USING (true);

-- ─── public.species ───────────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_species ON public.species;
CREATE POLICY admin_all_species ON public.species FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_species ON public.species;
CREATE POLICY agent_read_species ON public.species FOR SELECT TO bloom_agent USING ((deleted_at IS NULL));
DROP POLICY IF EXISTS user_insert_species ON public.species;
CREATE POLICY user_insert_species ON public.species FOR INSERT TO bloom_user WITH CHECK (true);
DROP POLICY IF EXISTS user_read_species ON public.species;
CREATE POLICY user_read_species ON public.species FOR SELECT TO bloom_user USING ((deleted_at IS NULL));
DROP POLICY IF EXISTS user_update_species ON public.species;
CREATE POLICY user_update_species ON public.species FOR UPDATE TO bloom_user USING ((created_by = auth.uid()));

-- ─── storage.objects ──────────────────────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_objects ON storage.objects;
CREATE POLICY admin_all_objects ON storage.objects FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_objects ON storage.objects;
CREATE POLICY agent_read_objects ON storage.objects FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_images ON storage.objects;
CREATE POLICY user_read_cyl_images ON storage.objects FOR SELECT TO bloom_user USING ((bucket_id = 'cyl-images'::text));
DROP POLICY IF EXISTS user_read_exp_progress_logs ON storage.objects;
CREATE POLICY user_read_exp_progress_logs ON storage.objects FOR SELECT TO bloom_user USING ((bucket_id = 'exp-progress-logs'::text));
DROP POLICY IF EXISTS user_read_images ON storage.objects;
CREATE POLICY user_read_images ON storage.objects FOR SELECT TO bloom_user USING ((bucket_id = 'images'::text));
DROP POLICY IF EXISTS user_read_scrna ON storage.objects;
CREATE POLICY user_read_scrna ON storage.objects FOR SELECT TO bloom_user USING ((bucket_id = 'scrna'::text));
DROP POLICY IF EXISTS user_read_species_illustrations ON storage.objects;
CREATE POLICY user_read_species_illustrations ON storage.objects FOR SELECT TO bloom_user USING ((bucket_id = 'species_illustrations'::text));
DROP POLICY IF EXISTS user_read_videos ON storage.objects;
CREATE POLICY user_read_videos ON storage.objects FOR SELECT TO bloom_user USING ((bucket_id = 'videos'::text));

COMMIT;
