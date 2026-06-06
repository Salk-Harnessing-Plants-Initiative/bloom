-- Rollback for 20260609000000_add_cyl_trait_source_provenance.sql
-- Manual break-glass only: this repo applies migrations forward via `supabase db push`
-- (no automated down-runner). Drops the constraints and columns, returning
-- cyl_trait_sources to its prior (id, name) shape. Data in metadata / idempotency_key
-- is lost.

BEGIN;

ALTER TABLE cyl_trait_sources DROP CONSTRAINT IF EXISTS cyl_trait_sources_idempotency_key_nonempty;
ALTER TABLE cyl_trait_sources DROP CONSTRAINT IF EXISTS cyl_trait_sources_idempotency_key_key;
ALTER TABLE cyl_trait_sources DROP COLUMN IF EXISTS idempotency_key;
ALTER TABLE cyl_trait_sources DROP COLUMN IF EXISTS metadata;

COMMIT;
