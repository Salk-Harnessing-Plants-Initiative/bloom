-- Rollback for 20260630180000_add_cyl_writeback_rpc.sql
-- Manual break-glass only: this repo applies migrations forward via `supabase db push`
-- (no automated down-runner). Drops the write-back RPC and restores the write policies
-- that change E dropped, returning the three tables to their prior write posture.
--
-- The re-created policies reproduce their EXACT prior names and definitions, including
-- both USING and WITH CHECK on the UPDATE policies (a one-clause re-create would not be
-- catalog-equivalent). `DROP POLICY IF EXISTS` precedes each CREATE for re-runnability.

BEGIN;

DROP FUNCTION IF EXISTS public.insert_cyl_result_envelope(jsonb);

-- Legacy permissive `authenticated` INSERT policies (change A / allow-insert migrations).
DROP POLICY IF EXISTS "Authenticated users can insert cyl_trait_sources" ON public.cyl_trait_sources;
CREATE POLICY "Authenticated users can insert cyl_trait_sources"
  ON public.cyl_trait_sources AS permissive
  FOR INSERT TO authenticated
  WITH CHECK (true);

DROP POLICY IF EXISTS "Authenticated users can insert cyl_scan_traits" ON public.cyl_scan_traits;
CREATE POLICY "Authenticated users can insert cyl_scan_traits"
  ON public.cyl_scan_traits AS permissive
  FOR INSERT TO authenticated
  WITH CHECK (true);

-- bloom_writer INSERT/UPDATE policies (loop in add_bloom_writer_role; change C for intermediates).
DROP POLICY IF EXISTS writer_insert_cyl_trait_sources ON public.cyl_trait_sources;
CREATE POLICY writer_insert_cyl_trait_sources ON public.cyl_trait_sources
  FOR INSERT TO bloom_writer WITH CHECK (true);
DROP POLICY IF EXISTS writer_update_cyl_trait_sources ON public.cyl_trait_sources;
CREATE POLICY writer_update_cyl_trait_sources ON public.cyl_trait_sources
  FOR UPDATE TO bloom_writer USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS writer_insert_cyl_scan_traits ON public.cyl_scan_traits;
CREATE POLICY writer_insert_cyl_scan_traits ON public.cyl_scan_traits
  FOR INSERT TO bloom_writer WITH CHECK (true);
DROP POLICY IF EXISTS writer_update_cyl_scan_traits ON public.cyl_scan_traits;
CREATE POLICY writer_update_cyl_scan_traits ON public.cyl_scan_traits
  FOR UPDATE TO bloom_writer USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS writer_insert_cyl_scan_intermediates ON public.cyl_scan_intermediates;
CREATE POLICY writer_insert_cyl_scan_intermediates ON public.cyl_scan_intermediates
  FOR INSERT TO bloom_writer WITH CHECK (true);
DROP POLICY IF EXISTS writer_update_cyl_scan_intermediates ON public.cyl_scan_intermediates;
CREATE POLICY writer_update_cyl_scan_intermediates ON public.cyl_scan_intermediates
  FOR UPDATE TO bloom_writer USING (true) WITH CHECK (true);

COMMIT;
