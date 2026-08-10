/**
 * Server-side proxy for on-demand cyl scan video generation.
 *
 * The workflows API requires a Supabase user JWT and is reachable in-cluster at
 * `workflows:5100` (both services sit on `supanet` in dev and prod). Proxying
 * here rather than calling it from the browser keeps the service off the public
 * origin, keeps the access token out of client JS, and avoids adding a
 * `NEXT_PUBLIC_*` key just to name a backend.
 *
 * Generation is synchronous upstream — encoding a full rotation takes a while,
 * so callers should expect this request to stay open.
 */

import { NextResponse } from "next/server";
import { getSession } from "@/lib/supabase/server";
import { getStoredScanVideoUrl } from "@/lib/supabase/scan-video";
import { isScanVideoResult, parseId } from "@/components/scan-video.helpers";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// Under undici's 300s header timeout, so a slow encode surfaces as our own 504
// rather than an opaque UND_ERR — the encode itself carries on upstream.
const UPSTREAM_TIMEOUT_MS = 240_000;

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ experimentId: string; scanId: string }> }
) {
  const { experimentId, scanId } = await params;

  const experiment = parseId(experimentId);
  const scan = parseId(scanId);
  if (experiment === null || scan === null) {
    return NextResponse.json(
      { detail: "experimentId and scanId must be positive integers" },
      { status: 400 }
    );
  }

  // A short-circuit for the signed-out case, not the authorization decision:
  // the token is forwarded as-is and workflows verifies it against Supabase
  // (`GET /auth/v1/user`), so an expired or forged cookie is refused there.
  const session = await getSession();
  if (!session?.access_token) {
    return NextResponse.json(
      { detail: "Sign in to generate a video." },
      { status: 401 }
    );
  }

  // A stored video is final. Upstream overwrites `cyl-videos/{scan_id}.mp4`
  // in place, the bucket has no versioning, and videos predating
  // `cyl_scan_videos` carry no frame count for upstream's anti-degradation
  // check to compare against — so a regenerate could silently replace a
  // complete rotation with a worse one, with no undo.
  if (await getStoredScanVideoUrl(scan)) {
    return NextResponse.json(
      {
        detail:
          "This scan already has a video. Open the stored one — existing videos are not regenerated.",
      },
      { status: 409 }
    );
  }

  // Read per request, not at module load, so one image works in any
  // environment and tests can vary it.
  const workflowsUrl = process.env.WORKFLOWS_URL ?? "http://workflows:5100";

  let upstream: Response;
  try {
    upstream = await fetch(
      `${workflowsUrl}/cyl/experiments/${experiment}/scans/${scan}/video`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${session.access_token}` },
        signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
      }
    );
  } catch (err) {
    // A timeout is not a failure — the encode is still running, so say that
    // rather than sending the user back to click Generate again.
    if (err instanceof Error && err.name === "TimeoutError") {
      return NextResponse.json(
        {
          detail:
            "Still encoding — this scan is taking longer than expected. Check back shortly.",
        },
        { status: 504 }
      );
    }
    // Unreachable service — don't leak the internal host into the response.
    return NextResponse.json(
      { detail: "The video service is unavailable." },
      { status: 502 }
    );
  }

  // Pass the upstream status through so the client can tell a rate limit from a
  // missing scan. A non-JSON body (a proxy error page) becomes a generic detail
  // rather than a parse crash.
  const body = await upstream.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return NextResponse.json(
      {
        detail: upstream.ok ? "Unexpected response from the video service." : null,
      },
      { status: upstream.ok ? 502 : upstream.status }
    );
  }

  // A success whose shape we don't recognise is a failure — the client renders
  // these fields directly.
  if (upstream.ok && !isScanVideoResult(parsed)) {
    return NextResponse.json(
      { detail: "Unexpected response from the video service." },
      { status: 502 }
    );
  }

  return NextResponse.json(parsed, { status: upstream.status });
}
