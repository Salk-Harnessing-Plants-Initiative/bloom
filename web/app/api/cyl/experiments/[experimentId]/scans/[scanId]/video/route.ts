/**
 * Server-side proxy for on-demand cyl scan video generation.
 *
 * The workflows API requires a Supabase user JWT and is reachable in-cluster at
 * `workflows:5100` (both services sit on `supanet` in dev and prod). Proxying
 * here rather than calling it from the browser keeps the access token out of
 * client JS and avoids adding a `NEXT_PUBLIC_*` key just to name a backend.
 *
 * It is not a security boundary: Caddy also publishes the service at
 * `/workflows/*`, so everything below — the 409, the detail suppression — is
 * about what this app does, not about what the service permits. Authorization
 * lives upstream (`require_supabase_user`, then `scan_in_experiment`).
 *
 * Generation is synchronous upstream — encoding a full rotation takes a while,
 * so callers should expect this request to stay open.
 */

import { NextResponse } from "next/server";
import { getSession } from "@/lib/supabase/server";
import { getStoredScanVideo } from "@/lib/supabase/scan-video";
import { isScanVideoResult, parseId } from "@/components/scan-video.helpers";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// Under undici's 300s header timeout, so a slow encode surfaces as our own 504
// rather than an opaque UND_ERR — the encode itself carries on upstream.
const UPSTREAM_TIMEOUT_MS = 240_000;

// Upstream failure details are written for operators, not callers: the 5xx ones
// carry the internal gateway URL and the names of the service account's env
// keys. Only 404 says something the caller needs ("No images found for scan 5");
// every other status falls back to the client's own wording.
// Only 404. Upstream also answers 503 for its own refusals, whose wording would be worth
// showing — but `require_supabase_user` returns 503 too, carrying the internal gateway URL in
// its message, so the status alone cannot tell a safe detail from a leaky one. The client
// words 503 for itself instead (`videoErrorMessage`).
const DETAIL_PASSTHROUGH_STATUSES = new Set([404]);

function callerSafeDetail(status: number, parsed: unknown): string | null {
  if (!DETAIL_PASSTHROUGH_STATUSES.has(status)) return null;
  const detail = (parsed as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" && detail.trim() ? detail : null;
}

// Route ids as validated integers, or a 400 response. They land in an upstream
// URL path segment, so a value like "1/../../health" would retarget the request.
type RouteIds =
  | { error: NextResponse; experiment?: undefined; scan?: undefined }
  | { error?: undefined; experiment: number; scan: number };

function parseRouteIds(experimentId: string, scanId: string): RouteIds {
  const experiment = parseId(experimentId);
  const scan = parseId(scanId);
  if (experiment === null || scan === null) {
    return {
      error: NextResponse.json(
        { detail: "experimentId and scanId must be positive integers" },
        { status: 400 }
      ),
    };
  }
  return { experiment, scan };
}

// Reading whether a video has landed lives at /api/cyl/scans/{id}/video: it is
// keyed by scan alone, and a path carrying an experiment id it never checked
// would claim a scoping the lookup does not do. Only generation needs the pair,
// and only generation has something upstream that verifies it.

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ experimentId: string; scanId: string }> }
) {
  const { experimentId, scanId } = await params;

  const ids = parseRouteIds(experimentId, scanId);
  if (ids.error) return ids.error;
  const { experiment, scan } = ids;

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

  // The product does not offer replacing a stored video. The object is overwritten in place on
  // a bucket with no versioning, and the recorded frame counts a comparison would rest on are
  // derived from `cyl_images` rather than measured from the files — an upper bound on what is
  // in them. Regeneration follows measuring those counts, not this change.
  //
  // This refusal covers the browser path only — the service is also reachable directly, and
  // keeps a stored video on its own account there. A lookup that failed is refused too:
  // only a confirmed absence may proceed.
  const stored = await getStoredScanVideo(scan);
  if (stored.status === "present") {
    return NextResponse.json(
      {
        detail:
          "This scan already has a video. Open the stored one — existing videos are not regenerated.",
      },
      { status: 409 }
    );
  }
  if (stored.status === "unknown") {
    return NextResponse.json(
      {
        detail:
          "Could not check whether this scan already has a video, so generation was not started. Try again shortly.",
      },
      { status: 503 }
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

  // Forward only the detail, and only where it is meant for the caller —
  // the rest of an upstream error body is operator diagnostics.
  if (!upstream.ok) {
    return NextResponse.json(
      { detail: callerSafeDetail(upstream.status, parsed) },
      { status: upstream.status }
    );
  }

  return NextResponse.json(parsed, { status: upstream.status });
}
