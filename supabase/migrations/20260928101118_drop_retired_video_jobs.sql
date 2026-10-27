-- Migration: drop_retired_video_jobs
-- Created: 2026-09-28
--
-- video_jobs was the first-generation cyl video queue: the browser inserted a
-- row over PostgREST, a trigger fired pg_notify, and services/video-worker
-- listened and rendered the scan. That listener is retired and starts nowhere,
-- no code reads or writes the table, and it is empty in both staging and
-- production (0 rows, no timestamps, checked 2026-09-28).
--
-- Dropping it also removes a write surface: the table granted anon both SELECT
-- and INSERT with USING (true), and every insert fired the notify nothing
-- listens for.
--
-- Rollback: supabase/rollbacks/20260928101118_drop_retired_video_jobs_rollback.sql

-- No CASCADE: the trigger and the policies belong to the table and go with it,
-- and Postgres drops its membership of supabase_realtime for the same reason.
-- Anything else that turns out to depend on it should fail here and be looked
-- at, rather than be dropped silently.
DROP TABLE IF EXISTS public.video_jobs;

-- Standalone, so it has to be named: the trigger referenced it, not the other
-- way round.
DROP FUNCTION IF EXISTS public.notify_video_job();
