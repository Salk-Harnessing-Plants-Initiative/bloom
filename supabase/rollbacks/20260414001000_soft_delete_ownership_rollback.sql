-- =============================================================================
-- Rollback Migration 001: Soft Delete + Row Ownership
-- Drops triggers, functions, indexes, and columns added by migration 001.
-- Run manually if you need to undo: psql -U supabase_admin -d postgres -f <this file>
-- This file is NOT applied automatically by supabase migration up.
-- =============================================================================

BEGIN;

-- -------------------
-- 1. Drop triggers (reverse of step 4)
-- -------------------

DROP TRIGGER IF EXISTS set_created_by_chat_threads ON chat_threads;
DROP TRIGGER IF EXISTS set_created_by_gene_candidates ON gene_candidates;
DROP TRIGGER IF EXISTS set_created_by_cyl_experiments ON cyl_experiments;
DROP TRIGGER IF EXISTS set_created_by_scrna_datasets ON scrna_datasets;
DROP TRIGGER IF EXISTS set_created_by_species ON species;

-- -------------------
-- 2. Drop functions (reverse of steps 3 and 4)
-- -------------------

DROP FUNCTION IF EXISTS set_created_by();
DROP FUNCTION IF EXISTS soft_delete(TEXT, BIGINT);

-- -------------------
-- 3. Drop indexes (reverse of step 2)
-- -------------------

DROP INDEX IF EXISTS idx_chat_threads_deleted_at;
DROP INDEX IF EXISTS idx_gene_candidates_deleted_at;
DROP INDEX IF EXISTS idx_cyl_experiments_deleted_at;
DROP INDEX IF EXISTS idx_scrna_datasets_deleted_at;
DROP INDEX IF EXISTS idx_species_deleted_at;

-- -------------------
-- 4. Drop columns (reverse of step 1)
-- -------------------

ALTER TABLE chat_threads DROP COLUMN IF EXISTS created_by;
ALTER TABLE chat_threads DROP COLUMN IF EXISTS deleted_at;

ALTER TABLE gene_candidates DROP COLUMN IF EXISTS created_by;
ALTER TABLE gene_candidates DROP COLUMN IF EXISTS deleted_at;

ALTER TABLE cyl_experiments DROP COLUMN IF EXISTS created_by;
ALTER TABLE cyl_experiments DROP COLUMN IF EXISTS deleted_at;

ALTER TABLE scrna_datasets DROP COLUMN IF EXISTS created_by;
ALTER TABLE scrna_datasets DROP COLUMN IF EXISTS deleted_at;

ALTER TABLE species DROP COLUMN IF EXISTS created_by;
ALTER TABLE species DROP COLUMN IF EXISTS deleted_at;

COMMIT;
