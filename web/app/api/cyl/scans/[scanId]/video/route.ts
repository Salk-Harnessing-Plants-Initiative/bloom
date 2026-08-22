/**
 * Has this scan's video landed yet?
 *
 * A video is stored at `cyl-videos/{scan_id}.mp4`, so the scan id is the whole
 * key and this route is scoped to it alone. Generation lives at
 * `/api/cyl/experiments/{id}/scans/{id}/video`, where the experiment id is
 * load-bearing — `scan_in_experiment` checks the pair upstream before encoding.
 * Asking whether a video exists needs no such pair, and a URL that carried one
 * without checking it would claim a scoping this lookup does not do.
 *
 * POST there can time out at 240s while the encode carries on (the upstream
 * handler is synchronous, so a client disconnect doesn't cancel it), leaving
 * the browser with a 504 and no handle on the work. This is how it finds out
 * how that ended.
 *
 * The URL is signed with the caller's own credentials, so this hands back only
 * what they could sign for themselves.
 */

import { NextResponse } from "next/server";
import { getSession } from "@/lib/supabase/server";
import { getStoredScanVideo } from "@/lib/supabase/scan-video";
import { parseId } from "@/components/scan-video.helpers";

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
  if (stored.status === "absent") {
    return NextResponse.json(
      { detail: "No video is stored for this scan yet." },
      { status: 404 }
    );
  }

  return NextResponse.json({ download_url: stored.url });
}
