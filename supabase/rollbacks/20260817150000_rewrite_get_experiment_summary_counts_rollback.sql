-- Manual rollback for 20260817150000_rewrite_get_experiment_summary_counts.sql
-- Restores the prior (bloom#625) live-join-only function body verbatim -- not a DROP, since the
-- function itself isn't new, only its body is changing back.
--
-- *** ROLLBACK ORDER: apply this one FIRST (before 20260817140000's and 20260817130000's) *** --
-- nothing else in this change depends on this migration's own objects, so it has no ordering
-- precondition of its own to check.

BEGIN;

CREATE OR REPLACE FUNCTION public.get_experiment_summary_counts(
    experiment_id_ bigint DEFAULT NULL,
    source_id_     bigint DEFAULT NULL,
    run_id_        text   DEFAULT NULL
) RETURNS TABLE (
    experiment_id bigint,
    n_plants      int,
    n_traits      int
)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
AS $$
BEGIN
    IF source_id_ IS NOT NULL AND run_id_ IS NOT NULL THEN
        RAISE EXCEPTION 'get_experiment_summary_counts: specify at most one of source_id_ and run_id_';
    END IF;

    RETURN QUERY
    SELECT
        cyl_experiments.id AS experiment_id,
        COUNT(DISTINCT cyl_plants.id)::int AS n_plants,
        COUNT(DISTINCT src.trait_name)::int AS n_traits
    FROM cyl_experiments
    JOIN cyl_waves       ON cyl_waves.experiment_id = cyl_experiments.id
    JOIN cyl_plants      ON cyl_plants.wave_id = cyl_waves.id
    JOIN accessions      ON cyl_plants.accession_id = accessions.id
    JOIN cyl_scans       ON cyl_scans.plant_id = cyl_plants.id
    JOIN public.cyl_scan_traits_source src ON src.scan_id = cyl_scans.id
    WHERE (experiment_id_ IS NULL OR cyl_experiments.id = experiment_id_)
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
    GROUP BY cyl_experiments.id;
END;
$$;

-- REVOKE also from anon, not just PUBLIC -- preserves the anon-EXECUTE fix this PR made even in
-- the rolled-back state, rather than regressing back to the pre-existing leak.
REVOKE EXECUTE ON FUNCTION public.get_experiment_summary_counts(bigint, bigint, text)
    FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.get_experiment_summary_counts(bigint, bigint, text)
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

DROP FUNCTION IF EXISTS public.compute_cyl_experiment_summary_counts_live(bigint, bigint, text);

COMMIT;
