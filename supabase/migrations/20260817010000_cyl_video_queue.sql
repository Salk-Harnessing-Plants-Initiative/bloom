-- Queued cyl-scan video generation: a pgmq queue, a job status table, and the
-- SECURITY DEFINER wrappers that are the only way to reach either.
--
-- Flow: enqueue -> (row 'queued' + pgmq.send) -> claim (pgmq.read + row 'processing')
-- -> complete (row 'complete' + pgmq.delete) or fail (row 'failed' + pgmq.archive).
--
-- Schema only. The route, worker and compose service follow; nothing calls these yet.

BEGIN;

-- 1. Definer identity ------------------------------------------------------
-- Not granted to authenticator, so no JWT can assume it.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bloom_video_queue_owner') THEN
    CREATE ROLE bloom_video_queue_owner NOLOGIN;
  END IF;
END
$$;
-- Both are needed by the ALTER FUNCTION ... OWNER TO block in section 5. The CREATE is
-- revoked again at the end of that section.
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

-- Grants, not ownership: the wrappers only send/read/archive/delete. USAGE on the pgmq
-- schema lives in supabase/grants/schema_grants.sql — supabase_admin owns that schema,
-- and a grant from any other role silently no-ops (#333).
GRANT SELECT, INSERT, UPDATE, DELETE
  ON pgmq.q_cyl_video_generation TO bloom_video_queue_owner;
GRANT SELECT, INSERT ON pgmq.a_cyl_video_generation TO bloom_video_queue_owner;

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
-- At most one active job per scan — the dedupe enqueue's check-then-insert can't
-- guarantee under concurrent calls.
CREATE UNIQUE INDEX IF NOT EXISTS cyl_video_jobs_one_active_per_scan
  ON public.cyl_video_jobs(scan_id) WHERE status IN ('queued', 'processing');
ALTER TABLE public.cyl_video_jobs ENABLE ROW LEVEL SECURITY;

-- Let a client poll job status. Sessions hold bloom_user/writer/admin from the JWT hook.
DROP POLICY IF EXISTS cyl_video_jobs_read ON public.cyl_video_jobs;
CREATE POLICY cyl_video_jobs_read ON public.cyl_video_jobs
  FOR SELECT TO bloom_user, bloom_writer, bloom_admin USING (true);
-- Column-scoped: `error` carries raw pipeline output and `path` is the storage key
-- cyl_scan_videos withholds from these roles (20260716000000). Polling needs neither.
REVOKE SELECT ON public.cyl_video_jobs FROM bloom_user, bloom_writer, bloom_admin;
GRANT SELECT (id, scan_id, experiment_id, status, created_at, started_at, completed_at)
  ON public.cyl_video_jobs TO bloom_user, bloom_writer, bloom_admin;
-- Writes come only from the wrappers. Default privileges grant every new public table to
-- the bloom_* roles (20260414002000) and to anon/authenticated/service_role, so revoke
-- explicitly — service_role is BYPASSRLS, which no policy would stop.
REVOKE INSERT, UPDATE, DELETE ON public.cyl_video_jobs
  FROM bloom_user, bloom_writer, bloom_admin;
REVOKE ALL ON public.cyl_video_jobs FROM anon, authenticated, service_role, bloom_agent;

-- The definer role is neither table owner nor BYPASSRLS, so RLS applies to it and it
-- needs a policy on each table the wrappers touch, not just a grant.
GRANT SELECT, INSERT, UPDATE ON public.cyl_video_jobs TO bloom_video_queue_owner;
DROP POLICY IF EXISTS cyl_video_jobs_definer ON public.cyl_video_jobs;
CREATE POLICY cyl_video_jobs_definer ON public.cyl_video_jobs
  FOR ALL TO bloom_video_queue_owner USING (true) WITH CHECK (true);

-- enqueue reads this to skip a scan that already has a video.
GRANT SELECT (scan_id) ON public.cyl_scan_videos TO bloom_video_queue_owner;
DROP POLICY IF EXISTS cyl_scan_videos_queue_definer ON public.cyl_scan_videos;
CREATE POLICY cyl_scan_videos_queue_definer ON public.cyl_scan_videos
  FOR SELECT TO bloom_video_queue_owner USING (true);

-- enqueue walks scan -> plant -> wave to verify experiment_id. Column-scoped to the join
-- keys only: these tables carry the phenotyping data itself.
GRANT SELECT (id, plant_id) ON public.cyl_scans TO bloom_video_queue_owner;
GRANT SELECT (id, wave_id) ON public.cyl_plants TO bloom_video_queue_owner;
GRANT SELECT (id, experiment_id) ON public.cyl_waves TO bloom_video_queue_owner;
DROP POLICY IF EXISTS cyl_scans_queue_definer ON public.cyl_scans;
CREATE POLICY cyl_scans_queue_definer ON public.cyl_scans
  FOR SELECT TO bloom_video_queue_owner USING (true);
DROP POLICY IF EXISTS cyl_plants_queue_definer ON public.cyl_plants;
CREATE POLICY cyl_plants_queue_definer ON public.cyl_plants
  FOR SELECT TO bloom_video_queue_owner USING (true);
DROP POLICY IF EXISTS cyl_waves_queue_definer ON public.cyl_waves;
CREATE POLICY cyl_waves_queue_definer ON public.cyl_waves
  FOR SELECT TO bloom_video_queue_owner USING (true);

-- 4. Wrapper functions -----------------------------------------------------
-- SECURITY DEFINER so they run as bloom_video_queue_owner (which can reach pgmq);
-- bloom_workflows only ever gets EXECUTE. search_path is pinned to avoid capture.

-- CREATE OR REPLACE cannot change a return type, and a differing arity adds an overload
-- rather than replacing. Drop first so an earlier draft cannot survive.
DROP FUNCTION IF EXISTS public.claim_cyl_video_job(integer, integer);
DROP FUNCTION IF EXISTS public.cyl_video_queue_stats();
DROP FUNCTION IF EXISTS public.complete_cyl_video_job(uuid, bigint, text);
DROP FUNCTION IF EXISTS public.fail_cyl_video_job(uuid, bigint, text);
DROP FUNCTION IF EXISTS public.fail_cyl_video_job(uuid, bigint, text, integer);

-- enqueue: idempotent per scan — reuse an in-flight job instead of piling up.
CREATE OR REPLACE FUNCTION public.enqueue_cyl_video(p_scan_id bigint, p_experiment_id bigint)
RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pgmq
AS $$
DECLARE
  v_job_id uuid;
  v_msg_id bigint;
BEGIN
  -- The FK proves the experiment exists, not that it is this scan's. Every hop is
  -- nullable, so reject only where the chain resolves and disagrees.
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

  -- A concurrent enqueue that won the race trips the partial unique index — reuse its job.
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
    -- NULL means the winner settled in between; the caller reads that as nothing queued.
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

  -- Caught, not raised: aborting here would roll back pgmq's read_ct increment and pin
  -- the message at the queue head, out of reach of the poison guard below.
  BEGIN
    v_job_id := (r.message->>'job_id')::uuid;
    v_scan_id := (r.message->>'scan_id')::bigint;
    v_experiment_id := (r.message->>'experiment_id')::bigint;
  EXCEPTION WHEN others THEN
    RAISE WARNING 'cyl_video queue: discarding unparseable message %', r.msg_id;
    PERFORM pgmq.archive('cyl_video_generation', r.msg_id);
    RETURN;
  END;

  -- Poison-message guard: dead-letter a message delivered too many times.
  IF r.read_ct > p_max_reads THEN
    RAISE WARNING 'cyl_video_job % dead-lettered after % deliveries (poison message)',
      v_job_id, r.read_ct;
    -- Non-terminal states only: a job that already settled keeps its outcome.
    UPDATE public.cyl_video_jobs
    SET status = 'failed',
        error = format('dead-lettered after %s deliveries', r.read_ct),
        completed_at = now()
    WHERE id = v_job_id AND status IN ('queued', 'processing');
    PERFORM pgmq.archive('cyl_video_generation', r.msg_id);
    RETURN;  -- poison message — do not hand it to the worker again
  END IF;

  -- 'processing' is accepted on purpose: it recovers a job whose worker died mid-render
  -- once the message becomes visible again. started_at is kept, so it means when work
  -- first began and a staleness check's clock is not reset by every redelivery.
  UPDATE public.cyl_video_jobs
  SET status = 'processing', started_at = COALESCE(started_at, now())
  WHERE id = v_job_id AND status IN ('queued', 'processing');
  IF NOT FOUND THEN
    -- Terminal job with a stray live message — archive rather than re-run it.
    PERFORM pgmq.archive('cyl_video_generation', r.msg_id);
    RETURN;
  END IF;

  RETURN QUERY SELECT v_job_id, v_scan_id, v_experiment_id, r.msg_id;
END;
$$;

-- complete: record the path, drop the message. Matching msg_id to the job keeps a
-- mismatched pair from destroying an unrelated message and wedging its scan forever;
-- the boolean tells a caller whose transition was rejected.
CREATE OR REPLACE FUNCTION public.complete_cyl_video_job(p_job_id uuid, p_msg_id bigint, p_path text)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pgmq
AS $$
BEGIN
  UPDATE public.cyl_video_jobs
  SET status = 'complete', path = p_path, completed_at = now()
  WHERE id = p_job_id AND msg_id = p_msg_id AND status = 'processing';
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  PERFORM pgmq.delete('cyl_video_generation', p_msg_id);
  RETURN true;
END;
$$;

-- fail: mark the job 'failed' and dead-letter its message. Terminal — retry is deferred
-- to the queue-hardening work. Same msg_id/job_id pairing as complete.
CREATE OR REPLACE FUNCTION public.fail_cyl_video_job(
  p_job_id uuid, p_msg_id bigint, p_error text
)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pgmq
AS $$
BEGIN
  UPDATE public.cyl_video_jobs
  SET status = 'failed', error = left(p_error, 2000), completed_at = now()
  WHERE id = p_job_id AND msg_id = p_msg_id AND status = 'processing';
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  PERFORM pgmq.archive('cyl_video_generation', p_msg_id);
  RETURN true;
END;
$$;

-- Backlog for the worker's log. bloom_workflows cannot read cyl_video_jobs directly, so
-- counts come through a wrapper like everything else it touches.
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
-- Left unset, a SECURITY DEFINER function inherits whoever applied the migration — in
-- practice a superuser.
ALTER FUNCTION public.enqueue_cyl_video(bigint, bigint) OWNER TO bloom_video_queue_owner;
ALTER FUNCTION public.claim_cyl_video_job(integer, integer) OWNER TO bloom_video_queue_owner;
ALTER FUNCTION public.complete_cyl_video_job(uuid, bigint, text) OWNER TO bloom_video_queue_owner;
ALTER FUNCTION public.fail_cyl_video_job(uuid, bigint, text) OWNER TO bloom_video_queue_owner;
ALTER FUNCTION public.cyl_video_queue_stats() OWNER TO bloom_video_queue_owner;

-- Lock EXECUTE to bloom_workflows. These sit in the PostgREST-exposed public schema, so
-- any client-reachable grant lets anon/authenticated call them via /rest/v1/rpc. Supabase
-- grants EXECUTE to PUBLIC and anon/authenticated by default, so revoke before granting.
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

-- Hand back section 1's DDL right, needed only for the ownership transfer above.
REVOKE CREATE ON SCHEMA public FROM bloom_video_queue_owner;

COMMIT;
