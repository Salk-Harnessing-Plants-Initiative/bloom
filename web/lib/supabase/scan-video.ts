// Where a cyl scan's video is stored, and whether one already exists.
// Shared so the scan page and the generate route agree on one key —
// services/workflows/video.py builds the same path.

import { createServerSupabaseClient } from "@/lib/supabase/server";
import { toPublicStorageUrl } from "@/lib/supabase/storage-url";

const VIDEOS_BUCKET = "videos";
const VIDEO_URL_TTL = 3600;

// `unknown` exists because the answer gates an irreversible write: upstream
// overwrites the video in place and the bucket has no versioning. A lookup that
// failed must not be read as "no video here" — that is how a complete rotation
// gets replaced by a worse one.
export type StoredScanVideo =
  | { status: "present"; url: string }
  | { status: "absent" }
  | { status: "unknown"; reason: string };

export function scanVideoPath(scanId: number): string {
  return `cyl-videos/${scanId}.mp4`;
}

// Storage reports a missing object as an error, so only a genuine not-found
// counts as "absent" — anything else (permissions, gateway, timeout) is unknown.
function isNotFound(error: { message?: string; status?: number }): boolean {
  if (error.status === 404) return true;
  return /not[_ ]?found|does not exist|no such/i.test(error.message ?? "");
}

// Whether this scan has a stored video, and its browser-usable URL if so.
// Signed against the internal host, so it is rewritten for the browser.
export async function getStoredScanVideo(
  scanId: number
): Promise<StoredScanVideo> {
  const supabase = await createServerSupabaseClient();

  const { data, error } = await supabase.storage
    .from(VIDEOS_BUCKET)
    .createSignedUrl(scanVideoPath(scanId), VIDEO_URL_TTL);

  if (error) {
    return isNotFound(error)
      ? { status: "absent" }
      : { status: "unknown", reason: error.message ?? "storage lookup failed" };
  }

  const url = toPublicStorageUrl(data?.signedUrl);
  if (!url) {
    return { status: "unknown", reason: "storage returned no signed url" };
  }
  return { status: "present", url };
}

// The URL when one is stored, else null. For read-only callers that only need
// something to link to — never for deciding whether it is safe to generate.
export async function getStoredScanVideoUrl(
  scanId: number
): Promise<string | null> {
  const stored = await getStoredScanVideo(scanId);
  return stored.status === "present" ? stored.url : null;
}
