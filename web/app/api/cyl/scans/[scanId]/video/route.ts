/**
 * Has this scan's video landed yet, and how is its latest render going?
 *
 * A video is stored at `cyl-videos/{scan_id}.mp4`, so the scan id is the whole
 * key and this route is scoped to it alone. Generation lives at
 * `/api/cyl/experiments/{id}/scans/{id}/video`, where the experiment id is
 * load-bearing — `scan_in_experiment` checks the pair upstream before queuing.
 * Asking whether a video exists needs no such pair, and a URL that carried one
 * without checking it would claim a scoping this lookup does not do.
 *
 * The button polls this after queuing a render. The URL is signed with the
 * caller's own credentials, so this hands back only what they could sign for
 * themselves.
 */

import { NextResponse } from "next/server";
import { getSession } from "@/lib/supabase/server";
import { getStoredScanVideo } from "@/lib/supabase/scan-video";
import { getLatestScanVideoJob } from "@/lib/supabase/video-jobs";
import { parseId } from "@/lib/route-params";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ scanId: string }> }
) {
  const { scanId } = await params;

  const scan = parseId(scanId);
  if (scan === null) {
    return NextResponse.json(
      { detail: "scanId must be a positive integer" },
      { status: 400 }
    );
  }

  const session = await getSession();
  if (!session?.access_token) {
    return NextResponse.json(
      { detail: "Sign in to view this video." },
      { status: 401 }
    );
  }

  // A lookup that failed is not an absence. Answering 404 for a storage outage
  // states as fact that this scan has no video, which is how a complete
  // rotation comes to be reported as missing — the same conflation
  // getStoredScanVideo's three-state answer exists to prevent.
  const stored = await getStoredScanVideo(scan);
  if (stored.status === "unknown") {
    return NextResponse.json(
      { detail: "Could not check whether this scan has a video. Try again shortly." },
      { status: 503 }
    );
  }
  const job = await getLatestScanVideoJob(scan);

  return NextResponse.json({
    download_url: stored.status === "present" ? stored.url : null,
    job,
  });
}
