-- Two pgmq queues and their job tables: one for plate videos, one for cylinder scan
-- videos. Additive and inert — nothing enqueues, claims or reads these yet.
--
-- Flow per queue: enqueue -> (row 'queued' + pgmq.send) -> claim (pgmq.read + row
-- 'rendering') -> report progress (renews the message's visibility) -> complete
-- ('rendered' or 'kept' + pgmq.delete) or fail ('failed' + pgmq.archive).
--
-- Rollback: supabase/rollbacks/20260928190000_create_video_generation_queues_rollback.sql

BEGIN;

-- 1. Queues ----------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pgmq.list_queues() WHERE queue_name = 'gravi_plate_video') THEN
    PERFORM pgmq.create('gravi_plate_video');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pgmq.list_queues() WHERE queue_name = 'cyl_video_generation') THEN
    PERFORM pgmq.create('cyl_video_generation');
  END IF;
END
$$;

-- The wrappers below are SECURITY DEFINER owned by postgres, and pgmq's own functions
-- are SECURITY INVOKER, so postgres needs these privileges directly or every call fails.
-- send INSERTs into q_, read and set_vt UPDATE it, delete DELETEs from it, and archive
-- moves a row from q_ to a_. Granted here rather than in supabase/grants/: this migration
-- creates the tables, so it always holds grant authority on them.
GRANT SELECT, INSERT, UPDATE, DELETE ON pgmq.q_gravi_plate_video TO postgres;
GRANT SELECT, INSERT, DELETE ON pgmq.a_gravi_plate_video TO postgres;
GRANT SELECT, INSERT, UPDATE, DELETE ON pgmq.q_cyl_video_generation TO postgres;
GRANT SELECT, INSERT, DELETE ON pgmq.a_cyl_video_generation TO postgres;

-- 2. Job tables ------------------------------------------------------------
-- A pgmq message is invisible while claimed and gone once deleted, so it cannot back a
-- status view. These tables carry the state the UI reads; the message carries only job_id.

CREATE TABLE IF NOT EXISTS public.gravi_plate_video_jobs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_id bigint NOT NULL REFERENCES public.gravi_experiments(id),
  plate_id      text NOT NULL,
  -- gravi_scans.wave_number, the column the renderer filters frames by — not the
  -- metadata wave the plate list reads.
  wave_number   integer,
  status        text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'rendering', 'rendered', 'kept', 'failed')),
  stage         text,
  frames_done   integer,
  frames_total  integer,
  error_code    text,
  error         text,
  requested_by  uuid,
  msg_id        bigint,
  reads         integer NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now(),
  started_at    timestamptz,
  finished_at   timestamptz
);

CREATE TABLE IF NOT EXISTS public.cyl_video_jobs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scan_id       bigint NOT NULL REFERENCES public.cyl_scans(id),
  experiment_id bigint NOT NULL REFERENCES public.cyl_experiments(id),
  status        text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'rendering', 'rendered', 'kept', 'failed')),
  stage         text,
  frames_done   integer,
  frames_total  integer,
  error_code    text,
  error         text,
  requested_by  uuid,
  msg_id        bigint,
  reads         integer NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now(),
  started_at    timestamptz,
  finished_at   timestamptz
);

CREATE INDEX IF NOT EXISTS idx_gravi_plate_video_jobs_status
  ON public.gravi_plate_video_jobs(status);
CREATE INDEX IF NOT EXISTS idx_gravi_plate_video_jobs_experiment
  ON public.gravi_plate_video_jobs(experiment_id);
CREATE INDEX IF NOT EXISTS idx_cyl_video_jobs_status
  ON public.cyl_video_jobs(status);
CREATE INDEX IF NOT EXISTS idx_cyl_video_jobs_scan
  ON public.cyl_video_jobs(scan_id);

-- One active job per item. The index, not the enqueue's check-then-insert, is what makes
-- a second active job impossible under concurrent calls. COALESCE because wave_number is
-- nullable and NULLs would otherwise never collide.
CREATE UNIQUE INDEX IF NOT EXISTS gravi_plate_video_jobs_one_active_per_plate
  ON public.gravi_plate_video_jobs (experiment_id, plate_id, COALESCE(wave_number, -1))
  WHERE status IN ('queued', 'rendering');
CREATE UNIQUE INDEX IF NOT EXISTS cyl_video_jobs_one_active_per_scan
  ON public.cyl_video_jobs (scan_id)
  WHERE status IN ('queued', 'rendering');

-- 3. Row-level security ----------------------------------------------------
-- Read-only for signed-in roles; every write goes through a wrapper. postgres is
-- BYPASSRLS, so the definer functions need no policy of their own.
ALTER TABLE public.gravi_plate_video_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cyl_video_jobs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS gravi_plate_video_jobs_read ON public.gravi_plate_video_jobs;
CREATE POLICY gravi_plate_video_jobs_read ON public.gravi_plate_video_jobs
  FOR SELECT TO bloom_user, bloom_writer, bloom_admin USING (true);
DROP POLICY IF EXISTS cyl_video_jobs_read ON public.cyl_video_jobs;
CREATE POLICY cyl_video_jobs_read ON public.cyl_video_jobs
  FOR SELECT TO bloom_user, bloom_writer, bloom_admin USING (true);

-- Default privileges grant every new public table to the bloom_* roles and to
-- anon/authenticated/service_role, so revoke explicitly. service_role is BYPASSRLS,
-- which no policy would stop.
REVOKE INSERT, UPDATE, DELETE ON public.gravi_plate_video_jobs
  FROM bloom_user, bloom_writer, bloom_admin;
REVOKE INSERT, UPDATE, DELETE ON public.cyl_video_jobs
  FROM bloom_user, bloom_writer, bloom_admin;
REVOKE ALL ON public.gravi_plate_video_jobs FROM anon, authenticated, service_role, bloom_agent;
REVOKE ALL ON public.cyl_video_jobs FROM anon, authenticated, service_role, bloom_agent;

-- 4. Wrappers --------------------------------------------------------------
-- SECURITY DEFINER, owned by postgres (section 5), EXECUTE for bloom_workflows only.
-- p_vt defaults are longer than one encode plus the upload and the recording that
-- follow it, so the one stage that reports no progress cannot outlive its visibility.
-- PR 3's worker passes the value it computes from ENCODE_TIMEOUT_SECONDS.
--
-- A differing arity adds an overload rather than replacing, so drop first.
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

-- 4a. Plate ----------------------------------------------------------------

-- Returns the active job for this plate, creating one only if there is none, so a double
-- click or two people on the same plate share one render.
CREATE OR REPLACE FUNCTION public.enqueue_gravi_plate_video(
  p_experiment_id bigint,
  p_plate_id text,
  p_wave_number integer DEFAULT NULL,
  p_requested_by uuid DEFAULT NULL
)
RETURNS TABLE(job_id uuid, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $fn$
DECLARE
  v_job_id uuid;
  v_msg_id bigint;
BEGIN
  SELECT j.id INTO v_job_id
  FROM public.gravi_plate_video_jobs j
  WHERE j.experiment_id = p_experiment_id
    AND j.plate_id = p_plate_id
    AND COALESCE(j.wave_number, -1) = COALESCE(p_wave_number, -1)
    AND j.status IN ('queued', 'rendering');
  IF v_job_id IS NOT NULL THEN
    RETURN QUERY SELECT v_job_id, false;
    RETURN;
  END IF;

  BEGIN
    INSERT INTO public.gravi_plate_video_jobs (experiment_id, plate_id, wave_number, requested_by)
    VALUES (p_experiment_id, p_plate_id, p_wave_number, p_requested_by)
    RETURNING id INTO v_job_id;
  EXCEPTION WHEN unique_violation THEN
    -- A concurrent enqueue won the race; hand back its job rather than failing.
    SELECT j.id INTO v_job_id
    FROM public.gravi_plate_video_jobs j
    WHERE j.experiment_id = p_experiment_id
      AND j.plate_id = p_plate_id
      AND COALESCE(j.wave_number, -1) = COALESCE(p_wave_number, -1)
      AND j.status IN ('queued', 'rendering');
    RETURN QUERY SELECT v_job_id, false;
    RETURN;
  END;

  SELECT pgmq.send('gravi_plate_video', jsonb_build_object('job_id', v_job_id)) INTO v_msg_id;
  UPDATE public.gravi_plate_video_jobs SET msg_id = v_msg_id WHERE id = v_job_id;
  RETURN QUERY SELECT v_job_id, true;
END;
$fn$;

-- Hand the worker the next job: read one message (hidden for p_vt seconds so no other
-- worker takes it) and mark the job 'rendering'.
CREATE OR REPLACE FUNCTION public.claim_gravi_plate_video_job(
  p_vt integer DEFAULT 300,
  p_max_reads integer DEFAULT 3
)
RETURNS TABLE(job_id uuid, experiment_id bigint, plate_id text, wave_number integer, msg_id bigint, reads integer)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $fn$
DECLARE
  r pgmq.message_record;
  v_job_id uuid;
  v_experiment_id bigint;
  v_plate_id text;
  v_wave_number integer;
BEGIN
  SELECT * INTO r FROM pgmq.read('gravi_plate_video', p_vt, 1);
  IF NOT FOUND THEN
    RETURN;
  END IF;

  -- Caught, not raised: aborting here would roll back pgmq's read_ct increment and pin
  -- the message at the queue head, out of reach of the redelivery guard below.
  BEGIN
    v_job_id := (r.message->>'job_id')::uuid;
  EXCEPTION WHEN others THEN
    RAISE WARNING 'gravi_plate_video: discarding unreadable message %', r.msg_id;
    PERFORM pgmq.archive('gravi_plate_video', r.msg_id);
    RETURN;
  END;

  IF r.read_ct > p_max_reads THEN
    UPDATE public.gravi_plate_video_jobs
    SET status = 'failed',
        error_code = 'redelivered',
        error = format('redelivered %s times without completing', r.read_ct),
        finished_at = now()
    WHERE id = v_job_id AND status IN ('queued', 'rendering');
    PERFORM pgmq.archive('gravi_plate_video', r.msg_id);
    RETURN;
  END IF;

  -- 'rendering' is accepted on purpose: it recovers a job whose worker died, once the
  -- message becomes visible again. started_at is kept, so it means when work first began
  -- and is not reset by every redelivery.
  UPDATE public.gravi_plate_video_jobs
  SET status = 'rendering',
      started_at = COALESCE(started_at, now()),
      msg_id = r.msg_id,
      reads = r.read_ct
  WHERE id = v_job_id AND status IN ('queued', 'rendering')
  RETURNING gravi_plate_video_jobs.experiment_id,
            gravi_plate_video_jobs.plate_id,
            gravi_plate_video_jobs.wave_number
  INTO v_experiment_id, v_plate_id, v_wave_number;
  IF NOT FOUND THEN
    -- A settled job with a stray live message — archive rather than re-run it.
    PERFORM pgmq.archive('gravi_plate_video', r.msg_id);
    RETURN;
  END IF;

  RETURN QUERY SELECT v_job_id, v_experiment_id, v_plate_id, v_wave_number, r.msg_id, r.read_ct;
END;
$fn$;

-- Record progress and push the message's visibility forward, so a live worker keeps its
-- job for as long as it reports and a dead one's job reappears within one timeout.
CREATE OR REPLACE FUNCTION public.report_gravi_plate_video_progress(
  p_job_id uuid,
  p_msg_id bigint,
  p_stage text,
  p_frames_done integer DEFAULT NULL,
  p_frames_total integer DEFAULT NULL,
  p_vt integer DEFAULT 300
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $fn$
BEGIN
  UPDATE public.gravi_plate_video_jobs
  SET stage = p_stage,
      frames_done = COALESCE(p_frames_done, frames_done),
      frames_total = COALESCE(p_frames_total, frames_total)
  WHERE id = p_job_id AND msg_id = p_msg_id AND status = 'rendering';
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  PERFORM pgmq.set_vt('gravi_plate_video', p_msg_id, p_vt);
  RETURN true;
END;
$fn$;

-- 'rendered' made a new video, 'kept' found one already there. Matching msg_id to the job
-- keeps a mismatched pair from destroying an unrelated message.
CREATE OR REPLACE FUNCTION public.complete_gravi_plate_video_job(
  p_job_id uuid,
  p_msg_id bigint,
  p_outcome text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $fn$
BEGIN
  IF p_outcome NOT IN ('rendered', 'kept') THEN
    RAISE EXCEPTION 'outcome must be rendered or kept, got %', p_outcome
      USING ERRCODE = 'check_violation';
  END IF;
  UPDATE public.gravi_plate_video_jobs
  SET status = p_outcome, stage = NULL, finished_at = now()
  WHERE id = p_job_id AND msg_id = p_msg_id AND status = 'rendering';
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  PERFORM pgmq.delete('gravi_plate_video', p_msg_id);
  RETURN true;
END;
$fn$;

-- Terminal. Retry is the queue-hardening work, not this change.
CREATE OR REPLACE FUNCTION public.fail_gravi_plate_video_job(
  p_job_id uuid,
  p_msg_id bigint,
  p_error_code text,
  p_error text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $fn$
BEGIN
  UPDATE public.gravi_plate_video_jobs
  SET status = 'failed',
      error_code = p_error_code,
      error = left(p_error, 2000),
      stage = NULL,
      finished_at = now()
  WHERE id = p_job_id AND msg_id = p_msg_id AND status = 'rendering';
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  PERFORM pgmq.archive('gravi_plate_video', p_msg_id);
  RETURN true;
END;
$fn$;

-- 4b. Cylinder -------------------------------------------------------------
-- Read against 4a: same shape, keyed on scan_id.

CREATE OR REPLACE FUNCTION public.enqueue_cyl_video(
  p_scan_id bigint,
  p_experiment_id bigint,
  p_requested_by uuid DEFAULT NULL
)
RETURNS TABLE(job_id uuid, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $fn$
DECLARE
  v_job_id uuid;
  v_msg_id bigint;
BEGIN
  -- The foreign key proves the experiment exists, not that it is this scan's. Every hop
  -- is nullable, so reject only where the chain resolves and disagrees.
  IF EXISTS (
    SELECT 1
    FROM public.cyl_scans s
    JOIN public.cyl_plants p ON p.id = s.plant_id
    JOIN public.cyl_waves  w ON w.id = p.wave_id
    WHERE s.id = p_scan_id
      AND w.experiment_id IS NOT NULL
      AND w.experiment_id IS DISTINCT FROM p_experiment_id
  ) THEN
    RAISE EXCEPTION 'scan % does not belong to experiment %', p_scan_id, p_experiment_id
      USING ERRCODE = 'check_violation';
  END IF;

  SELECT j.id INTO v_job_id
  FROM public.cyl_video_jobs j
  WHERE j.scan_id = p_scan_id AND j.status IN ('queued', 'rendering');
  IF v_job_id IS NOT NULL THEN
    RETURN QUERY SELECT v_job_id, false;
    RETURN;
  END IF;

  BEGIN
    INSERT INTO public.cyl_video_jobs (scan_id, experiment_id, requested_by)
    VALUES (p_scan_id, p_experiment_id, p_requested_by)
    RETURNING id INTO v_job_id;
  EXCEPTION WHEN unique_violation THEN
    SELECT j.id INTO v_job_id
    FROM public.cyl_video_jobs j
    WHERE j.scan_id = p_scan_id AND j.status IN ('queued', 'rendering');
    RETURN QUERY SELECT v_job_id, false;
    RETURN;
  END;

  SELECT pgmq.send('cyl_video_generation', jsonb_build_object('job_id', v_job_id)) INTO v_msg_id;
  UPDATE public.cyl_video_jobs SET msg_id = v_msg_id WHERE id = v_job_id;
  RETURN QUERY SELECT v_job_id, true;
END;
$fn$;

CREATE OR REPLACE FUNCTION public.claim_cyl_video_job(
  p_vt integer DEFAULT 300,
  p_max_reads integer DEFAULT 3
)
RETURNS TABLE(job_id uuid, scan_id bigint, experiment_id bigint, msg_id bigint, reads integer)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $fn$
DECLARE
  r pgmq.message_record;
  v_job_id uuid;
  v_scan_id bigint;
  v_experiment_id bigint;
BEGIN
  SELECT * INTO r FROM pgmq.read('cyl_video_generation', p_vt, 1);
  IF NOT FOUND THEN
    RETURN;
  END IF;

  BEGIN
    v_job_id := (r.message->>'job_id')::uuid;
  EXCEPTION WHEN others THEN
    RAISE WARNING 'cyl_video_generation: discarding unreadable message %', r.msg_id;
    PERFORM pgmq.archive('cyl_video_generation', r.msg_id);
    RETURN;
  END;

  IF r.read_ct > p_max_reads THEN
    UPDATE public.cyl_video_jobs
    SET status = 'failed',
        error_code = 'redelivered',
        error = format('redelivered %s times without completing', r.read_ct),
        finished_at = now()
    WHERE id = v_job_id AND status IN ('queued', 'rendering');
    PERFORM pgmq.archive('cyl_video_generation', r.msg_id);
    RETURN;
  END IF;

  UPDATE public.cyl_video_jobs
  SET status = 'rendering',
      started_at = COALESCE(started_at, now()),
      msg_id = r.msg_id,
      reads = r.read_ct
  WHERE id = v_job_id AND status IN ('queued', 'rendering')
  RETURNING cyl_video_jobs.scan_id, cyl_video_jobs.experiment_id
  INTO v_scan_id, v_experiment_id;
  IF NOT FOUND THEN
    PERFORM pgmq.archive('cyl_video_generation', r.msg_id);
    RETURN;
  END IF;

  RETURN QUERY SELECT v_job_id, v_scan_id, v_experiment_id, r.msg_id, r.read_ct;
END;
$fn$;

CREATE OR REPLACE FUNCTION public.report_cyl_video_progress(
  p_job_id uuid,
  p_msg_id bigint,
  p_stage text,
  p_frames_done integer DEFAULT NULL,
  p_frames_total integer DEFAULT NULL,
  p_vt integer DEFAULT 300
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $fn$
BEGIN
  UPDATE public.cyl_video_jobs
  SET stage = p_stage,
      frames_done = COALESCE(p_frames_done, frames_done),
      frames_total = COALESCE(p_frames_total, frames_total)
  WHERE id = p_job_id AND msg_id = p_msg_id AND status = 'rendering';
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  PERFORM pgmq.set_vt('cyl_video_generation', p_msg_id, p_vt);
  RETURN true;
END;
$fn$;

CREATE OR REPLACE FUNCTION public.complete_cyl_video_job(
  p_job_id uuid,
  p_msg_id bigint,
  p_outcome text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $fn$
BEGIN
  IF p_outcome NOT IN ('rendered', 'kept') THEN
    RAISE EXCEPTION 'outcome must be rendered or kept, got %', p_outcome
      USING ERRCODE = 'check_violation';
  END IF;
  UPDATE public.cyl_video_jobs
  SET status = p_outcome, stage = NULL, finished_at = now()
  WHERE id = p_job_id AND msg_id = p_msg_id AND status = 'rendering';
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  PERFORM pgmq.delete('cyl_video_generation', p_msg_id);
  RETURN true;
END;
$fn$;

CREATE OR REPLACE FUNCTION public.fail_cyl_video_job(
  p_job_id uuid,
  p_msg_id bigint,
  p_error_code text,
  p_error text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $fn$
BEGIN
  UPDATE public.cyl_video_jobs
  SET status = 'failed',
      error_code = p_error_code,
      error = left(p_error, 2000),
      stage = NULL,
      finished_at = now()
  WHERE id = p_job_id AND msg_id = p_msg_id AND status = 'rendering';
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  PERFORM pgmq.archive('cyl_video_generation', p_msg_id);
  RETURN true;
END;
$fn$;

-- 5. Definer identity and EXECUTE ------------------------------------------
-- Left unset, the owner is whoever applied the migration: supabase_admin (a superuser
-- bypassing every policy) on one path, postgres on another. Pinned here so the wrappers
-- run as the same role in every environment, and so section 1's pgmq grants are the
-- privileges they actually use.
ALTER FUNCTION public.enqueue_gravi_plate_video(bigint, text, integer, uuid) OWNER TO postgres;
ALTER FUNCTION public.claim_gravi_plate_video_job(integer, integer) OWNER TO postgres;
ALTER FUNCTION public.report_gravi_plate_video_progress(uuid, bigint, text, integer, integer, integer) OWNER TO postgres;
ALTER FUNCTION public.complete_gravi_plate_video_job(uuid, bigint, text) OWNER TO postgres;
ALTER FUNCTION public.fail_gravi_plate_video_job(uuid, bigint, text, text) OWNER TO postgres;
ALTER FUNCTION public.enqueue_cyl_video(bigint, bigint, uuid) OWNER TO postgres;
ALTER FUNCTION public.claim_cyl_video_job(integer, integer) OWNER TO postgres;
ALTER FUNCTION public.report_cyl_video_progress(uuid, bigint, text, integer, integer, integer) OWNER TO postgres;
ALTER FUNCTION public.complete_cyl_video_job(uuid, bigint, text) OWNER TO postgres;
ALTER FUNCTION public.fail_cyl_video_job(uuid, bigint, text, text) OWNER TO postgres;

-- These sit in the PostgREST-exposed public schema, so any client-reachable grant lets
-- anon or authenticated call them over /rest/v1/rpc. Supabase grants EXECUTE to PUBLIC
-- and anon/authenticated by default, so revoke before granting.
REVOKE EXECUTE ON FUNCTION public.enqueue_gravi_plate_video(bigint, text, integer, uuid) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.claim_gravi_plate_video_job(integer, integer) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.report_gravi_plate_video_progress(uuid, bigint, text, integer, integer, integer) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.complete_gravi_plate_video_job(uuid, bigint, text) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.fail_gravi_plate_video_job(uuid, bigint, text, text) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.enqueue_cyl_video(bigint, bigint, uuid) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.claim_cyl_video_job(integer, integer) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.report_cyl_video_progress(uuid, bigint, text, integer, integer, integer) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.complete_cyl_video_job(uuid, bigint, text) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.fail_cyl_video_job(uuid, bigint, text, text) FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.enqueue_gravi_plate_video(bigint, text, integer, uuid) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.claim_gravi_plate_video_job(integer, integer) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.report_gravi_plate_video_progress(uuid, bigint, text, integer, integer, integer) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.complete_gravi_plate_video_job(uuid, bigint, text) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.fail_gravi_plate_video_job(uuid, bigint, text, text) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.enqueue_cyl_video(bigint, bigint, uuid) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.claim_cyl_video_job(integer, integer) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.report_cyl_video_progress(uuid, bigint, text, integer, integer, integer) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.complete_cyl_video_job(uuid, bigint, text) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.fail_cyl_video_job(uuid, bigint, text, text) TO bloom_workflows;

COMMIT;
