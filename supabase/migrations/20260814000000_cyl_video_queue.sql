-- Queued cyl-scan video generation: a pgmq queue, a job status table, and the
-- SECURITY DEFINER wrappers that are the only way to reach either.
--
-- The workflows service (role bloom_workflows, reached through PostgREST — no direct
-- DB connection) cannot touch pgmq. It calls these public wrappers instead, so no
-- pgmq grants or PostgREST schema exposure are needed.
--
-- Flow: enqueue -> (row 'queued' + pgmq.send) -> claim (pgmq.read + row 'processing')
-- -> complete (row 'complete' + pgmq.delete) or fail (row 'failed' + pgmq.archive).
--
-- This migration ships the schema only. The enqueue route, the worker, and the
-- compose service follow in later changes; nothing calls these functions yet.

BEGIN;

-- 1. Definer identity ------------------------------------------------------
-- The wrappers run as this role, not as the caller and not as a superuser. It is
-- NOT granted to authenticator, so no JWT can ever assume it — that is the whole
-- difference from bloom_workflows, which only ever gets EXECUTE.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bloom_video_queue_owner') THEN
    CREATE ROLE bloom_video_queue_owner NOLOGIN;
  END IF;
END
$$;
-- ALTER FUNCTION ... OWNER TO below needs the applier to be a member of the new
-- owner, and the new owner to hold CREATE on the function's schema.
GRANT bloom_video_queue_owner TO CURRENT_USER;
GRANT CREATE ON SCHEMA public TO bloom_video_queue_owner;

-- 2. The queue -------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pgmq.list_queues() WHERE queue_name = 'cyl_video_generation') THEN
    PERFORM pgmq.create('cyl_video_generation');
  END IF;
END
$$;

-- 3. Status table ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.cyl_video_jobs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scan_id       bigint NOT NULL REFERENCES public.cyl_scans(id),
  experiment_id bigint NOT NULL REFERENCES public.cyl_experiments(id),
  status        text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'processing', 'complete', 'failed')),
  msg_id        bigint,       -- pgmq message id (for delete/archive)
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

-- Let a client poll job status. Real sessions hold bloom_user/writer/admin from the
-- JWT hook, so the read policy targets those roles.
DROP POLICY IF EXISTS cyl_video_jobs_read ON public.cyl_video_jobs;
CREATE POLICY cyl_video_jobs_read ON public.cyl_video_jobs
  FOR SELECT TO bloom_user, bloom_writer, bloom_admin USING (true);
GRANT SELECT ON public.cyl_video_jobs TO bloom_user, bloom_writer, bloom_admin;
-- Writes come only from the wrappers below. Default privileges hand every new public
-- table to the bloom_* roles (20260414002000_security_groups.sql) and to Supabase's own
-- anon/authenticated/service_role, so revoke explicitly rather than relying on the
-- absence of a write policy. service_role matters most: it is BYPASSRLS, so a policy
-- would not stop it.
REVOKE INSERT, UPDATE, DELETE ON public.cyl_video_jobs
  FROM bloom_user, bloom_writer, bloom_admin;
REVOKE ALL ON public.cyl_video_jobs FROM anon, authenticated, service_role;

-- The wrappers run as bloom_video_queue_owner, which is neither the table owner nor
-- BYPASSRLS, so RLS applies to it and a grant alone leaves every statement filtered to
-- nothing. It needs its own policy on each table the wrappers touch.
GRANT SELECT, INSERT, UPDATE ON public.cyl_video_jobs TO bloom_video_queue_owner;
DROP POLICY IF EXISTS cyl_video_jobs_definer ON public.cyl_video_jobs;
CREATE POLICY cyl_video_jobs_definer ON public.cyl_video_jobs
  FOR ALL TO bloom_video_queue_owner USING (true) WITH CHECK (true);

-- enqueue reads this to skip a scan that already has a video.
GRANT SELECT ON public.cyl_scan_videos TO bloom_video_queue_owner;
DROP POLICY IF EXISTS cyl_scan_videos_queue_definer ON public.cyl_scan_videos;
CREATE POLICY cyl_scan_videos_queue_definer ON public.cyl_scan_videos
  FOR SELECT TO bloom_video_queue_owner USING (true);

-- 4. Wrapper functions -----------------------------------------------------
-- SECURITY DEFINER so they run as bloom_video_queue_owner (which can reach pgmq);
-- bloom_workflows only ever gets EXECUTE. search_path is pinned to avoid capture.

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

  -- Skip if a video already exists for this scan.
  IF EXISTS (SELECT 1 FROM public.cyl_scan_videos WHERE scan_id = p_scan_id) THEN
    RETURN NULL;  -- already generated; nothing enqueued
  END IF;

  -- If a concurrent enqueue won the race for this scan, the partial unique index
  -- raises unique_violation — reuse the winner's job.
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
    -- NULL here means the winner settled between the conflict and this re-select;
    -- the caller treats that as "nothing queued" rather than a phantom job.
    RETURN v_job_id;
  END;

  SELECT pgmq.send(
    'cyl_video_generation',
    jsonb_build_object('job_id', v_job_id, 'scan_id', p_scan_id, 'experiment_id', p_experiment_id)
  ) INTO v_msg_id;

  UPDATE public.cyl_video_jobs SET msg_id = v_msg_id WHERE id = v_job_id;
  RETURN v_job_id;
END;
$$;

-- claim: hand the worker the next job — read one message (hidden for p_vt seconds so
-- no other worker takes it), mark the job 'processing', and return its details.
CREATE OR REPLACE FUNCTION public.claim_cyl_video_job(
  p_vt integer DEFAULT 120, p_max_reads integer DEFAULT 5
)
RETURNS TABLE(job_id uuid, scan_id bigint, experiment_id bigint, msg_id bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pgmq
AS $$
DECLARE
  r pgmq.message_record;
  v_job_id uuid;
  v_scan_id bigint;
  v_experiment_id bigint;
BEGIN
  SELECT * INTO r FROM pgmq.read('cyl_video_generation', p_vt, 1);
  IF NOT FOUND THEN
    RETURN;  -- empty queue
  END IF;

  -- A malformed payload must not abort the statement: that would roll back pgmq's
  -- read_ct increment, leaving the message at the queue head where the poison guard
  -- below can never reach it.
  BEGIN
    v_job_id := (r.message->>'job_id')::uuid;
    v_scan_id := (r.message->>'scan_id')::bigint;
    v_experiment_id := (r.message->>'experiment_id')::bigint;
  EXCEPTION WHEN others THEN
    RAISE WARNING 'cyl_video queue: discarding unparseable message %', r.msg_id;
    PERFORM pgmq.archive('cyl_video_generation', r.msg_id);
    RETURN;
  END;

  -- Poison-message guard: dead-letter a message delivered too many times. read_ct is
  -- pgmq's per-delivery counter, so this catches a worker that keeps crashing before
  -- it can call fail_cyl_video_job.
  IF r.read_ct > p_max_reads THEN
    RAISE WARNING 'cyl_video_job % dead-lettered after % deliveries (poison message)',
      v_job_id, r.read_ct;
    -- Guard on the non-terminal states: a job that already settled keeps its outcome.
    UPDATE public.cyl_video_jobs
    SET status = 'failed',
        error = format('dead-lettered after %s deliveries', r.read_ct),
        completed_at = now()
    WHERE id = v_job_id AND status IN ('queued', 'processing');
    PERFORM pgmq.archive('cyl_video_generation', r.msg_id);
    RETURN;  -- poison message — do not hand it to the worker again
  END IF;

  -- Mark processing from a non-terminal state. 'processing' is accepted on purpose:
  -- it is how a job whose worker died mid-render is recovered once its message
  -- becomes visible again. The cost is that a lease expiring under a still-live
  -- worker double-renders, so the visibility timeout must exceed a worst-case render.
  UPDATE public.cyl_video_jobs
  SET status = 'processing', started_at = now()
  WHERE id = v_job_id AND status IN ('queued', 'processing');
  IF NOT FOUND THEN
    -- Terminal job with a stray live message — archive rather than re-run it.
    PERFORM pgmq.archive('cyl_video_generation', r.msg_id);
    RETURN;
  END IF;

  RETURN QUERY SELECT v_job_id, v_scan_id, v_experiment_id, r.msg_id;
END;
$$;

-- complete: record the path, drop the message. Guard on 'processing' so a late second
-- worker can't clobber a job another worker already settled.
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

-- fail: mark the job 'failed' and dead-letter its message. Terminal — retry is
-- deferred to the queue-hardening work.
CREATE OR REPLACE FUNCTION public.fail_cyl_video_job(
  p_job_id uuid, p_msg_id bigint, p_error text
)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pgmq
AS $$
BEGIN
  UPDATE public.cyl_video_jobs
  SET status = 'failed', error = p_error, completed_at = now()
  WHERE id = p_job_id AND status = 'processing';
  PERFORM pgmq.archive('cyl_video_generation', p_msg_id);
END;
$$;

-- Backlog for the worker's log. bloom_workflows cannot read cyl_video_jobs (the read
-- policy and grant target the session roles only), so counts come through a wrapper
-- like everything else the worker touches. 'failed' is included so a queue that is
-- silently discarding work is distinguishable from an idle one.
CREATE OR REPLACE FUNCTION public.cyl_video_queue_stats()
RETURNS TABLE(queued bigint, processing bigint, failed bigint, oldest_queued_seconds double precision)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
  SELECT
    count(*) FILTER (WHERE status = 'queued'),
    count(*) FILTER (WHERE status = 'processing'),
    count(*) FILTER (WHERE status = 'failed'),
    -- extract() returns numeric on PG14+, so cast to match the declared column type.
    extract(epoch FROM now() - min(created_at) FILTER (WHERE status = 'queued'))::double precision
  FROM public.cyl_video_jobs;
$$;

-- 5. Definer identity and EXECUTE ------------------------------------------
-- Pin the owner explicitly. Left unset, a SECURITY DEFINER function inherits whoever
-- applied the migration — in practice supabase_admin, a superuser — so these five
-- would run with unrestricted database access to insert a row and send a message.
ALTER FUNCTION public.enqueue_cyl_video(bigint, bigint) OWNER TO bloom_video_queue_owner;
ALTER FUNCTION public.claim_cyl_video_job(integer, integer) OWNER TO bloom_video_queue_owner;
ALTER FUNCTION public.complete_cyl_video_job(uuid, bigint, text) OWNER TO bloom_video_queue_owner;
ALTER FUNCTION public.fail_cyl_video_job(uuid, bigint, text) OWNER TO bloom_video_queue_owner;
ALTER FUNCTION public.cyl_video_queue_stats() OWNER TO bloom_video_queue_owner;

-- Lock EXECUTE to bloom_workflows only. These are SECURITY DEFINER on the
-- PostgREST-exposed public schema, so any client-reachable grant lets anon/
-- authenticated call them via /rest/v1/rpc, bypassing the API's auth and scan
-- validation. Supabase grants EXECUTE to PUBLIC *and* anon/authenticated by default,
-- so revoke all of them before granting the one sanctioned caller.
REVOKE EXECUTE ON FUNCTION public.enqueue_cyl_video(bigint, bigint) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.claim_cyl_video_job(integer, integer) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.complete_cyl_video_job(uuid, bigint, text) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.fail_cyl_video_job(uuid, bigint, text) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.cyl_video_queue_stats() FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.enqueue_cyl_video(bigint, bigint) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.claim_cyl_video_job(integer, integer) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.complete_cyl_video_job(uuid, bigint, text) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.fail_cyl_video_job(uuid, bigint, text) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.cyl_video_queue_stats() TO bloom_workflows;

-- The definer role's access to pgmq's own tables lives in
-- supabase/grants/schema_grants.sql: pgmq's queue tables are owned by supabase_admin,
-- and `supabase db push` downgrades the session role, so an in-migration grant here
-- would silently no-op.

COMMIT;
