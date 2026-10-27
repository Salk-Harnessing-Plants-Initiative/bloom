-- Rollback for 20260929204846_add_species_function.sql (run by hand).
-- Drops the function; species already added stay.

BEGIN;

DROP FUNCTION IF EXISTS public.add_species(TEXT, TEXT, TEXT);

COMMIT;
