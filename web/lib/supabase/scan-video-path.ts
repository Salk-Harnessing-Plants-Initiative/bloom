// Where a cyl scan's video lives. Pure strings, no Supabase client and no
// `next/headers`, so a client component can import this — `scan-video.ts`
// cannot be imported from one, which is how the literals ended up copied into
// plant-scan.tsx.
//
// services/workflows/video.py writes this same key from the encoder side and
// is pinned against this file by tests/unit/test_cyl_video_path_agreement.py.

export const VIDEOS_BUCKET = "videos";

export function scanVideoPath(scanId: number): string {
  return `cyl-videos/${scanId}.mp4`;
}
