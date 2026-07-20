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
-- 'processing') -> complete (row 'complete' + pgmq.delete) or fail (retry via vt
-- expiry, or 'failed' + pgmq.archive as dead-letter after max attempts).

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
ALTER TABLE public.cyl_video_jobs ENABLE ROW LEVEL SECURITY;

-- Reads: the service (bloom_workflows) and authenticated users (frontend poll).
-- All writes go through the SECURITY DEFINER functions below, so no write grants.
GRANT SELECT ON public.cyl_video_jobs TO bloom_workflows;
DROP POLICY IF EXISTS cyl_video_jobs_read ON public.cyl_video_jobs;
CREATE POLICY cyl_video_jobs_read ON public.cyl_video_jobs
  FOR SELECT TO bloom_workflows, authenticated USING (true);

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

  INSERT INTO public.cyl_video_jobs (scan_id, experiment_id, status)
  VALUES (p_scan_id, p_experiment_id, 'queued')
  RETURNING id INTO v_job_id;

  SELECT pgmq.send(
    'cyl_video_generation',
    jsonb_build_object('job_id', v_job_id, 'scan_id', p_scan_id, 'experiment_id', p_experiment_id)
  ) INTO v_msg_id;

  UPDATE public.cyl_video_jobs SET msg_id = v_msg_id WHERE id = v_job_id;
  RETURN v_job_id;
END;
$$;

-- claim: read one message (invisible for p_vt seconds), mark the job processing.
CREATE OR REPLACE FUNCTION public.claim_cyl_video_job(p_vt integer DEFAULT 120)
RETURNS TABLE(job_id uuid, scan_id bigint, experiment_id bigint, msg_id bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pgmq
AS $$
DECLARE
  r pgmq.message_record;
BEGIN
  SELECT * INTO r FROM pgmq.read('cyl_video_generation', p_vt, 1) LIMIT 1;
  IF NOT FOUND THEN
    RETURN;  -- empty queue
  END IF;

  UPDATE public.cyl_video_jobs
  SET status = 'processing', started_at = now()
  WHERE id = (r.message->>'job_id')::uuid;

  RETURN QUERY SELECT
    (r.message->>'job_id')::uuid,
    (r.message->>'scan_id')::bigint,
    (r.message->>'experiment_id')::bigint,
    r.msg_id;
END;
$$;

-- complete: record the path, drop the message.
CREATE OR REPLACE FUNCTION public.complete_cyl_video_job(p_job_id uuid, p_msg_id bigint, p_path text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pgmq
AS $$
BEGIN
  UPDATE public.cyl_video_jobs
  SET status = 'complete', path = p_path, completed_at = now()
  WHERE id = p_job_id;
  PERFORM pgmq.delete('cyl_video_generation', p_msg_id);
END;
$$;

-- fail: under max attempts, leave the message to redeliver after its vt expires
-- (row back to 'queued'); at max attempts, dead-letter it (archive) + mark failed.
CREATE OR REPLACE FUNCTION public.fail_cyl_video_job(
  p_job_id uuid, p_msg_id bigint, p_error text, p_max_attempts integer DEFAULT 3
)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pgmq
AS $$
DECLARE
  v_attempts integer;
BEGIN
  UPDATE public.cyl_video_jobs
  SET attempts = attempts + 1, error = p_error
  WHERE id = p_job_id
  RETURNING attempts INTO v_attempts;

  IF v_attempts >= p_max_attempts THEN
    UPDATE public.cyl_video_jobs SET status = 'failed', completed_at = now() WHERE id = p_job_id;
    PERFORM pgmq.archive('cyl_video_generation', p_msg_id);  -- dead-letter
  ELSE
    UPDATE public.cyl_video_jobs SET status = 'queued' WHERE id = p_job_id;
  END IF;
END;
$$;

GRANT EXECUTE ON FUNCTION public.enqueue_cyl_video(bigint, bigint) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.claim_cyl_video_job(integer) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.complete_cyl_video_job(uuid, bigint, text) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.fail_cyl_video_job(uuid, bigint, text, integer) TO bloom_workflows;

COMMIT;
