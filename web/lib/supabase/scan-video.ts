// Where a cyl scan's video is stored, and whether one already exists.
// Shared so the scan page and the generate route agree on one key —
// services/workflows/video.py builds the same path.

import { createServerSupabaseClient } from "@/lib/supabase/server";
import { toPublicStorageUrl } from "@/lib/supabase/storage-url";

const VIDEOS_BUCKET = "videos";
const VIDEO_URL_TTL = 3600;

export function scanVideoPath(scanId: number): string {
  return `cyl-videos/${scanId}.mp4`;
}

// A signed URL for the scan's stored video, or null when none is stored.
// Signed against the internal host, so it is rewritten for the browser.
export async function getStoredScanVideoUrl(
  scanId: number
): Promise<string | null> {
  const supabase = await createServerSupabaseClient();

  const { data } = await supabase.storage
    .from(VIDEOS_BUCKET)
    .createSignedUrl(scanVideoPath(scanId), VIDEO_URL_TTL);

  return toPublicStorageUrl(data?.signedUrl);
}
