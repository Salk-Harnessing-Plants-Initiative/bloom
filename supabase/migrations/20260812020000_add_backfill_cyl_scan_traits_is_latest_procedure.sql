-- fix-cyl-scan-traits-latest-rollup (bloom#637), Phase 1, M2: batched is_latest backfill.
--
-- 20260812010000_add_cyl_scan_traits_is_latest_column.sql added the is_latest column, defaulted
-- to false for every pre-existing row -- correct for new writes (the trigger maintains it), but
-- still wrong for the ~28.8M rows that predate the trigger on staging. A single UPDATE across all
-- of them would hold a lock for the duration of that one giant transaction; this procedure batches
-- the work into independently-committed scan_id ranges instead.
--
-- Postgres 11+ allows COMMIT inside a PROCEDURE (not a FUNCTION) called via a top-level CALL, as
-- long as the calling session is not already inside an explicit transaction block -- this is why
-- the actual invocation is a separate, manual operational step (this migration only defines the
-- procedure; it does not CALL it). See design.md D4/D8 for the full reasoning, including why
-- `batch_size` is a scan_id-RANGE WIDTH, not a row count.
--
-- Batched by scan_id ranges (not raw id/PK ranges) so every row for a given scan lands in the same
-- batch -- the max(source_id) grouping is only correct if a scan's rows aren't split across
-- batches. Idempotent and resumable: re-running recomputes deterministically from current state.
--
-- ADDITIVE: this migration only adds the procedure definition. It grants no new privilege beyond
-- EXECUTE on itself, and does not touch RLS.
--
-- Manual rollback:
-- supabase/rollbacks/20260812020000_add_backfill_cyl_scan_traits_is_latest_procedure_rollback.sql

BEGIN;

CREATE OR REPLACE PROCEDURE public.backfill_cyl_scan_traits_is_latest(
    batch_size bigint DEFAULT 10000
)
LANGUAGE plpgsql
AS $$
DECLARE
    lo bigint;
    hi bigint;
    max_scan_id bigint;
BEGIN
    SELECT min(scan_id), max(scan_id) INTO lo, max_scan_id FROM public.cyl_scan_traits;
    WHILE lo IS NOT NULL AND lo <= max_scan_id LOOP
        hi := lo + batch_size - 1;
        UPDATE public.cyl_scan_traits t
        SET is_latest = (t.source_id IS NOT DISTINCT FROM sub.max_source_id)
        FROM (
            SELECT scan_id, max(source_id) AS max_source_id
            FROM public.cyl_scan_traits
            WHERE scan_id BETWEEN lo AND hi
            GROUP BY scan_id
        ) sub
        WHERE t.scan_id = sub.scan_id AND t.scan_id BETWEEN lo AND hi;
        COMMIT;
        lo := hi + 1;
    END LOOP;
END;
$$;

REVOKE ALL ON PROCEDURE public.backfill_cyl_scan_traits_is_latest(bigint) FROM PUBLIC;
GRANT EXECUTE ON PROCEDURE public.backfill_cyl_scan_traits_is_latest(bigint) TO bloom_admin;

COMMIT;
