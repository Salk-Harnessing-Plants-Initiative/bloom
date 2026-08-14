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
--
-- This is the same wrapper #469 introduces for the queue worker, stated here so the
-- on-demand route is correct on its own. `CREATE OR REPLACE` with an identical body means
-- whichever change lands second re-applies it unchanged.

BEGIN;

CREATE OR REPLACE FUNCTION public.record_cyl_scan_video(
  p_scan_id bigint, p_path text, p_frames integer
)
RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp
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

-- Pinned rather than left to whoever applies this, matching the other cyl DEFINER wrappers
-- (20260630180000, 20260706170000). cyl_scan_videos has RLS on with policies for
-- bloom_workflows only, so a DEFINER owned by anyone else raises on the insert — and
-- _record_video logs that rather than raising, which is precisely the silent failure this
-- migration exists to end. It also settles the owner for whichever change lands second,
-- since CREATE OR REPLACE keeps the existing owner and errors if the applier is not it.
ALTER FUNCTION public.record_cyl_scan_video(bigint, text, integer) OWNER TO postgres;

COMMIT;
