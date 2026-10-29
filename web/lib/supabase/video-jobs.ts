// The latest render job for a plate or a scan, read with the caller's session.
//
// Advisory to the poll that uses it: a lookup that fails answers null and
// warns, so the stored video is still served.

import { createServerSupabaseClient } from "@/lib/supabase/server";
import { toVideoJob, type VideoJob } from "@/lib/video-job";

// The columns signed-in roles may read; `error`, `msg_id` and `reads` are not.
const JOB_COLUMNS =
  "id, status, stage, frames_done, frames_total, error_code, created_at, updated_at";

type Table = "gravi_plate_video_jobs" | "cyl_video_jobs";

type Supabase = Awaited<ReturnType<typeof createServerSupabaseClient>>;

async function withQueuePosition(
  supabase: Supabase,
  table: Table,
  row: unknown,
  label: string
): Promise<VideoJob | null> {
  // The row has no queued_ahead column, so the picker leaves it null.
  const job = toVideoJob(row);
  if (!job || job.status !== "queued") return job;

  const { count, error } = await supabase
    .from(table)
    .select("id", { count: "exact", head: true })
    .eq("status", "queued")
    .lt("created_at", job.created_at);
  if (error) {
    console.warn(`could not count the jobs ahead of ${label}: ${error.message}`);
    return job;
  }
  return { ...job, queued_ahead: count ?? 0 };
}

export async function getLatestPlateVideoJob(
  experimentId: number,
  plateId: string,
  waveNumber: number | null
): Promise<VideoJob | null> {
  const label = `plate ${experimentId}/${plateId}/wave ${waveNumber}`;
  try {
    const supabase = await createServerSupabaseClient();
    const base = supabase
      .from("gravi_plate_video_jobs")
      .select(JOB_COLUMNS)
      .eq("experiment_id", experimentId)
      .eq("plate_id", plateId);
    // A plate with no wave is a real case, and `= NULL` matches nothing.
    const { data, error } = await (waveNumber === null
      ? base.is("wave_number", null)
      : base.eq("wave_number", waveNumber)
    )
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (error) {
      console.warn(`could not read the video job for ${label}: ${error.message}`);
      return null;
    }
    return data ? withQueuePosition(supabase, "gravi_plate_video_jobs", data, label) : null;
  } catch (err) {
    console.warn(`could not read the video job for ${label}`, err);
    return null;
  }
}

export async function getLatestScanVideoJob(scanId: number): Promise<VideoJob | null> {
  const label = `scan ${scanId}`;
  try {
    const supabase = await createServerSupabaseClient();
    const { data, error } = await supabase
      .from("cyl_video_jobs")
      .select(JOB_COLUMNS)
      .eq("scan_id", scanId)
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (error) {
      console.warn(`could not read the video job for ${label}: ${error.message}`);
      return null;
    }
    return data ? withQueuePosition(supabase, "cyl_video_jobs", data, label) : null;
  } catch (err) {
    console.warn(`could not read the video job for ${label}`, err);
    return null;
  }
}
