-- Rollback for 20260930130000_scrna_genotypes_link_accessions.sql.
--
-- This is the STAGING HOT-APPLY only. After a hand-apply, run
--   supabase migration repair --status reverted 20260930130000
--
-- Points scrna_genotypes.accession_id back at arabidopsis_accessions. Fails if
-- that table is missing or a genotype names an id it doesn't hold.

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE public.scrna_genotypes
  DROP CONSTRAINT IF EXISTS scrna_genotypes_accession_id_fkey;

ALTER TABLE public.scrna_genotypes
  ADD CONSTRAINT scrna_genotypes_accession_id_fkey
  FOREIGN KEY (accession_id) REFERENCES public.arabidopsis_accessions (id);

COMMIT;
