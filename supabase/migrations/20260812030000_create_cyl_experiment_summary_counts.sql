-- fix-cyl-scan-traits-latest-rollup (bloom#637), Phase 1, M3: per-experiment summary rollup.
--
-- Even with is_latest indexed (M1), the all-experiments count still has to join
-- cyl_scan_traits_source up through scans -> plants -> waves -> experiments and GROUP BY across
-- ~26M "latest" rows -- the other half of list_experiments()'s timeout, independent of how
-- is_latest gets computed. This adds a small per-experiment rollup table, refreshed event-driven
-- (scoped to the one experiment whose data just changed, piggybacking on the same trigger that
-- maintains is_latest) rather than a scheduled whole-table refresh -- see design.md D6/Open
-- Questions for why, and that this choice is flagged for @blm3886's confirmation, not assumed
-- silently.
--
-- Also adds compute_cyl_experiment_summary_counts_live -- the live-join aggregation, extracted
-- into its own SECURITY DEFINER helper so both this migration's refresh function AND Phase 2's
-- get_experiment_summary_counts rewrite (its source/run-pinned branch) call the SAME definition,
-- not two copies that drift apart. Uses a GROUP BY subquery over SELECT DISTINCT pairs instead of
-- COUNT(DISTINCT ...), per Benfica's "additionally" note (avoids a large per-group sort).
--
-- INERT IN PHASE 1: nothing reads cyl_experiment_summary_counts yet -- get_experiment_summary_counts
-- is not rewritten until Phase 2. New writes populate this table correctly from the moment this
-- merges; it just isn't consulted by anything until the RPC rewrite lands.
--
-- Manual rollback: supabase/rollbacks/20260812030000_create_cyl_experiment_summary_counts_rollback.sql

BEGIN;

CREATE TABLE IF NOT EXISTS public.cyl_experiment_summary_counts (
    experiment_id bigint PRIMARY KEY REFERENCES public.cyl_experiments(id) ON DELETE CASCADE,
    n_plants      int NOT NULL,
    n_traits      int NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Shared live-join helper: same join chain and selection disjunction get_experiment_traits uses.
-- SECURITY DEFINER for the same reason as the is_latest trigger (M1) -- this must be callable by
-- the refresh trigger regardless of which role's write triggered it. No writer today actually
-- needs the escalation (all current writers already have equal-or-greater privilege) -- see M1's
-- migration comment for the same caveat, which applies identically here.
CREATE OR REPLACE FUNCTION public.compute_cyl_experiment_summary_counts_live(
    experiment_id_ bigint,
    source_id_     bigint,
    run_id_        text
) RETURNS TABLE (
    experiment_id bigint, n_plants int, n_traits int
) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    RETURN QUERY
    WITH matched AS (
        SELECT cyl_experiments.id AS experiment_id, cyl_plants.id AS plant_id, src.trait_name
        FROM public.cyl_experiments
        JOIN public.cyl_waves       ON cyl_waves.experiment_id = cyl_experiments.id
        JOIN public.cyl_plants      ON cyl_plants.wave_id = cyl_waves.id
        JOIN public.accessions      ON cyl_plants.accession_id = accessions.id
        JOIN public.cyl_scans       ON cyl_scans.plant_id = cyl_plants.id
        JOIN public.cyl_scan_traits_source src ON src.scan_id = cyl_scans.id
        WHERE (experiment_id_ IS NULL OR cyl_experiments.id = experiment_id_)
          AND ( (source_id_ IS NULL AND run_id_ IS NULL AND src.is_latest)
             OR (source_id_ IS NOT NULL AND src.source_id = source_id_)
             OR (run_id_ IS NOT NULL AND src.source_id = (
                    SELECT max(s2.source_id) FROM public.cyl_scan_traits_source s2
                    WHERE s2.scan_id = src.scan_id AND s2.trait_id = src.trait_id
                      AND s2.pipeline_run_id = run_id_)) )
    ),
    plant_counts AS (
        SELECT d.experiment_id, count(*)::int AS n_plants
        FROM (SELECT DISTINCT matched.experiment_id, matched.plant_id FROM matched) d
        GROUP BY d.experiment_id
    ),
    trait_counts AS (
        SELECT d.experiment_id, count(*)::int AS n_traits
        FROM (SELECT DISTINCT matched.experiment_id, matched.trait_name FROM matched
              WHERE matched.trait_name IS NOT NULL) d
        GROUP BY d.experiment_id
    )
    SELECT plant_counts.experiment_id, plant_counts.n_plants, COALESCE(trait_counts.n_traits, 0)
    FROM plant_counts
    LEFT JOIN trait_counts ON trait_counts.experiment_id = plant_counts.experiment_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.compute_cyl_experiment_summary_counts_live(bigint, bigint, text)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.compute_cyl_experiment_summary_counts_live(bigint, bigint, text)
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

-- Refresh one experiment's rollup row from the scan whose write just landed. Delete-then-reinsert
-- (not UPDATE) so an experiment that drops to zero matching rows disappears from the rollup,
-- matching get_experiment_summary_counts's existing "absent if zero, not zero-valued" contract.
--
-- Calls the helper above directly -- NEVER get_experiment_summary_counts itself (that RPC's
-- no-override branch reads THIS table, so calling it here would read a value this very refresh
-- hasn't written yet; see design.md D7's self-reference note).
CREATE OR REPLACE FUNCTION public.refresh_cyl_experiment_summary_counts_for_scan(p_scan_id bigint)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    exp_id bigint;
    counts record;
BEGIN
    SELECT cyl_experiments.id INTO exp_id
    FROM public.cyl_scans
    JOIN public.cyl_plants ON cyl_plants.id = cyl_scans.plant_id
    JOIN public.cyl_waves ON cyl_waves.id = cyl_plants.wave_id
    JOIN public.cyl_experiments ON cyl_experiments.id = cyl_waves.experiment_id
    WHERE cyl_scans.id = p_scan_id;

    IF exp_id IS NULL THEN
        RETURN;  -- scan not reachable from any experiment
    END IF;

    DELETE FROM public.cyl_experiment_summary_counts WHERE experiment_id = exp_id;

    SELECT * INTO counts
    FROM public.compute_cyl_experiment_summary_counts_live(exp_id, NULL, NULL);

    IF counts.experiment_id IS NOT NULL THEN
        INSERT INTO public.cyl_experiment_summary_counts (experiment_id, n_plants, n_traits, updated_at)
        VALUES (counts.experiment_id, counts.n_plants, counts.n_traits, now());
    END IF;
END;
$$;

-- Trigger wrapper: cyl_scan_traits triggers can't take arbitrary arguments, so this extracts the
-- affected scan_id from NEW/OLD before delegating.
CREATE OR REPLACE FUNCTION public.trigger_refresh_cyl_experiment_summary_counts()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    PERFORM public.refresh_cyl_experiment_summary_counts_for_scan(COALESCE(NEW.scan_id, OLD.scan_id));
    RETURN NULL;
END;
$$;

-- Named to sort AFTER maintain_is_latest_after_write (M1) so same-event AFTER triggers on
-- cyl_scan_traits fire in the order Postgres uses (alphabetical by trigger name): is_latest is
-- fully maintained before this refresh reads it.
CREATE OR REPLACE TRIGGER refresh_cyl_experiment_summary_counts_after_write
    AFTER INSERT OR UPDATE OR DELETE ON public.cyl_scan_traits
    FOR EACH ROW
    EXECUTE FUNCTION public.trigger_refresh_cyl_experiment_summary_counts();

-- One-time backfill for pre-existing experiments, batched by experiment_id (same lock-avoidance
-- reasoning as backfill_cyl_scan_traits_is_latest). Definition only -- not invoked here; see the
-- operator runbook (design.md's Migration Plan). MUST NOT run before
-- backfill_cyl_scan_traits_is_latest has completed and been verified (see
-- scripts/verify_cyl_scan_traits_is_latest_backfill.sql) -- running it earlier computes rollup
-- counts from a still-incompletely-populated is_latest column, silently under-counting.
-- Calls the helper once PER experiment_id in each batch (not once per batch with a NULL/global
-- experiment_id_ filtered after the fact) -- the latter would recompute the full, unfiltered
-- all-experiments aggregate on every iteration, defeating the entire point of batching and
-- reintroducing the exact bulk-scan cost this change exists to avoid. Each per-experiment call is
-- cheap on its own (is_latest is indexed by M1); batching here is purely for COMMIT/lock-avoidance
-- across many experiments, not to reduce any single call's cost.
CREATE OR REPLACE PROCEDURE public.backfill_cyl_experiment_summary_counts(
    batch_size bigint DEFAULT 10000
)
LANGUAGE plpgsql
AS $$
DECLARE
    lo bigint;
    hi bigint;
    max_experiment_id bigint;
    exp record;
    counts record;
BEGIN
    SELECT min(id), max(id) INTO lo, max_experiment_id FROM public.cyl_experiments;
    WHILE lo IS NOT NULL AND lo <= max_experiment_id LOOP
        hi := lo + batch_size - 1;
        DELETE FROM public.cyl_experiment_summary_counts
        WHERE experiment_id BETWEEN lo AND hi;
        FOR exp IN
            SELECT id FROM public.cyl_experiments WHERE id BETWEEN lo AND hi
        LOOP
            SELECT * INTO counts
            FROM public.compute_cyl_experiment_summary_counts_live(exp.id, NULL, NULL);
            IF counts.experiment_id IS NOT NULL THEN
                INSERT INTO public.cyl_experiment_summary_counts
                    (experiment_id, n_plants, n_traits, updated_at)
                VALUES (counts.experiment_id, counts.n_plants, counts.n_traits, now());
            END IF;
        END LOOP;
        COMMIT;
        lo := hi + 1;
    END LOOP;
END;
$$;

REVOKE ALL ON PROCEDURE public.backfill_cyl_experiment_summary_counts(bigint) FROM PUBLIC;
GRANT EXECUTE ON PROCEDURE public.backfill_cyl_experiment_summary_counts(bigint) TO bloom_admin;

COMMIT;
