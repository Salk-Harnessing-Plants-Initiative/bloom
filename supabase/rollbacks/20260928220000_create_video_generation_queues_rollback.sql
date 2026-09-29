-- Rollback for 20260928220000_create_video_generation_queues.sql.
--
-- Nothing applies this automatically. Apply it by hand as supabase_admin:
--   docker compose -f docker-compose.<env>.yml exec -T db \
--     psql -v ON_ERROR_STOP=1 -U supabase_admin -d postgres \
--     < supabase/rollbacks/20260928220000_create_video_generation_queues_rollback.sql
--
-- A hand-apply leaves 20260928220000 recorded in supabase_migrations.schema_migrations,
-- so CI, a fresh stack and the next promotion will re-apply the migration. To roll back
-- durably, delete that row as well.
--
-- Destructive: this drops the job history along with the tables, and pgmq.drop_queue
-- discards any message still in flight. Nothing reads either queue while this change is
-- the newest, so at that point there is nothing to lose.

BEGIN;

DROP FUNCTION IF EXISTS public.enqueue_gravi_plate_video(bigint, text, integer, uuid);
DROP FUNCTION IF EXISTS public.claim_gravi_plate_video_job(integer, integer);
DROP FUNCTION IF EXISTS public.report_gravi_plate_video_progress(uuid, bigint, text, integer, integer, integer);
DROP FUNCTION IF EXISTS public.complete_gravi_plate_video_job(uuid, bigint, text);
DROP FUNCTION IF EXISTS public.fail_gravi_plate_video_job(uuid, bigint, text, text);
DROP FUNCTION IF EXISTS public.enqueue_cyl_video(bigint, bigint, uuid);
DROP FUNCTION IF EXISTS public.claim_cyl_video_job(integer, integer);
DROP FUNCTION IF EXISTS public.report_cyl_video_progress(uuid, bigint, text, integer, integer, integer);
DROP FUNCTION IF EXISTS public.complete_cyl_video_job(uuid, bigint, text);
DROP FUNCTION IF EXISTS public.fail_cyl_video_job(uuid, bigint, text, text);

-- The indexes and policies belong to the tables and go with them. No CASCADE: anything
-- that turns out to depend on these should fail here and be looked at.
DROP TABLE IF EXISTS public.gravi_plate_video_jobs;
DROP TABLE IF EXISTS public.cyl_video_jobs;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pgmq.list_queues() WHERE queue_name = 'gravi_plate_video') THEN
    PERFORM pgmq.drop_queue('gravi_plate_video');
  END IF;
  IF EXISTS (SELECT 1 FROM pgmq.list_queues() WHERE queue_name = 'cyl_video_generation') THEN
    PERFORM pgmq.drop_queue('cyl_video_generation');
  END IF;
END
$$;

COMMIT;
