-- Migration: scrna_embedding_reads
--
-- Two follow-ups to 20260911082453_scrna_joint_embedding.sql.
--
-- 1. scrna_embedding_label_codes numbers a label's few distinct values first and
--    looks each point up in that list, instead of ranking every point on every
--    call. Same name, arguments, output and grants; the answer is unchanged.
-- 2. Readers see only finished maps on the tables themselves, not only through
--    the two read functions. The writer reads the four tables through its own
--    policies, so loading and resuming a map no longer depend on it also being
--    a reader.
--
-- Replaces one function body and the read policies; no table or column changes.

BEGIN;

-- 1. One label for every point ------------------------------------------------------

-- The distinct values are numbered in "C" order, as before; each point then takes
-- its value's number, or -1 when it has none.
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
  ), lv AS (
    SELECT d.value,
           (row_number() OVER (ORDER BY d.value COLLATE "C") - 1)::INTEGER AS code
      FROM (SELECT DISTINCT v.value FROM vals v WHERE v.value IS NOT NULL) d
  )
  SELECT (SELECT array_agg(lv.value ORDER BY lv.code) FROM lv),
         array_agg(COALESCE(lv.code, -1) ORDER BY vals.ordinal)
    INTO found_levels, found_codes
    FROM vals
    LEFT JOIN lv ON lv.value = vals.value;

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


-- 2. Reading the tables ---------------------------------------------------------------

-- The writer's own read access. Its reads used to come only from bloom_user, which
-- it is a member of; that policy now shows finished maps only.
DO $$
DECLARE
  t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['scrna_embeddings', 'scrna_embedding_dataset_members',
                           'scrna_embedding_labels', 'scrna_embedding_points'] LOOP
    EXECUTE format('DROP POLICY IF EXISTS writer_read_%1$s ON public.%1$I', t);
    EXECUTE format('CREATE POLICY writer_read_%1$s ON public.%1$I FOR SELECT TO bloom_writer '
                   'USING (true)', t);
  END LOOP;
END $$;

-- Readers and the agent see an embedding once it is finished ...
DROP POLICY IF EXISTS user_read_scrna_embeddings ON public.scrna_embeddings;
CREATE POLICY user_read_scrna_embeddings
  ON public.scrna_embeddings FOR SELECT TO bloom_user
  USING (ingested_at IS NOT NULL);
DROP POLICY IF EXISTS agent_read_scrna_embeddings ON public.scrna_embeddings;
CREATE POLICY agent_read_scrna_embeddings
  ON public.scrna_embeddings FOR SELECT TO bloom_agent
  USING (ingested_at IS NOT NULL);

-- ... and its members, labels and points with it. The finished ids are gathered
-- once per query, not once per row.
DO $$
DECLARE
  t    TEXT;
  role TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['scrna_embedding_dataset_members', 'scrna_embedding_labels',
                           'scrna_embedding_points'] LOOP
    FOREACH role IN ARRAY ARRAY['user', 'agent'] LOOP
      EXECUTE format('DROP POLICY IF EXISTS %2$s_read_%1$s ON public.%1$I', t, role);
      EXECUTE format('CREATE POLICY %2$s_read_%1$s ON public.%1$I FOR SELECT TO bloom_%2$s '
                     'USING (embedding_id = ANY (ARRAY(SELECT e.id FROM public.scrna_embeddings e '
                     'WHERE e.ingested_at IS NOT NULL)))', t, role);
    END LOOP;
  END LOOP;
END $$;

COMMIT;
