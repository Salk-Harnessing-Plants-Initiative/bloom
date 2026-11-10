-- Refusals at enqueue for the video queues: a plate with no capture for the wave asked
-- for (after naming a missing experiment as missing), and a scan in a deleted cylinder
-- experiment. Both used to be accepted and either fail later in the worker or file a
-- job under a retired experiment.
-- Only the two enqueue functions change; signatures, owner and grants are unchanged.
--
-- Rollback: supabase/rollbacks/20261106200000_guard_video_enqueue_rollback.sql

BEGIN;

CREATE OR REPLACE FUNCTION public.enqueue_gravi_plate_video(
  p_experiment_id bigint,
  p_plate_id text,
  p_wave_number integer DEFAULT NULL,
  p_requested_by uuid DEFAULT NULL
)
RETURNS TABLE(job_id uuid, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq, pg_temp
AS $fn$
DECLARE
  v_job_id uuid;
  v_msg_id bigint;
BEGIN
  -- Checked first so a missing experiment is named as such, not as a plate without captures.
  IF NOT EXISTS (SELECT 1 FROM public.gravi_experiments e WHERE e.id = p_experiment_id) THEN
    RAISE EXCEPTION 'experiment % not found', p_experiment_id
      USING ERRCODE = 'foreign_key_violation';
  END IF;

  -- A plate with no capture for this wave has nothing to render, so it is refused now
  -- rather than failing as no_frames after its turn. The wave is gravi_scans.wave_number,
  -- the column the renderer reads, not the wave the page may show from metadata.
  IF NOT EXISTS (
    SELECT 1
    FROM public.gravi_scans s
    WHERE s.experiment_id = p_experiment_id
      AND s.plate_id = p_plate_id
      AND s.wave_number IS NOT DISTINCT FROM p_wave_number
  ) THEN
    RAISE EXCEPTION 'no captures for plate % in wave % of experiment %',
      p_plate_id, p_wave_number, p_experiment_id
      USING ERRCODE = 'no_data_found';
  END IF;

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
    -- The winner settled before this read; the caller retries rather than get no job.
    IF v_job_id IS NULL THEN
      RAISE EXCEPTION 'plate video job settled during enqueue; retry'
        USING ERRCODE = 'serialization_failure';
    END IF;
    RETURN QUERY SELECT v_job_id, false;
    RETURN;
  END;

  SELECT pgmq.send('gravi_plate_video_dispatch', jsonb_build_object('job_id', v_job_id)) INTO v_msg_id;
  UPDATE public.gravi_plate_video_jobs SET msg_id = v_msg_id WHERE id = v_job_id;
  RETURN QUERY SELECT v_job_id, true;
END;
$fn$;

CREATE OR REPLACE FUNCTION public.enqueue_cyl_video(
  p_scan_id bigint,
  p_experiment_id bigint,
  p_requested_by uuid DEFAULT NULL
)
RETURNS TABLE(job_id uuid, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq, pg_temp
AS $fn$
DECLARE
  v_job_id uuid;
  v_msg_id bigint;
  v_active_experiment bigint;
BEGIN
  -- A deleted experiment takes no new videos: deleted_at hides it from members, and the
  -- older V1 deleted flag marks it retired. Its own SQLSTATE so callers can say so.
  IF EXISTS (
    SELECT 1
    FROM public.cyl_experiments e
    WHERE e.id = p_experiment_id
      AND (e.deleted_at IS NOT NULL OR e.deleted IS TRUE)
  ) THEN
    RAISE EXCEPTION 'experiment % has been deleted', p_experiment_id
      USING ERRCODE = 'no_data_found';
  END IF;

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
    -- Its own code: the caller answers this 404, and an active job under another
    -- experiment (check_violation below) 409.
    RAISE EXCEPTION 'scan % does not belong to experiment %', p_scan_id, p_experiment_id
      USING ERRCODE = 'foreign_key_violation';
  END IF;

  SELECT j.id, j.experiment_id INTO v_job_id, v_active_experiment
  FROM public.cyl_video_jobs j
  WHERE j.scan_id = p_scan_id AND j.status IN ('queued', 'rendering');
  IF v_job_id IS NOT NULL THEN
    -- Where the scan's own chain is broken the active job's experiment is all there is
    -- to go on, so a request naming a different one is a disagreement, not a duplicate.
    -- Returning the job anyway would file the video under the other experiment and
    -- silently discard the correction.
    IF v_active_experiment IS DISTINCT FROM p_experiment_id THEN
      RAISE EXCEPTION 'scan % has an active video job under experiment %, not %',
        p_scan_id, v_active_experiment, p_experiment_id
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN QUERY SELECT v_job_id, false;
    RETURN;
  END IF;

  BEGIN
    INSERT INTO public.cyl_video_jobs (scan_id, experiment_id, requested_by)
    VALUES (p_scan_id, p_experiment_id, p_requested_by)
    RETURNING id INTO v_job_id;
  EXCEPTION WHEN unique_violation THEN
    SELECT j.id, j.experiment_id INTO v_job_id, v_active_experiment
    FROM public.cyl_video_jobs j
    WHERE j.scan_id = p_scan_id AND j.status IN ('queued', 'rendering');
    IF v_job_id IS NULL THEN
      RAISE EXCEPTION 'scan video job settled during enqueue; retry'
        USING ERRCODE = 'serialization_failure';
    END IF;
    IF v_active_experiment IS DISTINCT FROM p_experiment_id THEN
      RAISE EXCEPTION 'scan % has an active video job under experiment %, not %',
        p_scan_id, v_active_experiment, p_experiment_id
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN QUERY SELECT v_job_id, false;
    RETURN;
  END;

  SELECT pgmq.send('cyl_video_dispatch', jsonb_build_object('job_id', v_job_id)) INTO v_msg_id;
  UPDATE public.cyl_video_jobs SET msg_id = v_msg_id WHERE id = v_job_id;
  RETURN QUERY SELECT v_job_id, true;
END;
$fn$;

-- Replacing keeps owner and grants; restated so the file holds them whatever ran before.
ALTER FUNCTION public.enqueue_gravi_plate_video(bigint, text, integer, uuid) OWNER TO postgres;
ALTER FUNCTION public.enqueue_cyl_video(bigint, bigint, uuid) OWNER TO postgres;
REVOKE EXECUTE ON FUNCTION public.enqueue_gravi_plate_video(bigint, text, integer, uuid) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.enqueue_cyl_video(bigint, bigint, uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.enqueue_gravi_plate_video(bigint, text, integer, uuid) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.enqueue_cyl_video(bigint, bigint, uuid) TO bloom_workflows;

COMMIT;
