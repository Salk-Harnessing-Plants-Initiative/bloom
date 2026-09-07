// Where a plate's time-lapse is stored, and whether one is there.
//
// Mirrors `scan-video.ts` for the cylinder path, and reuses its `isNotFound`:
// Storage answers a missing object with HTTP 400 and a *string* `statusCode`,
// so a check on `status === 404` never matches and the wording is the guard.

import { createServerSupabaseClient } from "@/lib/supabase/server";
import { toPublicStorageUrl } from "@/lib/supabase/storage-url";
import { isNotFound } from "@/lib/supabase/scan-video";
import {
  GRAVISCAN_VIDEOS_BUCKET,
  plateVideoPath,
} from "@/lib/supabase/plate-video-path";

const VIDEO_URL_TTL = 3600;

// `unknown` exists because a failed lookup is not an absence. The plate page
// reads it to decide what to offer, and reporting "no video" for a plate that
// has one sends a scientist to re-render something that was already fine.
// `frames` is null when the object is there and its row is not, which is a
// broken record rather than an empty video — the object still plays.
export type StoredPlateVideo =
  | { status: "present"; url: string; frames: number | null }
  | { status: "absent" }
  | { status: "unknown"; reason: string };

export async function getStoredPlateVideo(
  experimentId: number,
  plateId: string,
  waveNumber: number | null
): Promise<StoredPlateVideo> {
  const key = plateVideoPath(experimentId, waveNumber, plateId);
  if (key === null) {
    // Not "unknown": the key cannot be built, so no object can exist under it.
    return { status: "absent" };
  }

  const supabase = await createServerSupabaseClient();
  const { data, error } = await supabase.storage
    .from(GRAVISCAN_VIDEOS_BUCKET)
    .createSignedUrl(key, VIDEO_URL_TTL);

  if (error) {
    return isNotFound(error)
      ? { status: "absent" }
      : { status: "unknown", reason: error.message ?? "storage lookup failed" };
  }

  const url = toPublicStorageUrl(data?.signedUrl);
  if (!url) {
    return { status: "unknown", reason: "storage returned no signed url" };
  }

  // Storage decides whether a video exists; the row says what it holds. Read in
  // that order, because a row can outlive its object — trusting it for presence
  // would offer a player for a file that is gone.
  // A plate with no wave is a real case, and `= NULL` matches nothing — the
  // same branch the service makes on this column.
  const base = supabase
    .from("gravi_plate_videos")
    .select("frame_count")
    .eq("experiment_id", experimentId)
    .eq("plate_id", plateId);
  const { data: row } = await (waveNumber === null
    ? base.is("wave_number", null)
    : base.eq("wave_number", waveNumber)
  ).maybeSingle();

  if (!row) {
    console.warn(
      `no metadata present for stored plate video ${key}; serving it without a frame count`
    );
  }

  return { status: "present", url, frames: row?.frame_count ?? null };
}

// The URL when one is stored, else null. For read-only callers that only need
// something to link to — never for deciding whether it is safe to render.
export async function getStoredPlateVideoUrl(
  experimentId: number,
  plateId: string,
  waveNumber: number | null
): Promise<string | null> {
  const stored = await getStoredPlateVideo(experimentId, plateId, waveNumber);
  return stored.status === "present" ? stored.url : null;
}
