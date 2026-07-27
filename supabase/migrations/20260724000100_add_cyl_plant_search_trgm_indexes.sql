-- Migration: add_cyl_plant_search_trgm_indexes
-- The plant search bar matches with ILIKE '%term%'. A pattern that starts with
-- '%' can't use a normal index, so today every search reads the whole table.
-- A pg_trgm index handles that pattern. These go on the tables underneath
-- cyl_plant_search, because a view can't hold indexes of its own.
--
-- Additive only: no existing columns or rows are modified.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_cyl_plants_qr_code_trgm
  ON public.cyl_plants USING gin (qr_code gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_accessions_name_trgm
  ON public.accessions USING gin (name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_species_common_name_trgm
  ON public.species USING gin (common_name gin_trgm_ops);

COMMIT;
