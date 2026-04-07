-- =============================================================================
-- Migration 002: Security Groups (Roles + RLS)
-- Creates bloom_user, bloom_admin, bloom_agent roles with RLS policies.
-- Depends on: 001-soft-delete-ownership.sql (created_by, deleted_at columns)
--
-- bloom_user:  SELECT, INSERT, UPDATE (no DELETE), can only UPDATE own rows
-- bloom_admin: Full CRUD + can see soft-deleted rows
-- bloom_agent: SELECT only (read-only for chat agent)
-- anon:        SELECT on public-facing tables only
-- =============================================================================

BEGIN;

-- ===========================
-- 1. Create Roles
-- ===========================

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bloom_user') THEN
    CREATE ROLE bloom_user NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bloom_admin') THEN
    CREATE ROLE bloom_admin NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bloom_agent') THEN
    CREATE ROLE bloom_agent NOLOGIN;
  END IF;
END
$$;

GRANT bloom_user TO authenticator;
GRANT bloom_admin TO authenticator;
GRANT bloom_agent TO authenticator;

-- ===========================
-- 2. Schema + Table Permissions
-- ===========================

GRANT USAGE ON SCHEMA public TO bloom_user, bloom_admin, bloom_agent;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO bloom_user, bloom_admin;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO bloom_agent;

GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO bloom_user;
GRANT ALL ON ALL TABLES IN SCHEMA public TO bloom_admin;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO bloom_agent;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE ON TABLES TO bloom_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO bloom_admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bloom_agent;

-- ===========================
-- 3. Enable RLS
-- ===========================

ALTER TABLE species ENABLE ROW LEVEL SECURITY;
ALTER TABLE scrna_datasets ENABLE ROW LEVEL SECURITY;
ALTER TABLE scrna_cells ENABLE ROW LEVEL SECURITY;
ALTER TABLE scrna_counts ENABLE ROW LEVEL SECURITY;
ALTER TABLE scrna_genes ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyl_experiments ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyl_scans ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyl_plants ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyl_waves ENABLE ROW LEVEL SECURITY;
ALTER TABLE proteins ENABLE ROW LEVEL SECURITY;
ALTER TABLE gene_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE people ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_threads ENABLE ROW LEVEL SECURITY;

-- ===========================
-- 4. RLS Policies — Anon
-- ===========================

CREATE POLICY anon_read_species ON species FOR SELECT TO anon USING (deleted_at IS NULL);
CREATE POLICY anon_read_scrna_datasets ON scrna_datasets FOR SELECT TO anon USING (deleted_at IS NULL);
CREATE POLICY anon_read_cyl_experiments ON cyl_experiments FOR SELECT TO anon USING (deleted_at IS NULL);
CREATE POLICY anon_read_proteins ON proteins FOR SELECT TO anon USING (true);
CREATE POLICY anon_read_people ON people FOR SELECT TO anon USING (true);

-- ===========================
-- 5. RLS Policies — bloom_user
-- ===========================

CREATE POLICY user_read_species ON species FOR SELECT TO bloom_user USING (deleted_at IS NULL);
CREATE POLICY user_read_scrna_datasets ON scrna_datasets FOR SELECT TO bloom_user USING (deleted_at IS NULL);
CREATE POLICY user_read_scrna_cells ON scrna_cells FOR SELECT TO bloom_user USING (true);
CREATE POLICY user_read_scrna_counts ON scrna_counts FOR SELECT TO bloom_user USING (true);
CREATE POLICY user_read_scrna_genes ON scrna_genes FOR SELECT TO bloom_user USING (true);
CREATE POLICY user_read_cyl_experiments ON cyl_experiments FOR SELECT TO bloom_user USING (deleted_at IS NULL);
CREATE POLICY user_read_cyl_scans ON cyl_scans FOR SELECT TO bloom_user USING (true);
CREATE POLICY user_read_cyl_plants ON cyl_plants FOR SELECT TO bloom_user USING (true);
CREATE POLICY user_read_cyl_waves ON cyl_waves FOR SELECT TO bloom_user USING (true);
CREATE POLICY user_read_proteins ON proteins FOR SELECT TO bloom_user USING (true);
CREATE POLICY user_read_gene_candidates ON gene_candidates FOR SELECT TO bloom_user USING (deleted_at IS NULL);
CREATE POLICY user_read_people ON people FOR SELECT TO bloom_user USING (true);
CREATE POLICY user_read_chat_threads ON chat_threads FOR SELECT TO bloom_user USING (deleted_at IS NULL);

CREATE POLICY user_insert_species ON species FOR INSERT TO bloom_user WITH CHECK (true);
CREATE POLICY user_insert_scrna_datasets ON scrna_datasets FOR INSERT TO bloom_user WITH CHECK (true);
CREATE POLICY user_insert_cyl_experiments ON cyl_experiments FOR INSERT TO bloom_user WITH CHECK (true);
CREATE POLICY user_insert_gene_candidates ON gene_candidates FOR INSERT TO bloom_user WITH CHECK (true);
CREATE POLICY user_insert_chat_threads ON chat_threads FOR INSERT TO bloom_user WITH CHECK (true);

CREATE POLICY user_update_species ON species FOR UPDATE TO bloom_user USING (created_by = auth.uid());
CREATE POLICY user_update_scrna_datasets ON scrna_datasets FOR UPDATE TO bloom_user USING (created_by = auth.uid());
CREATE POLICY user_update_cyl_experiments ON cyl_experiments FOR UPDATE TO bloom_user USING (created_by = auth.uid());
CREATE POLICY user_update_gene_candidates ON gene_candidates FOR UPDATE TO bloom_user USING (created_by = auth.uid());
CREATE POLICY user_update_chat_threads ON chat_threads FOR UPDATE TO bloom_user USING (created_by = auth.uid());

-- ===========================
-- 6. RLS Policies — bloom_admin (full access including soft-deleted)
-- ===========================

CREATE POLICY admin_all_species ON species FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_all_scrna_datasets ON scrna_datasets FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_all_scrna_cells ON scrna_cells FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_all_scrna_counts ON scrna_counts FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_all_scrna_genes ON scrna_genes FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_all_cyl_experiments ON cyl_experiments FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_all_cyl_scans ON cyl_scans FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_all_cyl_plants ON cyl_plants FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_all_cyl_waves ON cyl_waves FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_all_proteins ON proteins FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_all_gene_candidates ON gene_candidates FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_all_people ON people FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
CREATE POLICY admin_all_chat_threads ON chat_threads FOR ALL TO bloom_admin USING (true) WITH CHECK (true);

-- ===========================
-- 7. RLS Policies — bloom_agent (read only, no soft-deleted)
-- ===========================

CREATE POLICY agent_read_species ON species FOR SELECT TO bloom_agent USING (deleted_at IS NULL);
CREATE POLICY agent_read_scrna_datasets ON scrna_datasets FOR SELECT TO bloom_agent USING (deleted_at IS NULL);
CREATE POLICY agent_read_scrna_cells ON scrna_cells FOR SELECT TO bloom_agent USING (true);
CREATE POLICY agent_read_scrna_counts ON scrna_counts FOR SELECT TO bloom_agent USING (true);
CREATE POLICY agent_read_scrna_genes ON scrna_genes FOR SELECT TO bloom_agent USING (true);
CREATE POLICY agent_read_cyl_experiments ON cyl_experiments FOR SELECT TO bloom_agent USING (deleted_at IS NULL);
CREATE POLICY agent_read_cyl_scans ON cyl_scans FOR SELECT TO bloom_agent USING (true);
CREATE POLICY agent_read_cyl_plants ON cyl_plants FOR SELECT TO bloom_agent USING (true);
CREATE POLICY agent_read_cyl_waves ON cyl_waves FOR SELECT TO bloom_agent USING (true);
CREATE POLICY agent_read_proteins ON proteins FOR SELECT TO bloom_agent USING (true);
CREATE POLICY agent_read_gene_candidates ON gene_candidates FOR SELECT TO bloom_agent USING (deleted_at IS NULL);
CREATE POLICY agent_read_people ON people FOR SELECT TO bloom_agent USING (true);
CREATE POLICY agent_read_chat_threads ON chat_threads FOR SELECT TO bloom_agent USING (deleted_at IS NULL);

COMMIT;
