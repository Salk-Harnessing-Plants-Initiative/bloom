-- Manual rollback for 20260912100000_scrna_embedding_reads.sql
--
-- Restores scrna_embedding_label_codes as 20260911082453_scrna_joint_embedding.sql
-- defined it, lets readers and the agent read unfinished maps again, and drops the
-- writer's own read policies (it reads through bloom_user once more).

BEGIN;

CREATE OR REPLACE FUNCTION public.scrna_embedding_label_codes(emb_id BIGINT, label_key TEXT)
RETURNS TABLE (
  levels TEXT[],
  codes  SMALLINT[]
)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  found_levels TEXT[];
  found_codes  INTEGER[];
BEGIN
  WITH vals AS (
    SELECT p.ordinal,
           CASE WHEN label_key = 'genotype' THEN g.name
                ELSE COALESCE(p.facets ->> label_key, c.facets ->> label_key)
           END AS value
      FROM public.scrna_embedding_points p
      JOIN public.scrna_embeddings e
        ON e.id = p.embedding_id AND e.ingested_at IS NOT NULL
      LEFT JOIN public.scrna_cells c
        ON c.dataset_id = p.dataset_id AND c.id = p.cell_id
      LEFT JOIN public.scrna_genotypes g
        ON g.dataset_id = c.dataset_id AND g.id = c.genotype_id
     WHERE p.embedding_id = emb_id
  ), coded AS (
    SELECT ordinal, value,
           CASE WHEN value IS NULL THEN -1
                ELSE (dense_rank() OVER (PARTITION BY value IS NULL
                                         ORDER BY value COLLATE "C") - 1)::INTEGER
           END AS code
      FROM vals
  )
  SELECT array_agg(DISTINCT value COLLATE "C" ORDER BY value COLLATE "C")
           FILTER (WHERE value IS NOT NULL),
         array_agg(code ORDER BY ordinal)
    INTO found_levels, found_codes
    FROM coded;

  IF found_codes IS NULL THEN
    RETURN;
  END IF;
  IF coalesce(array_length(found_levels, 1), 0) > 32767 THEN
    RAISE EXCEPTION 'label % has % values, more than a map can colour',
      label_key, array_length(found_levels, 1)
      USING ERRCODE = 'program_limit_exceeded';
  END IF;
  levels := coalesce(found_levels, '{}'::TEXT[]);
  codes := found_codes::SMALLINT[];
  RETURN NEXT;
END $$;

DO $$
DECLARE
  t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['scrna_embeddings', 'scrna_embedding_dataset_members',
                           'scrna_embedding_labels', 'scrna_embedding_points'] LOOP
    EXECUTE format('DROP POLICY IF EXISTS writer_read_%1$s ON public.%1$I', t);
    EXECUTE format('DROP POLICY IF EXISTS user_read_%1$s ON public.%1$I', t);
    EXECUTE format('CREATE POLICY user_read_%1$s ON public.%1$I FOR SELECT TO bloom_user '
                   'USING (true)', t);
    EXECUTE format('DROP POLICY IF EXISTS agent_read_%1$s ON public.%1$I', t);
    EXECUTE format('CREATE POLICY agent_read_%1$s ON public.%1$I FOR SELECT TO bloom_agent '
                   'USING (true)', t);
  END LOOP;
END $$;

COMMIT;
