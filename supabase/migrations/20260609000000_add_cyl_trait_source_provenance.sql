-- Add run provenance + idempotency anchor to cyl_trait_sources
-- (sleap-roots write-back, sub-project #2, change A foundation).
--
-- metadata:        the contract `Provenance` envelope, stored as opaque jsonb. Bloom does
--                  NOT validate its shape at the DB layer (the contract validates it
--                  producer-side). Nullable so legacy name-only source rows stay valid.
-- idempotency_key: deterministic per-run key the write-back RPC upserts on. Nullable so
--                  legacy/non-pipeline sources coexist (Postgres permits multiple NULLs
--                  under UNIQUE). The CHECK rejects the contract's "" default, so keyless
--                  runs cannot all collide onto a single source row.
--
-- Additive (forward-only `supabase db push`); see the companion manual rollback at
-- supabase/rollbacks/20260609000000_add_cyl_trait_source_provenance_rollback.sql.
-- Written re-runnably so a partial-failure re-apply is a clean no-op: IF NOT EXISTS on the
-- columns, and drop-then-add on the constraints (Postgres has no ADD CONSTRAINT IF NOT EXISTS).

ALTER TABLE cyl_trait_sources ADD COLUMN IF NOT EXISTS metadata jsonb;
ALTER TABLE cyl_trait_sources ADD COLUMN IF NOT EXISTS idempotency_key text;

ALTER TABLE cyl_trait_sources DROP CONSTRAINT IF EXISTS cyl_trait_sources_idempotency_key_key;
ALTER TABLE cyl_trait_sources
  ADD CONSTRAINT cyl_trait_sources_idempotency_key_key UNIQUE (idempotency_key);

ALTER TABLE cyl_trait_sources DROP CONSTRAINT IF EXISTS cyl_trait_sources_idempotency_key_nonempty;
ALTER TABLE cyl_trait_sources
  ADD CONSTRAINT cyl_trait_sources_idempotency_key_nonempty
  CHECK (idempotency_key IS NULL OR length(idempotency_key) > 0);
