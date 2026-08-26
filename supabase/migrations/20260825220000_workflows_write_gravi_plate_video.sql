-- Write access to a plate's rendered video for the workflows service.
--
-- Grants bloom_workflows:
--
--   graviscan-videos bucket     SELECT, INSERT, UPDATE — the MP4 itself
--   public.gravi_plate_videos   EXECUTE on record_gravi_plate_video, and no
--                               table grant — the wrapper is the only write

BEGIN;

-- 1. fps ---------------------------------------------------------------------
-- frame_count and duration_seconds do not imply the rate.

ALTER TABLE public.gravi_plate_videos ADD COLUMN IF NOT EXISTS fps INT;

-- 2. The write path ----------------------------------------------------------
-- The API does not write this table directly. It calls this helper function,
-- which makes the write.

CREATE OR REPLACE FUNCTION public.record_gravi_plate_video(
  p_experiment_id   bigint,
  p_plate_id        text,
  p_wave_number     int,
  p_object_path     text,
  p_frame_count     int,
  p_duration_seconds int,
  p_fps             int,
  p_file_size_bytes bigint,
  p_file_hash       text
)
RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp
AS $$
  INSERT INTO public.gravi_plate_videos (
    experiment_id, plate_id, wave_number, object_path,
    frame_count, duration_seconds, fps, file_size_bytes, file_hash, generated_at
  )
  VALUES (
    p_experiment_id, p_plate_id, p_wave_number, p_object_path,
    p_frame_count, p_duration_seconds, p_fps, p_file_size_bytes, p_file_hash, now()
  )
  ON CONFLICT (experiment_id, plate_id, COALESCE(wave_number, -1)) DO UPDATE
    SET object_path      = EXCLUDED.object_path,
        frame_count      = EXCLUDED.frame_count,
        duration_seconds = EXCLUDED.duration_seconds,
        fps              = EXCLUDED.fps,
        file_size_bytes  = EXCLUDED.file_size_bytes,
        file_hash        = EXCLUDED.file_hash,
        generated_at     = EXCLUDED.generated_at;
$$;

-- Supabase grants EXECUTE to anon and authenticated by default, and this is
-- reachable over the API. Only the rendering service may record.
REVOKE EXECUTE ON FUNCTION public.record_gravi_plate_video(
  bigint, text, int, text, int, int, int, bigint, text
) FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.record_gravi_plate_video(
  bigint, text, int, text, int, int, int, bigint, text
) TO bloom_workflows;

-- Owner pinned rather than left to whoever applies this, matching 20260813220000.
ALTER FUNCTION public.record_gravi_plate_video(
  bigint, text, int, text, int, int, int, bigint, text
) OWNER TO postgres;

-- 3. Storage -----------------------------------------------------------------
-- Lets the workflows service write the graviscan-videos bucket. SELECT too,
-- because an upsert reads the object back.

DROP POLICY IF EXISTS workflows_read_graviscan_videos ON storage.objects;
CREATE POLICY workflows_read_graviscan_videos ON storage.objects
  FOR SELECT TO bloom_workflows USING (bucket_id = 'graviscan-videos');

DROP POLICY IF EXISTS workflows_insert_graviscan_videos ON storage.objects;
CREATE POLICY workflows_insert_graviscan_videos ON storage.objects
  FOR INSERT TO bloom_workflows WITH CHECK (bucket_id = 'graviscan-videos');

DROP POLICY IF EXISTS workflows_update_graviscan_videos ON storage.objects;
CREATE POLICY workflows_update_graviscan_videos ON storage.objects
  FOR UPDATE TO bloom_workflows
  USING (bucket_id = 'graviscan-videos')
  WITH CHECK (bucket_id = 'graviscan-videos');

COMMIT;
