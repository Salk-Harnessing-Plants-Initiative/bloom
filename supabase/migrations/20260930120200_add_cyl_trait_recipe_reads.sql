-- Recipe-aware trait reads: list a selection's recipes, report per-scan coverage,
-- and read one recipe's traits.
-- Change: add-cyl-trait-recipe-key (bloom#935).
--
-- WHY: pipeline write-back stores one source per scan, so "latest" per scan can mix
--   computations. With the recipe key stored (20260930120000), a caller can choose
--   one recipe for a whole selection and see which scans it leaves out.
--
-- WHAT:
--   1. _cyl_trait_recipe_presence(experiment_ids_, scan_ids_): for each selected
--      scan, one row per recipe it has (with its highest source of that recipe),
--      or one row with a NULL recipe when it has none. "Has" is defined by trait
--      rows (cyl-trait-read "Recipe presence is defined by trait rows"): a scan's
--      candidates are the sources whose scan_id is that scan or NULL, each confirmed
--      by a LATERAL ... LIMIT 1 probe on the (scan_id, source_id, trait_id) index.
--      An EXISTS form was planned as a hash aggregate over all of cyl_scan_traits.
--      It is reachable over PostgREST, so with both selectors NULL it selects no
--      scans (the public wrappers raise on that call instead).
--   2. list_trait_recipes(experiment_ids_, scan_ids_).
--   3. get_trait_recipe_coverage(experiment_ids_, scan_ids_, recipe_key_).
--   4. get_experiment_traits gains recipe_key_ and scan_ids_ and a trailing
--      recipe_key column. The return type changes, so the 3-argument form is
--      dropped first (leaving it would make a named 3-key call ambiguous,
--      PGRST203). With the new arguments NULL, the first eleven columns and the
--      row order are exactly the 20260728000000 definition's.
--
-- All SECURITY INVOKER; EXECUTE revoked from PUBLIC and anon, granted to the four
-- read roles. anon loses EXECUTE on get_experiment_traits (it held it through
-- Supabase default privileges). No policy or write grant changes.
-- Manual rollback (staging hot-apply only -- see its header):
--   supabase/rollbacks/20260930120200_add_cyl_trait_recipe_reads_rollback.sql

BEGIN;

-- 1. Presence ---------------------------------------------------------------

CREATE OR REPLACE FUNCTION public._cyl_trait_recipe_presence(
    experiment_ids_ bigint[],
    scan_ids_       bigint[]
) RETURNS TABLE (
    scan_id       bigint,
    experiment_id bigint,
    plant_qr_code text,
    has_traits    boolean,
    recipe_key    text,
    source_id     bigint
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $fn$
    WITH selected AS (
        SELECT sc.id AS scan_id, w.experiment_id, p.qr_code::text AS plant_qr_code
          FROM public.cyl_experiments e
          JOIN public.cyl_waves  w  ON w.experiment_id = e.id
          JOIN public.cyl_plants p  ON p.wave_id = w.id
          JOIN public.accessions a  ON p.accession_id = a.id
          JOIN public.cyl_scans  sc ON sc.plant_id = p.id
         WHERE (experiment_ids_ IS NOT NULL OR scan_ids_ IS NOT NULL)
           AND (experiment_ids_ IS NULL OR e.id = ANY (experiment_ids_))
           AND (scan_ids_ IS NULL OR sc.id = ANY (scan_ids_))
    ), unplaced AS MATERIALIZED (
        -- Legacy sources and any pipeline source whose scan could not be resolved.
        SELECT ts.id, ts.recipe_key
          FROM public.cyl_trait_sources ts
         WHERE ts.scan_id IS NULL AND ts.recipe_key IS NOT NULL
    ), candidates AS (
        SELECT s.scan_id, ts.id AS source_id, ts.recipe_key
          FROM selected s
          JOIN public.cyl_trait_sources ts ON ts.scan_id = s.scan_id
         WHERE ts.recipe_key IS NOT NULL
        UNION ALL
        SELECT s.scan_id, u.id, u.recipe_key
          FROM selected s CROSS JOIN unplaced u
    ), hits AS (
        SELECT c.scan_id, c.recipe_key, c.source_id
          FROM candidates c
          CROSS JOIN LATERAL (
              SELECT 1 FROM public.cyl_scan_traits t
               WHERE t.scan_id = c.scan_id AND t.source_id = c.source_id LIMIT 1
          ) AS probe
        UNION ALL
        SELECT s.scan_id, 'unattributed', NULL::bigint
          FROM selected s
          CROSS JOIN LATERAL (
              SELECT 1 FROM public.cyl_scan_traits t
               WHERE t.scan_id = s.scan_id AND t.source_id IS NULL LIMIT 1
          ) AS probe
    ), best AS (
        SELECT h.scan_id, h.recipe_key, max(h.source_id) AS source_id
          FROM hits h
         GROUP BY h.scan_id, h.recipe_key
    )
    SELECT s.scan_id, s.experiment_id, s.plant_qr_code,
           any_row.found IS NOT NULL,
           b.recipe_key, b.source_id
      FROM selected s
      LEFT JOIN LATERAL (
          SELECT 1 AS found FROM public.cyl_scan_traits t
           WHERE t.scan_id = s.scan_id LIMIT 1
      ) AS any_row ON true
      LEFT JOIN best b ON b.scan_id = s.scan_id
$fn$;

-- 2. Recipe listing -----------------------------------------------------------

CREATE OR REPLACE FUNCTION public.list_trait_recipes(
    experiment_ids_ bigint[] DEFAULT NULL,
    scan_ids_       bigint[] DEFAULT NULL
) RETURNS TABLE (
    recipe_key         text,
    recipe_key_version smallint,
    recipe_kind        text,
    definition         jsonb,
    n_scans            int,
    newest_source_id   bigint,
    is_default         boolean
)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $fn$
#variable_conflict use_column
BEGIN
    IF experiment_ids_ IS NULL AND scan_ids_ IS NULL THEN
        RAISE EXCEPTION 'list_trait_recipes: give experiment_ids_, scan_ids_, or both';
    END IF;

    RETURN QUERY
    WITH present AS (
        SELECT p.scan_id, p.recipe_key, p.source_id
          FROM public._cyl_trait_recipe_presence(experiment_ids_, scan_ids_) p
         WHERE p.recipe_key IS NOT NULL
    ), per_recipe AS (
        SELECT pr.recipe_key, count(DISTINCT pr.scan_id)::int AS n_scans,
               max(pr.source_id) AS newest_source_id
          FROM present pr
         GROUP BY pr.recipe_key
    ), ranked AS (
        SELECT r.*, row_number() OVER (ORDER BY r.newest_source_id DESC NULLS LAST) AS rn
          FROM per_recipe r
    )
    SELECT r.recipe_key,
           ts.recipe_key_version,
           CASE WHEN r.recipe_key = 'unattributed' THEN 'unattributed'
                WHEN r.recipe_key LIKE 'legacy:%' THEN 'legacy'
                ELSE 'pipeline' END,
           CASE WHEN r.recipe_key = 'unattributed' THEN NULL
                WHEN r.recipe_key LIKE 'legacy:%'
                    THEN jsonb_build_object('source_id', ts.id, 'source_name', ts.name)
                ELSE public.cyl_trait_recipe_payload_v1(ts.metadata) END,
           r.n_scans,
           r.newest_source_id,
           r.rn = 1
      FROM ranked r
      LEFT JOIN public.cyl_trait_sources ts ON ts.id = r.newest_source_id
     ORDER BY r.rn;
END;
$fn$;

-- 3. Per-scan coverage ----------------------------------------------------------

CREATE OR REPLACE FUNCTION public.get_trait_recipe_coverage(
    experiment_ids_ bigint[] DEFAULT NULL,
    scan_ids_       bigint[] DEFAULT NULL,
    recipe_key_     text     DEFAULT NULL
) RETURNS TABLE (
    scan_id           bigint,
    experiment_id     bigint,
    plant_qr_code     text,
    recipe_key        text,
    status            text,
    source_id         bigint,
    available_recipes text[]
)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $fn$
#variable_conflict use_column
BEGIN
    IF experiment_ids_ IS NULL AND scan_ids_ IS NULL THEN
        RAISE EXCEPTION 'get_trait_recipe_coverage: give experiment_ids_, scan_ids_, or both';
    END IF;
    IF recipe_key_ IS NOT NULL AND recipe_key_ <> 'unattributed' AND NOT EXISTS (
        SELECT 1 FROM public.cyl_trait_sources ts WHERE ts.recipe_key = recipe_key_
    ) THEN
        RAISE EXCEPTION 'get_trait_recipe_coverage: unknown recipe_key %', recipe_key_;
    END IF;

    RETURN QUERY
    WITH presence AS MATERIALIZED (
        SELECT * FROM public._cyl_trait_recipe_presence(experiment_ids_, scan_ids_)
    ), evaluated AS (
        SELECT coalesce(recipe_key_, (
            SELECT p.recipe_key FROM presence p
             WHERE p.recipe_key IS NOT NULL
             GROUP BY p.recipe_key
             ORDER BY max(p.source_id) DESC NULLS LAST
             LIMIT 1
        )) AS k
    ), per_scan AS (
        SELECT p.scan_id,
               min(p.experiment_id) AS experiment_id,
               min(p.plant_qr_code) AS plant_qr_code,
               bool_or(p.has_traits) AS has_traits,
               coalesce(array_agg(p.recipe_key ORDER BY p.recipe_key COLLATE "C")
                        FILTER (WHERE p.recipe_key IS NOT NULL), '{}') AS recipes,
               bool_or(p.recipe_key = (SELECT e.k FROM evaluated e)) AS included,
               max(p.source_id) FILTER (WHERE p.recipe_key = (SELECT e.k FROM evaluated e)) AS source_id
          FROM presence p
         GROUP BY p.scan_id
    )
    SELECT ps.scan_id, ps.experiment_id, ps.plant_qr_code,
           (SELECT e.k FROM evaluated e),
           CASE WHEN coalesce(ps.included, false) THEN 'included'
                WHEN NOT ps.has_traits THEN 'no_traits'
                WHEN cardinality(ps.recipes) > 0 AND NOT EXISTS (
                         SELECT 1 FROM unnest(ps.recipes) r
                          WHERE r NOT LIKE 'legacy:%' AND r <> 'unattributed')
                    THEN 'legacy_only'
                ELSE 'other_recipe' END,
           CASE WHEN coalesce(ps.included, false) THEN ps.source_id END,
           ps.recipes
      FROM per_scan ps
     ORDER BY ps.scan_id;
END;
$fn$;

-- 4. get_experiment_traits ------------------------------------------------------

DROP FUNCTION IF EXISTS public.get_experiment_traits(bigint, bigint, text);

CREATE OR REPLACE FUNCTION public.get_experiment_traits(
    experiment_id_ bigint,
    source_id_     bigint   DEFAULT NULL,
    run_id_        text     DEFAULT NULL,
    recipe_key_    text     DEFAULT NULL,
    scan_ids_      bigint[] DEFAULT NULL
) RETURNS TABLE (
    scan_id        bigint,
    date_scanned   text,
    plant_age_days int,
    wave_number    int,
    plant_id       bigint,
    germ_day       int,
    plant_qr_code  text,
    accession_name text,
    trait_name     text,
    source_id      bigint,
    trait_value    float,
    recipe_key     text
)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF (source_id_ IS NOT NULL)::int + (run_id_ IS NOT NULL)::int
       + (recipe_key_ IS NOT NULL)::int > 1 THEN
        RAISE EXCEPTION 'get_experiment_traits: specify at most one of source_id_, run_id_ and recipe_key_';
    END IF;
    IF recipe_key_ IS NOT NULL AND recipe_key_ <> 'unattributed' AND NOT EXISTS (
        SELECT 1 FROM public.cyl_trait_sources ts WHERE ts.recipe_key = recipe_key_
    ) THEN
        RAISE EXCEPTION 'get_experiment_traits: unknown recipe_key %', recipe_key_;
    END IF;

    IF recipe_key_ IS NULL THEN
        -- The 20260728000000 read, unchanged, plus the scan filter and recipe_key column.
        RETURN QUERY
        SELECT
            cyl_scans.id::bigint,
            cyl_scans.date_scanned::text,
            cyl_scans.plant_age_days::int,
            cyl_waves.number::int,
            cyl_plants.id::bigint,
            cyl_plants.germ_day::int,
            cyl_plants.qr_code::text,
            accessions.name::text,
            src.trait_name::text,
            src.source_id::bigint,
            src.value::float,
            CASE WHEN src.source_id IS NULL THEN 'unattributed' ELSE ts.recipe_key END
        FROM cyl_experiments
        JOIN cyl_waves       ON cyl_waves.experiment_id = cyl_experiments.id
        JOIN cyl_plants      ON cyl_plants.wave_id = cyl_waves.id
        JOIN accessions      ON cyl_plants.accession_id = accessions.id
        JOIN cyl_scans       ON cyl_scans.plant_id = cyl_plants.id
        JOIN public.cyl_scan_traits_source src ON src.scan_id = cyl_scans.id
        LEFT JOIN public.cyl_trait_sources ts ON ts.id = src.source_id
        WHERE cyl_experiments.id = experiment_id_
          AND (scan_ids_ IS NULL OR cyl_scans.id = ANY (scan_ids_))
          AND (
                (source_id_ IS NULL AND run_id_ IS NULL AND src.is_latest)
             OR (source_id_ IS NOT NULL AND src.source_id = source_id_)
             OR (run_id_ IS NOT NULL AND src.source_id = (
                    SELECT max(s2.source_id)
                    FROM public.cyl_scan_traits_source s2
                    WHERE s2.scan_id = src.scan_id
                      AND s2.trait_id = src.trait_id
                      AND s2.pipeline_run_id = run_id_))
              )
        ORDER BY accessions.name, cyl_plants.id, cyl_scans.id, src.trait_name;
    ELSE
        -- One recipe: each scan's highest source of it (NULL-source rows for
        -- 'unattributed'), per _cyl_trait_recipe_presence.
        RETURN QUERY
        WITH chosen AS (
            SELECT p.scan_id AS chosen_scan, p.source_id AS chosen_source
              FROM public._cyl_trait_recipe_presence(ARRAY[experiment_id_], scan_ids_) p
             WHERE p.recipe_key = recipe_key_
        )
        SELECT
            cyl_scans.id::bigint,
            cyl_scans.date_scanned::text,
            cyl_scans.plant_age_days::int,
            cyl_waves.number::int,
            cyl_plants.id::bigint,
            cyl_plants.germ_day::int,
            cyl_plants.qr_code::text,
            accessions.name::text,
            src.trait_name::text,
            src.source_id::bigint,
            src.value::float,
            recipe_key_
        FROM chosen
        JOIN cyl_scans       ON cyl_scans.id = chosen.chosen_scan
        JOIN cyl_plants      ON cyl_plants.id = cyl_scans.plant_id
        JOIN cyl_waves       ON cyl_waves.id = cyl_plants.wave_id
        JOIN accessions      ON cyl_plants.accession_id = accessions.id
        JOIN public.cyl_scan_traits_source src
             ON src.scan_id = cyl_scans.id
            AND src.source_id IS NOT DISTINCT FROM chosen.chosen_source
        WHERE cyl_waves.experiment_id = experiment_id_
        ORDER BY accessions.name, cyl_plants.id, cyl_scans.id, src.trait_name;
    END IF;
END;
$$;

-- 5. Ownership and grants --------------------------------------------------------

ALTER FUNCTION public._cyl_trait_recipe_presence(bigint[], bigint[]) OWNER TO postgres;
ALTER FUNCTION public.list_trait_recipes(bigint[], bigint[]) OWNER TO postgres;
ALTER FUNCTION public.get_trait_recipe_coverage(bigint[], bigint[], text) OWNER TO postgres;
ALTER FUNCTION public.get_experiment_traits(bigint, bigint, text, text, bigint[]) OWNER TO postgres;

REVOKE EXECUTE ON FUNCTION public._cyl_trait_recipe_presence(bigint[], bigint[]) FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.list_trait_recipes(bigint[], bigint[]) FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.get_trait_recipe_coverage(bigint[], bigint[], text) FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.get_experiment_traits(bigint, bigint, text, text, bigint[]) FROM PUBLIC, anon;

GRANT EXECUTE ON FUNCTION public._cyl_trait_recipe_presence(bigint[], bigint[])
    TO bloom_agent, bloom_user, bloom_admin, authenticated;
GRANT EXECUTE ON FUNCTION public.list_trait_recipes(bigint[], bigint[])
    TO bloom_agent, bloom_user, bloom_admin, authenticated;
GRANT EXECUTE ON FUNCTION public.get_trait_recipe_coverage(bigint[], bigint[], text)
    TO bloom_agent, bloom_user, bloom_admin, authenticated;
GRANT EXECUTE ON FUNCTION public.get_experiment_traits(bigint, bigint, text, text, bigint[])
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

NOTIFY pgrst, 'reload schema';

COMMIT;
