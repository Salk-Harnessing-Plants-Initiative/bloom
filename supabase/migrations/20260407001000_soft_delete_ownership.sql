-- =============================================================================
-- Migration 001: Soft Delete + Row Ownership
-- Adds created_by and deleted_at columns to key tables.
-- =============================================================================

BEGIN;

-- -------------------
-- 1. Add columns to tables that support soft delete + ownership
-- -------------------

-- species
ALTER TABLE species ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES auth.users(id);
ALTER TABLE species ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ DEFAULT NULL;

-- scrna_datasets
ALTER TABLE scrna_datasets ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES auth.users(id);
ALTER TABLE scrna_datasets ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ DEFAULT NULL;

-- cyl_experiments
ALTER TABLE cyl_experiments ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES auth.users(id);
ALTER TABLE cyl_experiments ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ DEFAULT NULL;

-- gene_candidates
ALTER TABLE gene_candidates ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES auth.users(id);
ALTER TABLE gene_candidates ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ DEFAULT NULL;

-- chat_threads
ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES auth.users(id);
ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ DEFAULT NULL;

-- -------------------
-- 2. Create indexes for soft delete queries
-- -------------------

CREATE INDEX IF NOT EXISTS idx_species_deleted_at ON species(deleted_at) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_scrna_datasets_deleted_at ON scrna_datasets(deleted_at) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_cyl_experiments_deleted_at ON cyl_experiments(deleted_at) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_gene_candidates_deleted_at ON gene_candidates(deleted_at) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_chat_threads_deleted_at ON chat_threads(deleted_at) WHERE deleted_at IS NULL;

-- -------------------
-- 3. Helper function for soft delete
-- -------------------

CREATE OR REPLACE FUNCTION soft_delete(target_table TEXT, target_id BIGINT)
RETURNS VOID AS $$
BEGIN
  EXECUTE format('UPDATE %I SET deleted_at = NOW() WHERE id = $1', target_table) USING target_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- -------------------
-- 4. Auto-set created_by on insert via trigger
-- -------------------

CREATE OR REPLACE FUNCTION set_created_by()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.created_by IS NULL THEN
    NEW.created_by := auth.uid();
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_created_by_species BEFORE INSERT ON species
  FOR EACH ROW EXECUTE FUNCTION set_created_by();

CREATE TRIGGER set_created_by_scrna_datasets BEFORE INSERT ON scrna_datasets
  FOR EACH ROW EXECUTE FUNCTION set_created_by();

CREATE TRIGGER set_created_by_cyl_experiments BEFORE INSERT ON cyl_experiments
  FOR EACH ROW EXECUTE FUNCTION set_created_by();

CREATE TRIGGER set_created_by_gene_candidates BEFORE INSERT ON gene_candidates
  FOR EACH ROW EXECUTE FUNCTION set_created_by();

CREATE TRIGGER set_created_by_chat_threads BEFORE INSERT ON chat_threads
  FOR EACH ROW EXECUTE FUNCTION set_created_by();

COMMIT;
