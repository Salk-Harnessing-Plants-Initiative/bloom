-- =============================================================================
-- Rollback Migration 001: Security Groups
-- Removes bloom_user, bloom_admin, bloom_agent roles and all RLS policies.
-- =============================================================================

BEGIN;

-- Drop all RLS policies
DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN (
    SELECT schemaname, tablename, policyname
    FROM pg_policies
    WHERE policyname LIKE 'anon_%'
       OR policyname LIKE 'user_%'
       OR policyname LIKE 'admin_%'
       OR policyname LIKE 'agent_%'
  ) LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', r.policyname, r.schemaname, r.tablename);
  END LOOP;
END
$$;

-- Disable RLS on all tables
ALTER TABLE IF EXISTS species DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS scrna_datasets DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS scrna_cells DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS scrna_counts DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS scrna_genes DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS cyl_experiments DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS cyl_scans DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS cyl_plants DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS cyl_waves DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS proteins DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS gene_candidates DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS people DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS chat_threads DISABLE ROW LEVEL SECURITY;

-- Revoke permissions
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM bloom_user, bloom_admin, bloom_agent;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM bloom_user, bloom_admin, bloom_agent;
REVOKE USAGE ON SCHEMA public FROM bloom_user, bloom_admin, bloom_agent;

-- Remove roles from authenticator
REVOKE bloom_user FROM authenticator;
REVOKE bloom_admin FROM authenticator;
REVOKE bloom_agent FROM authenticator;

-- Drop roles
DROP ROLE IF EXISTS bloom_user;
DROP ROLE IF EXISTS bloom_admin;
DROP ROLE IF EXISTS bloom_agent;

COMMIT;
