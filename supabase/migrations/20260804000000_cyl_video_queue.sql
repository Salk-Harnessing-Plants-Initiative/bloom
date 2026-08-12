-- Queued cyl-scan video generation: a pgmq queue + a status table + SECURITY
-- DEFINER wrapper functions.
--
-- The workflows service (role bloom_workflows, reached via the Supabase client —
-- no direct DB connection) cannot call pgmq directly. Instead it calls these
-- public wrapper functions, which run as the owner and encapsulate both the
-- queue and the status table. The enqueue route and the worker both go through
-- them, so no pgmq grants or PostgREST schema exposure are needed.
--
-- Flow: enqueue -> (row 'queued' + pgmq.send) -> worker claim (pgmq.read + row
-- 'processing') -> complete (row 'complete' + pgmq.delete) or fail (row 'failed' +
-- pgmq.archive; terminal for now — retry/requeue is deferred to a polling redesign).

BEGIN;

-- 1. The queue -------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pgmq.list_queues() WHERE queue_name = 'cyl_video_generation') THEN
    PERFORM pgmq.create('cyl_video_generation');
  END IF;
END
$$;

-- 2. Status table (the job viewer) -----------------------------------------
CREATE TABLE IF NOT EXISTS public.cyl_video_jobs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scan_id       bigint NOT NULL REFERENCES public.cyl_scans(id),
  experiment_id bigint,
  status        text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'processing', 'complete', 'failed')),
  msg_id        bigint,       -- pgmq message id (for delete/archive)
  attempts      integer NOT NULL DEFAULT 0,
  error         text,
  path          text,         -- stored video path when complete
  created_at    timestamptz DEFAULT now(),
  started_at    timestamptz,
  completed_at  timestamptz
);
CREATE INDEX IF NOT EXISTS idx_cyl_video_jobs_scan ON public.cyl_video_jobs(scan_id);
CREATE INDEX IF NOT EXISTS idx_cyl_video_jobs_status ON public.cyl_video_jobs(status);
-- At most one active job per scan — the DB enforces the dedupe that enqueue's
-- check-then-insert can't guarantee under concurrent calls.
CREATE UNIQUE INDEX IF NOT EXISTS cyl_video_jobs_one_active_per_scan
  ON public.cyl_video_jobs(scan_id) WHERE status IN ('queued', 'processing');
ALTER TABLE public.cyl_video_jobs ENABLE ROW LEVEL SECURITY;

-- Let the frontend poll job status. Real sessions hold bloom_user/writer/admin (from the JWT
-- hook), so the read policy targets those roles. Writes never come from clients — only the
-- SECURITY DEFINER wrappers below touch the table — so there is no write policy.
DROP POLICY IF EXISTS cyl_video_jobs_read ON public.cyl_video_jobs;
CREATE POLICY cyl_video_jobs_read ON public.cyl_video_jobs
  FOR SELECT TO bloom_user, bloom_writer, bloom_admin USING (true);
GRANT SELECT ON public.cyl_video_jobs TO bloom_user, bloom_writer, bloom_admin;

-- 3. Wrapper functions -----------------------------------------------------
-- SECURITY DEFINER so they run as the owner (which can use pgmq); bloom_workflows
-- only gets EXECUTE. search_path is pinned to avoid capture.

-- enqueue: idempotent per scan — reuse an in-flight job instead of piling up.
CREATE OR REPLACE FUNCTION public.enqueue_cyl_video(p_scan_id bigint, p_experiment_id bigint)
RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pgmq
AS $$
DECLARE
  v_job_id uuid;
  v_msg_id bigint;
BEGIN
  SELECT id INTO v_job_id
  FROM public.cyl_video_jobs
  WHERE scan_id = p_scan_id AND status IN ('queued', 'processing')
  ORDER BY created_at DESC
  LIMIT 1;
  IF v_job_id IS NOT NULL THEN
    RETURN v_job_id;  -- already queued/processing
  END IF;

  -- Skip if a video already exists for this scan
  IF EXISTS (SELECT 1 FROM public.cyl_scan_videos WHERE scan_id = p_scan_id) THEN
    RETURN NULL;  -- already generated; nothing enqueued
  END IF;

  -- Insert the new job. If a concurrent enqueue won the race for this scan, the
  -- partial unique index raises unique_violation — reuse the winner's job.
  BEGIN
    INSERT INTO public.cyl_video_jobs (scan_id, experiment_id, status)
    VALUES (p_scan_id, p_experiment_id, 'queued')
    RETURNING id INTO v_job_id;
  EXCEPTION WHEN unique_violation THEN
    SELECT id INTO v_job_id
    FROM public.cyl_video_jobs
    WHERE scan_id = p_scan_id AND status IN ('queued', 'processing')
    ORDER BY created_at DESC
    LIMIT 1;
    RETURN v_job_id;  -- winner already sent the message; don't enqueue a duplicate
  END;

  SELECT pgmq.send(
    'cyl_video_generation',
    jsonb_build_object('job_id', v_job_id, 'scan_id', p_scan_id, 'experiment_id', p_experiment_id)
  ) INTO v_msg_id;

  UPDATE public.cyl_video_jobs SET msg_id = v_msg_id WHERE id = v_job_id;
  RETURN v_job_id;
END;
$$;

-- claim: hand the worker the next job — read one message (hidden for p_vt seconds
-- so no other worker takes it), mark the job 'processing', and return its details.
CREATE OR REPLACE FUNCTION public.claim_cyl_video_job(
  p_vt integer DEFAULT 120, p_max_reads integer DEFAULT 5
)
RETURNS TABLE(job_id uuid, scan_id bigint, experiment_id bigint, msg_id bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pgmq
AS $$
DECLARE
  r pgmq.message_record;
  v_job_id uuid;
BEGIN
  SELECT * INTO r FROM pgmq.read('cyl_video_generation', p_vt, 1) LIMIT 1;
  IF NOT FOUND THEN
    RETURN;  -- empty queue
  END IF;

  v_job_id := (r.message->>'job_id')::uuid;

  -- Poison-message guard: dead-letter a message delivered too many times. read_ct is pgmq's
  -- per-delivery counter, so this catches a worker that keeps crashing before it can call
  -- fail_cyl_video_job (which a normal caught error would).
  IF r.read_ct > p_max_reads THEN
    RAISE WARNING 'cyl_video_job % dead-lettered after % deliveries (poison message)',
      v_job_id, r.read_ct;
    UPDATE public.cyl_video_jobs
    SET status = 'failed',
        error = coalesce(error, format('dead-lettered after %s deliveries', r.read_ct)),
        completed_at = now()
    WHERE id = v_job_id;
    PERFORM pgmq.archive('cyl_video_generation', r.msg_id);
    RETURN;  -- poison message — do not hand it to the worker again
  END IF;

  -- Mark processing, but only from a non-terminal state. A terminal job never keeps a live
  -- message today; if a stray message ever pointed at one, don't hand it to the worker —
  -- archive it and return nothing (the complete/fail wrappers below also guard on 'processing').
  UPDATE public.cyl_video_jobs
  SET status = 'processing', started_at = now()
  WHERE id = v_job_id AND status IN ('queued', 'processing');
  IF NOT FOUND THEN
    PERFORM pgmq.archive('cyl_video_generation', r.msg_id);
    RETURN;
  END IF;

  RETURN QUERY SELECT
    v_job_id,
    (r.message->>'scan_id')::bigint,
    (r.message->>'experiment_id')::bigint,
    r.msg_id;
END;
$$;

-- complete: record the path, drop the message. Guard on 'processing' so a late second worker
-- (vt-expiry redelivery / deploy race) can't clobber a job another worker already settled.
CREATE OR REPLACE FUNCTION public.complete_cyl_video_job(p_job_id uuid, p_msg_id bigint, p_path text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pgmq
AS $$
BEGIN
  UPDATE public.cyl_video_jobs
  SET status = 'complete', path = p_path, completed_at = now()
  WHERE id = p_job_id AND status = 'processing';
  PERFORM pgmq.delete('cyl_video_generation', p_msg_id);
END;
$$;

-- fail: mark the job 'failed' and dead-letter its message. Terminal for now; retry/requeue is
-- deferred to a later polling-based redesign. (p_max_attempts is unused, kept for that restore.)
CREATE OR REPLACE FUNCTION public.fail_cyl_video_job(
  p_job_id uuid, p_msg_id bigint, p_error text, p_max_attempts integer DEFAULT 3
)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pgmq
AS $$
BEGIN
  -- Guard on 'processing' so a late/duplicate failure can't clobber a job another worker
  -- already completed (vt-expiry redelivery / deploy race).
  UPDATE public.cyl_video_jobs
  SET attempts = attempts + 1, status = 'failed', error = p_error, completed_at = now()
  WHERE id = p_job_id AND status = 'processing';
  PERFORM pgmq.archive('cyl_video_generation', p_msg_id);  -- dead-letter immediately
END;
$$;

-- Backlog for the worker's log. bloom_workflows cannot read cyl_video_jobs (the read policy
-- and grant target the session roles only), so the counts come through a wrapper like
-- everything else the worker touches.
CREATE OR REPLACE FUNCTION public.cyl_video_queue_stats()
RETURNS TABLE(queued bigint, processing bigint, oldest_queued_seconds double precision)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
  SELECT
    count(*) FILTER (WHERE status = 'queued'),
    count(*) FILTER (WHERE status = 'processing'),
    -- extract() returns numeric on PG14+, so cast to match the declared column type.
    extract(epoch FROM now() - min(created_at) FILTER (WHERE status = 'queued'))::double precision
  FROM public.cyl_video_jobs;
$$;

-- Lock EXECUTE to bloom_workflows only. These are SECURITY DEFINER on the
-- PostgREST-exposed public schema, so any client-reachable grant lets anon/
-- authenticated call them via /rest/v1/rpc, bypassing the API's auth and scan
-- validation. Supabase grants EXECUTE to PUBLIC *and* anon/authenticated by
-- default, so revoke all three before granting the one sanctioned caller.
REVOKE EXECUTE ON FUNCTION public.enqueue_cyl_video(bigint, bigint) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.claim_cyl_video_job(integer, integer) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.complete_cyl_video_job(uuid, bigint, text) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.fail_cyl_video_job(uuid, bigint, text, integer) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.cyl_video_queue_stats() FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.enqueue_cyl_video(bigint, bigint) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.claim_cyl_video_job(integer, integer) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.complete_cyl_video_job(uuid, bigint, text) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.fail_cyl_video_job(uuid, bigint, text, integer) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.cyl_video_queue_stats() TO bloom_workflows;

-- Deterministic DEFINER identity, pinned to the role that owns the queue. A SECURITY DEFINER
-- function runs with its owner's privileges, and the pgmq schema and the q_/a_ tables belong
-- to supabase_admin (pgmq.create is SECURITY INVOKER, so the queue belongs to whoever ran the
-- migration). postgres cannot be the owner here: it is not a superuser on Supabase and holds
-- no privileges on another role's tables — rolbypassrls only skips row policies, it grants
-- nothing at the table level, so every wrapper fails with "permission denied for table
-- q_cyl_video_generation". Without an explicit owner the DEFINER would be whoever applied the
-- migration.
ALTER FUNCTION public.enqueue_cyl_video(bigint, bigint) OWNER TO supabase_admin;
ALTER FUNCTION public.claim_cyl_video_job(integer, integer) OWNER TO supabase_admin;
ALTER FUNCTION public.complete_cyl_video_job(uuid, bigint, text) OWNER TO supabase_admin;
ALTER FUNCTION public.fail_cyl_video_job(uuid, bigint, text, integer) OWNER TO supabase_admin;
ALTER FUNCTION public.cyl_video_queue_stats() OWNER TO supabase_admin;

COMMIT;
