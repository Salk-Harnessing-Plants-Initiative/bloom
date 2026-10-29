-- Migration: scrna_genotypes_link_accessions
--
-- Points scrna_genotypes.accession_id at the shared accessions table, which
-- cyl_plants, translation_lines and the Gravi tables already use.

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE public.scrna_genotypes
  DROP CONSTRAINT IF EXISTS scrna_genotypes_accession_id_fkey;

ALTER TABLE public.scrna_genotypes
  ADD CONSTRAINT scrna_genotypes_accession_id_fkey
  FOREIGN KEY (accession_id) REFERENCES public.accessions (id);

COMMIT;
