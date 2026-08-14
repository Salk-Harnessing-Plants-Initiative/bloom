-- Record a scan's generated video through a wrapper instead of a client-side upsert.
--
-- Both video paths (the on-demand route and the queue worker) run as bloom_workflows and
-- recorded the row with PostgREST's `resolution=merge-duplicates`. That builds its DO UPDATE
-- from every key in the payload, so it emitted `scan_id = EXCLUDED.scan_id` — a no-op write,
-- but still a write. bloom_workflows holds UPDATE on (path, frames) and deliberately not on
-- scan_id, so postgres rejected the whole statement with "permission denied for table
-- cyl_scan_videos" and no video was ever recorded.
--
-- Matching on scan_id without writing it is the behaviour that was always intended, and a
-- function is the only place it can be stated: the client cannot express "key, don't set".
-- Keeping scan_id immutable also keeps a row from being repointed at another scan.

BEGIN;

CREATE OR REPLACE FUNCTION public.record_cyl_scan_video(
  p_scan_id bigint, p_path text, p_frames integer
)
RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
  INSERT INTO public.cyl_scan_videos (scan_id, path, frames)
  VALUES (p_scan_id, p_path, p_frames)
  ON CONFLICT (scan_id) DO UPDATE
    SET path = EXCLUDED.path, frames = EXCLUDED.frames;
$$;

-- Supabase grants EXECUTE to PUBLIC and anon/authenticated by default, and this is reachable
-- at /rest/v1/rpc. Only the services that generate videos may record one.
REVOKE EXECUTE ON FUNCTION public.record_cyl_scan_video(bigint, text, integer)
  FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.record_cyl_scan_video(bigint, text, integer) TO bloom_workflows;

-- Owner left to whoever applies this migration, matching the queue wrappers: it is the role
-- that owns cyl_scan_videos, so the DEFINER can always write the row.

COMMIT;
