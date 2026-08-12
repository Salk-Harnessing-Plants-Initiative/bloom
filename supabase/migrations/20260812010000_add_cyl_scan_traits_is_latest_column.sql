-- fix-cyl-scan-traits-latest-rollup (bloom#637), Phase 1, M1: store is_latest as a real column.
--
-- cyl_scan_traits_source.is_latest is currently a live WindowAgg (max(source_id) OVER (PARTITION
-- BY scan_id)), recomputed on every read over the full cyl_scan_traits table (~16.4s at staging's
-- 28.8M-row scale, in isolation) -- the dominant cost behind list_experiments()'s timeout. There is
-- no data on disk to index, so every is_latest-filtered query pays this cost.
--
-- This adds a stored, indexed is_latest boolean column on cyl_scan_traits, maintained by an AFTER
-- trigger covering every write path (the write-back RPC's SECURITY DEFINER inserts, and
-- bloom_admin's break-glass direct table access -- the only two live write surfaces). The
-- selection rule is unchanged: true iff source_id IS NOT DISTINCT FROM max(source_id) OVER
-- (PARTITION BY scan_id) -- partitioned by scan_id alone, not (scan_id, trait_id); see the
-- cyl-trait-read spec's "Canonical source-aware trait view" requirement for why that grain is
-- intentional (test_no_cross_source_mixing), not a bug to fix here.
--
-- ADDITIVE AND INERT: cyl_scan_traits_source keeps computing is_latest live (unchanged) -- this
-- migration does not touch the view. Every pre-existing row gets is_latest=false by the column's
-- DEFAULT until a separate, batched backfill (next migration) populates it correctly; the view
-- does not read this column until a later migration cuts it over, once that backfill is verified
-- complete. See design.md's Migration Plan (M1-M5) for the full sequencing.
--
-- Manual rollback: supabase/rollbacks/20260812010000_add_cyl_scan_traits_is_latest_column_rollback.sql

BEGIN;

-- Metadata-only on Postgres 11+ (constant DEFAULT, no table rewrite, no long lock).
ALTER TABLE public.cyl_scan_traits
    ADD COLUMN IF NOT EXISTS is_latest boolean NOT NULL DEFAULT false;

-- SECURITY DEFINER is not functionally required by any writer that exists today (postgres via the
-- write-back RPC, bloom_admin, and bloom_writer all already have privilege equal to or exceeding
-- what this maintenance UPDATE needs as SECURITY INVOKER) -- kept defensively, in case a future RLS
-- policy on cyl_scan_traits would otherwise block this trigger's own write for some writer.
-- Revisit (drop to SECURITY INVOKER, or scope explicitly) if a narrower writer role is added.
CREATE OR REPLACE FUNCTION public.maintain_cyl_scan_traits_is_latest()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    affected_scan_id bigint := COALESCE(NEW.scan_id, OLD.scan_id);
BEGIN
    RAISE DEBUG 'maintain_cyl_scan_traits_is_latest fired for scan_id=%', affected_scan_id;

    -- Serialize concurrent writers to the SAME scan_id on a stable resource, not on the rows
    -- being written. Without this, two connections concurrently inserting the FIRST-EVER rows
    -- for a brand-new scan_id acquire no overlapping row locks (there is nothing pre-existing to
    -- lock), so each one's maintenance UPDATE below only sees its own not-yet-committed row and
    -- concludes "I'm the only/max source" independently -- both end up is_latest=true. A rerun
    -- against an existing row doesn't have this gap (the UPDATE's WHERE scan_id=... already locks
    -- the pre-existing row, serializing the second writer behind the first), which is why this
    -- was only caught by a dedicated first-insert concurrency test, not the rerun one.
    -- Two-int-key form, namespaced by a fixed hash so this doesn't collide with any other
    -- advisory-lock user (none exist elsewhere in this codebase today). scan_id fits int4 at any
    -- scale this application will plausibly reach before this needs revisiting.
    PERFORM pg_advisory_xact_lock(hashtext('cyl_scan_traits.is_latest'), affected_scan_id::int);

    -- Recompute is scoped to scan_id, not (scan_id, trait_id): a single write can change which
    -- source_id is the max for the whole scan, affecting every trait row of that scan, not just
    -- the row that was written.
    --
    -- The "IS DISTINCT FROM (...)" clause in the WHERE below is a recursion TERMINATOR, not an
    -- optimization: this UPDATE re-fires this same AFTER trigger on every row it touches, but the
    -- second pass finds nothing left to change (every row's is_latest already matches the
    -- recomputed value), so it updates zero rows and the recursion ends after depth 2. Do not
    -- "simplify" this WHERE clause away -- doing so reintroduces infinite trigger recursion.
    UPDATE public.cyl_scan_traits t
    SET is_latest = (t.source_id IS NOT DISTINCT FROM sub.max_source_id)
    FROM (
        SELECT max(source_id) AS max_source_id
        FROM public.cyl_scan_traits
        WHERE scan_id = affected_scan_id
    ) sub
    WHERE t.scan_id = affected_scan_id
      AND t.is_latest IS DISTINCT FROM (t.source_id IS NOT DISTINCT FROM sub.max_source_id);

    RETURN NULL;  -- AFTER trigger; return value is ignored.
END;
$$;

-- CREATE OR REPLACE TRIGGER (Postgres 14+) so this migration body is safely re-runnable.
CREATE OR REPLACE TRIGGER maintain_is_latest_after_write
    AFTER INSERT OR UPDATE OR DELETE ON public.cyl_scan_traits
    FOR EACH ROW
    EXECUTE FUNCTION public.maintain_cyl_scan_traits_is_latest();

CREATE INDEX IF NOT EXISTS idx_cyl_scan_traits_latest
    ON public.cyl_scan_traits (scan_id) WHERE is_latest;

COMMIT;
